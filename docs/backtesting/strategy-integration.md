# Strategy Integration for Backtesting

How to make existing strategies backtestable and add new ones.

---

## Requirements for Backtestable Strategies

A strategy is backtestable if it:

1. **Implements `analyze(market_data: list[MarketData]) -> list[Signal]`** — The engine calls this with historical snapshots converted to `MarketData`.
2. **Uses `binance_feed` only for price/momentum** — The engine provides `ReplayBinanceFeed` built from snapshot `btc_price` series.
3. **Does not require live WebSocket/orderbook** — Unless you download with `--include-orderbook` and extend the engine.

---

## Terminal Convergence (Reference Implementation)

`TerminalConvergenceStrategy` is the reference backtestable strategy.

### Dependencies

- **binance_feed** — Required. Must implement `get_state(asset) -> BinanceState` with:
  - `last_price`, `price_change_pct_10s`, `price_change_pct_30s`, `price_change_pct_60s`
  - `volatility_5m`, `bid_pressure`, `connected`

### How the Engine Wires It

```python
replay_feed = ReplayBinanceFeed(snapshots)
strategy = TerminalConvergenceStrategy(config={"binance_feed": replay_feed})

# For each snapshot in convergence window:
replay_feed.set_current_idx(j)
market_data_list = snapshots_to_market_data_list(snap, market)
signals = strategy.analyze(market_data_list)
```

### MarketData Mapping

| MarketData Field | Source |
|------------------|--------|
| `token_id` | `clob_token_up` or `clob_token_down` |
| `mid_price` | `price_up` or `price_down` from snapshot |
| `best_bid`, `best_ask` | Mid ± 1¢ (simplified) |
| `end_date_ts` | Parsed from market `end_time` |
| `question`, `market_slug` | From market |

---

## Adding a New Strategy to Backtest

### 1. Ensure Strategy Accepts Config

```python
class MyStrategy(BaseStrategy):
    def __init__(self, config=None):
        super().__init__(config)
        self.binance_feed = self.config.get("binance_feed")  # Optional
```

### 2. Register in Engine

Edit `src/backtest/engine.py`:

```python
STRATEGY_MAP = {
    "terminal_convergence": TerminalConvergenceStrategy,
    "my_strategy": MyStrategy,
}

# In run():
strategy_cls = STRATEGY_MAP.get(strategy_name)
strategy = strategy_cls(config={"binance_feed": replay_feed})
```

### 3. Handle Strategy-Specific Requirements

- **Needs orderbook?** — Download with `--include-orderbook`, extend `snapshots_to_market_data_list` to pass `orderbook` in MarketData.
- **Needs Binance state?** — Pass `ReplayBinanceFeed` in config.
- **Needs different outcome format?** — Extend mappers or add strategy-specific mapper.

---

## Creating a Backtest-Only Strategy

You can create a strategy that only runs in backtest (no live execution):

```python
class BacktestOnlyStrategy(BaseStrategy):
    name = "backtest_only"
    description = "Strategy for backtesting only"

    def analyze(self, market_data):
        # Your logic using MarketData
        return signals

    def execute(self, signals, order_manager):
        # No-op for backtest; engine simulates fills
        return []
```

The engine does not call `execute`; it simulates fills from `analyze()` signals.

---

## Simulated Fill Logic

The engine:

1. Calls `strategy.analyze(market_data_list)` at each snapshot step.
2. For each BUY signal: records position `{token_id: {size, price, outcome, timestamp}}`.
3. At market end: resolves using `market.winner` (Up/Down).
4. PnL = `size * (1 if outcome==winner else 0) - size * price`.

### Limitations

- **Single fill per token per market** — First signal wins; later signals for same token are ignored.
- **No slippage** — Fill at signal price.
- **No fees** — Can be added in engine if needed.
- **No partial fills** — Assumes full fill.

---

## Extending for Orderbook Strategies

To backtest `orderbook_imbalance` or similar:

1. Download with `--include-orderbook`.
2. Ensure `snapshot_to_market_data` passes `orderbook` when present.
3. The strategy receives `MarketData.orderbook` with `bids` and `asks` arrays.
4. Engine logic remains the same; only the data passed to `analyze()` changes.
