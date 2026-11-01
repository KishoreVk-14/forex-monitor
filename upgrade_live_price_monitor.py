#!/usr/bin/env python3
"""Add an optional live-price feed without changing candle collection or entry signals."""
from pathlib import Path

def read(path):
    p=Path(path)
    if not p.exists():
        raise SystemExit(f"Required file missing: {path}. Install the V2 dashboard and paper trader first.")
    return p.read_text(encoding="utf-8")

def write(path, value):
    Path(path).write_text(value, encoding="utf-8")

# Add Twelve Data /price client. One request per symbol; keep this service independently configurable.
api=read("app/api/twelve_data.py")
if "def fetch_live_price(" not in api:
    api += '''

def fetch_live_price(symbol):
    """Fetch the latest provider quote. This is separate from completed OHLC candles."""
    key=os.getenv("TWELVE_DATA_API_KEY")
    if not key:
        raise RuntimeError("TWELVE_DATA_API_KEY missing in .env")
    response=requests.get(
        "https://api.twelvedata.com/price",
        params={"symbol":symbol,"apikey":key},
        timeout=15,
    )
    response.raise_for_status()
    payload=response.json()
    if "price" not in payload:
        raise RuntimeError(payload.get("message","Twelve Data returned no live price"))
    price=float(payload["price"])
    if not (price > 0):
        raise RuntimeError("Provider returned a non-positive price")
    return price
'''
    write("app/api/twelve_data.py",api)

# Store quotes in their own table; never overwrite candle history.
db=read("app/database/db.py")
if "def save_live_price(" not in db:
    db += '''

def save_live_price(symbol, price, source="twelvedata"):
    from datetime import datetime, timezone
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS fx_live_prices(
            symbol TEXT PRIMARY KEY, price REAL NOT NULL, observed_at TEXT NOT NULL,
            source TEXT NOT NULL)""")
        c.execute("""INSERT INTO fx_live_prices(symbol,price,observed_at,source)
            VALUES(?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET
            price=excluded.price,observed_at=excluded.observed_at,source=excluded.source""",
            (symbol,float(price),datetime.now(timezone.utc).isoformat(),source))

def latest_live_prices():
    with connect() as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fx_live_prices'").fetchone()
        if not exists:
            return {}
        rows=c.execute("SELECT symbol,price,observed_at,source FROM fx_live_prices").fetchall()
    return {r["symbol"]:dict(r) for r in rows}
'''
    write("app/database/db.py",db)

# Standalone collector process. It does not touch fx_candles or fx_runs.
Path("app/data/live_price_monitor.py").parent.mkdir(parents=True,exist_ok=True)
write("app/data/live_price_monitor.py",'''import logging, os, time
from datetime import datetime, timezone
from app.config import PAIRS
from app.api.twelve_data import fetch_live_price
from app.database.db import save_live_price

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")

def collect_live_prices():
    ok=0
    for symbol in PAIRS:
        try:
            price=fetch_live_price(symbol)
            save_live_price(symbol,price)
            ok+=1
            logging.info("%s live quote %.6f",symbol,price)
        except Exception:
            logging.exception("Live quote failed for %s",symbol)
        time.sleep(float(os.getenv("FOREX_LIVE_PRICE_SYMBOL_DELAY_SECONDS","1")))
    return ok

def main():
    seconds=max(30,int(os.getenv("FOREX_LIVE_PRICE_POLL_SECONDS","60")))
    logging.info("Live quote monitor started; interval=%ss; symbols=%s",seconds,",".join(PAIRS))
    while True:
        started=time.monotonic()
        try:
            count=collect_live_prices()
            logging.info("Live quote cycle complete: %s/%s pairs",count,len(PAIRS))
        except Exception:
            logging.exception("Live quote cycle failed")
        time.sleep(max(1,seconds-(time.monotonic()-started)))

if __name__=="__main__":
    main()
''')

# New read-only API endpoint and include quote snapshots in market payload.
web=read("app/web.py")
if "latest_live_prices" not in web:
    web=web.replace("from app.database.db import read_candles, latest_runs",
                    "from app.database.db import read_candles, latest_runs, latest_live_prices")
    web=web.replace("    out=[]\n    for pair in PAIRS:",
                    "    out=[]\n    live=latest_live_prices()\n    for pair in PAIRS:")
    web=web.replace("out.append(dict(symbol=pair,interval=INTERVAL,candles=len(rows),last=last,change_pct=change,**s))",
                    "out.append(dict(symbol=pair,interval=INTERVAL,candles=len(rows),last=last,change_pct=change,live_price=live.get(pair),**s))")
    marker='@app.get("/")'
    route='''@app.get("/api/live-prices")
def live_prices():
    from datetime import datetime, timezone
    from app.database.db import latest_live_prices
    now=datetime.now(timezone.utc)
    result=[]
    for symbol,item in latest_live_prices().items():
        try:
            age=max(0,(now-datetime.fromisoformat(item["observed_at"])).total_seconds())
        except Exception:
            age=None
        result.append({**item,"age_seconds":age,"stale":age is None or age>180})
    return jsonify(result)

'''
    if marker not in web:
        raise SystemExit("Cannot locate Flask home route in app/web.py")
    web=web.replace(marker,route+marker)
    write("app/web.py",web)

# Prefer fresh quotes for paper-account marking/conversion, but keep candle-based entry logic intact.
engine_path=Path("app/paper_trading/engine.py")
if engine_path.exists():
    engine=read(str(engine_path))
    if "def monitor_open_trades_live(" not in engine:
        engine=engine.replace(
            "from app.database.db import connect, read_candles",
            "from app.database.db import connect, read_candles, latest_live_prices"
        )
        # Extend price_map with quotes only when they are no older than three minutes.
        old='''def price_map():
    out={}
    for s in PAIRS:
        r=read_candles(s,INTERVAL,1)
        if r: out[s]=float(r[-1]["close"])
    return out'''
        new='''def price_map():
    out={}
    for s in PAIRS:
        r=read_candles(s,INTERVAL,1)
        if r: out[s]=float(r[-1]["close"])
    # Fresh provider quotes take precedence for valuation/conversion only.
    from datetime import timedelta
    live=latest_live_prices()
    current=datetime.now(timezone.utc)
    for s,item in live.items():
        try:
            observed=datetime.fromisoformat(item["observed_at"])
            if current-observed <= timedelta(minutes=3):
                out[s]=float(item["price"])
        except Exception:
            pass
    return out'''
        if old not in engine:
            raise SystemExit("Could not safely locate paper engine price_map()")
        engine=engine.replace(old,new)
        engine += '''

def monitor_open_trades_live():
    """Check open simulated positions against fresh provider quotes; never places broker orders."""
    init_db()
    quotes=latest_live_prices()
    prices=price_map()
    current=datetime.now(timezone.utc)
    from datetime import timedelta
    checked=0
    for trade in open_trades():
        item=quotes.get(trade["symbol"])
        if not item:
            continue
        try:
            observed=datetime.fromisoformat(item["observed_at"])
            if current-observed > timedelta(minutes=3):
                continue
            price=float(item["price"])
        except Exception:
            continue
        checked+=1
        hit=None
        if trade["side"]=="LONG":
            if price <= float(trade["stop_loss"]): hit=("LIVE_STOP_LOSS",price-pip(trade["symbol"])*SPREAD_PIPS/2)
            elif price >= float(trade["take_profit"]): hit=("LIVE_TAKE_PROFIT",price-pip(trade["symbol"])*SPREAD_PIPS/2)
        else:
            if price >= float(trade["stop_loss"]): hit=("LIVE_STOP_LOSS",price+pip(trade["symbol"])*SPREAD_PIPS/2)
            elif price <= float(trade["take_profit"]): hit=("LIVE_TAKE_PROFIT",price+pip(trade["symbol"])*SPREAD_PIPS/2)
        if hit:
            close_trade(trade,hit[1],current.isoformat(),hit[0],quote_usd(trade["symbol"],prices))
    return checked
'''
        write(str(engine_path),engine)
    # Run live monitoring before candle processing on each paper-trader cycle.
    runner_path=Path("app/paper_trading/runner.py")
    runner=read(str(runner_path))
    if "monitor_open_trades_live" not in runner:
        runner=runner.replace("from app.paper_trading.engine import init_db, run_once",
                              "from app.paper_trading.engine import init_db, run_once, monitor_open_trades_live")
        runner=runner.replace("            n=run_once()",
                              "            monitor_open_trades_live()\n            n=run_once()")
        write(str(runner_path),runner)

# Dashboard: show live quote and timestamp separately from the candle close used by indicators.
js=read("static/app.js")
if "LIVE QUOTE" not in js:
    js=js.replace(
        "fmt(x.last&&x.last.close,x.symbol.includes('JPY')?3:5)",
        "fmt(x.live_price?x.live_price.price:(x.last&&x.last.close),x.symbol.includes('JPY')?3:5)"
    )
    js=js.replace(
        "'<div class=\"muted\">'+(x.change_pct==null?",
        "'<div class=\"muted\">'+(x.live_price?'LIVE QUOTE · '+new Date(x.live_price.observed_at).toLocaleTimeString()+' · '+(x.live_price.source||'provider'):'LIVE QUOTE UNAVAILABLE · showing last closed candle')+'</div><div class=\"muted\">'+(x.change_pct==null?"
    )
    js=js.replace(
        "'LATEST STORED CLOSE · '+(x.last?x.last.datetime:'No data')",
        "'SIGNAL CANDLE CLOSE · '+(x.last?x.last.datetime:'No data')+' · LIVE QUOTE '+(x.live_price?fmt(x.live_price.price,x.symbol.includes('JPY')?3:5)+' @ '+new Date(x.live_price.observed_at).toLocaleTimeString():'unavailable')"
    )
    write("static/app.js",js)

# Dedicated systemd unit; deliberately not enabled automatically because API quota is plan-dependent.
Path("systemd").mkdir(exist_ok=True)
write("systemd/forex-live-prices.service",'''[Unit]
Description=Forex Monitor live quote poller
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/forex-monitor
EnvironmentFile=/home/ubuntu/forex-monitor/.env
Environment=FOREX_LIVE_PRICE_POLL_SECONDS=60
ExecStart=/home/ubuntu/forex-monitor/venv/bin/python -m app.data.live_price_monitor
Restart=always
RestartSec=15
NoNewPrivileges=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
''')

print("Live quote layer installed in source: independent quote table/API/service, dashboard quote display, and live SL/TP checks.")
print("Existing candle collector, indicator calculations, entry signals and trade history are preserved.")
print("The live-price service is NOT enabled automatically; confirm Twelve Data plan/API credits before starting it.")
