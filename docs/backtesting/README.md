# Backtesting Module

Backtest Polymarket strategies on historical Up/Down market data using the [PolyBackTest](https://polybacktest.com) API.

---

## Overview

The backtesting module:

1. **Downloads** historical Polymarket Up/Down markets and snapshots from PolyBackTest
2. **Stores** data locally in SQLite (`data/backtest/polybacktest.db`)
3. **Replays** snapshots through strategies (e.g. `terminal_convergence`) with a synthetic Binance-like feed
4. **Computes** simulated PnL, win rate, and trade statistics

### Data Sources

| Source | Granularity | Markets | Resolution Data | Best For |
|--------|-------------|---------|-----------------|----------|
| **PolyBackTest** | 8 snapshots/sec | 5m, 15m, 1h, 4h, 24h Up/Down | Yes (winner, btc_price_start/end) | Terminal convergence, 5m/15m backtest |
| Parquet hourly | 1 snapshot/hour | All Polymarket | No | Spread, arb, politics (Phase 2) |

---

## Quick Start

### 1. Get API Key

1. Sign up at [polybacktest.com](https://polybacktest.com)
2. Create an API key in the [Dashboard](https://polybacktest.com/dashboard)
3. Add to `.env`:

```env
POLYBACKTEST_API_KEY=pdm_your_key_here
```

### 2. Download Data

```bash
# Download all market types (free plan limits)
./venv/bin/python -m scripts.download_polybacktest

# Download only 5m and 15m (faster)
./venv/bin/python -m scripts.download_polybacktest --types 5m,15m

# Include full orderbook (larger payloads, for OBI strategies)
./venv/bin/python -m scripts.download_polybacktest --include-orderbook
```

### 3. Run Backtest

```bash
# 5m markets, terminal_convergence strategy
./venv/bin/python -m scripts.run_backtest --strategy terminal_convergence --market-type 5m

# Quick test (first 10 markets)
./venv/bin/python -m scripts.run_backtest --strategy terminal_convergence --market-type 5m --limit 10

# 15m markets
./venv/bin/python -m scripts.run_backtest --strategy terminal_convergence --market-type 15m
```

---

## Free Plan Limits

| Market Type | Markets Accessible | Snapshots |
|-------------|--------------------|-----------|
| 5m | Last 50 | Unlimited |
| 15m | Last 50 | Unlimited |
| 1h | Last 24 | Unlimited |
| 4h | Last 24 | Unlimited |
| 24h | Last 5 | Unlimited |

Markets are the **most recently created**; older ones rotate out of the free-plan window.
Data is retained for **31 days** by PolyBackTest.

---

## Architecture

```
PolyBackTest API
       │
       ▼
┌──────────────────┐
│ PolyBackTestClient
└────────┬─────────┘
         │
         ▼
┌──────────────────┐     ┌──────────────┐
│ DataDownloader    │────▶│ BacktestStore │
└──────────────────┘     │ (SQLite)      │
                         └───────┬───────┘
                                 │
                                 ▼
                         ┌──────────────┐
                         │ BacktestEngine│
                         └───────┬──────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
     ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
     │ ReplayBinanceFeed│   │ Mappers      │   │ Strategy     │
     │ (btc_price)   │   │ (snapshot→   │   │ (e.g. TC)     │
     │               │   │  MarketData) │   │               │
     └──────────────┘   └──────────────┘   └──────────────┘
```

---

## Data Model

### Market

PolyBackTest provides:
- `btc_price_start` — Price to Beat (candle open, Chainlink)
- `btc_price_end` — Closing price
- `winner` — "Up" or "Down"
- `clob_token_up`, `clob_token_down` — Token IDs
- `start_time`, `end_time` — Window boundaries

### Snapshot

Each snapshot (8/sec per market):
- `price_up`, `price_down` — Market prices for UP/DOWN tokens
- `btc_price` — BTC price at snapshot time (Chainlink for 5m/15m)
- Optional `orderbook_up`, `orderbook_down` — Full depth

---

## Supported Strategies

| Strategy | Status | Notes |
|----------|--------|-------|
| `terminal_convergence` | ✅ | Uses ReplayBinanceFeed for btc_price momentum |
| `orderbook_imbalance` | Phase 2 | Requires `--include-orderbook` |
| `cross_asset` | Phase 2 | Requires replay feed |

---

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `POLYBACKTEST_API_KEY` | — | Required for API access |
| `POLYBACKTEST_BASE_URL` | `https://api.polybacktest.com` | API base URL |
| `BACKTEST_DB` | `data/backtest/polybacktest.db` | SQLite path |

---

## Troubleshooting

- **`POLYBACKTEST_API_KEY not set`** — Add key to `.env` from [polybacktest.com/dashboard](https://polybacktest.com/dashboard)
- **`No markets found`** — Run `download_polybacktest` first; ensure API key is valid
- **`402 Upgrade required`** — Market is outside free-plan window; try different market types or upgrade
- **`429 Rate limit`** — Client retries automatically; if persistent, reduce concurrency

## Related Docs

- [API Reference](api-reference.md) — Module API, methods, parameters
- [Strategy Integration](strategy-integration.md) — Adding new strategies to backtest
- [Data Sources](data-sources.md) — PolyBackTest vs Parquet vs Polymarket API
- [PolyBackTest Docs](https://docs.polybacktest.com) — Official API documentation
