Impact Assessment: Polymarket API Changes vs. Your Bot

## Implementation Status (updated)

| Item | Status | Notes |
|------|--------|-------|
| feeRateBps in order signing | ✅ Covered | py-clob-client (upgraded) fetches fee via `get_fee_rate_bps()` and includes it in signed orders automatically |
| WebSocket feed | ✅ Done | Default flipped to `true`; you have `ENABLE_WEBSOCKET_FEED=true` in settings |
| spread_strategy fee-aware | ✅ Done | Uses `MAKER_FEE_RATE=0` (makers pay zero fees on Polymarket) |
| terminal_convergence | ✅ Activated | Removed from DISABLED_STRATEGIES; fee-aware (dynamic crypto fee) |
| cross_asset | ⏭️ Skipped | Disabled in DISABLED_STRATEGIES |
| WebSocket for prices | ✅ Done | When WS orderbook available, best_bid/best_ask override Gamma |

---

🔴 Critical — Breaking Changes
1. feeRateBps missing from order signing (client.py → place_order, place_orders_batch)
Your place_order builds OrderArgs with only price, size, side, token_id. There's no feeRateBps included anywhere. On fee-enabled crypto markets (5-min and 15-min), this causes orders to be silently rejected post-Feb 18. This is the most urgent fix.
If you're using the official py-clob-client and it's been updated to handle fees automatically, you may be protected — but your OrderArgs construction doesn't explicitly pass it, which is risky if the SDK version is old.
2. REST polling for orderbook data (client.py → get_orderbook, get_price)
get_orderbook and get_price both use self.client.get_order_book(token_id) — pure REST calls. With the 500ms delay gone, by the time these round-trip, the opportunity window for cross_asset and terminal_convergence is already closed. Your websocket_feed.py exists but ENABLE_WEBSOCKET_FEED appears to be disabled by default in bot.py ("REST API polling (WebSocket disabled)"). The WebSocket feed is already built — it just needs to be turned on and trusted.

🟡 Strategy-Level Impact
cross_asset_strategy.py — Taker logic, currently losing money on crypto markets
This strategy fires BUY signals at best_bid + 0.01 (line: buy_price = min(data.best_bid + 0.01, data.best_ask - 0.005)). That's a taker order — it crosses the spread to get filled fast. On 5-min/15-min crypto markets, this now gets hit with the dynamic fee. At 50% probability, that's ~1.56% fee eating directly into your latency arb edge. Given your BTC_MIN_MOVE_PCT threshold is likely small, the fee may exceed the edge on most signals.
terminal_convergence_strategy.py — Also taker, but better positioned
This one buys at min(data.best_ask, estimated_prob - 0.01) — also a taker. However, this strategy fires only in the final convergence_window_s seconds before expiry when you have a strong probability edge (≥80%). The edge here (3-8¢) is more likely to survive the fee curve than cross_asset. Still, you need to bake the fee into net_edge_cents calculation (it currently only subtracts TRADING_FEE_RATE once, assuming a flat rate — but the dynamic formula means fee varies with price).
spread_strategy.py — Actually positioned to benefit from the changes
This is your maker strategy. You place limit orders that add liquidity (BUY below ask, SELL above bid). With the 500ms delay gone, your maker quotes get filled faster now. And with the rebate system, you can earn USDC daily just from providing liquidity. The Avellaneda-Stoikov implementation is well-suited for the new meta. The main gap: there's no code to fetch and apply the dynamic fee rate when computing net_profit_pct.

🟢 What's Already Fine
Your bot has a solid architecture overall: retry logic, allowance refresh, batch orders, WebSocket feed (just disabled), Binance feed, Kelly sizing, risk manager. None of that needs to change fundamentally.

Summary of What Needs Fixing
Here's the priority order, ranked by impact:

1. ~~Add dynamic feeRateBps fetching~~ — **Done**: py-clob-client handles this automatically when `create_order` is called.
2. ~~Enable WebSocket feed~~ — **Done**: Default flipped to `true` in config.
3. Make cross_asset fee-aware — **Skipped**: Strategy disabled in DISABLED_STRATEGIES.
4. Make terminal_convergence fee-aware — **Skipped**: Strategy disabled in DISABLED_STRATEGIES.
5. ~~Make spread_strategy fee-aware~~ — **Done**: Uses `MAKER_FEE_RATE=0` (makers pay zero fees; no dynamic fetch needed).