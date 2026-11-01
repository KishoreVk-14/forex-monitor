from pathlib import Path
import os

# 1) Preserve the exact existing signal thresholds/classification, but explain every gate.
signals = '''from app.indicators.technical import calculate

def analyze(df):
    if df is None or len(df) < 220:
        return {"signal":"WARMING_UP","reasons":["EMA 200 warm-up: need at least 220 bars; have %d" % (0 if df is None else len(df))],"indicators":{},"conditions":[]}
    r=calculate(df).iloc[-1]
    keys=("ema_50","ema_200","rsi_14","adx_14","atr_14","plus_di","minus_di")
    v={k:(None if k not in r or r[k]!=r[k] else float(r[k])) for k in keys}
    if any(v[k] is None for k in keys):
        missing=[k for k in keys if v[k] is None]
        return {"signal":"WARMING_UP","reasons":["Indicator warm-up incomplete: "+", ".join(missing)],"indicators":v,"conditions":[]}
    bull_ema=v["ema_50"]>v["ema_200"]
    bear_ema=v["ema_50"]<v["ema_200"]
    bull_di=v["plus_di"]>v["minus_di"]
    bear_di=v["minus_di"]>v["plus_di"]
    adx_ok=v["adx_14"]>=20
    bull_rsi=50<=v["rsi_14"]<=68
    bear_rsi=32<=v["rsi_14"]<=50
    conditions=[
      {"name":"EMA trend","bullish":bull_ema,"bearish":bear_ema,"value":"EMA50 %.5f / EMA200 %.5f"%(v["ema_50"],v["ema_200"]),"rule":"EMA50 above EMA200 for LONG; below for SHORT"},
      {"name":"Directional movement","bullish":bull_di,"bearish":bear_di,"value":"+DI %.2f / -DI %.2f"%(v["plus_di"],v["minus_di"]),"rule":"+DI > -DI for LONG; -DI > +DI for SHORT"},
      {"name":"ADX strength","bullish":adx_ok,"bearish":adx_ok,"value":"ADX %.1f (minimum 20)"%v["adx_14"],"rule":"ADX >= 20"},
      {"name":"RSI momentum","bullish":bull_rsi,"bearish":bear_rsi,"value":"RSI %.1f"%v["rsi_14"],"rule":"50–68 for LONG; 32–50 for SHORT"}
    ]
    bull=bull_ema and bull_di and adx_ok and bull_rsi
    bear=bear_ema and bear_di and adx_ok and bear_rsi
    if bull: signal="BULLISH"
    elif bear: signal="BEARISH"
    elif (bull_ema and bull_di) or (bear_ema and bear_di): signal="WATCH"
    else: signal="NEUTRAL"
    reasons=[]
    for c in conditions:
        if signal=="BULLISH": passed=c["bullish"]
        elif signal=="BEARISH": passed=c["bearish"]
        else: passed=(c["bullish"] or c["bearish"])
        reasons.append(("PASS: " if passed else "NOT MET: ")+c["name"]+" — "+c["value"])
    if signal=="WATCH":
        if not adx_ok: reasons.append("ENTRY BLOCKED: ADX is below the required 20 trend-strength threshold")
        if not bull_rsi and not bear_rsi: reasons.append("ENTRY BLOCKED: RSI is outside both permitted entry ranges")
    elif signal=="NEUTRAL":
        reasons.append("ENTRY BLOCKED: EMA trend and directional movement are not aligned")
    return {"signal":signal,"indicators":v,"reasons":reasons,"conditions":conditions}
'''

# 2) Add a persistent scan heartbeat, diagnostics and date/symbol filtering to paper engine.
ep=Path("app/paper_trading/engine.py")
e=ep.read_text(encoding="utf-8")
if "fx_paper_runtime" not in e:
    e=e.replace('''        c.execute("""CREATE TABLE IF NOT EXISTS fx_paper_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_time TEXT NOT NULL,symbol TEXT,event TEXT NOT NULL,detail TEXT NOT NULL)""")''','''        c.execute("""CREATE TABLE IF NOT EXISTS fx_paper_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event_time TEXT NOT NULL,symbol TEXT,event TEXT NOT NULL,detail TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS fx_paper_runtime(id INTEGER PRIMARY KEY CHECK(id=1),last_scan_at TEXT,last_processed_count INTEGER DEFAULT 0)""")
        c.execute("INSERT OR IGNORE INTO fx_paper_runtime(id,last_processed_count) VALUES(1,0)")''')
    e=e.replace('''    return count

def summary():''','''    with connect() as c:
        c.execute("UPDATE fx_paper_runtime SET last_scan_at=?,last_processed_count=? WHERE id=1",(now(),count))
    return count

def summary():''')
    e=e.replace('''        events=[dict(x) for x in c.execute("SELECT * FROM fx_paper_events ORDER BY id DESC LIMIT 10")]
        total=c.execute("SELECT COUNT(*) FROM fx_paper_trades").fetchone()[0]''','''        events=[dict(x) for x in c.execute("SELECT * FROM fx_paper_events ORDER BY id DESC LIMIT 10")]
        total=c.execute("SELECT COUNT(*) FROM fx_paper_trades").fetchone()[0]
        runtime=c.execute("SELECT * FROM fx_paper_runtime WHERE id=1").fetchone()''')
    e=e.replace('''"max_open_trades":MAX_OPEN,"risk_per_trade_pct":1,"spread_pips":SPREAD_PIPS,"events":events}''','''"max_open_trades":MAX_OPEN,"risk_per_trade_pct":1,"spread_pips":SPREAD_PIPS,"scan_interval_seconds":30,"last_scan_at":runtime["last_scan_at"] if runtime else None,"last_processed_count":runtime["last_processed_count"] if runtime else 0,"events":events}''')
# Replace trades() with filter-capable implementation.
start=e.find("def trades(status=None, limit=100):")
if start<0: raise SystemExit("Could not find trades() in engine.py")
e=e[:start]+'''def trades(status=None, limit=100, symbol=None, date_from=None, date_to=None):
    init_db()
    q="SELECT * FROM fx_paper_trades WHERE 1=1"; args=[]
    if status in ("OPEN","CLOSED"):
        q+=" AND status=?"; args.append(status)
    if symbol:
        q+=" AND symbol=?"; args.append(symbol)
    if date_from:
        q+=" AND date(entry_time)>=date(?)"; args.append(date_from)
    if date_to:
        q+=" AND date(entry_time)<=date(?)"; args.append(date_to)
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
ep.write_text(e,encoding="utf-8")

# 3) Replace API route to accept filters.
wp=Path("app/web.py"); w=wp.read_text(encoding="utf-8")
old='''    return jsonify(paper_trades(request.args.get("status"),limit))'''
new='''    symbol=request.args.get("symbol") or None
    if symbol not in PAIRS: symbol=None
    date_from=request.args.get("date_from") or None
    date_to=request.args.get("date_to") or None
    return jsonify(paper_trades(request.args.get("status"),limit,symbol,date_from,date_to))'''
if old in w: w=w.replace(old,new)
else: print("Paper trades route filter insertion may already exist")
wp.write_text(w,encoding="utf-8")

# 4) Replace strategy file, preserving all current signal decision thresholds.
sp=Path("app/strategies/signals.py"); sp.write_text(signals,encoding="utf-8")

# 5) Add trade filters and transparent strategy criteria to dashboard.
hp=Path("templates/index.html"); h=hp.read_text(encoding="utf-8")
if 'id="paper-filter-symbol"' not in h:
    h=h.replace('''<div class="sectiontitle"><div><small>CURRENT EXPOSURE</small><h2>Open positions</h2></div></div>''','''<div class="sectiontitle"><div><small>CURRENT EXPOSURE</small><h2>Open positions</h2></div></div>
<div class="paper-filters"><label>Currency pair<select id="paper-filter-symbol"><option value="">All pairs</option><option>EUR/USD</option><option>GBP/USD</option><option>USD/JPY</option><option>AUD/USD</option><option>USD/CHF</option><option>USD/CAD</option></select></label><label>From date<input type="date" id="paper-filter-from"></label><label>To date<input type="date" id="paper-filter-to"></label><button id="paper-filter-apply">Apply filters</button><button id="paper-filter-clear" class="link">Clear</button></div>''')
    h=h.replace('''<div class="sectiontitle" style="margin-top:25px"><div><small>EXECUTION LEDGER</small><h2>Trade history</h2></div></div>''','''<div class="sectiontitle" style="margin-top:25px"><div><small>EXECUTION LEDGER</small><h2>Trade history</h2></div></div>''')
    h=h.replace('''<div id="paper-events" class="paper-events">Waiting for engine…</div>''','''<div id="paper-events" class="paper-events">Waiting for engine…</div><p class="muted" id="paper-scan-info">Scan schedule loading…</p>''')
    hp.write_text(h,encoding="utf-8")

# 6) Detailed reason display + refresh/filter behavior.
jp=Path("static/app.js"); j=jp.read_text(encoding="utf-8")
# replace the reasons area in signal cards: detailed reasons already supplied by analyze().
j=j.replace('''<div class="reasons">'+(x.reasons||[]).join('<br>')+'</div></article>''','''<div class="reasons">'+(x.reasons||[]).map(r=>'<div>'+r+'</div>').join('')+'</div></article>''')
# replace paper fetch with filter-aware version and scan timestamp display
j=j.replace('''async function refreshPaper(){try{let s=await get('/api/paper/summary'),t=await get('/api/paper/trades?limit=100'),o=t.filter(x=>x.status==='OPEN'),c=t.filter(x=>x.status==='CLOSED');''','''async function refreshPaper(){try{let s=await get('/api/paper/summary');let qs=new URLSearchParams({limit:'500'});if($('#paper-filter-symbol')?.value)qs.set('symbol',$('#paper-filter-symbol').value);if($('#paper-filter-from')?.value)qs.set('date_from',$('#paper-filter-from').value);if($('#paper-filter-to')?.value)qs.set('date_to',$('#paper-filter-to').value);let t=await get('/api/paper/trades?'+qs.toString()),o=t.filter(x=>x.status==='OPEN'),c=t.filter(x=>x.status==='CLOSED');''')
j=j.replace('''$('#paper-mode').textContent=s.mode+' · READ ONLY';''','''$('#paper-mode').textContent=s.mode+' · READ ONLY';$('#paper-scan-info').textContent='Last engine scan: '+(s.last_scan_at||'starting')+' · Checks every 30 seconds · New entries only after a newly stored closed 15-minute candle (collector interval 15 minutes). Last scan processed '+(s.last_processed_count||0)+' new candle(s).';''')
# bind controls and refresh on filter change; filters affect both open list and history.
if "paper-filter-apply" not in j:
    j += '''
document.querySelector('#paper-filter-apply')?.addEventListener('click',refreshPaper);
document.querySelector('#paper-filter-clear')?.addEventListener('click',()=>{['paper-filter-symbol','paper-filter-from','paper-filter-to'].forEach(id=>{const x=document.getElementById(id);if(x)x.value=''});refreshPaper()});
'''
jp.write_text(j,encoding="utf-8")

# 7) CSS for filter row and condition explanations.
cp=Path("static/styles.css")
cp.write_text(cp.read_text(encoding="utf-8")+'''
.paper-filters{display:flex;align-items:end;gap:10px;flex-wrap:wrap;margin:12px 0 18px;padding:12px;background:var(--panel);border:1px solid var(--line);border-radius:9px}.paper-filters label{display:flex;flex-direction:column;gap:5px;color:var(--dim);font:9px var(--mono)}.paper-filters select,.paper-filters input{background:#172a40;color:var(--text);border:1px solid #2a405a;border-radius:6px;padding:8px;font:10px var(--font)}.paper-filters button{background:#244967;color:white;border:0;border-radius:6px;padding:9px 13px;font:10px var(--font)}.reasons div{padding:4px 0;border-bottom:1px solid #203249}.reasons div:first-child{margin-top:5px}
''',encoding="utf-8")

print("Added detailed strategy diagnostics, persistent scan heartbeat and date/pair trade filters.")
