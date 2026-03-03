# Polymarket Trading Bot

An institutional-grade algorithmic trading platform for Polymarket prediction markets, powered by the **bs-p native math engine** for theoretically optimal quoting and sizing.

## Features

- **bs-p Native Engine** — C math library (Avellaneda-Stoikov quoting, inventory-aware Kelly, portfolio Greeks, shock testing) loaded via ctypes with automatic pure-Python fallback
- **12 Trading Strategies** — from wallet-copy to cross-asset latency arb
- **Binance BTC Feed** — real-time VWAP, volatility, and price velocity for 5-min BTC markets
- **Adaptive Risk Manager** — bankroll-proportional limits, drawdown throttling, portfolio Greeks monitoring, pre-trade shock testing, auto-halt
- **Multi-Wallet Profiles** — run CHR/BB/etc. concurrently from one shared config file
- **Strategy Analytics** — per-strategy P&L, Sharpe ratio, win rate, streaks, auto-disable losers
- **Telegram Alerts** — fills, risk events, Greeks threshold alerts, daily summaries
- **Inventory-Aware Kelly Sizing** — position sizes shrink as existing inventory grows, preventing over-concentration
- **Paper Trading** — full simulation mode, zero risk
- **Order Lifecycle** — tracking, duplicate prevention, stale cleanup, graceful shutdown cancellation
- **Wallet Analysis** — reverse-engineer tracked wallets to infer strategies (markets, sizing, horizons)

## Quick Start

### 1. Set Up Virtual Environment

```bash
cd polymarket-bot
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

### 2. Build the Native Math Engine (Optional but Recommended)

```bash
./scripts/build_native.sh
```

This compiles `libpmkernel` from the sibling `bs-p/` repo and copies it to `lib/`.
Requires only a C compiler (`cc` / `clang` / `gcc`).  If the library isn't found
at runtime, all functions fall back to pure-Python automatically.

Verify:

```bash
./venv/bin/python -c "from src.native.pmkernel import NATIVE_AVAILABLE; print(NATIVE_AVAILABLE)"
# True
```

### 3. Configure Environment

```bash
cp env.example .env
```

Edit `.env` with your credentials:

| Variable | Required | Description |
|----------|----------|-------------|
| `POLYMARKET_PRIVATE_KEY` | Yes | Wallet private key (MetaMask, Binance Web3, etc.) |
| `POLYMARKET_PROXY_ADDRESS` | Yes | Polymarket proxy wallet (found on profile page) |
| `BINANCE_API_KEY` | No | For cross-asset BTC strategies |
| `BINANCE_SECRET_KEY` | No | For cross-asset BTC strategies |
| `TELEGRAM_BOT_TOKEN` | No | For push notifications (see Telegram Setup) |
| `TELEGRAM_CHAT_ID` | No | Your Telegram chat ID |

Optional (recommended for multi-wallet):

- Use one shared non-secret config file via `BOT_PUBLIC_CONFIG_FILE`.
- Keep per-wallet secrets in `.env`:
  - `POLYMARKET_PRIVATE_KEY_<ID>`
  - `POLYMARKET_PROXY_ADDRESS_<ID>`
  - `SIGNATURE_TYPE_<ID>`

### 4. Run the Bot

```bash
# Paper test with recommended BTC 5-min strategies (default)
./venv/bin/python -m src.bot

# Run specific strategy
./venv/bin/python -m src.bot --strategy cross_asset

# Run all non-disabled strategies
./venv/bin/python -m src.bot --strategy all

# List available strategies
./venv/bin/python -m src.bot --list-strategies

# Force paper mode
./venv/bin/python -m src.bot --paper

# bs-p spread + OBI with native engine (CHR wallet, paper first)
BOT_PUBLIC_CONFIG_FILE=./config/settings.chr.live BOT_WALLET_ID=CHR PAPER_TRADING=true caffeinate -i ./venv/bin/python -m src.bot --strategy all

# Multi-wallet live (two processes)
BOT_PUBLIC_CONFIG_FILE=./config/settings.chr.live BOT_WALLET_ID=CHR caffeinate -i ./venv/bin/python -m src.bot --strategy all
BOT_PUBLIC_CONFIG_FILE=./config/settings.bb.live BOT_WALLET_ID=BB caffeinate -i ./venv/bin/python -m src.bot --strategy all
```

Flags: `-i` prevents idle sleep, `-d` prevents display sleep.

## Configuration

Configuration can be layered:

1. shared non-secret file via `BOT_PUBLIC_CONFIG_FILE` (optional)
2. private `.env` (secrets)
3. shell env vars (highest priority)

Key settings:

### Trading Parameters

| Setting | Default | Description |
|---------|---------|-------------|
| `PAPER_TRADING` | `true` | Set `false` for live trading |
| `ORDER_SIZE_USD` | `10` | Base order size (adaptive risk may override) |
| `MAX_POSITION_USD` | `100` | Max position per market |
| `DAILY_LOSS_LIMIT_USD` | `50` | Circuit breaker |
| `SCAN_INTERVAL_SECONDS` | `5` | Time between strategy scans |

### bs-p Native Engine

| Setting | Default | Description |
|---------|---------|-------------|
| `NATIVE_ENGINE_ENABLED` | `true` | Master kill-switch (false = pure-Python fallback) |
| `PMKERNEL_LIB_PATH` | auto | Path to `libpmkernel.dylib/.so` |
| `QUOTING_GAMMA` | `1.0` | Risk aversion (higher = wider spreads, safer) |
| `QUOTING_K` | `2.0` | Liquidity/arrival rate (higher = tighter) |
| `QUOTING_TAU_DEFAULT` | `0.05` | Default time-to-resolution (fraction of day) |
| `GREEKS_DELTA_ALERT` | `0.3` | Telegram alert if `|net_delta|` exceeds this |
| `GREEKS_GAMMA_ALERT` | `0.2` | Telegram alert if `|net_gamma|` exceeds this |

### Adaptive Risk (Phase 3)

When `ADAPTIVE_RISK_ENABLED=true`, limits scale with current balance and drawdown state:

| Metric | % of Bankroll | With $304 |
|--------|---------------|-----------|
| Max per trade | 3% | ~$9.12 |
| Max per market | 12% | ~$36.48 |
| Max total exposure | 30% | ~$91.20 |
| Daily loss limit | 6% | ~$18.24 |
| Drawdown throttle | configurable | Reduces size progressively |
| Drawdown halt | configurable | Full stop |
| Balance floor | 70% | ~$212.80 |

Related controls:
- `ADAPTIVE_MAX_POSITION_PCT`
- `ADAPTIVE_MAX_EXPOSURE_PCT`
- `ADAPTIVE_MAX_SINGLE_TRADE_PCT`
- `ADAPTIVE_DAILY_LOSS_LIMIT_PCT`
- `ADAPTIVE_DRAWDOWN_THROTTLE_PCT`
- `ADAPTIVE_DRAWDOWN_HALT_PCT`
- `ADAPTIVE_MIN_BALANCE_FLOOR_PCT`
- `BALANCE_STALE_BLOCK_BUYS` / `BALANCE_STALE_MAX_SECONDS` (safety gate when balance refresh is stale)

### Strategy Selection

| Setting | Default | Description |
|---------|---------|-------------|
| `DISABLED_STRATEGIES` | `spread,arbitrage,favorite_longshot,cross_platform_arbitrage` | Comma-separated disabled list |
| `ADAPTIVE_RISK_ENABLED` | `true` | Bankroll-proportional risk limits |

## Strategies

### Active Strategies (Recommended for small bankrolls)

#### 1. Cross-Asset Latency Arbitrage ✅ `cross_asset`
**Primary edge.** Binance BTC price moves hit Polymarket 5-min markets with multi-second lag:
- Real-time Binance BTC/USDT VWAP + volatility
- Detects price moves before Polymarket reprices
- Trades the lag for consistent small profits
- Requires: `BINANCE_API_KEY` (or works with public feed)

#### 2. Terminal Convergence ✅ `terminal_convergence`
Buy near-certain outcomes in the last 60 seconds before expiry:
- 85%+ win rate on markets already trading at extreme prices
- Small edge per trade, high frequency on 5-min markets
- Minimal capital at risk per position

#### 3. Spread Farming ✅ `spread` (bs-p Avellaneda-Stoikov)
**Primary edge with bs-p.** Theoretically optimal bid/ask quoting that accounts for inventory, volatility, and market depth:
- `calculate_quotes_logit` computes inventory-aware optimal bid/ask (prices skew when you hold a position)
- Implied belief volatility bootstrapped from observed market spreads on startup
- Risk aversion (`gamma`) scales inversely with bankroll growth
- Falls back to manual bid+improvement logic when native engine unavailable
- Requires: bs-p native engine built (`./scripts/build_native.sh`)

#### 4. Orderbook Imbalance ✅ `orderbook_imbalance` (bs-p enhanced)
Detect bid/ask pressure with native microstructure analysis:
- bs-p `order_book_microstructure_batch` computes proper OBI, VWAP mid, and directional pressure from real orderbook depth
- Confirms direction with Binance BTC momentum
- VWAP mid-price replaces naive `(bid+ask)/2` for more accurate reference
- Falls back to spread-asymmetry heuristic when orderbook data unavailable
- Requires: `ENABLE_WEBSOCKET_FEED=true` for real orderbook data

#### 4. Stink Bid ✅ `stink_bid`
Passive asymmetric bets — $1 bids for potential 100x:
- Places 1¢ limit bids on high-volume markets
- Fills on panic sells and fat-finger trades
- Perfect for small accounts — high optionality, low capital

#### 5. Wallet Copy ✅ `wallet_copy`
Copy trades from top Polymarket traders:
- Tracks wallets from leaderboard (by PnL) or manual list
- Polls their trades via Data API, copies new BUYs with configurable size
- Filters: crypto-only, min trade size, max copy delay, per-wallet poll throttle
- `TRACKED_WALLETS` format: comma-separated addresses (`0xabc...,0xdef...`)
- Run: `./venv/bin/python -m src.bot --strategy wallet_copy`

**Analyze wallets before copying** — use the analysis script to infer a wallet's strategy (BTC vs multi-crypto, 5m vs 15m, avg size, etc.):

```bash
python scripts/analyze_wallets.py
# Custom wallets:
TRACKED_WALLETS=0xabc...,0xdef... python scripts/analyze_wallets.py
# If you hit proxy errors:
unset http_proxy https_proxy; python scripts/analyze_wallets.py
```

**Reverse-engineer top wallets** — deep analysis: price distribution, size vs conviction, outcome preference (UP vs DOWN), inferred playbook:

```bash
TRACKED_WALLETS=0xabc...,0xdef... python scripts/reverse_engineer_wallets.py
```

#### 6. Late Money ✅ `late_money`
Follow informed traders near expiration:
- Tracks price velocity in final hours
- 40% of volume occurs in last minute (more informed)
- Follows sharp late moves with momentum

### Advanced Strategies (Phase 5)

#### 6. VPIN / Smart Money ✅ `vpin`
Detect and follow informed trader flow using volume imbalance:
- Buckets trades into time windows, classifies buyer/seller-initiated
- Computes VPIN (Volume-Synchronized Probability of Informed Trading)
- When VPIN spikes → someone with information is positioning → follow them
- Based on Easley, López de Prado & O'Hara (2012)

#### 7. Sentiment ✅ `sentiment`
Trade news sentiment divergences vs market price:
- Polls CryptoPanic for real-time crypto headlines
- Keyword-based sentiment scoring (bullish/bearish lexicon)
- Matches news to Polymarket markets by asset
- Trades when sentiment diverges from current price (2-10 min lag)
- Optional: `CRYPTOPANIC_API_KEY` for premium feed

#### 8. Combinatorial Arbitrage ✅ `combinatorial_arb`
Exploit logical pricing violations across related markets:
- Parses market questions to extract thresholds ("BTC > $100k")
- Groups by asset + direction, checks monotonicity constraints
- P(BTC > $100k) must be ≤ P(BTC > $90k) — violations = free money
- $40M+ extracted from Polymarket via this edge (Milionis et al. 2024)

### Strategy Modes

```bash
# Recommended: BTC 5-min + advanced strategies (default)
./venv/bin/python -m src.bot --strategy btc_5min

# Just cross-asset latency arb
./venv/bin/python -m src.bot --strategy cross_asset

# Just VPIN smart money detection
./venv/bin/python -m src.bot --strategy vpin

# Just sentiment-driven trading
./venv/bin/python -m src.bot --strategy sentiment

# All non-disabled strategies (12 total, 8 active)
./venv/bin/python -m src.bot --strategy all
```

## Wallet-Copy Troubleshooting

- **`not enough balance / allowance` while TUI balance looks high**:
  - This usually means low **spendable collateral**, not low total portfolio value.
  - Enable `ALLOWANCE_DIAGNOSTICS_ENABLED=true` to log allowance snapshots.
- **Repeated 401 on fill checks**:
  - Credential refresh is automatic, but network/latency can still slow loops.
  - Tune `TRADE_FETCH_TIMEOUT_SECONDS` and `BALANCE_FETCH_TIMEOUT_SECONDS`.
- **Adaptive risk with stale balance**:
  - With `BALANCE_STALE_BLOCK_BUYS=true`, new BUYs are blocked if balance is stale too long.
  - This prevents oversizing on outdated balance data.

## Telegram Alerts

Get push notifications for every fill, risk event, and daily summary.

### Setup (2 minutes)

1. Open Telegram → message `@BotFather` → `/newbot` → copy the **bot token**
2. Start a chat with your new bot (send it any message)
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
4. Find `"chat":{"id":123456789}` in the JSON — that's your **chat ID**
5. Add to `.env`:

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=123456789
TELEGRAM_ALERTS_ENABLED=true
```

### What You'll Receive

- 🟢 **Fill alerts** — strategy, side, price, size, market name
- ⚠️ **Throttle alerts** — when drawdown triggers size reduction
- 🛑 **Halt alerts** — when trading is auto-stopped
- 🚀 **Startup** — balance, active strategies, mode
- 📊 **Shutdown summary** — runtime, fills, net P&L

## Analytics & Risk

### Strategy Analytics (Phase 3)

The bot tracks per-strategy performance in real time:
- **Win rate** — percentage of profitable trades
- **Net P&L** — cumulative profit/loss
- **Sharpe ratio** — risk-adjusted return
- **Max drawdown** — worst peak-to-trough
- **Streaks** — consecutive wins/losses
- **Health score** — 0.0 (dead) to 1.0 (perfect)
- **Auto-disable** — strategies scoring below threshold are paused automatically

On shutdown, a **scorecard** is printed showing every strategy's metrics.

### Adaptive Risk Manager (Phase 3 + Phase 6)

When enabled, all risk limits scale dynamically with your balance:
- Lose money → limits tighten automatically
- Make money → limits expand proportionally
- **Drawdown throttle** at -5% → trade sizes cut to 50%
- **Drawdown halt** at -12% → all trading stopped
- **Portfolio Greeks** — net delta/gamma computed every scan cycle (Phase 6)
- **Pre-trade shock test** — simulates +-5%/+-10% probability shocks before each trade; rejects if worst-case PnL exceeds 50% of remaining daily loss budget (Phase 6)
- **Balance floor** at 70% of starting balance → nuclear stop
- Never touch disabled strategies regardless of performance

### Order Management

- Full lifecycle: PENDING → OPEN → PARTIAL → FILLED / CANCELLED
- SQLite persistence (survives restarts)
- Duplicate prevention (won't double-order same token+side)
- Stale order auto-cleanup after timeout
- Graceful shutdown cancels all active orders
- Exchange sync distinguishes fills from cancels

## Project Structure

```
polymarket-bot/
├── scripts/
│   ├── analyze_wallets.py        # Reverse-engineer wallets to infer strategies
│   └── build_native.sh           # Build bs-p libpmkernel and install to lib/
├── lib/                          # Compiled native library (gitignored)
│   └── libpmkernel.dylib         # macOS — or .so on Linux
├── src/
│   ├── bot.py                    # Main orchestrator
│   ├── client.py                 # Polymarket CLOB client
│   ├── config.py                 # All configuration (incl. bs-p params)
│   ├── order_manager.py          # Order lifecycle
│   ├── risk_manager.py           # Adaptive risk + portfolio Greeks + shock testing
│   ├── persistence.py            # SQLite state store
│   ├── dashboard.py              # TUI with Risk Engine panel
│   ├── websocket_feed.py         # Polymarket WebSocket
│   ├── logging_utils.py          # Colored console output
│   ├── native/                   # bs-p FFI bridge
│   │   ├── __init__.py
│   │   └── pmkernel.py           # ctypes wrapper (sigmoid, logit, quotes, kelly, greeks)
│   ├── feeds/
│   │   └── binance_ws.py         # Binance BTC/USDT real-time feed
│   ├── sizing/
│   │   └── kelly.py              # Inventory-aware Kelly sizing (bs-p enhanced)
│   ├── analytics/
│   │   └── strategy_tracker.py   # Per-strategy P&L, Sharpe, health
│   ├── alerts/
│   │   └── telegram.py           # Telegram notifications (incl. Greeks alerts)
│   └── strategies/
│       ├── base_strategy.py              # Abstract base class
│       ├── spread_strategy.py            # Avellaneda-Stoikov quoting (bs-p)
│       ├── orderbook_imbalance_strategy.py   # Native OBI + VWAP mid (bs-p)
│       ├── cross_asset_strategy.py       # Binance → Polymarket latency arb
│       ├── terminal_convergence_strategy.py  # Near-expiry convergence
│       ├── wallet_copy_strategy.py       # Copy top traders
│       └── ...                           # + 7 more strategies
├── tests/
│   └── unit/
│       ├── test_native_engine.py         # bs-p bridge + parity tests
│       └── ...
├── config/
│   ├── settings.chr.live.example         # CHR wallet bs-p config template
│   └── settings.bb.example
├── docs/
│   ├── bs-p/
│   │   ├── bs-p_integration_plan_*.md    # Full integration plan
│   │   ├── deployment-guide.md           # Testing → paper → live guide
│   │   └── What your bot already does well.md
│   ├── to-do.md
│   └── knowledge/
├── env.example
├── requirements.txt
└── README.md
```

## Development Phases

| Phase | Status | Description |
|-------|--------|-------------|
| 1 | ✅ Complete | Core bot, 5 strategies, WebSocket, risk management |
| 2 | ✅ Complete | Binance feed, Kelly sizing, 3 BTC 5-min strategies, vol-regime |
| 3 | ✅ Complete | Strategy analytics, adaptive risk, drawdown throttling |
| 4 | ✅ Complete | Telegram alerts, order lifecycle fixes, deployment hardening |
| 5 | ✅ Complete | VPIN smart money, sentiment pipeline, combinatorial arb |
| 6 | ✅ Complete | bs-p native engine: A-S quoting, inventory Kelly, portfolio Greeks, shock testing |

## Adding New Strategies

1. Create `src/strategies/my_strategy.py`:

```python
from .base_strategy import BaseStrategy, Signal, SignalType, MarketData

class MyStrategy(BaseStrategy):
    name = "my_strategy"
    description = "My custom trading strategy"

    def analyze(self, market_data: list[MarketData]) -> list[Signal]:
        signals = []
        # Your analysis logic here
        return signals

    def execute(self, signals: list[Signal], order_manager) -> list[dict]:
        results = []
        # Your execution logic here
        return results
```

2. Register in `src/strategies/__init__.py`:

```python
from .my_strategy import MyStrategy

AVAILABLE_STRATEGIES = {
    # ... existing strategies
    "my_strategy": MyStrategy,
}
```

3. Run:

```bash
python -m src.bot --strategy my_strategy
```

## Risk Warning

⚠️ **This bot trades real money when `PAPER_TRADING=false`**

- Always paper test first
- Start with small position sizes
- The adaptive risk manager protects your bankroll, but no system is foolproof
- Understand the strategies before deploying live
- Trading involves risk of loss

## API Documentation

- [Polymarket CLOB API](https://docs.polymarket.com/quickstart/orders/first-order)
- [Polymarket WebSocket](https://docs.polymarket.com/developers/CLOB/websocket)
- [Binance WebSocket](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [bs-p native engine](docs/bs-p/deployment-guide.md) — Avellaneda-Stoikov quoting, Kelly sizing, portfolio Greeks

## License

MIT - Use at your own risk.

