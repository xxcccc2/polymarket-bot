# ML Directional Edge Implementation Checklist

Use this as the operational runbook for `ml_directional`.

## 1. Data Pipeline

- [x] Confirm OHLCV CSVs exist under `data/ml/ohlc/btc/`
- [x] Normalize CSV schema through `src/ml/data_loader.py`
- [x] Build leakage-aware multi-timeframe features in `src/ml/features.py`
- [x] Add collector for raw microstructure events in `src/ml/collectors/binance_depth.py`
- [x] Start collector:
  - [x] `./.venv/bin/python -m scripts.collect_binance_microstructure --symbol btcusdt`
- [ ] On the VPS:
  - [ ] Set timezone to `UTC`
  - [ ] Enable `chrony`
  - [ ] Verify `timedatectl`
  - [ ] Verify `chronyc tracking`
- [ ] Benchmark `VPS -> Binance` connectivity before committing to the region
- [ ] Keep collector running for 2-4 weeks before microstructure-enhanced training
- [x] Verify collector quality periodically:
  - [x] `./.venv/bin/python -m scripts.check_microstructure_quality --db-path data/ml/collectors/binance_microstructure.sqlite`
  - [x] Confirm `depth_snapshots`, `agg_trades`, and `funding_open_interest` continue increasing
  - [ ] Keep re-checking freshness while the collector stays online

## 2. Baseline Training

- [ ] Train 15m OHLCV-only baseline:
  - [ ] `./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 15m`
- [ ] Train 1h OHLCV-only baseline:
  - [ ] `./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 1h`
- [ ] Confirm artifact exists:
  - [ ] `data/ml/artifacts/ml_directional_latest.pkl`
- [ ] Review printed summary:
  - [ ] Average accuracy
  - [ ] Worst fold accuracy
  - [ ] Average Brier score
  - [ ] Average net EV per trade
- [ ] Treat current trainer output as baseline research only:
  - [ ] Calibration is still manual / follow-up work
  - [ ] Final untouched holdout is still follow-up work
  - [ ] Promotion gates are still manual / follow-up work

## 3. Microstructure-Enhanced Training

- [ ] Only after enough collector history exists:
  - [ ] `./.venv/bin/python -m scripts.train_ml_directional --target-timeframe 15m --include-microstructure --microstructure-db data/ml/collectors/binance_microstructure.sqlite`
- [ ] Compare results vs OHLCV-only baseline
- [ ] Keep microstructure features only if they improve post-friction results

## 4. Replay Backtest

- [ ] Status for now: waiting for a trained artifact worth evaluating

- [ ] Run dedicated replay:
  - [ ] `./.venv/bin/python -m scripts.backtest_ml_directional --model-path data/ml/artifacts/ml_directional_latest.pkl --market-type 15m --coin btc`
- [ ] Confirm:
  - [ ] Net EV per trade > 0
  - [ ] Profit factor > 1.2
  - [ ] Max drawdown <= 15%
  - [ ] Friction-aware assumptions still look realistic

## 5. Profile Config

- [x] Add ML settings to profile config such as `config/settings.chr.live`
- [ ] Before paper trading, set:
  - [ ] `PAPER_TRADING=true`
  - [ ] `ML_DIRECTIONAL_ENABLED=true`
  - [ ] `ML_DIRECTIONAL_MODEL_PATH=./data/ml/artifacts/ml_directional_latest.pkl`
- [ ] Keep `.env` for secrets only

## 6. Paper Trading

- [ ] Run:
  - [ ] `BOT_PUBLIC_CONFIG_FILE=./config/settings.chr.live BOT_WALLET_ID=CHR ./.venv/bin/python -m src.bot --strategy ml_directional`
- [ ] Confirm runtime behavior:
  - [ ] Strategy loads artifact successfully
  - [ ] Only 15m/1h BTC Up/Down markets are considered
  - [ ] Signals include model probability and edge metadata
  - [ ] Feed staleness / rolling accuracy / Brier halts work

## 7. Promotion Gates

- [ ] Collect at least 200 resolved paper trades
- [ ] Accuracy >= 53% on 15m and >= 52% on 1h
- [ ] Brier <= 0.25
- [ ] Net EV > 0 after frictions
- [ ] No catastrophic 50-trade window below 48% accuracy
- [ ] Only then consider enabling live capital
