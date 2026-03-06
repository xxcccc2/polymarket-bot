---
name: BTC Directional ML Roadmap
overview: Design a skeptical, friction-aware roadmap for adding a BTC Up/Down directional ML layer to Polymarket trading, reusing the existing execution/risk stack while isolating the ML lifecycle. The plan assumes the strategy only deserves capital if it survives exact market-rule labeling, fee/slippage modeling, and walk-forward validation.
todos:
  - id: spec-target-label
    content: Specify the exact BTC Up/Down Polymarket contract template, resolution rule, and label alignment for a single initial horizon (preferably 15m).
    status: pending
  - id: assemble-research-dataset
    content: Build a timestamped offline dataset combining BTC OHLCV, Binance microstructure, derivatives context, and Polymarket market-state snapshots.
    status: pending
  - id: train-baselines
    content: Train and calibrate logistic and LightGBM/XGBoost baselines before considering TCN or other deep sequence models.
    status: pending
  - id: build-friction-simulator
    content: Create a walk-forward simulator with fees, slippage, latency, missed fills, and partial-fill assumptions that match Polymarket execution realities.
    status: pending
  - id: design-service-boundary
    content: Define the ML-service-to-bot signal contract and thin adapter strategy so the existing bot handles execution, risk, alerts, and persistence.
    status: pending
  - id: paper-then-live
    content: Run paper trading and live-shadow evaluation, then start with tiny fractional Kelly only if calibration and net EV survive contact with real fills.
    status: pending
isProject: false
---

# BTC Directional ML Strategy Roadmap

## Verdict

This is **feasible only as a selective pricing-dislocation strategy**, not as a pure “predict BTC candles and print money” system.

Brutally honest base case:

- **OHLCV alone** is unlikely to sustain meaningful live edge on short BTC horizons. Expect something like **near-random to marginally useful** out of sample once you remove leakage and regime overfit.
- The defensible version is: **estimate short-horizon BTC direction and trade only when Polymarket probability materially lags that estimate**.
- Realistic live target is not “high accuracy”; it is **well-calibrated probabilities on a small subset of trades**.
- A model showing **52–54% raw accuracy** can still be useless after fees, spread, queue loss, stale quotes, and selection bias.

Reasonable expectation ranges if the research is done correctly:

- **OHLCV-only live directional accuracy**: roughly **50.5–52.5%** on tradable horizons; often not enough.
- **OHLCV + microstructure + derivatives regime filters** on a selective subset: **52–56%** may be achievable in favorable regimes, but not stably across all periods.
- **Live Sharpe** for a small, selective strategy after frictions: think **0.5–1.5** if real edge exists. Higher backtest Sharpe should be treated as suspicious until latency and fill assumptions are punished hard.

What would invalidate the edge:

- Labeling the wrong target relative to actual Polymarket market resolution.
- Using features not available at the decision timestamp.
- Fee/slippage assumptions that are even slightly too optimistic.
- Structural change in Polymarket crypto participation after crypto taker fees and increased bot competition.
- Regime dependence: the model works in trend or squeeze regimes and dies in chop.

## Strategy Identity

Formal taxonomy:

- **Cross-venue probabilistic directional trading**
- **Multi-timeframe BTC microstructure nowcasting**
- **Prediction-market statistical arbitrage / dislocation trading**

If you want a concise internal name:

- **BTC Cross-Venue Directional Dislocation**

## Architecture Recommendation

Do **not** build this as a fresh standalone trading repo unless you intend to replace your current bot.

Best structure:

- Keep `[/Users/christian/Documents/www/polymarket-bot](file:///Users/christian/Documents/www/polymarket-bot)` as the **execution, risk, persistence, dashboard, and alerting engine**.
- Build the ML stack as a **separate research/inference service**.
- Add a thin adapter strategy in the bot that consumes model outputs and converts them into `Signal`s.

Why:

- The current bot already has a usable strategy boundary in `[/Users/christian/Documents/www/polymarket-bot/src/strategies/base_strategy.py](file:///Users/christian/Documents/www/polymarket-bot/src/strategies/base_strategy.py)` and shared dependency injection in `[/Users/christian/Documents/www/polymarket-bot/src/bot.py](file:///Users/christian/Documents/www/polymarket-bot/src/bot.py)`.
- It is strong on execution/risk, weak on experiment tracking, feature storage, model versioning, and offline validation.
- Mixing model training and the live trading loop in one process is an unnecessary operational risk.

Suggested boundary:

- ML service outputs: `timestamp`, `market_id`, `horizon`, `p_up`, `p_down`, `calibration_band`, `feature_version`, `model_version`, `signal_expiry`, `regime_flags`.
- Bot adapter reads that payload, checks market state, re-prices for fees/slippage, applies sizing, and places/cancels orders through the existing stack.

Relevant existing integration points:

- `[/Users/christian/Documents/www/polymarket-bot/src/strategies/base_strategy.py](file:///Users/christian/Documents/www/polymarket-bot/src/strategies/base_strategy.py)`
- `[/Users/christian/Documents/www/polymarket-bot/src/bot.py](file:///Users/christian/Documents/www/polymarket-bot/src/bot.py)`
- `[/Users/christian/Documents/www/polymarket-bot/src/feeds/binance_ws.py](file:///Users/christian/Documents/www/polymarket-bot/src/feeds/binance_ws.py)`
- `[/Users/christian/Documents/www/polymarket-bot/src/risk_manager.py](file:///Users/christian/Documents/www/polymarket-bot/src/risk_manager.py)`
- `[/Users/christian/Documents/www/polymarket-bot/docs/backtesting/strategy-integration.md](file:///Users/christian/Documents/www/polymarket-bot/docs/backtesting/strategy-integration.md)`

## Recommended Model Stack

Start simple. The failure mode here is not “model too dumb”; it is “pipeline too optimistic.”

### Phase 1 model hierarchy

1. **Calibrated LightGBM / XGBoost classifier**
2. **Regularized logistic baseline**
3. Optional: **TCN or small 1D CNN/TCN hybrid** only if tree models leave clear residual signal
4. Avoid starting with LSTM/Transformer

Why this order:

- Tree models handle heterogeneous tabular features, missingness, nonlinear interactions, and feature importance inspection well.
- They are fast to train, easier to debug, and less likely to hide leakage than deep sequence models.
- Logistic regression gives you a brutally useful sanity check: if boosted trees cannot beat it by much after calibration, your fancy pipeline probably has no durable edge.
- LSTM/Transformer complexity is rarely justified at this stage. For short-horizon BTC, **better inputs usually matter more than a more exotic architecture**.

Recommended training target:

- Binary label aligned to the **exact Polymarket contract resolution rule** for each BTC Up/Down market.
- Not generic “next candle green/red” unless that is truly the settlement rule.

Calibration:

- Use **Platt scaling or isotonic regression** on a validation fold.
- Optimization target should be **expected value after frictions**, not accuracy alone.

## Feature Pipeline

### Rank by expected alpha contribution

1. **Polymarket-vs-Binance dislocation features**
2. **Binance microstructure and trade-flow features**
3. **Derivatives regime features**
4. **Multi-timeframe OHLCV structure**
5. **Polymarket-specific local state**
6. **Slow macro / sentiment / on-chain**

### Core feature groups

#### 1. Cross-venue dislocation

Most important because this is what turns forecasting into tradable EV.

- Polymarket implied probability minus model probability
- Distance to fee-adjusted fair value
- Probability drift over last 10s / 30s / 60s on Polymarket
- Local spread, depth, queue asymmetry, and recent fill intensity
- Time remaining to resolution

#### 2. Binance microstructure

Highest expected incremental value versus OHLCV.

- Top-of-book imbalance
- Depth imbalance at multiple bands
- Microprice / VAMP-style price
- Trade imbalance / CVD / aggressive buy-sell delta
- Short-horizon realized volatility
- Order book slope, replenishment, and cancel pressure

#### 3. Derivatives regime

Useful as context and filters, not necessarily as primary trigger.

- Funding rate level and change
- Open interest level and delta
- Basis / perp-premium proxies
- Liquidation burst indicators and signed liquidation imbalance

#### 4. Multi-timeframe OHLCV

Useful, but weak on its own.

- Returns over 1m / 5m / 15m / 1h / 4h windows
- Realized volatility and ATR-like measures
- Candle body / wick ratios
- Position within recent range
- Trend persistence / reversal markers
- Cross-timeframe alignment features, e.g. `5m` momentum with `1h` regime and `4h` volatility state

#### 5. Polymarket local market state

Retail behavior may create temporary inefficiency, but this can disappear fast.

- Quote staleness
- Thin-liquidity zones
- One-sided book gaps
- Volume spikes and last-minute price jumps
- Market-specific idiosyncrasy by resolution bucket and time-of-day

#### 6. Slow features

Use as coarse regime context only.

- BTC dominance
- Macro calendar flags
- Fear & Greed
- On-chain exchange inflows / whale alerts

Blunt view on slow features:

- They are usually **too slow, too sparse, or too noisy** for `5m–15m` directional betting.
- Keep them as regime gates, not core alpha.

## Validation and Leakage Control

This section determines whether the strategy is real.

### Non-negotiables

- Build labels from the **exact contract rule and timestamp convention**.
- Every feature row must have an **as-of timestamp** and a **feature availability timestamp**.
- Slow features must be forward-filled only from when they were actually observable.
- Multi-timeframe candles must be computed using only data closed before decision time.
- If you use the final minute of a candle to trade that same candle’s Polymarket market, you must model the reduced time-to-fill and end-of-window slippage explicitly.

### Validation design

Use a three-layer evaluation stack:

1. **Expanding-window walk-forward** as the main estimate of deployable performance
2. **Purged / embargoed time-series CV** inside the training set for model selection and threshold tuning
3. **Final untouched holdout** from the latest regime

Why:

- Standard k-fold is wrong here.
- Pure expanding-window alone can still leak through overlapping horizons if you are not careful.
- Purging matters because adjacent samples share information in overlapping price paths.

### What to optimize

Primary metrics:

- Net EV per trade after fees/slippage
- Brier score / calibration error
- Precision and recall in the “trade” region only
- Profit factor and drawdown
- Capacity-adjusted PnL

Secondary metrics:

- Accuracy
- ROC-AUC

If your best slide says “54% accuracy” without calibration and net EV, assume the model is not production-ready.

## Mispricing Detection Logic

The trade should fire only when the model probability exceeds a fee- and slippage-adjusted threshold.

### Core rule

For a YES buy at market price `q`:

- Trade only if `p_model - q` is greater than **all-in friction + uncertainty buffer**.

Where friction must include:

- Taker fee or expected maker adverse-selection cost
- Spread crossing cost or missed-fill probability
- Cancellation/requote cost near expiry
- Model calibration uncertainty

### Fee reality

Per current Polymarket docs, **crypto markets now have taker fees**, peaking around **1.56% effective rate near 50c** and falling toward the extremes. That matters a lot because your target markets often live near the middle before the move is obvious.

Conservative threshold guidance:

- **Absolute edge below ~3 percentage points** is usually noise after frictions.
- **Practical deployment threshold** likely starts around **4–6 points** of model-vs-market probability gap.
- For aggressive late-entry trades in thin books, you may need **6–10 points** to survive live execution.

Kelly should be based on **fee-adjusted fair odds**, not raw predicted probability.

## Order Timing and Execution

### Timing verdict

Best expected trade window:

- **Late-candle entry** is the most plausible edge window for `15m` and some `5m` markets because you have more information than the market had at open, but Polymarket can still lag.

Trade-offs by timing:

- **At open**: more time to fill, but weakest information edge; mostly a forecasting contest against a market that can update before you are proven right.
- **Mid-candle**: compromise; better signal than open, but still more noise and less certainty.
- **Near close**: strongest directional information and best chance to exploit stale retail odds, but worst fill risk, more adverse selection, and higher sensitivity to latency/jitter.

Execution policy:

- Prefer **resting maker orders** when dislocation is large enough and queue quality is decent.
- Cross the spread only when:
  - time to expiry is short,
  - the edge comfortably clears taker fees,
  - and expected missed-opportunity cost exceeds spread cost.
- Use cancel/replace logic aggressively near resolution.
- Model partial fills and non-fills; backtests without this are fiction.

Latency reality:

- This is **not HFT in the traditional sense**.
- You do **not** need microseconds.
- You do need stable **sub-100ms to low-hundreds-ms decision-to-order** plus low jitter if trading in the final minute of `5m` markets.
- For `15m` late-entry, robust software and consistent routing matter more than shaving every last millisecond.

## Infrastructure Recommendation

### Language choice

- **Python is sufficient** for research, feature engineering, model training, inference, and live execution at this horizon.
- Rust/C++ is unnecessary unless profiling shows a real bottleneck.

If anything gets rewritten later, the best candidates are:

- high-frequency local order book aggregation
- feature computation over dense event streams
- latency-critical serialization / routing

Do **not** rewrite the strategy in Rust before proving that Python is the bottleneck rather than the model being weak.

### Hosting

Budget-conscious first recommendation:

- Start with a VPS in **AWS `us-east-1`** or nearby East Coast infrastructure.

Why this is the pragmatic default:

- The strategy is not pure exchange-colo HFT.
- Polymarket is fronted through Cloudflare and does not publish a simple “best region” answer.
- For `15m` selective trading, stable execution and ops simplicity outweigh optimizing solely for Binance’s best region.

Critical caveat:

- Binance latency studies often favor **Tokyo** for direct exchange latency.
- If your final design depends heavily on ultra-fresh Binance microstructure inside the last seconds of a `5m` window, you should benchmark `**us-east-1` vs `ap-northeast-1`** end-to-end and choose based on actual **feature-arrival-to-order-ack** timing, not intuition.

Starting instance class:

- `c7g.large` / `c7i.large` class is enough for live inference and feed handling.
- You do not need GPU.

### Personal computer in São Paulo

Viable for:

- research
- offline backtests
- paper trading
- early integration tests

Not ideal for:

- serious live trading near expiry

Real risks on residential internet:

- route jitter and packet loss
- power failures
- ISP resets
- local clock drift / system sleep
- worse observability and restart automation
- materially worse fill quality on last-minute trades

Blunt recommendation:

- Use São Paulo local machine for research and paper tests.
- Move to VPS **before** any serious capital deployment.

## Bankroll and Risk

### Minimum viable bankroll

For real but small-scale live validation, a sensible floor is:

- **$2,000–$5,000** to learn whether the edge is real
- **$5,000+** is much more practical if you want enough repetitions without oversizing into thin books

Why not lower:

- Small edge plus fees means you need a decent sample of trades before the signal rises above noise.
- Polymarket crypto liquidity can be thin enough that large fraction-of-bankroll bets create their own slippage.
- A $500 bankroll is fine for smoke testing plumbing, not for validating a subtle probabilistic edge.

### Sizing model

Best default:

- **Fractional Kelly on calibrated net edge**, capped by liquidity and drawdown rules.

Recommendation:

- Start at **0.10x to 0.25x Kelly**
- Add hard caps:
  - max exposure per market
  - max concurrent BTC directional exposure
  - max size as fraction of visible book depth
  - daily loss and drawdown halts

### Main ruin risks

- Miscalibrated probabilities
- Edge decay after fees or competition changes
- Correlated losses during one BTC regime shift
- Late-candle non-fills followed by stale chases
- Market-rule mismatch across different Up/Down contracts

Required circuit breakers:

- stop trading after rolling calibration drift exceeds threshold
- stop trading after N consecutive losses beyond expected binomial tail
- halt on data feed desync between Binance and Polymarket
- halt on widened spreads / shallow depth / missing liquidation feed
- reduce size under drawdown and under elevated volatility

## Published Work Worth Studying

Useful categories, not as gospel:

- Short-horizon BTC direction papers using **XGBoost / CNN-LSTM / TCN / logistic baselines**
- Crypto microstructure literature on **order book imbalance, microprice, and order flow toxicity**
- Cross-venue lead-lag and basis/funding regime papers
- Prediction-market microstructure and retail-efficiency papers

Skeptical note:

- Many published BTC papers report inflated accuracy because of weak leakage controls, unrealistic fills, or targets too detached from tradable execution.
- Treat literature as feature inspiration, not as expected live performance.

## Prioritized 6-Step Action Plan

1. **Define the exact target market and label contract**
  - Restrict scope to one product first: likely BTC `15m` Up/Down.
  - Write the exact Polymarket settlement mapping and reject any ambiguous market variants.
2. **Build the research dataset with strict timestamping**
  - Combine your BTC OHLCV history from `[/Users/christian/Documents/www/algo-trading/_data](file:///Users/christian/Documents/www/algo-trading/_data)` with Binance microstructure, derivatives context, and Polymarket market-state snapshots.
  - Store every feature with as-of time and availability time.
3. **Train baseline models before deep models**
  - Train logistic and LightGBM/XGBoost baselines.
  - Calibrate probabilities.
  - Compare feature groups incrementally: OHLCV only, then +microstructure, then +derivatives, then +Polymarket local state.
4. **Build a friction-aware walk-forward simulator**
  - Extend the current backtesting setup in `[/Users/christian/Documents/www/polymarket-bot/docs/backtesting/strategy-integration.md](file:///Users/christian/Documents/www/polymarket-bot/docs/backtesting/strategy-integration.md)` because the current engine assumptions are too optimistic for this strategy.
  - Add fees, slippage bands, missed fills, partial fills, queue delay, and decision latency.
5. **Deploy as separate inference service plus bot adapter**
  - ML service publishes scored opportunities.
  - Existing bot consumes them through a thin strategy adapter and reuses its risk manager, persistence, dashboard, and alerts.
  - Do not let research/training logic block the live trading loop.
6. **Paper trade, then go live with tiny fractional Kelly**
  - Run at least several weeks of paper/live-shadow logging.
  - Promote only if calibration, realized slippage, and net EV remain inside research expectations.
  - Start with `0.10x` Kelly and hard kill-switches.

## Recommendation Summary

If you want the shortest honest answer:

- **Do not start with a fresh repo replacing the bot.**
- **Do not trust OHLCV-only direction models.**
- **Do start with a separate ML service feeding the existing bot.**
- **Do treat this as selective cross-venue dislocation trading, not generic BTC forecasting.**
- **Do require a large probability gap before trading because crypto-market Polymarket fees now matter.**
- **Do expect most apparent edge to disappear until proven otherwise.**

