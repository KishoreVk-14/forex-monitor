#!/usr/bin/env python3
"""Install signal-based read-only Forex paper trading."""
from pathlib import Path

engine = r'''import logging
from datetime import datetime, timezone
import pandas as pd
from app.config import PAIRS, INTERVAL
from app.database.db import connect, read_candles
from app.strategies.signals import analyze

INITIAL = 10000.0
RISK = 0.01
MAX_OPEN = 3
MAX_LEVERAGE = 5.0
ATR_STOP = 1.5
REWARD_RISK = 2.0
SPREAD_PIPS = 1.0

def now():
    return datetime.now(timezone.utc).isoformat()

def pip(symbol):
    return 0.01 if symbol.endswith("/JPY") else 0.0001

def init_db():
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS fx_paper_account(id INTEGER PRIMARY KEY CHECK(id=1),initial_capital REAL NOT NULL,cash_balance REAL NOT NULL,created_at TEXT NOT NULL)""")
        c.execute("""INSERT OR IGNORE INTO fx_paper_account VALUES(1,?,?,?)""",(INITIAL,INITIAL,now()))
        c.execute("""CREATE TABLE IF NOT EXISTS fx_paper_trades(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT NOT NULL,side TEXT NOT NULL,status TEXT NOT NULL,signal TEXT NOT NULL,entry_time TEXT NOT NULL,entry_price REAL NOT NULL,units REAL NOT NULL,stop_loss REAL NOT NULL,take_profit REAL NOT NULL,risk_usd REAL NOT NULL,exit_time TEXT,exit_price REAL,exit_reason TEXT,pnl_quote REAL,pnl_usd REAL,quote_to_usd REAL,spread_pips REAL NOT NULL,created_at TEXT NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_paper_status ON fx_paper_trades(status,symbol)")
        c.execute("""CREATE TABLE IF NOT EXISTS fx_paper_state(symbol TEXT PRIMARY KEY,last_processed_time TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS fx_paper_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_time TEXT NOT NULL,symbol TEXT,event TEXT NOT NULL,detail TEXT NOT NULL)""")

def event(symbol, kind, detail):
    with connect() as c:
        c.execute("INSERT INTO fx_paper_events(event_time,symbol,event,detail) VALUES(?,?,?,?)",(now(),symbol,kind,str(detail)[:400]))

def price_map():
    out={}
    for s in PAIRS:
        r=read_candles(s,INTERVAL,1)
        if r: out[s]=float(r[-1]["close"])
    return out

def quote_usd(symbol, prices):
    quote=symbol.split("/")[1]
    if quote=="USD": return 1.0
    rate=prices.get("USD/"+quote)
    return 1.0/rate if rate and rate>0 else None

def account():
    with connect() as c: return dict(c.execute("SELECT * FROM fx_paper_account WHERE id=1").fetchone())

def open_trades():
    with connect() as c: return [dict(x) for x in c.execute("SELECT * FROM fx_paper_trades WHERE status='OPEN'")]

def open_for(symbol):
    with connect() as c:
        r=c.execute("SELECT * FROM fx_paper_trades WHERE symbol=? AND status='OPEN' ORDER BY id DESC LIMIT 1",(symbol,)).fetchone()
        return dict(r) if r else None

def close_trade(t, price, timestamp, reason, conversion):
    direction=1 if t["side"]=="LONG" else -1
    pnlq=(float(price)-t["entry_price"])*t["units"]*direction
    pnlu=pnlq*conversion if conversion is not None else None
    with connect() as c:
        c.execute("UPDATE fx_paper_trades SET status='CLOSED',exit_time=?,exit_price=?,exit_reason=?,pnl_quote=?,pnl_usd=?,quote_to_usd=? WHERE id=? AND status='OPEN'",(timestamp,price,reason,pnlq,pnlu,conversion,t["id"]))
        if pnlu is not None: c.execute("UPDATE fx_paper_account SET cash_balance=cash_balance+? WHERE id=1",(pnlu,))
    event(t["symbol"],"TRADE_CLOSED",f"Trade #{t['id']} {reason}; P&L {pnlu:.2f} USD" if pnlu is not None else f"Trade #{t['id']} closed; quote P&L {pnlq:.2f}, USD conversion unavailable")

def open_trade(symbol, side, candle, indicators, prices):
    if len(open_trades())>=MAX_OPEN: return
    atr=indicators.get("atr_14")
    if atr is None or pd.isna(atr) or float(atr)<=0: return
    close=float(candle["close"])
    half=pip(symbol)*SPREAD_PIPS/2
    entry=close+half if side=="LONG" else close-half
    distance=float(atr)*ATR_STOP
    stop=entry-distance if side=="LONG" else entry+distance
    target=entry+distance*REWARD_RISK if side=="LONG" else entry-distance*REWARD_RISK
    conversion=quote_usd(symbol,prices)
    base,quote=symbol.split("/")
    base_usd=1.0 if base=="USD" else entry if quote=="USD" else None
    if conversion is None or base_usd is None:
        event(symbol,"ENTRY_SKIPPED","USD conversion unavailable"); return
    equity=float(account()["cash_balance"])
    risk_usd=equity*RISK
    units=min(risk_usd/(distance*conversion),equity*MAX_LEVERAGE/base_usd)
    if units<=0: return
    with connect() as c:
        cur=c.execute("""INSERT INTO fx_paper_trades(symbol,side,status,signal,entry_time,entry_price,units,stop_loss,take_profit,risk_usd,quote_to_usd,spread_pips,created_at) VALUES(?,?,'OPEN',?,?,?,?,?,?,?,?,?,?)""",(symbol,side,"BULLISH" if side=="LONG" else "BEARISH",str(candle["datetime"]),entry,units,stop,target,risk_usd,conversion,SPREAD_PIPS,now()))
        tid=cur.lastrowid
    event(symbol,"TRADE_OPENED",f"Trade #{tid} {side}; entry {entry:.6f}; SL {stop:.6f}; TP {target:.6f}; units {units:.0f}")

def process_bar(symbol, candle, frame, prices):
    t=open_for(symbol)
    high,low,close=float(candle["high"]),float(candle["low"]),float(candle["close"])
    stamp=str(candle["datetime"])
    if t:
        if t["side"]=="LONG":
            stop_hit,target_hit=low<=t["stop_loss"],high>=t["take_profit"]
        else:
            stop_hit,target_hit=high>=t["stop_loss"],low<=t["take_profit"]
        # Conservative fill if both stop and target occur in one candle.
        if stop_hit:
            px=min(float(candle["open"]),t["stop_loss"]) if t["side"]=="LONG" else max(float(candle["open"]),t["stop_loss"])
            close_trade(t,px,stamp,"STOP_LOSS",quote_usd(symbol,prices)); return
        if target_hit:
            close_trade(t,t["take_profit"],stamp,"TAKE_PROFIT",quote_usd(symbol,prices)); return
    sig=analyze(frame).get("signal")
    if t:
        reverse=(t["side"]=="LONG" and sig=="BEARISH") or (t["side"]=="SHORT" and sig=="BULLISH")
        if reverse:
            px=close-pip(symbol)*SPREAD_PIPS/2 if t["side"]=="LONG" else close+pip(symbol)*SPREAD_PIPS/2
            close_trade(t,px,stamp,"REVERSE_SIGNAL",quote_usd(symbol,prices))
        return
    if sig in ("BULLISH","BEARISH"):
        result=analyze(frame)
        open_trade(symbol,"LONG" if sig=="BULLISH" else "SHORT",candle,result.get("indicators",{}),prices)

def run_once():
    init_db()
    prices=price_map()
    count=0
    for symbol in PAIRS:
        rows=read_candles(symbol,INTERVAL,500)
        if not rows: continue
        with connect() as c:
            state=c.execute("SELECT last_processed_time FROM fx_paper_state WHERE symbol=?",(symbol,)).fetchone()
            if state is None:
                c.execute("INSERT INTO fx_paper_state VALUES(?,?)",(symbol,str(rows[-1]["datetime"])))
                event(symbol,"BASELINE_SET",f"Started after existing history at {rows[-1]['datetime']}; no backfilled trades")
                continue
            last=state["last_processed_time"]
        for row in [x for x in rows if str(x["datetime"])>last]:
            frame=pd.DataFrame([x for x in rows if str(x["datetime"])<=str(row["datetime"])])
            if len(frame)>=220: process_bar(symbol,row,frame,prices)
            with connect() as c: c.execute("UPDATE fx_paper_state SET last_processed_time=? WHERE symbol=?",(str(row["datetime"]),symbol))
            count+=1
    return count

def summary():
    init_db()
    a=account(); prices=price_map(); opened=open_trades()
    upnl=0.0
    for t in opened:
        p=prices.get(t["symbol"]); conv=quote_usd(t["symbol"],prices)
        if p is not None and conv is not None:
            upnl+=(p-t["entry_price"])*t["units"]*(1 if t["side"]=="LONG" else -1)*conv
    with connect() as c:
        closed=[dict(x) for x in c.execute("SELECT * FROM fx_paper_trades WHERE status='CLOSED'")]
        events=[dict(x) for x in c.execute("SELECT * FROM fx_paper_events ORDER BY id DESC LIMIT 10")]
        total=c.execute("SELECT COUNT(*) FROM fx_paper_trades").fetchone()[0]
    wins=[x for x in closed if (x["pnl_usd"] or 0)>0]
    losses=[x for x in closed if (x["pnl_usd"] or 0)<0]
    gp=sum(x["pnl_usd"] for x in wins); gl=abs(sum(x["pnl_usd"] for x in losses))
    return {"mode":"SIGNAL_PAPER_TRADING","read_only":True,"initial_capital":a["initial_capital"],"cash_balance":a["cash_balance"],"realized_pnl":sum(x["pnl_usd"] or 0 for x in closed),"unrealized_pnl":upnl,"equity":a["cash_balance"]+upnl,"open_trades":len(opened),"total_trades":total,"closed_trades":len(closed),"wins":len(wins),"losses":len(losses),"win_rate":len(wins)/len(closed)*100 if closed else 0,"profit_factor":gp/gl if gl else (None if gp==0 else 999),"max_open_trades":MAX_OPEN,"risk_per_trade_pct":1,"spread_pips":SPREAD_PIPS,"events":events}

def trades(status=None, limit=100):
    init_db()
    q="SELECT * FROM fx_paper_trades"; args=[]
    if status in ("OPEN","CLOSED"): q+=" WHERE status=?"; args.append(status)
    q+=" ORDER BY id DESC LIMIT ?"; args.append(min(500,max(1,int(limit))))
    with connect() as c: result=[dict(x) for x in c.execute(q,args)]
    prices=price_map()
    for t in result:
        if t["status"]=="OPEN" and t["symbol"] in prices:
            conv=quote_usd(t["symbol"],prices)
            if conv is not None:
                t["mark_price"]=prices[t["symbol"]]
                t["unrealized_pnl_usd"]=(prices[t["symbol"]]-t["entry_price"])*t["units"]*(1 if t["side"]=="LONG" else -1)*conv
    return result
'''

runner = r'''import logging, os, time
from app.paper_trading.engine import init_db, run_once
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
def main():
    init_db()
    seconds=max(15,int(os.getenv("FOREX_PAPER_POLL_SECONDS","30")))
    logging.info("Signal paper trader running in simulation-only mode")
    while True:
        try:
            n=run_once()
            if n: logging.info("Processed %s new candles",n)
        except Exception: logging.exception("Paper trading cycle failed")
        time.sleep(seconds)
if __name__=="__main__": main()
'''

service = """[Unit]
Description=Forex Monitor signal-based paper trader
After=network-online.target forex-collector.service
Wants=network-online.target
[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/forex-monitor
EnvironmentFile=/home/ubuntu/forex-monitor/.env
ExecStart=/home/ubuntu/forex-monitor/venv/bin/python -m app.paper_trading.runner
Restart=always
RestartSec=15
NoNewPrivileges=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
"""

# Patch V2 web API.
wp=Path("app/web.py")
w=wp.read_text(encoding="utf-8")
if "paper_summary_api" not in w:
    w=w.replace("from app.strategies.signals import analyze","from app.strategies.signals import analyze\nfrom app.paper_trading.engine import summary as paper_summary, trades as paper_trades")
    marker='@app.get("/")'
    routes='''@app.get("/api/paper/summary")
def paper_summary_api(): return jsonify(paper_summary())

@app.get("/api/paper/trades")
def paper_trades_api():
    try: limit=min(500,max(1,int(request.args.get("limit",100))))
    except ValueError: limit=100
    return jsonify(paper_trades(request.args.get("status"),limit))

'''
    if marker not in w: raise SystemExit("V2 Flask insertion point not found")
    wp.write_text(w.replace(marker,routes+marker),encoding="utf-8")

# Add a new dashboard tab and its summary/ledger.
hp=Path("templates/index.html"); h=hp.read_text(encoding="utf-8")
if 'data-tab="paper"' not in h:
    h=h.replace('<button data-tab="activity">◷ Activity & health</button>','<button data-tab="paper">▣ Paper trading</button><button data-tab="activity">◷ Activity & health</button>')
    section='''<section id="paper" class="tab"><div class="intro"><small>SIMULATED EXECUTION · NO BROKER CONNECTION</small><h2>Paper trading</h2><p>Automatic entries from the existing BULLISH / BEARISH signals. Virtual capital only.</p></div><div class="metrics"><article><small>VIRTUAL EQUITY</small><b id="paper-equity">$10,000</b><span id="paper-realized">Realized P&amp;L $0.00</span></article><article><small>OPEN POSITIONS</small><b id="paper-open">0 / 3</b><span id="paper-unrealized">Unrealized P&amp;L $0.00</span></article><article><small>CLOSED TRADES</small><b id="paper-closed">0</b><span id="paper-winrate">Win rate 0%</span></article><article><small>PROFIT FACTOR</small><b id="paper-pf">—</b><span>Closed trades only</span></article></div><div class="lower"><article class="panel"><small>RISK RULES</small><h3>Simulation configuration</h3><div class="paper-rules"><p>Starting virtual capital <b>$10,000</b></p><p>Risk per trade <b>1% of cash</b></p><p>Maximum open positions <b>3</b></p><p>Stop loss <b>1.5 × ATR(14)</b></p><p>Take profit <b>2R</b></p><p>Spread assumption <b>1 pip</b></p><p>Same-candle SL/TP <b>Stop first</b></p></div></article><article class="panel"><small>ENGINE ACTIVITY</small><h3 id="paper-mode">SIGNAL PAPER TRADING</h3><p class="muted">Processes each newly stored candle once. Existing history is used only for indicator warm-up; no historical trades are backfilled.</p><div id="paper-events" class="paper-events">Waiting for engine…</div></article></div><div class="sectiontitle"><div><small>CURRENT EXPOSURE</small><h2>Open positions</h2></div></div><article class="panel tablewrap"><table><thead><tr><th>PAIR</th><th>SIDE</th><th>ENTRY</th><th>MARK</th><th>STOP</th><th>TARGET</th><th>UNREALIZED P&amp;L</th></tr></thead><tbody id="paper-open-rows"></tbody></table></article><div class="sectiontitle" style="margin-top:25px"><div><small>EXECUTION LEDGER</small><h2>Trade history</h2></div></div><article class="panel tablewrap"><table><thead><tr><th>ID</th><th>PAIR</th><th>SIDE</th><th>ENTRY</th><th>EXIT</th><th>REASON</th><th>NET P&amp;L USD</th><th>STATUS</th></tr></thead><tbody id="paper-history-rows"></tbody></table></article></section>'''
    marker='<section id="activity" class="tab">'
    if marker not in h: raise SystemExit("V2 dashboard insertion point not found")
    hp.write_text(h.replace(marker,section+marker),encoding="utf-8")

cp=Path("static/styles.css")
cp.write_text(cp.read_text(encoding="utf-8")+"\n.paper-rules p{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding:8px 0;color:var(--muted);font-size:10px}.paper-rules b{color:var(--text);text-align:right}.paper-events{font-size:10px;color:var(--muted);line-height:1.8;margin-top:15px}.paper-events div{border-top:1px solid var(--line);padding:7px 0}.pnl-positive{color:var(--green)!important}.pnl-negative{color:var(--red)!important}\n",encoding="utf-8")

jp=Path("static/app.js"); j=jp.read_text(encoding="utf-8")
if "async function refreshPaper()" not in j:
    old="async function refresh(){try{data=await get('/api/market');render();await activity()}catch(e){$('#connection').textContent='● API ERROR'}}"
    new="""async function refreshPaper(){try{let s=await get('/api/paper/summary'),t=await get('/api/paper/trades?limit=100'),o=t.filter(x=>x.status==='OPEN'),c=t.filter(x=>x.status==='CLOSED');let money=x=>x==null?'—':(x>=0?'+':'')+'$'+Number(x).toFixed(2),cls=x=>x>0?'pnl-positive':x<0?'pnl-negative': '';$('#paper-equity').textContent='$'+Number(s.equity).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});$('#paper-realized').textContent='Realized P&L '+money(s.realized_pnl);$('#paper-open').textContent=s.open_trades+' / '+s.max_open_trades;$('#paper-unrealized').textContent='Unrealized P&L '+money(s.unrealized_pnl);$('#paper-closed').textContent=s.closed_trades;$('#paper-winrate').textContent='Win rate '+Number(s.win_rate).toFixed(1)+'%';$('#paper-pf').textContent=s.profit_factor==null?'—':Number(s.profit_factor).toFixed(2);$('#paper-mode').textContent=s.mode+' · READ ONLY';$('#paper-open-rows').innerHTML=o.map(x=>'<tr><td>'+x.symbol+'</td><td>'+x.side+'</td><td class=\"mono\">'+Number(x.entry_price).toFixed(5)+'</td><td class=\"mono\">'+(x.mark_price==null?'—':Number(x.mark_price).toFixed(5))+'</td><td class=\"mono\">'+Number(x.stop_loss).toFixed(5)+'</td><td class=\"mono\">'+Number(x.take_profit).toFixed(5)+'</td><td class=\"'+cls(x.unrealized_pnl_usd)+'\">'+money(x.unrealized_pnl_usd)+'</td></tr>').join('')||'<tr><td colspan=\"7\">No open positions yet. Waiting for a new closed candle.</td></tr>';$('#paper-history-rows').innerHTML=c.map(x=>'<tr><td>'+x.id+'</td><td>'+x.symbol+'</td><td>'+x.side+'</td><td>'+Number(x.entry_price).toFixed(5)+'</td><td>'+(x.exit_price==null?'—':Number(x.exit_price).toFixed(5))+'</td><td>'+x.exit_reason+'</td><td class=\"'+cls(x.pnl_usd)+'\">'+money(x.pnl_usd)+'</td><td>'+x.status+'</td></tr>').join('')||'<tr><td colspan=\"8\">No closed trades yet.</td></tr>';$('#paper-events').innerHTML=(s.events||[]).map(x=>'<div><b>'+x.event+'</b> · '+x.symbol+'<br>'+x.detail+'</div>').join('')||'No engine events yet'}catch(e){$('#paper-mode').textContent='PAPER API ERROR: '+e.message}}
async function refresh(){try{data=await get('/api/market');render();await activity();await refreshPaper()}catch(e){$('#connection').textContent='● API ERROR'}}"""
    if old not in j: raise SystemExit("V2 refresh function not found")
    j=j.replace(old,new)
    j=j.replace("activity:'Activity & health'})[id]","activity:'Activity & health',paper:'Paper trading'})[id]")
    jp.write_text(j,encoding="utf-8")

for path,body in {
"app/paper_trading/__init__.py":"",
"app/paper_trading/engine.py":engine,
"app/paper_trading/runner.py":runner,
"systemd/forex-paper-trader.service":service
}.items():
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(body,encoding="utf-8")

print("Installed signal-driven paper trading engine and dashboard APIs.")
print("Initial run seeds current candles only; trading starts on newly collected candles.")
