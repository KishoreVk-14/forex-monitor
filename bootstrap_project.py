#!/usr/bin/env python3
"""Generate the read-only Forex Monitor application files."""
from pathlib import Path
FILES = {
"requirements.txt": "requests>=2.32,<3\npandas>=2.2,<4\nnumpy>=2.0,<3\npython-dotenv>=1,<2\nFlask>=3,<4\n",
".gitignore": ".env\nvenv/\n__pycache__/\n*.py[cod]\ndata/\nlogs/\n",
".env.example": "TWELVE_DATA_API_KEY=\nFOREX_DB_PATH=data/forex.db\nFOREX_PAIRS=EUR/USD,GBP/USD,USD/JPY,AUD/USD,USD/CHF,USD/CAD\nFOREX_INTERVAL=15min\nFOREX_POLL_INTERVAL_SECONDS=900\nFOREX_API_OUTPUTSIZE=500\nFOREX_WEB_HOST=127.0.0.1\nFOREX_WEB_PORT=8050\n",
"app/__init__.py": "",
"app/config.py": """import os
from dotenv import load_dotenv
load_dotenv()
PAIRS=[x.strip() for x in os.getenv("FOREX_PAIRS","EUR/USD,GBP/USD,USD/JPY,AUD/USD,USD/CHF,USD/CAD").split(",") if x.strip()]
INTERVAL=os.getenv("FOREX_INTERVAL","15min")
POLL_SECONDS=max(60,int(os.getenv("FOREX_POLL_INTERVAL_SECONDS","900")))
OUTPUTSIZE=min(5000,max(250,int(os.getenv("FOREX_API_OUTPUTSIZE","500"))))
DB_PATH=os.getenv("FOREX_DB_PATH","data/forex.db")
""",
"app/api/__init__.py": "",
"app/api/twelve_data.py": """import os,time,requests,pandas as pd
from dotenv import load_dotenv
load_dotenv()
def fetch_candles(symbol,interval="15min",outputsize=500):
 key=os.getenv("TWELVE_DATA_API_KEY")
 if not key: raise RuntimeError("TWELVE_DATA_API_KEY missing in .env")
 params={"symbol":symbol,"interval":interval,"outputsize":outputsize,"apikey":key,"timezone":"UTC","format":"JSON"}
 err=None
 for n in range(3):
  try:
   r=requests.get("https://api.twelvedata.com/time_series",params=params,timeout=30); r.raise_for_status()
   j=r.json()
   if "values" not in j: raise RuntimeError(j.get("message","Unknown Twelve Data error"))
   d=pd.DataFrame(j["values"]); d["datetime"]=pd.to_datetime(d["datetime"],utc=True)
   for c in ("open","high","low","close","volume"):
    if c in d: d[c]=pd.to_numeric(d[c],errors="coerce")
   return d.dropna(subset=["open","high","low","close"]).sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
  except Exception as e:
   err=e
   if n<2: time.sleep(2**n)
 raise RuntimeError(f"{symbol}: {err}")
""",
"app/database/__init__.py": "",
"app/database/db.py": """import os,sqlite3
from app.config import DB_PATH
def connect():
 os.makedirs(os.path.dirname(DB_PATH) or ".",exist_ok=True)
 c=sqlite3.connect(DB_PATH,timeout=30); c.row_factory=sqlite3.Row; c.execute("PRAGMA journal_mode=WAL"); return c
def init_db():
 with connect() as c:
  c.execute('''CREATE TABLE IF NOT EXISTS fx_candles(id INTEGER PRIMARY KEY,symbol TEXT NOT NULL,interval TEXT NOT NULL,candle_time TEXT NOT NULL,open REAL NOT NULL,high REAL NOT NULL,low REAL NOT NULL,close REAL NOT NULL,volume REAL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(symbol,interval,candle_time))''')
  c.execute('''CREATE TABLE IF NOT EXISTS fx_runs(id INTEGER PRIMARY KEY,started_at TEXT DEFAULT CURRENT_TIMESTAMP,symbol TEXT,rows_received INTEGER,status TEXT,message TEXT)''')
def save_candles(symbol,interval,df):
 init_db(); rows=[]
 for r in df.itertuples(index=False):
  v=getattr(r,"volume",None)
  rows.append((symbol,interval,r.datetime.isoformat(),float(r.open),float(r.high),float(r.low),float(r.close),None if v is None else float(v)))
 with connect() as c:
  c.executemany('''INSERT INTO fx_candles(symbol,interval,candle_time,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol,interval,candle_time) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume''',rows)
 return len(rows)
def read_candles(symbol=None,interval=None,limit=500):
 init_db(); q="SELECT symbol,interval,candle_time AS datetime,open,high,low,close,volume FROM fx_candles WHERE 1=1"; a=[]
 if symbol: q+=" AND symbol=?"; a.append(symbol)
 if interval: q+=" AND interval=?"; a.append(interval)
 q+=" ORDER BY candle_time DESC LIMIT ?"; a.append(limit)
 with connect() as c: rows=c.execute(q,a).fetchall()
 return [dict(x) for x in reversed(rows)]
def log_run(symbol,count,status,message=""):
 with connect() as c: c.execute("INSERT INTO fx_runs(symbol,rows_received,status,message) VALUES(?,?,?,?)",(symbol,count,status,str(message)[:400]))
def latest_runs():
 with connect() as c: return [dict(x) for x in c.execute("SELECT * FROM fx_runs ORDER BY id DESC LIMIT 25")]
""",
"app/indicators/__init__.py": "",
"app/indicators/technical.py": """import numpy as np,pandas as pd
def rsi(s,n=14):
 d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
 ag=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); al=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
 z=100-100/(1+ag/al); return z.mask((al==0)&(ag>0),100).mask((al==0)&(ag==0),50)
def calculate(df):
 x=df.copy()
 for c in ("open","high","low","close"): x[c]=pd.to_numeric(x[c],errors="coerce")
 x["ema_50"]=x.close.ewm(span=50,adjust=False,min_periods=50).mean()
 x["ema_200"]=x.close.ewm(span=200,adjust=False,min_periods=200).mean()
 x["rsi_14"]=rsi(x.close)
 pc=x.close.shift(); tr=pd.concat([x.high-x.low,(x.high-pc).abs(),(x.low-pc).abs()],axis=1).max(axis=1)
 atr=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); x["atr_14"]=atr
 up=x.high.diff(); dn=-x.low.diff()
 p=pd.Series(np.where((up>dn)&(up>0),up,0),index=x.index).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
 m=pd.Series(np.where((dn>up)&(dn>0),dn,0),index=x.index).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
 x["plus_di"]=100*p/atr; x["minus_di"]=100*m/atr
 dx=100*(x.plus_di-x.minus_di).abs()/(x.plus_di+x.minus_di).replace(0,np.nan)
 x["adx_14"]=dx.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
 return x
""",
"app/strategies/__init__.py": "",
"app/strategies/signals.py": """from app.indicators.technical import calculate
def analyze(df):
 if df is None or len(df)<220: return {"signal":"WARMING_UP","reasons":["At least 220 bars required for EMA200 warm-up"],"indicators":{}}
 r=calculate(df).iloc[-1]; keys=("ema_50","ema_200","rsi_14","adx_14","atr_14","plus_di","minus_di")
 v={k:(None if k not in r or r[k]!=r[k] else float(r[k])) for k in keys}
 if any(v[k] is None for k in keys): return {"signal":"WARMING_UP","reasons":["Indicator warm-up incomplete"],"indicators":v}
 bull=v["ema_50"]>v["ema_200"] and v["plus_di"]>v["minus_di"]; bear=v["ema_50"]<v["ema_200"] and v["minus_di"]>v["plus_di"]
 if bull and v["adx_14"]>=20 and 50<=v["rsi_14"]<=68: s="BULLISH"
 elif bear and v["adx_14"]>=20 and 32<=v["rsi_14"]<=50: s="BEARISH"
 elif bull or bear: s="WATCH"
 else: s="NEUTRAL"
 return {"signal":s,"indicators":v,"reasons":[f"RSI {v['rsi_14']:.1f}",f"ADX {v['adx_14']:.1f}",f"+DI {v['plus_di']:.2f} / -DI {v['minus_di']:.2f}"]}
""",
"app/data/__init__.py": "",
"app/data/market_collector.py": """import time,logging
from datetime import datetime,timezone
from app.config import PAIRS,INTERVAL,OUTPUTSIZE,POLL_SECONDS
from app.api.twelve_data import fetch_candles
from app.database.db import save_candles,log_run
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
DUR={"1min":60,"5min":300,"15min":900,"30min":1800,"1h":3600,"2h":7200,"4h":14400}
def collect_once():
 total=0
 for pair in PAIRS:
  try:
   d=fetch_candles(pair,INTERVAL,OUTPUTSIZE); sec=DUR.get(INTERVAL)
   if sec:
    now=datetime.now(timezone.utc).timestamp()
    d=d[d.datetime.apply(lambda t:t.timestamp()+sec<=now)]
   n=save_candles(pair,INTERVAL,d); log_run(pair,n,"OK"); total+=n; logging.info("%s saved %d",pair,n)
  except Exception as e: log_run(pair,0,"ERROR",e); logging.exception("Pair failed: %s",pair)
  time.sleep(9)
 return total
def run_forever():
 while True: collect_once(); time.sleep(POLL_SECONDS)
""",
"app/web.py": """from flask import Flask,render_template,jsonify,request
import pandas as pd
from app.config import PAIRS,INTERVAL
from app.database.db import read_candles,latest_runs
from app.strategies.signals import analyze
app=Flask(__name__,template_folder="../templates",static_folder="../static")
@app.get("/health")
def health(): return jsonify(status="ok",mode="READ_ONLY")
@app.get("/api/market")
def market():
 out=[]
 for pair in PAIRS:
  rows=read_candles(pair,INTERVAL,500); df=pd.DataFrame(rows)
  s=analyze(df) if len(df) else {"signal":"NO_DATA","reasons":[],"indicators":{}}
  out.append(dict(symbol=pair,candles=len(df),last=rows[-1] if rows else None,**s))
 return jsonify(out)
@app.get("/api/history/<path:symbol>")
def history(symbol): return jsonify(read_candles(symbol,INTERVAL,min(1000,max(1,int(request.args.get("limit",300))))))
@app.get("/api/activity")
def activity(): return jsonify(latest_runs())
@app.get("/")
def index(): return render_template("index.html")
""",
"app/main.py": """import argparse,os
from app.database.db import init_db
from app.data.market_collector import collect_once,run_forever
from app.web import app
def main():
 p=argparse.ArgumentParser(); p.add_argument("command",choices=["init-db","collect-once","run-collector","web"]); a=p.parse_args()
 if a.command=="init-db": init_db(); print("Database ready")
 elif a.command=="collect-once": print("Rows:",collect_once())
 elif a.command=="run-collector": run_forever()
 else: app.run(host=os.getenv("FOREX_WEB_HOST","127.0.0.1"),port=int(os.getenv("FOREX_WEB_PORT","8050")),debug=False)
if __name__=="__main__": main()
""",
"templates/index.html": """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Forex Monitor</title><link rel="stylesheet" href="/static/styles.css"></head><body><header><h1>Forex Monitor</h1><b>READ ONLY</b></header><main><p>Major FX pairs · 15-minute bars · Informational signals only</p><button onclick="load()">Refresh</button><section id="cards">Loading…</section><h2>Collector activity</h2><div id="activity"></div></main><footer>No orders are placed. Confirm market-data display rights with your provider.</footer><script>
async function load(){try{let d=await(await fetch('/api/market')).json();document.querySelector('#cards').innerHTML=d.map(x=>'<article><h2>'+x.symbol+' <small>'+x.signal+'</small></h2><h3>'+(x.last?Number(x.last.close).toFixed(5):'No data')+'</h3><p>Bars: '+x.candles+' · RSI: '+(x.indicators.rsi_14??'—')+' · ADX: '+(x.indicators.adx_14??'—')+'</p><p>'+(x.reasons||[]).join(' | ')+'</p></article>').join('');let a=await(await fetch('/api/activity')).json();document.querySelector('#activity').innerHTML=a.map(x=>'<p>'+x.started_at+' '+x.symbol+' '+x.status+' '+x.rows_received+'</p>').join('')}catch(e){document.querySelector('#cards').textContent=e}}load();setInterval(load,60000)</script></body></html>
""",
"static/styles.css": """body{margin:0;background:#0b1220;color:#e6edf7;font:15px system-ui}header{padding:24px 5%;background:#142238;display:flex;justify-content:space-between}main{max-width:1100px;margin:30px auto;padding:0 20px}button{padding:10px 18px;background:#28628c;color:white;border:0;border-radius:8px}#cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:20px}article{background:#172a42;border:1px solid #30445e;border-radius:12px;padding:18px}article small{color:#76d9ae}footer{padding:30px;text-align:center;color:#9aabc2}
""",
"scripts/setup.sh": """#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
sudo apt-get update
sudo apt-get install -y python3-venv
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
mkdir -p data logs
[ -f .env ] || cp .env.example .env
venv/bin/python -m app.main init-db
echo "Setup complete. Add TWELVE_DATA_API_KEY to .env."
""",
"systemd/forex-collector.service": """[Unit]
Description=Read-only Forex collector
After=network-online.target
[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/forex-monitor
EnvironmentFile=/home/ubuntu/forex-monitor/.env
ExecStart=/home/ubuntu/forex-monitor/venv/bin/python -m app.main run-collector
Restart=always
RestartSec=30
[Install]
WantedBy=multi-user.target
""",
"systemd/forex-dashboard.service": """[Unit]
Description=Forex Monitor dashboard
After=network-online.target
[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/forex-monitor
EnvironmentFile=/home/ubuntu/forex-monitor/.env
ExecStart=/home/ubuntu/forex-monitor/venv/bin/python -m app.main web
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
""",
"tests/test_core.py": """import unittest,pandas as pd
from app.indicators.technical import rsi,calculate
from app.strategies.signals import analyze
class T(unittest.TestCase):
 def test_flat_rsi(self): self.assertEqual(float(rsi(pd.Series([1.]*40)).iloc[-1]),50.)
 def test_warmup(self): self.assertEqual(analyze(pd.DataFrame()).get("signal"),"WARMING_UP")
 def test_ema200(self):
  d=pd.DataFrame({"open":range(230),"high":[x+1 for x in range(230)],"low":[x-1 for x in range(230)],"close":range(230)})
  self.assertFalse(pd.isna(calculate(d).ema_200.iloc[-1]))
if __name__=="__main__": unittest.main()
"""
}
for name, text in FILES.items():
 p=Path(name); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text,encoding="utf-8")
print("Generated",len(FILES),"Forex Monitor files")
