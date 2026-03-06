I'll help you evaluate this systematic trading strategy. Let me break this down into technical feasibility, signal generation, execution optimization, and risk assessment.
Strategy Feasibility: The Core Edge
Yes, you can likely achieve a minimal edge, but with important caveats:
The Prediction Problem
You're framing this as binary classification: will close > open? This is easier than predicting exact prices. Even 52-55% accuracy can be profitable with proper sizing and fees considered.
Key challenges:
Crypto markets are highly efficient at 5m-1h timeframes
Polymarket's 2% fee structure (1% to enter, 1% to exit) requires ~52% win rate just to break even
The edge from pure OHLC patterns has decayed significantly since 2021
Signal Enhancement Stack
1. Orderbook & Liquidations (High Value)
Binance futures liquidations clusters as leading indicators
Orderbook imbalance (bid/ask depth ratio) at key levels
Funding rate arbitrage signals (perp vs spot)
Open interest changes delta
2. Additional Alpha Sources
On-chain flows: Exchange inflows/outflows (Glassnode/Arkham)
Cross-exchange basis: Spot vs perp premiums
Volatility regime detection: ATR compression/expansion
Session-based patterns: Asian vs EU vs US session behaviors
Social sentiment: Twitter/X velocity spikes (often lagging but useful for extremes)
Options flow: Deribit skew and volume concentration
3. Market Microstructure
Trade flow toxicity (VPIN-like metrics)
Book pressure (level 2 depth changes)
Tick-by-tick momentum ignition patterns
Optimal Execution Timing
Best entry: 30-60 seconds before candle close
Rationale:
Opening: Maximum uncertainty, highest volatility
Mid-candle: You're paying time decay for no information gain
Pre-close: You have 90% of the candle's information, minimal time decay
Latency optimization:
Rust is excellent choice (sub-microsecond parsing)
VPS: AWS Tokyo (for Binance) + US East (for Polymarket) - run both, bridge via dedicated line
WebSocket co-location where possible
Kernel bypass networking (DPDK) if truly serious
Detecting Mispricing
Polymarket-specific tells:
Sharp probability moves without BTC price movement (lagging retail)
Overreaction to wicks vs bodies (retail focuses on wicks)
Weekend/low liquidity periods (wider spreads, slower adjustment)
Correlated market delays (ETH moves, BTC hasn't yet)
Quantitative approach:
plain
Copy
Implied Probability (Polymarket) vs Model Probability
If |Polymarket - Model| > threshold + fees → Trade
Strategy Classification
Name: "Temporal Arbitrage" or "Retail Asymmetry Harvesting"
More formally:
Style: Systematic market making / Statistical arbitrage
Edge Source: Information asymmetry (faster data + superior models vs retail order flow)
Time Horizon: Ultra-short term (5m-4h)
Critical Risks & Realistic Assessment
The Hard Truth:
Edge decay: If this works at scale, it won't work for long
Capacity constraints: Polymarket liquidity is limited (~$100K-500K per crypto market)
Adverse selection: You may be picking off stale prices, but sometimes you're the stale one
Model risk: Overfitting to historical patterns that don't persist
Success metrics to track:
Win rate (target: >54% after fees)
Sharpe ratio (target: >1.5)
Maximum drawdown (<10%)
Fill rates and slippage
Recommended Architecture
plain
Copy
Data Layer:    Binance WS → Rust parser → Time-series DB (TimescaleDB)
               ↓
Signal Layer:  Feature engineering → ONNX model inference (Rust)
               ↓
Execution:     Polymarket API → Order management → Position tracking
               ↓
Risk Layer:    Kelly sizing → Drawdown circuit breakers → P&L attribution
Next Steps
Backtest rigorously: Walk-forward analysis, regime change detection
Paper trade: 3+ months minimum
Start tiny: $100-500 positions to validate fills and latency
Monitor correlation: If BTC drops 10%, does your edge disappear?
Would you like me to dive deeper into any specific component—perhaps the feature engineering pipeline or the risk management framework?
whats the minimum bankroll for this strategy. do you think starting on personal computer in sao paulo, with good internet, is ok for testing?
Edit
Copy
Share
Let me search for current information about Polymarket trading specifics, minimum order sizes, and latency considerations for crypto trading from Brazil.
Search
Polymarket minimum bet size order 2024 2025 Polymarket crypto markets liquidity volume limits trading latency Brazil to US servers ping crypto arbitrage minimum bankroll retail
29 results
Based on the search results, here's a realistic assessment of your setup:
Minimum Bankroll
Absolute minimum to start testing: $2,000-5,000 USDC
Viable operational bankroll: $10,000-25,000
Professional scale: $50,000+
Why this breakdown:
Polymarket mechanics: No explicit minimum order size, but with 2% fees (1% entry/exit), you need meaningful position sizes to overcome fixed costs 
Fee reality check: On a $100 bet with 52% win rate (your theoretical edge), expected value is $104 - $2 fees = $2 profit. Scale matters.
Risk management: With Kelly criterion (even conservative half-Kelly), you need sufficient bankroll to survive variance—crypto prediction markets can have 10+ consecutive losses easily
The math: If you achieve 54% win rate (realistic optimistic target), with 2% fees:
Break-even: 52% win rate
Your edge: 2% expected value per trade
At $500/trade × 20 trades/month = $200 expected monthly profit (before variance)
São Paulo Setup: Viable for Testing, Not for Production
Latency reality:
Brazil → US East Coast: ~180-250ms typical 
Brazil → US West Coast: ~280-350ms 
Polymarket infrastructure: Likely US East (AWS Virginia)
For testing/development: ACCEPTABLE
200ms latency won't kill a 5m/15m prediction strategy
You're predicting candle closes, not scalping sub-second moves
Personal computer + good internet (100mbps+, <1% packet loss) works for validation
For live production: PROBLEMATIC
Polymarket has taker fees up to 1.56% on 15m crypto markets as of January 2026 
Retail flow is fast—you're competing against bots with <50ms latency
Every 100ms delay increases slippage on entry/exit
São Paulo testing protocol:
plain
Copy
Phase 1 (Months 1-2): Local PC, validate signals, paper trade
Phase 2 (Months 3-4): Local PC, micro live trades ($50-100), measure fill quality
Phase 3 (Month 5+): Migrate to US East VPS if edge validates
Critical Fee Structure Update
Polymarket recently changed their fee model in January 2026 :
Taker fees: Now active on 15m crypto markets (your target!)
Fee curve: Peaks at 1.56% at 50% probability (your typical entry zone)
Maker rebates: Available if you provide liquidity
This changes your math significantly:
Old model: 2% total (1% in, 1% out)
New model: Up to 3.12% total if taking liquidity both sides
You MUST use maker orders (post-only) to capture rebates and avoid taker fees
Recommended Testing Architecture (São Paulo)
Hardware:
Wired connection (no WiFi for trading)
UPS backup power (Brazil power grid volatility)
Dual-monitor setup for monitoring
Software stack:
Rust bot running locally
Binance WebSocket via cloud relay (AWS São Paulo region → AWS Virginia)
Polymarket API direct
Redis locally for state management
Risk limits for testing:
plain
Copy
max_position = $500 (5% of $10k bankroll)
max_daily_loss = $1,000 (10% of bankroll)
max_open_positions = 2
pause_after_consecutive_losses = 3
Migration Triggers to VPS
Move to US East VPS when:
Win rate >53% sustained over 100+ live trades
You're getting filled at worse prices than expected (latency arbitrage against you)
Position sizes grow beyond $2,000 (slippage becomes material)
You're trading >10 times per day (latency costs compound)
Expected VPS costs: $200-500/month for proper setup (AWS/Azure Virginia, dedicated cores, premium networking)
Honest Assessment
Starting in São Paulo is smart for testing—you'll validate if the edge exists without burning infrastructure costs. The 200ms latency is actually a feature for strategy validation: if you can profit with 200ms handicap, you'll crush it with <20ms.
However, if you're profitable after 3 months, the VPS migration isn't optional—it's survival. Retail prediction markets are becoming institutionalized quickly, and latency compression is accelerating.
Bottom line: Start with $5,000 in São Paulo. If you can't make money there, you won't make it anywhere. If you do make money, reinvest first $3,000 into proper infrastructure before scaling.