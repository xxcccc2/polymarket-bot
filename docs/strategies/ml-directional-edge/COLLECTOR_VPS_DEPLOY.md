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

## 5. WebSocket endpoint (futures vs spot)

The ML collector **uses Binance futures WebSocket by default** (`wss://fstream.binance.com/stream`). It subscribes to depth, agg trades, and **forceOrder** (liquidations), and polls funding rate and open interest from the futures REST API.

- **Liquidations** are only published on the futures stream. If the collector used the spot combined URL (`wss://stream.binance.com:9443/stream`), the `liquidations` table would stay at 0 no matter how long you run.
- Config: `BINANCE_FUTURES_WS_COMBINED_URL` (default: `wss://fstream.binance.com/stream`). Override only if you need a different futures endpoint (e.g. testnet).

The live bot’s Binance feed (for price/VWAP) continues to use spot by default (`BINANCE_WS_COMBINED_URL`); only the ML collector uses the futures WS.

## 6. Start the collector service

```bash
sudo systemctl start ml-microstructure-collector
sudo systemctl status ml-microstructure-collector
```

Follow logs:

```bash
journalctl -u ml-microstructure-collector -f
```

## 7. Check data quality

```bash
cd /opt/polymarket-bot
./.venv/bin/python -m scripts.check_microstructure_quality --db-path data/ml/collectors/binance_microstructure.sqlite
```

Healthy signs:

- `depth_snapshots` increases quickly
- `agg_trades` increases continuously during active periods
- `funding_open_interest` increments every poll cycle
- latest timestamps stay near current UTC time
- `liquidations` will eventually show rows when the market has liquidations (bursty; can stay at the same count for a while)

## 8. Optional overrides

You can change the symbol, DB path, or service name at install time:

```bash
APP_DIR=/opt/polymarket-bot \
APP_USER=$USER \
COLLECTOR_SYMBOL=btcusdt \
DB_PATH=/opt/polymarket-bot/data/ml/collectors/binance_microstructure.sqlite \
SERVICE_NAME=ml-microstructure-collector \
bash scripts/setup_ml_collector_vps.sh
```

## 9. Operations

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

## 10. Upgrade procedure

From the app directory on the VPS:

```bash
cd /opt/polymarket-bot

# Optional: backup DB before upgrading (no data loss on restart; backup is for safety)
mkdir -p backups
sqlite3 data/ml/collectors/binance_microstructure.sqlite ".backup 'backups/binance_microstructure-$(date +%F-%H%M%S).sqlite'"

# Deploy code and restart
git pull
./.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart ml-microstructure-collector
```

Verify:

```bash
sudo systemctl status ml-microstructure-collector
journalctl -u ml-microstructure-collector -n 50 --no-pager
./.venv/bin/python -m scripts.check_microstructure_quality --db-path data/ml/collectors/binance_microstructure.sqlite
```

If you previously added a systemd override to force the futures WebSocket (`Environment=BINANCE_WS_COMBINED_URL=...` or `BINANCE_FUTURES_WS_COMBINED_URL=...`), the code now defaults to the futures URL. After confirming the upgraded collector runs correctly, you can remove the override:

```bash
sudo rm -f /etc/systemd/system/ml-microstructure-collector.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart ml-microstructure-collector
```

## Notes

- Binance public WebSocket and REST endpoints used by the collector do not require API keys.
- The ML collector uses **Binance futures** WebSocket by default so that liquidations are received; the live bot feed still uses spot by default.
- `liquidations` can stay at the same count during calm periods (events are bursty); that is not a fault. If you see `liquidations` at 0 for days, the process was likely using the spot URL before the futures-default change.
- Keep the collector separate from live trading if possible. A dedicated VPS for collection is a good choice.
