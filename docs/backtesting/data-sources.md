# Data Sources for Backtesting

Comparison of data sources and when to use each.

---

## PolyBackTest (Primary)

**URL:** [api.polybacktest.com](https://api.polybacktest.com)  
**Docs:** [docs.polybacktest.com](https://docs.polybacktest.com)

### What It Provides

- Polymarket Up/Down markets (5m, 15m, 1h, 4h, 24h)
- 8 snapshots per second per market
- `btc_price_start` (Price to Beat), `btc_price_end`, `winner`
- Optional full orderbook per snapshot
- 5m & 15m use **Chainlink** prices (aligned with Polymarket resolution)
- 1h, 4h, 24h use **Binance** prices

### Free Plan

| Type | Markets | Snapshots |
|------|---------|-----------|
| 5m | Last 50 | Unlimited |
| 15m | Last 50 | Unlimited |
| 1h | Last 24 | Unlimited |
| 4h | Last 24 | Unlimited |
| 24h | Last 5 | Unlimited |

### Best For

- Terminal convergence backtest
- 5m/15m crypto strategies
- Sub-second granularity
- Resolution-accurate data (Chainlink for short-term)

---

## Parquet Hourly (Complementary, Phase 2)

**Source:** Public index (e.g. ondb.ai), contact via Discord/Telegram  
**Format:** Parquet (~300–600 MB per hour)

### What It Provides

- All Polymarket markets (not just Up/Down)
- Orderbook + trade data
- 1 snapshot per hour
- No resolution metadata (winner, Price to Beat)

### Best For

- Spread strategy at hourly level
- Arbitrage across politics/sports/crypto
- Orderbook imbalance at coarse granularity
- Research and exploration

### Integration (Phase 2)

Would require:
- `pyarrow` or `pandas` for Parquet
- URL pattern for downloads
- Loader to convert to MarketData-like format
- No resolution data → cannot resolve PnL for Up/Down; useful for spread/arb only

---

## Polymarket Official API

**Gamma API:** `gamma-api.polymarket.com`  
**CLOB API:** `clob.polymarket.com`

This repository’s live path uses **py-clob-client-v2** with that CLOB host by default; see [CLOB SDK v2 notes](../clob-v2-sdk.md) for auth and order APIs.

### Limitations for Backtesting

- **No historical orderbook** — Only current snapshot
- **No Price to Beat** — Not exposed in Gamma/CLOB
- **Prices history** — `/prices-history` has 1m, 1h, 6h, 1d, 1w intervals (no 5m/15m)
- **No resolution outcomes** — Must infer from market metadata

### Use Case

Live trading and real-time data. For backtesting, use PolyBackTest instead.

---

## Summary

| Need | Use |
|------|-----|
| 5m/15m terminal convergence backtest | PolyBackTest |
| Sub-second granularity | PolyBackTest |
| Resolution data (winner, Price to Beat) | PolyBackTest |
| All markets, hourly | Parquet (Phase 2) |
| Live trading | Polymarket CLOB + Gamma |
