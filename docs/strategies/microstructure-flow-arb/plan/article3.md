This is a sophisticated idea that sits right at the intersection of several advanced trading concepts. Based on the data I've found, I can confirm that your core intuition is sound: combining machine learning on Bitcoin price action with real-time market microstructure data (order book, liquidations) to trade retail-driven prediction markets like Polymarket is a viable path to seeking a trading edge .

Let's break down your plan piece by piece, integrating the findings from the search results.

🧠 The Core Idea: Is an ML Edge Possible?
Yes, academic research strongly supports the idea that machine learning models can predict short-term Bitcoin price direction with a measurable edge. Your specific goal of predicting whether the close price will be higher or lower than the open is a standard classification problem in this field.

Academic Validation: A recent study from Gazi University compared various models (XGBoost, LSTM, Random Forest, etc.) for predicting the daily price direction of Bitcoin. The results showed that XGBoost achieved the highest directional accuracy at 55.9% . While this might seem only slightly better than a coin flip, in the world of trading, a consistent 55.9% edge is highly significant and can be extremely profitable, especially with leverage and high frequency.

Performance Potential: Another academic paper, "Bitcasting," demonstrated that simulated trading strategies based on ML model predictions significantly outperformed a simple buy-and-hold strategy, achieving substantial cumulative returns . This confirms that a well-trained model can capture profitable short-term movements.

Multi-Timeframe Approach: Your plan to use 5m, 15m, 1h, and 4h candles is a smart way to capture market dynamics across different horizons . Shorter timeframes (5m, 15m) are more sensitive to immediate order flow and noise, while longer ones (1h, 4h) can help identify the prevailing trend and higher-level support/resistance levels.

📈 Boosting Your Edge: Order Books, Liquidations, and More
Your instinct to combine price data with order book and liquidation data from Binance is excellent and represents a move from simple price prediction to understanding market microstructure .

Binance Order Book: This is a high-signal source.

What it tells you: Real-time supply and demand. Key features include the bid-ask spread, order flow imbalance (the net difference between buying and selling pressure at the top of the book), and the market depth (how many limit orders are stacked at different price levels) .

The Edge: Significant buying pressure (a thick bid side) just before a candle closes could be a strong indicator that the price will close higher. Academic research confirms that these engineered order book features have stable predictive importance across different cryptocurrencies .

Binance Liquidations: This is a powerful sentiment and volatility indicator.

What it tells you: When leveraged traders are forced to close their positions. A cascade of long liquidations can drive the price down rapidly, and vice-versa .

The Edge: Crucially, data suggests that retail stress and the potential for liquidations build before the actual market crash. One report noted that liquidation safety checks spiked 5× above baseline just hours before a $1.29 billion short wipeout . Monitoring live liquidation data from Binance can give you a lead indicator of impending volatility that hasn't yet fully printed on a standard candlestick chart.

Other Potential Signals:

Funding Rates: From perpetual futures on exchanges like Binance. Extremely high positive funding rates (longs paying shorts) can signal a crowded, overheated long trade that is ripe for a reversal.

Sentiment Analysis: This is a more advanced layer. AI-driven bots on Polymarket are known to monitor real-time sentiment on platforms like X (formerly Twitter) and Telegram to anticipate market shifts before they are priced in .

⏱️ Strategy & Execution: Timing and Mispricing
This is where the theory meets the practical reality of Polymarket.

When to Open Orders: The "best" time depends on your model's strengths.

At Candle Opening: You would need your model to predict the entire candle's direction based on data before the open. This is the most predictive challenge.

Mid-Candle: This allows you to react to the first half's price action, order book shifts, and any early liquidations. Your model could be designed to update its probability mid-candle.

Almost Closing: This is the lowest risk but also likely the lowest reward, as the edge is minimal. For a high-frequency, high-certainty strategy, this might be your window.

Detecting Mispricing on Polymarket: This is the key to your strategy. Polymarket is indeed very retail-driven, which creates opportunities .

The Mispricing: The price on Polymarket for a "BTC up in the next 5 minutes" contract is not a reflection of the true, mathematically derived probability. It's the crowd's sentiment. If your ML model, fed with live Binance microstructure data, calculates a 62% probability of Bitcoin closing up, but the Polymarket contract is trading at 55¢ (implying a 55% probability), that's your mispricing. You buy the undervalued contract.

The Arbitrage: More advanced strategies can also look for "complete set" arbitrage, where the price of a YES contract and a NO contract for the same event sum to less than 100¢, allowing for a theoretically risk-free profit .

⚙️ The Tech Stack: Latency is King
You are right on the money with your infrastructure ideas.

Latency & Execution: In this game, milliseconds matter. Bots on Polymarket can execute trades in milliseconds, front-running slower participants and capitalizing on short-lived inefficiencies . A human simply cannot compete.

Rust + VPS: This is an excellent choice.

Rust: Its performance and memory safety make it a top-tier language for low-latency systems. A project like Testudo, a high-frequency trading overlay, is built in Rust precisely to achieve sub-10ms internal latency for decision-making .

VPS (Virtual Private Server): You must host your bot on a VPS with a fast network connection, ideally geographically close to both Binance's servers (to receive market data quickly) and the Polymarket/blockchain infrastructure (to submit your orders quickly).

Data Pipeline: You'll need a robust system to ingest and store the multi-timeframe candle data, the raw Binance WebSocket feeds (order book, trades), and liquidation data for backtesting and model retraining .

🏆 What to Call This Strategy?
Based on the components, here are a few fitting names that capture its essence:

The Polymarket Pulse: Simple, catchy, and implies you're taking the market's pulse with real-time data.

Microstructure Momentum Arbitrage: A more technical and descriptive name for a portfolio or whitepaper. It highlights the use of microstructure data to capture momentum mispricing.

Cross-Exchange Signal Arbitrage: Emphasizes that you are generating a signal on one exchange (Binance) and trading on another (Polymarket).

🗺️ Your Roadmap to Implementation
Turning this plan into a profitable reality is a significant undertaking. Here’s a suggested phased approach:

Phase 1: Data Pipeline & Backtesting (The Foundation)

Set up a Rust or Python service to connect to Binance WebSockets and record order book snapshots, trade data, and liquidations. Store this in a time-series database .

Download historical 5m, 15m, 1h, 4h BTC data.

Engineer your features: technical indicators from OHLC data, order book imbalance, liquidation volumes, etc. .

Phase 2: Model Development (The Brain)

Start with a robust model like XGBoost, as it has shown superior performance in academic studies for this exact task .

Train it to predict the next candle's direction (up/down) on historical data. Use rigorous time-series cross-validation to avoid look-ahead bias.

Aim for that >55% accuracy threshold as a sign you have a viable edge .

Phase 3: Paper Trading & Refinement (The Sandbox)

Connect your model to live Binance data to generate real-time signals, but do not send orders to Polymarket yet.

Simulate trades on Polymarket by comparing your signal to the live market price. This is crucial for validating your strategy in the current market without risking capital.

Phase 4: Live Trading with Tiny Risk (The Launch)

Start with a minuscule amount of capital.

Implement your Rust bot to listen for your model's signal and automatically place orders on Polymarket via their API .

Focus obsessively on monitoring and risk management.

In summary, your idea is not only possible but is actively being pursued by sophisticated traders. The key differentiators will be the quality of your feature engineering, the robustness of your backtesting, and the speed of your execution. Good luck

Would you like a deeper dive into any of these phases, such as the specific technical indicators used in the academic studies or the architecture for a low-latency Rust bot?

Esta resposta é gerada por AI, apenas para referência.
whats the minimum bankroll for this strategy. do you think starting on personal computer in sao paulo, with good internet, is ok for testing?
Ler 8 páginas da web
This is a very practical next step in your planning. Let me address both your questions about bankroll and testing infrastructure, combining what I've found with some strategic advice.

💰 Minimum Bankroll: Start Small, Think Big
Based on the search results, you can start testing with surprisingly little capital. Here's what the data suggests:

Absolute Minimum for Testing: $50-$100 USDC . This is enough to fund a wallet and start paper trading or very small live tests without significant risk.

Realistic Starting Point: $250 is cited as a recommended starting capital in open-source bot configurations . This gives you enough buffer to cover minimum trade requirements and gas fees.

Minimum Trade Size: You must ensure each trade is at least $1.50 to guarantee exit capability in Polymarket's markets .

Critical Note on New Fee Structure: Polymarket recently introduced dynamic taker fees for 5m and 15m crypto markets (as of January 2026). In the 50% probability range, fees can reach up to 1.56% . This means:

Your edge needs to exceed ~1.56% just to break even if you're taking orders

Maker strategies (providing liquidity) pay zero fees and earn rebates - this is now the recommended approach 

💻 Testing from São Paulo: Yes, But Know the Limits
Starting on your personal computer in São Paulo is perfectly fine for Phase 1 testing, but you need to understand exactly what you're testing and what the limitations are.

What You Can Test Successfully from Home:
Model Development & Backtesting: Your ML model training on historical BTC data works anywhere. São Paulo internet is irrelevant here.

Strategy Logic Validation: Testing if your signals make sense.

Paper Trading (DRY_RUN mode): Most open-source bots include a "dry run" mode where they simulate trades without spending real money . This is ideal for your initial testing phase.

API Integration: Connecting to Polymarket's APIs and verifying your code works.

The Latency Reality from Home:
Here's the honest assessment: You cannot execute profitable live trades on 5-minute markets from a home connection in São Paulo once you move beyond testing.

Home Latency: Typical home internet connections experience 150ms or more latency 

Competition: Professional bots operate at 1-5ms latency from VPS servers located near Polymarket's infrastructure 

The Gap: A 150ms delay means you're making decisions on data that's already 150ms old. In fast-moving 5-minute BTC markets, that's enough to consistently lose to faster competitors 

A developer testing a similar strategy found that when their bot operated with higher latency, what should have been profitable trades turned into losses due to execution delays .

🗺️ Your Phased Approach for São Paulo
Here's how to structure your testing to make the most of your location:

Phase	Location	Bankroll	Goal
Phase 1: Model Development	Your PC	$0	Train ML model on historical data, achieve >55% directional accuracy
Phase 2: Paper Trading	Your PC	$0	Validate strategy logic with DRY_RUN mode, no real money 
Phase 3: Tiny Live Tests	Your PC	$50-$100	Test the entire execution pipeline with minimal risk 
Phase 4: Production	VPS (US/EU)	$250+	Move to low-latency hosting for actual profitability 
💡 Critical Advice from Recent Rule Changes
The Polymarket landscape shifted dramatically in February 2026. Here's what you must incorporate:

500ms Delay Removed: Taker orders now execute instantly - no more safety buffer 

Fee-Aware Signatures: Your orders must include feeRateBps in the signature payload, matching the current market fee 

Consider Maker Strategy: For 5-minute BTC markets, consider being a maker (providing liquidity) rather than a taker. You can target the T-10 second window where BTC direction is ~85% determined but Polymarket odds haven't fully adjusted 

📊 Summary: Yes, Start in São Paulo
Start on your personal computer in São Paulo. Use this time to:

Build and validate your ML model

Test your strategy logic in paper trading mode

Learn the Polymarket API and new fee structure

Perfect your order signing with fee awareness

Plan to migrate to a VPS once your strategy proves profitable in paper trading. A premium trading VPS can get you down to 1-5ms latency . The cost ($50-$100/month) is a necessary investment for the speed required in 5-minute markets.

Would you like specific recommendations on VPS providers with good latency from São Paulo, or help setting up the fee-aware order signing required by Polymarket's new rules?