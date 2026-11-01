from pathlib import Path

p=Path("app/paper_trading/engine.py")
s=p.read_text(encoding="utf-8")
old='''        with connect() as c:
            state=c.execute("SELECT last_processed_time FROM fx_paper_state WHERE symbol=?",(symbol,)).fetchone()
            if state is None:
                c.execute("INSERT INTO fx_paper_state VALUES(?,?)",(symbol,str(rows[-1]["datetime"])))
                event(symbol,"BASELINE_SET",f"Started after existing history at {rows[-1]['datetime']}; no backfilled trades")
                continue
            last=state["last_processed_time"]
'''
new='''        baseline=False
        with connect() as c:
            state=c.execute("SELECT last_processed_time FROM fx_paper_state WHERE symbol=?",(symbol,)).fetchone()
            if state is None:
                c.execute("INSERT INTO fx_paper_state VALUES(?,?)",(symbol,str(rows[-1]["datetime"])))
                baseline=True
                last=None
            else:
                last=state["last_processed_time"]
        if baseline:
            event(symbol,"BASELINE_SET",f"Started after existing history at {rows[-1]['datetime']}; no backfilled trades")
            continue
'''
if old in s:
    p.write_text(s.replace(old,new),encoding="utf-8")
    print("Patched first-run baseline transaction ordering.")
elif "baseline=False" in s:
    print("Baseline transaction fix already applied.")
else:
    raise SystemExit("Expected baseline block not found; no changes made.")
