# Backtesting API Reference

Internal API for the `src/backtest` module.

---

## PolyBackTestClient

HTTP client for the PolyBackTest REST API.

### Constructor

```python
from src.backtest import PolyBackTestClient

client = PolyBackTestClient(
    api_key=None,   # Uses POLYBACKTEST_API_KEY from env if None
    base_url=None,  # Uses POLYBACKTEST_BASE_URL from env if None
)
```

### Methods

#### `get_limits() -> dict`

Returns current plan and limits (5m_markets, 15m_markets, etc.).

```python
limits = client.get_limits()
# {"plan": "free", "limits": {"5m_markets": 50, ...}}
```

#### `list_markets(coin, market_type, limit, offset, resolved, start_time, end_time) -> dict`

List markets with pagination.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `coin` | str | `"btc"` | `btc` or `eth` |
| `market_type` | str \| None | None | `5m`, `15m`, `1hr`, `4hr`, `24hr` |
| `limit` | int | 50 | Max results (max 100) |
| `offset` | int | 0 | Pagination offset |
| `resolved` | bool \| None | None | Filter by resolution status |
| `start_time` | str \| None | None | ms epoch or ISO8601 |
| `end_time` | str \| None | None | ms epoch or ISO8601 |

Returns: `{markets: [...], total: int, limit: int, offset: int, warning?: str}`

#### `get_market(market_id, coin) -> dict`

Get single market by ID.

#### `get_snapshots(market_id, coin, limit, offset, start_time, end_time, include_orderbook) -> dict`

Get snapshots for a market.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `market_id` | str | — | Market ID |
| `coin` | str | `"btc"` | `btc` or `eth` |
| `limit` | int | 1000 | Max per request (max 1000) |
| `offset` | int | 0 | Pagination offset |
| `start_time` | str \| None | None | Filter snapshots after |
| `end_time` | str \| None | None | Filter snapshots before |
| `include_orderbook` | bool | False | Include full orderbook depth |

Returns: `{market: {...}, snapshots: [...], total: int, limit: int, offset: int}`

### Rate Limiting

- 2000 req/min, 100 req/sec burst (PolyBackTest)
- Client throttles to ~30 req/sec
- 429 responses trigger retry with backoff

### Error Handling

- `401` — Invalid/missing API key
- `402` — Market outside plan (upgrade required)
- `429` — Rate limit (retried automatically)

---

## BacktestStore

SQLite-backed storage for markets and snapshots.

### Constructor

```python
from src.backtest import BacktestStore

store = BacktestStore(db_path=None)  # Uses BACKTEST_DB from config
```

### Methods

#### `save_markets(markets: list[dict]) -> None`

Persist markets. Upserts by `market_id`.

#### `save_snapshots(market_id: str, snapshots: list[dict]) -> None`

Save snapshots for a market. Replaces existing snapshots for that market.

#### `load_markets(market_type, coin, limit) -> list[dict]`

Load markets, optionally filtered.

#### `load_snapshots(market_id, start_time, end_time) -> list[dict]`

Load snapshots for a market, optionally filtered by time.

#### `get_market_count(market_type) -> int`

Count stored markets.

---

## DataDownloader

Fetches data from PolyBackTest and stores locally.

### Constructor

```python
from src.backtest import DataDownloader

downloader = DataDownloader(
    client=None,  # PolyBackTestClient instance
    store=None,   # BacktestStore instance
)
```

### Methods

#### `download(coin, market_types, include_orderbook) -> dict`

Download markets and snapshots.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `coin` | str | `"btc"` | `btc` or `eth` |
| `market_types` | list \| None | All | e.g. `["5m", "15m"]` |
| `include_orderbook` | bool | False | Include orderbook in snapshots |

Returns: `{markets_downloaded, snapshots_downloaded, errors, warnings, plan?}`

---

## ReplayBinanceFeed

Synthetic Binance-like feed built from snapshot `btc_price` series.

### Constructor

```python
from src.backtest import ReplayBinanceFeed

feed = ReplayBinanceFeed(snapshots)  # list of dicts with 'time', 'btc_price'
```

### Methods

#### `seek(timestamp: float) -> None`

Seek to snapshot at or before given Unix timestamp.

#### `set_current_idx(idx: int) -> None`

Set current position by index (for stepping).

#### `get_state(asset: str) -> BinanceState`

Returns `BinanceState` with:
- `last_price` — btc_price at current snapshot
- `price_change_pct_10s`, `price_change_pct_30s`, `price_change_pct_60s`
- `volatility_5m` — annualized
- `bid_pressure` — 0.5 (no trade flow)
- `connected` — True if data available

### Properties

- `connected` — bool
- `current_timestamp` — float
- `__len__` — number of snapshots

---

## Mappers

### `snapshot_to_market_data(snapshot, market, outcome) -> MarketData | None`

Convert snapshot + market to `MarketData` for strategy.analyze().

| Parameter | Description |
|-----------|-------------|
| `snapshot` | Dict with price_up, price_down, btc_price, time |
| `market` | Dict with slug, clob_token_up, clob_token_down, end_time |
| `outcome` | `"Up"` or `"Down"` |

### `snapshots_to_market_data_list(snapshot, market) -> list[MarketData]`

Build MarketData for both Up and Down outcomes.

---

## BacktestEngine

Replays historical data through strategies.

### Constructor

```python
from src.backtest import BacktestEngine

engine = BacktestEngine(
    store=None,
    convergence_window_s=None,  # Seconds before expiry (default: from config)
    step_interval=1,           # Step every N snapshots (simulate scan interval)
)
```

### Methods

#### `run(strategy_name, market_type, coin, limit) -> BacktestResult`

Run backtest.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `strategy_name` | str | `"terminal_convergence"` | Strategy to run |
| `market_type` | str \| None | None | Filter: 5m, 15m, 1hr, 4hr, 24hr |
| `coin` | str | `"btc"` | btc or eth |
| `limit` | int \| None | None | Max markets (for quick tests) |

Returns: `BacktestResult` with trades, total_pnl, win_count, loss_count, markets_run, markets_skipped.

---

## BacktestResult

Dataclass with:
- `trades: list[BacktestTrade]`
- `total_pnl: float`
- `win_count: int`
- `loss_count: int`
- `markets_run: int`
- `markets_skipped: int`

## BacktestTrade

Dataclass with:
- `market_id`, `token_id`, `outcome`, `side`, `price`, `size`
- `timestamp`, `winner`, `pnl`, `resolved`
