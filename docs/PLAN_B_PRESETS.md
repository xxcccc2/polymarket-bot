# Plan B: Risk Management Presets

**Strategies:** `wallet_copy` + `terminal_convergence` + `combinatorial_arb`

Run with: `python -m src.bot --strategy btc_5min`

**Wallet copy:** Copies BUYs from tracked leaders (leaderboard or manual). When a leader *sells* a position we copied from them, the bot mirrors the exit (SELL) automatically.  
With the DISABLED_STRATEGIES below, only Plan B (wallet_copy + terminal_convergence + combinatorial_arb) will run.

---

## Setup 1: SAFE

**Goal:** Preserve capital, small bets, strict limits.

| Variable | Value | Notes |
|----------|-------|-------|
| `ADAPTIVE_RISK_ENABLED` | `true` | Bankroll-proportional limits |
| `ORDER_SIZE_USD` | `5` | Base size |
| `WALLET_COPY_SIZE_USD` | `3` | Smaller copy trades |
| `MAX_POSITION_USD` | `15` | Per market cap |
| `MAX_TOTAL_EXPOSURE_USD` | `50` | Tight total exposure |
| `MAX_ACTIVE_ORDERS` | `6` | Fewer concurrent orders |
| `DAILY_LOSS_LIMIT_USD` | `10` | Hard stop |
| `MIN_BALANCE_USD` | `10` | Floor |
| `SCAN_INTERVAL_SECONDS` | `5` | Normal scan speed |
| `WALLET_COPY_MAX_DELAY_SECONDS` | `90` | Only recent trades |
| `WALLET_COPY_COOLDOWN_SECONDS` | `90` | Less copy spam |
| `WALLET_COPY_LEADERBOARD_TOP_N` | `3` | Fewer leaders |
| `TERMINAL_CONVERGENCE_WINDOW_SECONDS` | `45` | Last 45s only |
| `TERMINAL_MIN_EDGE_CENTS` | `5` | Stricter edge |
| `COMBO_MIN_EDGE_CENTS` | `5` | Only clear arbs |
| `COMBO_COOLDOWN` | `600` | 10 min between combo trades |
| `DISABLED_STRATEGIES` | `spread,arbitrage,favorite_longshot,cross_platform_arbitrage,cross_asset,orderbook_imbalance,stink_bid,late_money,vpin,sentiment` | Only Plan B trio |

**Copy-paste for .env:**
```env
# SAFE preset
ADAPTIVE_RISK_ENABLED=true
ORDER_SIZE_USD=5
WALLET_COPY_SIZE_USD=3
MAX_POSITION_USD=15
MAX_TOTAL_EXPOSURE_USD=50
MAX_ACTIVE_ORDERS=6
DAILY_LOSS_LIMIT_USD=10
MIN_BALANCE_USD=10
SCAN_INTERVAL_SECONDS=5
WALLET_COPY_MAX_DELAY_SECONDS=90
WALLET_COPY_COOLDOWN_SECONDS=90
WALLET_COPY_LEADERBOARD_TOP_N=3
TERMINAL_CONVERGENCE_WINDOW_SECONDS=45
TERMINAL_MIN_EDGE_CENTS=5
COMBO_MIN_EDGE_CENTS=5
COMBO_COOLDOWN=600
DISABLED_STRATEGIES=spread,arbitrage,favorite_longshot,cross_platform_arbitrage,cross_asset,orderbook_imbalance,stink_bid,late_money,vpin,sentiment
```

---

## Setup 2: MID (Balanced)

**Goal:** Moderate risk, good activity, capital preservation.

| Variable | Value | Notes |
|----------|-------|-------|
| `ADAPTIVE_RISK_ENABLED` | `true` | Bankroll-proportional |
| `ORDER_SIZE_USD` | `6` | Medium base |
| `WALLET_COPY_SIZE_USD` | `5` | Standard copy size |
| `MAX_POSITION_USD` | `25` | Per market |
| `MAX_TOTAL_EXPOSURE_USD` | `100` | Moderate exposure |
| `MAX_ACTIVE_ORDERS` | `8` | More concurrent |
| `DAILY_LOSS_LIMIT_USD` | `15` | |
| `MIN_BALANCE_USD` | `15` | |
| `SCAN_INTERVAL_SECONDS` | `3` | Faster scans |
| `WALLET_COPY_MAX_DELAY_SECONDS` | `75` | |
| `WALLET_COPY_COOLDOWN_SECONDS` | `60` | |
| `WALLET_COPY_LEADERBOARD_TOP_N` | `5` | Top 5 leaders |
| `TERMINAL_CONVERGENCE_WINDOW_SECONDS` | `60` | Last 60s |
| `TERMINAL_MIN_EDGE_CENTS` | `3` | Standard edge |
| `COMBO_MIN_EDGE_CENTS` | `3` | |
| `COMBO_COOLDOWN` | `300` | 5 min |
| `DISABLED_STRATEGIES` | `spread,arbitrage,favorite_longshot,cross_platform_arbitrage,cross_asset,orderbook_imbalance,stink_bid,late_money,vpin,sentiment` | |

**Copy-paste for .env:**
```env
# MID preset
ADAPTIVE_RISK_ENABLED=true
ORDER_SIZE_USD=6
WALLET_COPY_SIZE_USD=5
MAX_POSITION_USD=25
MAX_TOTAL_EXPOSURE_USD=100
MAX_ACTIVE_ORDERS=8
DAILY_LOSS_LIMIT_USD=15
MIN_BALANCE_USD=15
SCAN_INTERVAL_SECONDS=3
WALLET_COPY_MAX_DELAY_SECONDS=75
WALLET_COPY_COOLDOWN_SECONDS=60
WALLET_COPY_LEADERBOARD_TOP_N=5
TERMINAL_CONVERGENCE_WINDOW_SECONDS=60
TERMINAL_MIN_EDGE_CENTS=3
COMBO_MIN_EDGE_CENTS=3
COMBO_COOLDOWN=300
DISABLED_STRATEGIES=spread,arbitrage,favorite_longshot,cross_platform_arbitrage,cross_asset,orderbook_imbalance,stink_bid,late_money,vpin,sentiment
```

---

## Setup 3: AGGRESSIVE

**Goal:** Higher turnover, bigger bets, more risk.

| Variable | Value | Notes |
|----------|-------|-------|
| `ADAPTIVE_RISK_ENABLED` | `false` | Fixed limits, no throttle |
| `ORDER_SIZE_USD` | `10` | Larger base |
| `WALLET_COPY_SIZE_USD` | `8` | Bigger copies |
| `MAX_POSITION_USD` | `40` | Per market |
| `MAX_TOTAL_EXPOSURE_USD` | `200` | High exposure |
| `MAX_ACTIVE_ORDERS` | `12` | More orders |
| `DAILY_LOSS_LIMIT_USD` | `25` | |
| `MIN_BALANCE_USD` | `20` | |
| `SCAN_INTERVAL_SECONDS` | `2` | Fast scans |
| `WALLET_COPY_MAX_DELAY_SECONDS` | `60` | Copy recent trades only |
| `WALLET_COPY_COOLDOWN_SECONDS` | `45` | More frequent copies |
| `WALLET_COPY_LEADERBOARD_TOP_N` | `5` | |
| `TERMINAL_CONVERGENCE_WINDOW_SECONDS` | `90` | Last 90s (more opportunities) |
| `TERMINAL_MIN_EDGE_CENTS` | `2` | Looser edge |
| `COMBO_MIN_EDGE_CENTS` | `3` | |
| `COMBO_COOLDOWN` | `180` | 3 min |
| `DISABLED_STRATEGIES` | `spread,arbitrage,favorite_longshot,cross_platform_arbitrage,cross_asset,orderbook_imbalance,stink_bid,late_money,vpin,sentiment` | |

**Copy-paste for .env:**
```env
# AGGRESSIVE preset
ADAPTIVE_RISK_ENABLED=false
ORDER_SIZE_USD=10
WALLET_COPY_SIZE_USD=8
MAX_POSITION_USD=40
MAX_TOTAL_EXPOSURE_USD=200
MAX_ACTIVE_ORDERS=12
DAILY_LOSS_LIMIT_USD=25
MIN_BALANCE_USD=20
SCAN_INTERVAL_SECONDS=2
WALLET_COPY_MAX_DELAY_SECONDS=60
WALLET_COPY_COOLDOWN_SECONDS=45
WALLET_COPY_LEADERBOARD_TOP_N=5
TERMINAL_CONVERGENCE_WINDOW_SECONDS=90
TERMINAL_MIN_EDGE_CENTS=2
COMBO_MIN_EDGE_CENTS=3
COMBO_COOLDOWN=180
DISABLED_STRATEGIES=spread,arbitrage,favorite_longshot,cross_platform_arbitrage,cross_asset,orderbook_imbalance,stink_bid,late_money,vpin,sentiment
```

---

## How to Use

1. Pick a preset (SAFE / MID / AGGRESSIVE).
2. Copy the variables into your `.env`.
3. Run: `python -m src.bot --strategy btc_5min`

With the presets above, only Plan B strategies run; the others are disabled.

**Optional:** Add `ONLY_CRYPTO_MARKETS=true` to limit to crypto markets.
