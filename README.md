# Polymarket Trading Bot

A modular, extensible trading bot for Polymarket prediction markets.

## Features

- **Modular Strategy System**: Easy to add new trading strategies
- **Real-time Data**: WebSocket feed for instant market updates
- **Risk Management**: Position limits, daily loss limits, circuit breakers
- **Paper Trading**: Test strategies without risking real funds
- **Rate Limiting**: Built-in compliance with Polymarket API limits

## Quick Start

### 1. Set Up Virtual Environment

```bash
cd polymarket-bot

# Create venv
python3 -m venv venv

# Activate venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

Copy the example environment file and fill in your credentials:

```bash
cp env.example .env
```

Edit `.env` with your:
- `POLYMARKET_PRIVATE_KEY`: Your wallet's private key (exported from MetaMask, Binance Web3 Wallet, etc.)
- `POLYMARKET_PROXY_ADDRESS`: Your Polymarket proxy wallet address (found on your profile page)

### 3. Run the Bot

```bash
# List available strategies
python -m src.bot --list-strategies

# Run with default spread strategy (paper trading mode by default)
python -m src.bot

# Run with specific strategy
python -m src.bot --strategy spread
```

## Configuration

All configuration is in `.env`. Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `PAPER_TRADING` | `true` | Set to `false` for live trading |
| `MIN_SPREAD_CENTS` | `2` | Minimum spread to trade |
| `ORDER_SIZE_USD` | `10` | Size per order |
| `MAX_POSITION_USD` | `100` | Max position per market |
| `DAILY_LOSS_LIMIT_USD` | `50` | Circuit breaker limit |

## Strategies

### Available Strategies

#### 1. Spread Strategy (default) ✅
Micro-spread farming - profits from bid-ask spreads:
- Identifies markets with wide spreads (> 2 cents)
- Places limit buys at bid price
- Places limit sells at bid + target spread
- Captures spread minus fees (~20% per trade, hundreds/day)

#### 2. Arbitrage Strategy ✅
Single-market arbitrage - risk-free when YES + NO < $1:
- Monitors combined YES/NO prices
- When total < 100¢, buy both outcomes
- Guaranteed profit on resolution
- Works on multi-outcome markets too

#### 3. Stink Bid Strategy ✅
Low-risk asymmetric bets for potential 100x returns:
- Find markets with high volume but thin orderbooks
- Place 1¢ limit bids waiting for orderbook nukes
- When someone panic-sells or fat-fingers, you get filled
- $10 bet can become $1000 (100x potential)
- Perfect for small accounts - high optionality, low capital at risk

#### 4. Favorite-Longshot Bias Strategy ✅
Exploits the most documented prediction market inefficiency (55%+ edge):
- **Fade longshots**: Sell/avoid contracts priced 2-10¢ (overpriced)
- **Buy favorites**: Buy contracts priced 85-95¢ (underpriced)
- Filter by time-to-expiration (prefer 3+ months out)
- Based on Snowberg & Wolfers research on probability misperception

#### 5. Late Money Strategy ✅
Follow informed traders near expiration:
- Monitor price velocity in final hours before resolution
- 40% of volume occurs in last minute (more informed)
- Follow sharp late moves rather than fade them
- 3-8% improved predictive accuracy over early prices

### Roadmap - Easy to Add 🟢

#### 6. Anchoring Bias Strategy
Exploit price stickiness around psychological levels:
- Detect prices clustering at 25/50/75¢ round numbers
- Trade away from anchors when fundamentals diverge
- Based on Tversky & Kahneman (1974) research

#### 7. Overreaction Strategy
Mean-reversion after extreme moves:
- After extreme events (>20% move), fade the initial reaction
- Expect 20-40% reversal within 24-48 hours
- Higher volume = stronger overreaction signal
- 2-5% abnormal returns documented

### Roadmap - Medium Effort 🟡

#### 8. Combinatorial Arbitrage Strategy
Cross-market logical inconsistencies ($40M extracted from Polymarket!):
- Parse market questions for logical dependencies
- "BTC >$100k" implies "BTC >$90k" must also be true
- Find pricing violations across related markets

- Requires NLP/embedding similarity detection

#### 9. Cross-Asset Signals Strategy
Financial markets lead prediction markets:
- Connect to crypto price feeds (BTC/ETH spot)
- Trade Polymarket when prediction lags spot price moves
- Fed futures → Fed policy prediction markets
- Speed edge: traditional markets have HFT, prediction markets don't

#### 10. Sentiment Strategy
News-based alpha with 5-minute optimal lag:
- Real-time news pipeline (NewsAPI, CryptoPanic)
- VADER/FinBERT sentiment scoring
- Trade divergences between sentiment and price
- 55%+ accuracy documented (Bollen et al.)

#### 11. Time Decay Strategy
Long-dated contracts compress toward 50%:
- Buy high-probability outcomes (>70%) in 3+ month markets
- Sell low-probability outcomes at same horizons
- Close positions as expiration approaches
- Exploits capital lockup discount rate bias

### Roadmap - Advanced 🔴

#### 12. VPIN/Order Flow Strategy
Track informed traders by wallet address:
- On-chain analysis of trader positions
- Identify accounts with historically accurate predictions
- Follow their position directions
- Requires blockchain indexing infrastructure

#### 13. Manipulation Counter-Trading
Detect and counter wash trading/manipulation:
- 25% of Polymarket volume is wash trading
- Identify unusual price moves without news
- Counter-trade manipulation attempts
- Markets revert after manipulation per research

#### 14. Liquidity Provision Strategy
Market making with inventory management:
- Place orders on both sides to tighten spreads
- Earn Polymarket liquidity rewards
- Requires sophisticated inventory risk management

#### 15. Hedge Strategy
Cross-platform hedging:
- Short on Polymarket prediction
- Hedge with long on perpetual DEX
- Requires Perpdex/HyperLiquid integration

### Adding New Strategies

1. Create a new file in `src/strategies/`:

```python
# src/strategies/my_strategy.py
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
    "spread": SpreadStrategy,
    "my_strategy": MyStrategy,  # Add your strategy
}
```

3. Run with your strategy:

```bash
python -m src.bot --strategy my_strategy
```

4. Run all strategies
```bash
python -m src.bot --strategy all
```

## Project Structure

```
polymarket-bot/
├── src/
│   ├── __init__.py
│   ├── config.py               # Configuration management
│   ├── client.py               # Polymarket CLOB client wrapper
│   ├── websocket_feed.py       # Real-time market data
│   ├── order_manager.py        # Order lifecycle management
│   ├── risk_manager.py         # Risk controls & circuit breakers
│   ├── bot.py                  # Main orchestrator
│   └── strategies/
│       ├── __init__.py              # Strategy registry
│       ├── base_strategy.py         # Abstract base class
│       ├── spread_strategy.py       # Spread farming
│       ├── arbitrage_strategy.py    # YES+NO < $1 arbitrage
│       ├── stink_bid_strategy.py    # 1¢ limit bids
│       ├── favorite_longshot_strategy.py  # Bias exploitation
│       └── late_money_strategy.py   # Price velocity signals
├── data/                   # Runtime data (ignored by git)
├── logs/                   # Log files (ignored by git)
├── docs/                   # Documentation
│   ├── CODE_REVIEW.md      # Technical code review
│   ├── STRATEGY_ROADMAP.md # Strategy implementation status
│   ├── to-do.md            # Development tasks
│   └── knowledge/          # Research & references
├── env.example             # Environment template (copy to .env)
├── requirements.txt        # Python dependencies
└── README.md
```

## Risk Warning

⚠️ **This bot trades real money when `PAPER_TRADING=false`**

- Always test with paper trading first
- Start with small position sizes
- Monitor the bot actively
- Understand the strategies before deploying
- Trading involves risk of loss

## API Documentation

- [Polymarket CLOB API](https://docs.polymarket.com/quickstart/orders/first-order)
- [Rate Limits](https://docs.polymarket.com/quickstart/introduction/rate-limits)
- [WebSocket](https://docs.polymarket.com/developers/CLOB/websocket)

## License

MIT - Use at your own risk.

