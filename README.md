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

- This is a read-only market monitor. It has no broker integration, order placement or automatic trading.
- Signals are basic, unvalidated research rules and are not investment advice or evidence of profitability.
- EMA 200 requires adequate historical warm-up; incomplete indicators are not treated as valid signals.
- FX spot feeds can differ by provider. Weekends, holidays and API outages may leave stale latest-candle times.
- Market-data licensing controls whether raw prices or derived data may be displayed publicly or commercially. A public source-code repository does not grant data-display rights.
- The SQLite database and collected data are local runtime files and are excluded from Git.
