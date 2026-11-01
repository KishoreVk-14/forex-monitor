# Forex Monitor

A read-only Forex market monitoring application using Twelve Data, SQLite, pandas and Flask.

## First-time EC2 setup

Clone the repository, expand the application source, install dependencies and configure your API key:

```bash
git clone https://github.com/KishoreVk-14/forex-monitor.git
cd forex-monitor
python3 bootstrap_project.py
bash scripts/setup.sh
nano .env
```

Set `TWELVE_DATA_API_KEY` in `.env`. Never commit that file or paste the key into chat.

Run one data collection cycle:

```bash
source venv/bin/activate
python -m app.main collect-once
```

Start the dashboard locally:

```bash
python -m app.main web
```

It listens on `127.0.0.1:8050` by default. Use an SSH tunnel or authenticated HTTPS reverse proxy; do not expose Flask's development server directly to the public internet.

## Features

- EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CHF and USD/CAD.
- Twelve Data time-series client with timeouts, retries and API error checks.
- UTC candle storage in SQLite with unique-key upserts.
- Excludes the currently forming candle for supported intervals.
- EMA 50/200, RSI 14, ADX 14, +DI/-DI and ATR 14.
- BULLISH / BEARISH / WATCH / NEUTRAL / WARMING_UP research labels.
- Flask dashboard, JSON endpoints and collector activity log.
- Signal-driven virtual paper trading with persistent trade ledger and performance metrics.
- Unit tests and systemd service templates.

## Continuous services

Review the service files and paths before installation. Default path/user are `/home/ubuntu/forex-monitor` and `ubuntu`.

```bash
sudo cp systemd/forex-collector.service /etc/systemd/system/
sudo cp systemd/forex-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now forex-collector forex-dashboard
sudo journalctl -u forex-collector -f
```

## Tests

```bash
source venv/bin/activate
python -m unittest discover -s tests -v
```

## Configuration

See `.env.example`. Defaults: 15-minute bars, 900-second collection interval, 500 candles requested per pair, six major pairs and dashboard port 8050. Six symbols require six time-series API requests per cycle; check your Twelve Data plan quotas before enabling continuous polling.

## Important limitations

- The application is read-only with respect to brokers: paper trades are simulated locally and no broker connection or real order placement exists.
- Signals are basic, unvalidated research rules and are not investment advice or evidence of profitability.
- EMA 200 requires adequate historical warm-up; incomplete indicators are not treated as valid signals.
- FX spot feeds can differ by provider. Weekends, holidays and API outages may leave stale latest-candle times.
- Market-data licensing controls whether raw prices or derived data may be displayed publicly or commercially. A public source-code repository does not grant data-display rights.
- The SQLite database and collected data are local runtime files and are excluded from Git.


## Upgrade an existing EC2 deployment to Forex Monitor V2

The V2 dashboard upgrade preserves the existing `.env`, SQLite database, collector service and signal rules. From the existing project directory:

```bash
cd ~/forex-monitor
git pull origin main
python3 upgrade_dashboard_v2.py
source venv/bin/activate
python -m compileall -q app
python -m unittest discover -s tests -v
sudo systemctl restart forex-dashboard
sudo systemctl status forex-dashboard --no-pager
curl -s http://127.0.0.1:8050/health
```

The dashboard refreshes its market snapshot every 60 seconds. Chart.js is loaded from a CDN, so chart rendering requires browser internet access. The application remains read-only and does not change the collector or trading signal rules.


## Signal-based paper trading

The optional paper trader uses the existing BULLISH / BEARISH strategy labels. It does not modify those rules or connect to a broker.

Default simulation rules:
- $10,000 starting virtual capital.
- 1% of current cash balance risked per trade.
- Maximum 3 simultaneous open positions.
- Stop loss at 1.5 × ATR(14); take profit at 2R.
- 1-pip assumed spread, applied adversely around entry/exit.
- If stop-loss and take-profit are both touched in one candle, stop-loss is assumed first.
- Existing candles are used for indicator warm-up only. On first startup, the engine seeds its per-pair cursor at the latest stored candle; it does not create historical/backfilled trades. Entries begin on subsequently collected candles.
- A signal is evaluated only after a candle is stored as closed. The simulated entry is at that signal candle's close, adjusted by half the assumed spread.
- P&L is converted to USD using the matching USD quote pair where available. This is a simplified simulation, not a broker fill model.

Install on an existing EC2 deployment:

```bash
cd ~/forex-monitor
git pull --ff-only origin main
python3 upgrade_paper_trading.py
python3 patch_paper_trading.py
source venv/bin/activate
python -m compileall -q app
python -m unittest discover -s tests -v
sudo cp systemd/forex-paper-trader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now forex-paper-trader
sudo systemctl restart forex-dashboard
sleep 3
sudo systemctl status forex-paper-trader --no-pager
curl -s http://127.0.0.1:8050/api/paper/summary
```

The dashboard adds a Paper trading tab with virtual equity, realized/unrealized P&L, open positions, closed trade history, win rate and profit factor. The collector and existing SQLite candle history are preserved. The paper trader stores its own tables in the existing SQLite database.


## Paper trading diagnostics and filters

The strategy monitor reports LONG PASS/FAIL and SHORT PASS/FAIL for EMA trend, directional movement, ADX and RSI. These diagnostics explain the existing unchanged entry gates; they do not alter the signal classification. The Paper trading workspace supports currency-pair and entry-date filters across open positions and trade history. The paper service scans its database every 30 seconds; the collector stores closed 15-minute candles on its 15-minute schedule, so a new trade can only be evaluated after a newly stored closed candle. The dashboard refreshes its display every 60 seconds.
