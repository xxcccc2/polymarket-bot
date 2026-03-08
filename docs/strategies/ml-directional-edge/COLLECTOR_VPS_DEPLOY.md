# ML Collector VPS Deploy

Use this when moving Binance microstructure collection onto a cheap always-on VPS.

## Recommended Hostinger Choice

- Location: `Germany`
- OS: `Ubuntu 24.04 LTS`

Why:

- Germany is the chosen location from your shortlist.
- Ubuntu LTS is the easiest target for Python, `systemd`, and package maintenance.
- Region choice should ultimately be validated by `VPS -> Binance` latency and stability, not `Brazil -> VPS` latency.

Your `KVM1` spec is enough for the collector:

- `1 vCPU` is enough for one websocket collector plus periodic REST polling
- `4 GB RAM` is comfortably enough
- `50 GB NVMe` is plenty for weeks of SQLite collector data

## Deploy Flow

## 1. Clone the repo

```bash
git clone <your-repo-url> /opt/polymarket-bot
cd /opt/polymarket-bot
```

## 2. Run the bootstrap script

```bash
chmod +x scripts/setup_ml_collector_vps.sh
APP_DIR=/opt/polymarket-bot APP_USER=$USER bash scripts/setup_ml_collector_vps.sh
```

What it does:

- installs `chrony`, `curl`, `python3`, `python3-venv`, `python3-pip`, and `sqlite3`
- sets the VPS timezone to `UTC`
- enables and restarts `chrony`
- creates `.venv`
- installs `requirements.txt`
- installs a `systemd` service called `ml-microstructure-collector`

## 3. Verify UTC and clock sync

```bash
timedatectl
chronyc tracking
```

Healthy expectations:

- timezone shows `UTC`
- NTP service is active
- the system clock is synchronized

## 4. Benchmark Binance connectivity from the VPS

Check REST latency a few times:

```bash
curl -s -o /dev/null -w 'spot time_total=%{time_total}\n' "https://api.binance.com/api/v3/time"
curl -s -o /dev/null -w 'futures time_total=%{time_total}\n' "https://fapi.binance.com/fapi/v1/time"
```

Check websocket handshake timing after the virtualenv is ready:

```bash
./.venv/bin/python - <<'PY'
import time
import websocket

url = "wss://fstream.binance.com/stream?streams=btcusdt@aggTrade"
start = time.perf_counter()
ws = websocket.create_connection(url, timeout=5)
elapsed = time.perf_counter() - start
print(f"ws_handshake_seconds={elapsed:.3f}")
ws.close()
PY
```

Do this a few times. Keep Germany unless those numbers are clearly unstable or consistently poor.

## 5. Start the collector service

```bash
sudo systemctl start ml-microstructure-collector
sudo systemctl status ml-microstructure-collector
```

Follow logs:

```bash
journalctl -u ml-microstructure-collector -f
```

## 6. Check data quality

```bash
./.venv/bin/python -m scripts.check_microstructure_quality --db-path data/ml/collectors/binance_microstructure.sqlite
```

Healthy signs:

- `depth_snapshots` increases quickly
- `agg_trades` increases continuously during active periods
- `funding_open_interest` increments every poll cycle
- latest timestamps stay near current UTC time

## 7. Optional overrides

You can change the symbol, DB path, or service name at install time:

```bash
APP_DIR=/opt/polymarket-bot \
APP_USER=$USER \
COLLECTOR_SYMBOL=btcusdt \
DB_PATH=/opt/polymarket-bot/data/ml/collectors/binance_microstructure.sqlite \
SERVICE_NAME=ml-microstructure-collector \
bash scripts/setup_ml_collector_vps.sh
```

## 8. Operations

Restart:

```bash
sudo systemctl restart ml-microstructure-collector
```

Stop:

```bash
sudo systemctl stop ml-microstructure-collector
```

Enable on boot:

```bash
sudo systemctl enable ml-microstructure-collector
```

Disable on boot:

```bash
sudo systemctl disable ml-microstructure-collector
```

## 9. Upgrade Procedure

```bash
cd /opt/polymarket-bot
git pull
./.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart ml-microstructure-collector
```

## Notes

- Binance public websocket and public REST endpoints used by the collector do not require API keys.
- `liquidations` can stay at `0` during calm periods; that alone is not a fault signal.
- Keep the collector separate from live trading if possible. A dedicated VPS for collection is a good choice.
