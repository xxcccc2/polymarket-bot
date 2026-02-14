# Polymarket Trading Bot

An institutional-grade algorithmic trading platform for Polymarket prediction markets, with real-time Binance cross-asset signals, adaptive risk management, and Telegram alerts.

## Features

- **9 Trading Strategies** — from cross-asset latency arb to stink bids
- **Binance BTC Feed** — real-time VWAP, volatility, and price velocity for 5-min BTC markets
- **Adaptive Risk Manager** — bankroll-proportional limits, drawdown throttling, auto-halt
- **Strategy Analytics** — per-strategy P&L, Sharpe ratio, win rate, streaks, auto-disable losers
- **Telegram Alerts** — fills, risk events, daily summaries pushed to your phone
- **Kelly Criterion Sizing** — mathematically optimal position sizing with safety caps
- **Paper Trading** — full simulation mode, zero risk
- **Order Lifecycle** — tracking, duplicate prevention, stale cleanup, graceful shutdown cancellation

## Quick Start

### 1. Set Up Virtual Environment

```bash
cd polymarket-bot
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

### 2. Configure Environment

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

### 3. Run the Bot

```bash
# Paper test with recommended BTC 5-min strategies (default)
python -m src.bot

# Run specific strategy
python -m src.bot --strategy cross_asset

# Run all non-disabled strategies
python -m src.bot --strategy all

# List available strategies
python -m src.bot --list-strategies

# Force paper mode
python -m src.bot --paper
```

## Configuration

All configuration lives in `.env`. Key settings:

### Trading Parameters

| Setting | Default | Description |
|---------|---------|-------------|
| `PAPER_TRADING` | `true` | Set `false` for live trading |
| `ORDER_SIZE_USD` | `10` | Base order size (adaptive risk may override) |
| `MAX_POSITION_USD` | `100` | Max position per market |
| `DAILY_LOSS_LIMIT_USD` | `50` | Circuit breaker |
| `SCAN_INTERVAL_SECONDS` | `30` | Time between strategy scans |

### Adaptive Risk (Phase 3)

When `ADAPTIVE_RISK_ENABLED=true`, all limits scale with your current balance:

| Metric | % of Bankroll | With $79 |
|--------|---------------|----------|
| Max per trade | 4% | ~$3.17 |
| Max per market | 12% | ~$9.52 |
| Max total exposure | 30% | ~$23.79 |
| Daily loss limit | 6% | ~$4.76 |
| Drawdown throttle | at -5% | Cuts sizes to 50% |
| Drawdown halt | at -12% | Full stop |
| Balance floor | 70% | ~$55.51 |

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

#### 3. Orderbook Imbalance ✅ `orderbook_imbalance`
Detect bid/ask pressure with Binance confirmation:
- Measures orderbook skew on Polymarket
- Confirms direction with Binance BTC momentum
- Only fires when both signals agree

#### 4. Stink Bid ✅ `stink_bid`
Passive asymmetric bets — $1 bids for potential 100x:
- Places 1¢ limit bids on high-volume markets
- Fills on panic sells and fat-finger trades
- Perfect for small accounts — high optionality, low capital

#### 5. Late Money ✅ `late_money`
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

### Disabled by Default (capital-inefficient for <$500)

#### 9. Spread Strategy `spread`
Micro-spread farming. Disabled: adverse selection eats small accounts alive.

#### 10. Arbitrage Strategy `arbitrage`
YES + NO < $1 arbs. Disabled: near-extinct on Polymarket.

#### 11. Favorite-Longshot Bias `favorite_longshot`
Buy favorites at 85-95¢. Disabled: locks up too much capital.

#### 12. Cross-Platform Arbitrage `cross_platform_arbitrage`
Polymarket vs Kalshi arb. Disabled: requires Kalshi infrastructure.

### Strategy Modes

```bash
# Recommended: BTC 5-min + advanced strategies (default)
python -m src.bot --strategy btc_5min

# Just cross-asset latency arb
python -m src.bot --strategy cross_asset

# Just VPIN smart money detection
python -m src.bot --strategy vpin

# Just sentiment-driven trading
python -m src.bot --strategy sentiment

# All non-disabled strategies (12 total, 8 active)
python -m src.bot --strategy all
```

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

### Adaptive Risk Manager (Phase 3)

When enabled, all risk limits scale dynamically with your balance:
- Lose money → limits tighten automatically
- Make money → limits expand proportionally
- **Drawdown throttle** at -5% → trade sizes cut to 50%
- **Drawdown halt** at -12% → all trading stopped
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
├── src/
│   ├── bot.py                    # Main orchestrator
│   ├── client.py                 # Polymarket CLOB client
│   ├── config.py                 # All configuration
│   ├── order_manager.py          # Order lifecycle
│   ├── risk_manager.py           # Adaptive risk controls
│   ├── persistence.py            # SQLite state store
│   ├── websocket_feed.py         # Polymarket WebSocket
│   ├── kalshi_client.py          # Kalshi REST client
│   ├── logging_utils.py          # Colored console output
│   ├── feeds/
│   │   └── binance_ws.py         # Binance BTC/USDT real-time feed
│   ├── sizing/
│   │   └── kelly.py              # Kelly Criterion position sizing
│   ├── analytics/
│   │   └── strategy_tracker.py   # Per-strategy P&L, Sharpe, health
│   ├── alerts/
│   │   └── telegram.py           # Telegram push notifications
│   └── strategies/
│       ├── base_strategy.py              # Abstract base class
│       ├── spread_strategy.py            # Spread farming (vol-regime aware)
│       ├── arbitrage_strategy.py         # YES+NO < $1 arb
│       ├── stink_bid_strategy.py         # 1¢ limit bids
│       ├── favorite_longshot_strategy.py # Bias exploitation
│       ├── late_money_strategy.py        # Price velocity signals
│       ├── cross_asset_strategy.py       # Binance → Polymarket latency arb
│       ├── terminal_convergence_strategy.py  # Near-expiry convergence
│       ├── orderbook_imbalance_strategy.py   # Bid/ask pressure
│       └── cross_platform_arbitrage_strategy.py  # Polymarket vs Kalshi
├── data/                   # Runtime data (gitignored)
├── logs/                   # Log files (gitignored)
├── docs/
│   ├── CODE_REVIEW.md
│   ├── STRATEGY_ROADMAP.md
│   ├── to-do.md
│   └── knowledge/          # Academic research & references
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

## License

MIT - Use at your own risk.

