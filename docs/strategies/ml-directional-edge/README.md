# ML Directional Edge

Build and operate the `ml_directional` strategy as an in-repo module. This strategy is intentionally split into two parts:

- **Offline ML pipeline** in `src/ml/`
- **Live execution strategy** in `src/strategies/ml_directional_strategy.py`

## What Is Implemented

- OHLCV loader and normalization in `src/ml/data_loader.py`
- Time-safe multi-timeframe feature engineering in `src/ml/features.py`
- Optional microstructure aggregation from the collector SQLite DB into training-ready `micro_*` columns
- LightGBM-compatible model/artifact wrapper in `src/ml/model.py`
- Walk-forward training and evaluation in `src/ml/train.py` and `src/ml/evaluate.py`
- Dedicated replay harness in `src/ml/backtest.py`
- Binance microstructure collector in `src/ml/collectors/binance_depth.py`
- Live strategy registration and runtime safety rails in `src/strategies/ml_directional_strategy.py`

## Important Reality Check

The master plan is a **two-stage build**, not a single-step train-and-trade flow.

1. **You can train immediately** on OHLCV-only features.
2. **You cannot train the full microstructure-enhanced model immediately** unless you already have enough collected Binance depth / trades / liquidations / funding / OI history.

### Is microstructure data used for training too?

Yes. The intended final model uses microstructure features in training, not just live inference.

The collector now writes raw depth, agg trades, liquidations, funding, and open interest to SQLite, and the offline loader can aggregate that into training-ready `micro_*` features such as:

- `micro_obi_l1`, `micro_obi_l5`, `micro_obi_l10`
- `micro_cvd_1m`, `micro_cvd_5m`, `micro_cvd_15m`
- `micro_liq_delta_1m`, `micro_liq_delta_5m`
- `micro_funding_rate`
- `micro_open_interest`, `micro_open_interest_delta`

### Do you need 2-4 weeks before you can start training?

- **For the baseline model:** no. Train now with OHLCV-only features.
- **For the full signal stack from the master plan:** yes, you should collect roughly 2-4 weeks of microstructure data first.

That matches the master plan exactly: establish the OHLCV baseline first, then add microstructure once enough data has accumulated.

## Storage Layout

- `data/ml/ohlc/` — BTC OHLCV CSVs
- `data/ml/artifacts/` — trained model bundles
- `data/ml/collectors/` — collector SQLite outputs

## Config Placement

Put ML directional runtime knobs in your shared profile config such as `config/settings.chr.live`, not in `.env`.

The CHR profile now includes an explicit `ML_DIRECTIONAL_*` section with conservative defaults:

- `ML_DIRECTIONAL_ENABLED=false`
- `ML_DIRECTIONAL_MODEL_PATH=./data/ml/artifacts/ml_directional_latest.pkl`
- `ML_DIRECTIONAL_ENABLED_HORIZONS=15m,1h`
- `ML_DIRECTIONAL_MIN_PROBABILITY=0.53`
- `ML_DIRECTIONAL_MIN_EDGE=0.03`
- `ML_DIRECTIONAL_MAKER_OFFSET=0.005`
- `ML_DIRECTIONAL_SIGNAL_COOLDOWN_SECONDS=45`
- `ML_BINANCE_COLLECTOR_DB=./data/ml/collectors/binance_microstructure.sqlite`

## CLI Scripts

These scripts now exist and are the recommended operator entrypoints.

### 1. Collect Binance microstructure

```bash
./.venv/bin/python -m scripts.collect_binance_microstructure --symbol btcusdt
```

What it does:

- subscribes to **Binance futures** WebSocket (depth, agg trades, force-order/liquidations) — liquidations only exist on the futures stream
- polls funding rate and open interest from the futures REST API
- writes to `data/ml/collectors/binance_microstructure.sqlite`

Override with `BINANCE_FUTURES_WS_COMBINED_URL` if needed (default: `wss://fstream.binance.com/stream`). See `docs/strategies/ml-directional-edge/COLLECTOR_VPS_DEPLOY.md` for VPS deploy and upgrade steps.

### 2. Check collector quality

```bash
./.venv/bin/python -m scripts.check_microstructure_quality --db-path data/ml/collectors/binance_microstructure.sqlite
```

What it does:

- prints row counts for each collector table
- shows the latest timestamp seen in each table

### 3. Train the baseline model

```bash
./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 15m
```

What it does:

- loads OHLCV from `data/ml/ohlc/`
- builds a leakage-aware training frame
- runs walk-forward training
- exports an artifact to `data/ml/artifacts/ml_directional_latest.pkl`

What it does not yet do:

- does not add probability calibration on top of the exported live threshold
- does not enforce a dedicated final untouched holdout after walk-forward selection
- does not yet gate promotion with the full master-plan acceptance checks automatically
- does not yet emit a machine-readable training report for artifact promotion workflows

Treat the current baseline trainer as a research-grade baseline, not the final promotion gate.

### 4. Train with microstructure

Only do this after enough collector history exists.

```bash
./.venv/bin/python -m scripts.train_ml_directional \
  --target-timeframe 15m \
  --include-microstructure \
  --microstructure-db data/ml/collectors/binance_microstructure.sqlite
```

### 5. Replay backtest

Do this after a baseline artifact exists. It is safe to defer replay until training is producing artifacts you actually want to compare.

```bash
./.venv/bin/python -m scripts.backtest_ml_directional \
  --model-path data/ml/artifacts/ml_directional_latest.pkl \
  --market-type 15m \
  --coin btc
```

### 6. Run live in paper mode

First set these in your profile config:

```dotenv
PAPER_TRADING=true
ML_DIRECTIONAL_ENABLED=true
ML_DIRECTIONAL_MODEL_PATH=./data/ml/artifacts/ml_directional_latest.pkl
```

Then run:

```bash
BOT_PUBLIC_CONFIG_FILE=./config/settings.chr.live BOT_WALLET_ID=CHR ./.venv/bin/python -m src.bot --strategy ml_directional
```

## Live Strategy Notes

- The live strategy only trades crypto `Up or Down` markets on horizons enabled by `ML_DIRECTIONAL_ENABLED_HORIZONS`.
- It assumes maker-first execution and requires model edge to clear a friction buffer.
- Final trade admission and resizing still flow through the bot and `RiskManager`.
- Runtime feature rows are aligned to the artifact’s `feature_columns`; any unavailable fields are filled with `0.0`.
- Safety rails include feed staleness halts, rolling accuracy / Brier monitoring, and automatic pauses after loss streaks.
- The strategy now suppresses both-side exposure on the same `condition_id` while an order or unresolved position is active.
- Resolution tracking is still conservative: it works only when the market is still observable after expiry, so do not treat rolling paper metrics as final audit-grade stats yet.

## Collector Status And Sanity Checks

Your latest collector run looks healthy:

- `depth_snapshots`, `agg_trades`, and `funding_open_interest` are all increasing over time
- websocket connectivity is confirmed
- `liquidations=0` is acceptable during quiet windows and does not by itself indicate collector failure

Keep checking freshness with:

```bash
./.venv/bin/python -m scripts.check_microstructure_quality --db-path data/ml/collectors/binance_microstructure.sqlite
```

Healthy expectations:

- `depth_snapshots` should keep climbing quickly
- `agg_trades` should keep climbing during active market hours
- `funding_open_interest` should step up on each REST poll
- latest timestamps should remain close to wall-clock time

## VPS Collector Deploy

Chosen deployment target:

- location: `Germany`
- OS: `Ubuntu 24.04 LTS`

Why:

- Ubuntu LTS is the lowest-friction target for Python + systemd operations
- final region validation should be based on `VPS -> Binance` latency and stability, not `Brazil -> VPS` latency

Deployment guide: `docs/strategies/ml-directional-edge/VPS_DEPLOY.md`

Bootstrap script: `scripts/setup_ml_collector_vps.sh`

## Implementation Checklist

### Phase 1: Baseline

- [x] Add `src/ml/` package with loader, features, model, train, evaluate, and backtest modules
- [x] Add Binance microstructure collector and SQLite storage
- [x] Add live `ml_directional` strategy and register it in the bot
- [x] Add focused unit tests for features, artifacts, and live signal generation
- [ ] Train OHLCV-only baseline on `15m`
- [ ] Train OHLCV-only baseline on `1h`
- [ ] Export validated artifact to `data/ml/artifacts/ml_directional_latest.pkl`
- [ ] Run replay backtest against PolyBackTest data
- [ ] Enable `ML_DIRECTIONAL_ENABLED=true` only in paper mode first

### Phase 2: Microstructure Upgrade

- [ ] Start `scripts.collect_binance_microstructure`
- [ ] Keep it running for 2-4 weeks
- [ ] Periodically run `scripts.check_microstructure_quality`
- [ ] Retrain with `--include-microstructure`
- [ ] Compare microstructure-enhanced folds against OHLCV baseline
- [ ] Promote only if validation and replay metrics improve after frictions

### Paper Trading Gates

- [ ] Accumulate 200+ resolved paper trades
- [ ] Walk-forward accuracy > 53% on 15m and > 52% on 1h
- [ ] Brier score < 0.25
- [ ] Net EV per trade > 0 after friction model
- [ ] Profit factor > 1.2 after replay assumptions

## Reference Plan

The validated roadmap remains in `docs/strategies/ml-directional-edge/plan/master-plan.md`.
