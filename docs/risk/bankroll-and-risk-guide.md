# Bankroll and Risk Guide

## Purpose

This note captures practical recommendations for running `ml_directional` alone or alongside other strategies, with emphasis on bankroll sizing, risk controls, Kelly sizing, adaptive risk, and the native engine.

## Recommended Strategy Pairings

### Best companion for `ml_directional`

- `combinatorial_arb`
  - Best diversification candidate
  - Edge source is different from BTC directional forecasting
  - Strong first add-on

### BTC-focused companion

- `terminal_convergence`
  - Good if you want more BTC/event exposure
  - More correlated with `ml_directional`

### More advanced companion

- `orderbook_imbalance`
  - Interesting for microstructure-driven entries
  - More correlated with BTC flow and execution conditions
  - Better suited to larger bankrolls and better fill quality

### Lower-priority companions

- `vpin`
- `sentiment`
- `wallet_copy`

These may be worth revisiting later, but they are not the first additions I would choose.

### Strategies to keep off by default

Unless there is a strong reason to enable them, keep these off:

- `spread`
- `arbitrage`
- `favorite_longshot`
- `cross_platform_arbitrage`

## Recommended Strategy Combos

### Small complexity, highest conviction

- `ml_directional`
- `combinatorial_arb`

### BTC-focused combo

- `ml_directional`
- `terminal_convergence`

This is viable, but risk is more clustered because both strategies lean into BTC/event dynamics.

### Advanced but still sane

- `ml_directional`
- `combinatorial_arb`
- `orderbook_imbalance`

This is about as far as I would go before demanding stronger bankroll and tighter risk governance.

## Bankroll Recommendations

These are practical bankroll ranges, not bare software minimums.

### `ml_directional` only

- Bare-minimum test: `$75-$150`
- Usable: `$150-$300`
- Comfortable: `$300-$600`

Why:

- short-term markets lock capital until fill, cancel, or resolution
- Polymarket minimum share constraints matter
- repeated maker attempts can tie up small balances quickly

### `ml_directional + 1 strategy`

- Minimum: `$250-$500`
- Comfortable: `$500-$1,000`

If the second strategy is `combinatorial_arb`, the lower end is more realistic.
If it is `terminal_convergence` or `orderbook_imbalance`, I would want more room.

### Curated 3-strategy basket

- Minimum: `$750-$1,500`
- Comfortable: `$1,500-$3,000`

### `all` strategies

I do not recommend running `all` below `$3,000-$5,000`.

Even then, I would still prefer a curated basket rather than full activation.

Why:

- many strategies are not independent
- exposure can cluster around BTC and short-term market structure
- capital gets fragmented across open orders and unresolved markets
- attribution becomes harder to trust

## Risk Posture Recommendations

## Small bankroll

Recommended:

- `ADAPTIVE_RISK_ENABLED=true`
- `KELLY_FRACTION_MODE=quarter`

Suggested adaptive caps:

- `ADAPTIVE_MAX_SINGLE_TRADE_PCT=0.02` to `0.03`
- `ADAPTIVE_MAX_POSITION_PCT=0.08` to `0.10`
- `ADAPTIVE_MAX_EXPOSURE_PCT=0.20` to `0.25`
- `ADAPTIVE_DAILY_LOSS_LIMIT_PCT=0.03` to `0.05`

## Medium bankroll

Reasonable posture:

- quarter Kelly
- single trade around `3%-4%`
- position around `10%-12%`
- total exposure around `25%-30%`

## Multi-strategy

The main mistake is treating strategies as if they were independent. They are not.

For multi-strategy live operation:

- keep `KELLY_FRACTION_MODE=quarter`
- lower total exposure more than you initially expect
- cap concurrent open risk harder than per-strategy intuition suggests

## Kelly Guidance

Kelly is useful because it ties sizing to estimated edge.

However, live probabilities are estimates, not certainties. Because of that:

- `full` Kelly is too aggressive for this system
- `quarter` Kelly is the preferred live default
- `half` Kelly is only reasonable once bankroll is larger and live calibration has been validated

## Adaptive Risk Guidance

If the goal is long-term survival and compounding, adaptive risk should be enabled.

It helps because:

- live balances change quickly
- unresolved markets tie up capital
- multiple strategies can stack exposure unexpectedly

For a bot that is intended to thrive rather than simply fire trades, adaptive risk should be part of the standard setup.

## Native Engine Guidance

`NATIVE_ENGINE_ENABLED` is not a bankroll threshold switch. It mainly affects:

- inventory-aware Kelly sizing
- quoting and clipping math
- performance and precision of native analytics

### For `ml_directional` only

If the bot is mainly taking a few directional entries, the native engine is nice to have but not essential.

### When it becomes more worthwhile

It matters more when there are:

- multiple strategies
- multiple simultaneous positions
- inventory buildup across markets
- tighter quoting and sizing requirements

### Practical rule of thumb

- Under `$300`: native engine is not important
- `$500-$1,000+`: worth enabling and testing
- `$1,500+` with multi-strategy operation: strongly preferred if stable

So if `NATIVE_ENGINE_ENABLED=false`, that is acceptable for small-bankroll single-strategy operation.

## Recommended Path

### Best current path

Run:

- `ml_directional`
- later add `combinatorial_arb`

Keep:

- adaptive risk on
- quarter Kelly
- tighter exposure caps if bankroll is small

### Avoid for now

- `all`
- too many BTC-correlated strategies together
- loosening risk controls just because the bot has started trading smoothly

## Simple Bankroll Matrix

- `$100-$300`
  - `ml_directional` only

- `$300-$700`
  - `ml_directional + combinatorial_arb`

- `$700-$1,500`
  - curated `2-3` strategy basket

- `$3,000+`
  - consider broader multi-strategy operation

## Bottom Line

- Best companion: `combinatorial_arb`
- Best default risk posture: adaptive risk on plus quarter Kelly
- Native engine: useful, but not the gating factor for small bankrolls
- `all` strategies should wait for materially larger capital
