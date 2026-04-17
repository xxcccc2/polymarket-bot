# Terminal Convergence Strategy — Report

*How it works and what variables it uses*

---

## How It Works

### Core idea

In the final 60–120 seconds of a crypto Up/Down market, the price **must** converge to ~0 (NO wins) or ~1 (YES wins). The strategy buys the underpriced near-certain outcome in the final window before expiry and holds to resolution.

**1h-only mode (default):** Only trades 1h Up/Down markets. Polymarket resolves 1h via **Binance** — so Binance WebSocket + 1h candle open = exact Price to Beat. 5m/15m/4h use Chainlink and are excluded.

### Flow

1. **Market filter** — Only trades markets that are:
   - Crypto (BTC, ETH, SOL, XRP)
   - **1h only** (when `TERMINAL_CONVERGENCE_1H_ONLY=true`): `1h`, `1 hour`, `updown-1h`
   - Within the convergence window (120s for 1h)

2. **Fair value estimate** — Uses Binance data:
   - **1h Up/Down:** Price to Beat = Binance 1h candle open (REST kline). Current vs open → Up vs Down.
   - **Strike markets** (legacy): distance from strike vs. volatility
   - **5m/15m Up/Down** (when 1h_only=false): momentum from 10s/30s/60s — less accurate (Chainlink resolution)

3. **Edge check** — If estimated probability > `min_certainty` (e.g. 80%) and market price is below that by at least `min_edge_cents`, it considers a buy.

4. **Fee adjustment** — Uses Polymarket's dynamic crypto taker fee and requires at least 1¢ net edge after fees.

5. **Order placement** — Places aggressive limit orders (up to best ask or slightly below fair value) to fill quickly before expiry.

---

## Variables & Parameters

### Config / env vars

| Variable | Env | Default | Description |
|----------|-----|---------|-------------|
| `1h_only` | `TERMINAL_CONVERGENCE_1H_ONLY` | true | Only trade 1h Up/Down (Binance = resolution) |
| `convergence_window_s` | `TERMINAL_CONVERGENCE_WINDOW_SECONDS` | 60 | Seconds before expiry (120s used for 1h) |
| `min_edge_cents` | `TERMINAL_MIN_EDGE_CENTS` | 3 | Minimum mispricing (cents) to trade |
| `min_certainty` | — | 0.80 | Minimum estimated probability to treat as near-certain |
| `order_size_usd` | `ORDER_SIZE_USD` | 10 | Per-trade size (USD) |
| `max_position_usd` | `MAX_POSITION_USD` | 100 | Max position per market (USD) |
| `signal_cooldown_s` | — | 15 | Cooldown between signals per market (seconds) |

### Side confirmation thresholds (1h mode)

| Threshold | Value | Description |
|-----------|-------|-------------|
| **Price diff (1h)** | ≥ 0.03% | Current vs candle open — clear Up or Down |
| **min_certainty** | 0.80 | Estimated prob must be ≥ 80% |
| **min_edge_cents** | 2 | Gross edge before fees |
| **net_edge_floor** | 1.0 | Net edge after fees (hardcoded) |

### Strategy-internal (hardcoded)

| Variable | Value | Description |
|----------|-------|-------------|
| `max_signals_per_cycle` | 3 | Max signals per scan cycle |
| `net_edge_floor` | 1.0 | Minimum net edge in cents after fees |
| `diff_pct_min` (1h) | 0.03 | Min |current - open|/open % for 1h |
| `price_move_min` (5m/15m) | 0.02 | Minimum price move (0.02%) when not 1h-only |
| Momentum weights (5m/15m) | 60s: 40%, 30s: 35%, 10s: 15%, pressure: 10% | When 1h_only=false |

### Binance feed inputs

| Field | Description |
|-------|-------------|
| `last_price` | Spot price |
| `price_change_pct_10s` | % change over last 10s |
| `price_change_pct_30s` | % change over last 30s |
| `price_change_pct_60s` | % change over last 60s |
| `bid_pressure` | Buy volume / total volume (0–1) |
| `volatility_5m` | Annualized volatility (σ) |

### Market keywords (from `BTC_5MIN_KEYWORDS`)

`updown`, `up-or-down`, `5m`, `15m`, `1h`, `4h`, `up or down - 5 min`, `up or down - 15 min`, `up or down - 1 hour`, `up or down - 4 hour`, `5 min`, `5-min`, `5min`, etc.

---

## Dependencies

- **BinanceFeed** must be connected (WebSocket)
- **`ENABLE_CRYPTO_EVENT_INFRA=true`**
- Markets must have `end_date_ts` for time-to-expiry

---

## Summary

The strategy uses Binance spot data to estimate which outcome will win, compares that to Polymarket prices, and buys when the near-certain outcome is underpriced in the last minute before expiry. It uses momentum for Up/Down markets and strike distance vs. volatility for strike markets, then sizes with Kelly and caps exposure per market.
