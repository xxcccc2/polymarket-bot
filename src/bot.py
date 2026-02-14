"""
Polymarket Trading Bot - Main Orchestrator

Ties all components together:
- Client connection
- WebSocket feed for real-time data
- Strategy execution
- Order management
- Risk management
"""

import sys
import time
import signal
import threading
from typing import Dict, List, Optional, Type
from datetime import datetime

from .logging_utils import cprint

from .config import (
    SCAN_INTERVAL_SECONDS,
    BALANCE_REFRESH_SECONDS,
    MARKET_REFRESH_SECONDS,
    ENABLE_WEBSOCKET_FEED,
    ENABLE_BTC_5MIN,
    PAPER_TRADING,
    CRYPTO_MARKET_KEYWORDS,
    ONLY_CRYPTO_MARKETS,
    BTC_5MIN_KEYWORDS,
    DISABLED_STRATEGIES,
    ADAPTIVE_RISK_ENABLED,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_ALERTS_ENABLED,
    MIN_VOLUME_USD,
    print_config,
    validate_config,
)
from .client import PolymarketClient
from .websocket_feed import WebSocketFeed
from .feeds.binance_ws import BinanceFeed
from .order_manager import OrderManager
from .risk_manager import RiskManager, RiskLevel
from .persistence import SqliteStore
from .analytics.strategy_tracker import StrategyTracker
from .alerts.telegram import TelegramAlerter
from .strategies import get_strategy, list_strategies, BaseStrategy
from .strategies.base_strategy import MarketData


class PolymarketBot:
    """
    Main trading bot orchestrator.
    
    Usage:
        bot = PolymarketBot(strategy="spread")
        bot.start()
        
        # Or run all strategies:
        bot = PolymarketBot(strategy="all")
        bot.start()
    """
    
    def __init__(
        self,
        strategy: str = "btc_5min",
        strategy_config: Optional[Dict] = None
    ):
        """
        Initialize the trading bot.
        
        Args:
            strategy: Strategy name (see strategies/) or "all" for multi-strategy
            strategy_config: Optional strategy configuration overrides
        """
        cprint("\n" + "="*60, "cyan")
        cprint("🤖 Polymarket Trading Bot", "cyan", attrs=["bold"])
        cprint("="*60, "cyan")
        
        # Validate configuration
        errors = validate_config()
        if errors:
            for error in errors:
                cprint(f"❌ {error}", "red")
            raise ValueError("Configuration errors - check .env file")
        
        # Print config
        print_config()
        
        # Initialize components
        cprint("🔧 Initializing components...", "cyan")

        self.client = PolymarketClient()
        self.feed = WebSocketFeed()
        self.store = SqliteStore()
        self.order_manager: Optional[OrderManager] = None
        self.risk_manager = RiskManager(
            store=self.store,
            adaptive_config={"enabled": ADAPTIVE_RISK_ENABLED},
        )
        self.strategy_tracker = StrategyTracker()
        
        # Telegram alerter
        self.telegram = TelegramAlerter(
            bot_token=TELEGRAM_BOT_TOKEN,
            chat_id=TELEGRAM_CHAT_ID,
            enabled=TELEGRAM_ALERTS_ENABLED,
        )
        
        # Wire risk level changes to Telegram
        self.risk_manager.on_risk_change(self._on_risk_level_change)
        
        # Track previous throttle to avoid spamming alerts
        self._last_alerted_throttle: float = 1.0
        
        # Binance feed for cross-asset strategies
        self.binance_feed: Optional[BinanceFeed] = None
        if ENABLE_BTC_5MIN:
            self.binance_feed = BinanceFeed()
            cprint("   ₿ Binance feed initialized", "cyan")
        
        # Strategy config — inject binance_feed into strategies that need it
        merged_config = dict(strategy_config or {})
        if self.binance_feed:
            merged_config["binance_feed"] = self.binance_feed
        
        # Multi-strategy mode
        self.multi_strategy_mode = (strategy == "all")
        self.strategies: List[BaseStrategy] = []
        
        # BTC 5-min strategies that need Binance feed
        _BTC_5MIN_STRATEGIES = ["cross_asset", "terminal_convergence", "orderbook_imbalance"]
        # Phase 5 advanced strategies (work on all markets)
        _ADVANCED_STRATEGIES = ["vpin", "sentiment", "combinatorial_arb"]
        # All original strategies
        _CLASSIC_STRATEGIES = ["spread", "arbitrage", "stink_bid", "favorite_longshot", "late_money"]
        
        # Log disabled strategies
        if DISABLED_STRATEGIES:
            cprint(f"   🚫 Disabled: {', '.join(DISABLED_STRATEGIES)}", "yellow")
        
        def _load_strategy(name: str) -> Optional[BaseStrategy]:
            """Load a strategy, skipping if disabled."""
            if name in DISABLED_STRATEGIES:
                return None
            return get_strategy(name, **merged_config)
        
        if self.multi_strategy_mode:
            cprint("📈 Loading ALL strategies (multi-strategy mode)", "cyan", attrs=["bold"])
            strat_names = list(_CLASSIC_STRATEGIES)
            if ENABLE_BTC_5MIN and self.binance_feed:
                strat_names.extend(_BTC_5MIN_STRATEGIES)
            strat_names.extend(_ADVANCED_STRATEGIES)
            for name in strat_names:
                strat = _load_strategy(name)
                if strat:
                    self.strategies.append(strat)
                    cprint(f"   ✅ {name}: {strat.description}", "white")
            self.strategy = self.strategies[0] if self.strategies else None
        elif strategy == "btc_5min":
            # Special mode: run only the 5-min BTC strategies
            cprint("📈 Loading BTC 5-min strategies", "cyan", attrs=["bold"])
            for name in _BTC_5MIN_STRATEGIES:
                strat = _load_strategy(name)
                if strat:
                    self.strategies.append(strat)
                    cprint(f"   ✅ {name}: {strat.description}", "white")
            # Also load passive/advanced strategies that pair well
            for extra in ["stink_bid", "vpin", "sentiment", "combinatorial_arb"]:
                strat = _load_strategy(extra)
                if strat:
                    self.strategies.append(strat)
                    tag = "(passive)" if extra == "stink_bid" else "(advanced)"
                    cprint(f"   ✅ {extra}: {strat.description} {tag}", "white")
            self.strategy = self.strategies[0] if self.strategies else None
        else:
            cprint(f"📈 Loading strategy: {strategy}", "cyan")
            self.strategy: BaseStrategy = get_strategy(strategy, **merged_config)
            self.strategies = [self.strategy]
            cprint(f"   {self.strategy.description}", "white")
        
        # State
        self.is_running = False
        self.markets: Dict[str, Dict] = {}  # condition_id -> market data
        self.market_data_cache: Dict[str, MarketData] = {}
        
        # Threads
        self._main_thread: Optional[threading.Thread] = None
        self._market_fetch_thread: Optional[threading.Thread] = None
        
        # Register signal handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        
        cprint("✅ Bot initialized!\n", "green")
    
    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully."""
        cprint("\n\n⚠️  Shutdown signal received...", "yellow")
        self.stop()
    
    def start(self):
        """Start the trading bot."""
        if self.is_running:
            cprint("⚠️  Bot already running", "yellow")
            return
        
        cprint("\n" + "="*60, "green")
        cprint("🚀 Starting Polymarket Bot", "green", attrs=["bold"])
        cprint("="*60, "green")
        
        # Connect to Polymarket
        if not self.client.connect():
            cprint("❌ Failed to connect to Polymarket", "red")
            return
        
        # Initialize order manager with connected client
        self.order_manager = OrderManager(self.client, store=self.store)
        self.order_manager.start_cleanup_loop()
        
        # Register order callbacks
        self.order_manager.on_fill(self._on_order_fill)
        self.order_manager.on_cancel(self._on_order_cancel)
        
        # Set up WebSocket callbacks
        self.feed.on_orderbook(self._on_orderbook_update)
        self.feed.on_connect(self._on_feed_connect)
        self.feed.on_disconnect(self._on_feed_disconnect)
        
        # Fetch initial markets
        cprint("\n📡 Fetching markets...", "cyan")
        self._fetch_markets()

        # Refresh balance before starting
        self._refresh_balance(force_log=True)
        
        # WebSocket disabled - using REST polling only (more reliable)
        if ENABLE_WEBSOCKET_FEED:
            self.feed.start(blocking=False)
            cprint("📡 WebSocket feed enabled", "cyan")
        else:
            cprint("📡 Using REST API polling (WebSocket disabled)", "cyan")
        
        # Start Binance feed for cross-asset strategies
        if self.binance_feed:
            self.binance_feed.start()
        
        # Start strategies
        for strat in self.strategies:
            strat.on_start()
        
        self.is_running = True
        
        # Start main trading loop
        strat_names = [s.name for s in self.strategies]
        cprint("\n✨ Bot is running!", "green", attrs=["bold"])
        if self.multi_strategy_mode or len(self.strategies) > 1:
            cprint(f"   Strategies: {', '.join(strat_names)}", "cyan", attrs=["bold"])
        elif self.strategy:
            cprint(f"   Strategy: {self.strategy.name}", "white")
        cprint(f"   Markets: {len(self.markets)} crypto markets", "white")
        cprint(f"   Paper Trading: {'✅ ON' if PAPER_TRADING else '❌ OFF (LIVE!)'}", 
               "green" if PAPER_TRADING else "red")
        cprint(f"   Scan Interval: {SCAN_INTERVAL_SECONDS}s", "white")
        cprint(f"   Mode: {'WebSocket + REST' if ENABLE_WEBSOCKET_FEED else 'REST API polling'}", "white")
        if self.binance_feed:
            cprint(f"   Binance Feed: ✅ streaming BTC/USDT", "cyan")
        if self.risk_manager.adaptive_enabled:
            cprint(f"   Adaptive Risk: ✅ bankroll-proportional", "cyan")
        cprint("\n   Press Ctrl+C to stop\n", "yellow")
        
        # Telegram startup alert
        balance = self.risk_manager.current_balance
        self.telegram.alert_startup(
            balance=balance,
            strategies=strat_names,
            mode="PAPER" if PAPER_TRADING else "LIVE",
        )
        
        # Run main loop
        self._main_loop()
    
    def stop(self):
        """Stop the trading bot gracefully."""
        if not self.is_running:
            return
        
        cprint("\n🛑 Stopping bot...", "yellow")
        self.is_running = False
        
        # Stop all strategies
        for strat in self.strategies:
            try:
                strat.on_stop()
            except Exception:
                pass
        
        # Cancel all open orders on shutdown (safety)
        if self.order_manager:
            active = self.order_manager.get_active_orders()
            if active:
                cprint(f"   🧹 Cancelling {len(active)} active orders...", "yellow")
            cancelled = self.order_manager.cancel_all_orders("Bot shutdown")
            if cancelled:
                cprint(f"   ✅ Cancelled {cancelled} orders", "yellow")
            self.order_manager.stop_cleanup_loop()
        
        # Stop Binance feed
        if self.binance_feed:
            try:
                self.binance_feed.stop()
            except Exception:
                pass
        
        # Stop WebSocket feed (if it was running)
        try:
            self.feed.stop()
        except Exception:
            pass
        
        # Print final stats
        self._print_final_stats()
        self.strategy_tracker.print_scorecard()
        
        # Telegram shutdown alert (includes session summary)
        self.telegram.alert_shutdown("User requested")
        self.telegram.stop()
        
        cprint("👋 Bot stopped\n", "green")
    
    def _main_loop(self):
        """Main trading loop."""
        last_scan = 0
        last_fill_check = 0
        last_balance_check = 0
        last_market_refresh = 0
        fill_check_interval = 10  # Check for fills every 10 seconds
        
        while self.is_running:
            try:
                now = time.time()
                
                # Check if it's time to scan
                if now - last_scan >= SCAN_INTERVAL_SECONDS:
                    self._scan_and_trade()
                    last_scan = now
                
                # Check for fills periodically
                if now - last_fill_check >= fill_check_interval:
                    self._check_for_fills()
                    last_fill_check = now

                # Refresh balance periodically
                if now - last_balance_check >= BALANCE_REFRESH_SECONDS:
                    self._refresh_balance()
                    last_balance_check = now

                # Refresh market universe periodically
                if now - last_market_refresh >= MARKET_REFRESH_SECONDS:
                    self._fetch_markets(is_refresh=True)
                    last_market_refresh = now
                
                # Small sleep to prevent CPU spinning
                time.sleep(0.1)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                cprint(f"❌ Main loop error: {e}", "red")
                time.sleep(1)
        
        self.stop()
    
    def _scan_and_trade(self):
        """Scan markets and execute strategy (or all strategies in multi-mode)."""
        # Check if trading is allowed
        can_trade, reason = self.risk_manager.can_trade()
        if not can_trade:
            cprint(f"⏸️  Trading paused: {reason}", "yellow")
            return
        
        # Build market data for strategy
        market_data_list = self._build_market_data()
        
        if not market_data_list:
            cprint(f"✅ Loaded {len(self.markets)} markets", "green")
            cprint(f"📊 Scanning {len(self.markets)} markets... (no price data yet)", "white")
            return
        
        cprint(f"\n📊 Analyzed {len(market_data_list)} tokens @ {datetime.now().strftime('%H:%M:%S')}", "cyan")
        
        # Show adaptive risk state
        if self.risk_manager.adaptive_enabled:
            throttle = self.risk_manager.get_throttle_factor()
            if throttle < 1.0:
                cprint(f"   ⚠️  Throttle: {throttle*100:.0f}% (drawdown protection active)", "yellow")
        
        # Run each strategy
        total_signals = 0
        for strategy in self.strategies:
            strat_name = strategy.name.upper()
            
            # Check strategy health before running
            healthy, health_reason = self.strategy_tracker.is_strategy_healthy(strategy.name)
            if not healthy:
                cprint(f"   [{strat_name}] ⏸️  Disabled: {health_reason}", "yellow")
                continue
            
            # Run strategy analysis
            signals = strategy.analyze(market_data_list)
            
            if signals:
                total_signals += len(signals)
                cprint(f"🎯 [{strat_name}] {len(signals)} signal(s)!", "green", attrs=["bold"])
                
                # Execute signals
                for signal in signals:
                    # Use adaptive order sizing
                    trade_value = signal.size * signal.price
                    adaptive_size = self.risk_manager.get_adaptive_order_size(trade_value)
                    
                    # Check risk limits with adaptive-sized trade
                    can_open, reason = self.risk_manager.can_open_position(
                        signal.token_id,
                        adaptive_size,
                        signal.price
                    )
                    
                    if not can_open:
                        cprint(f"   ⚠️  Risk blocked: {reason}", "yellow")
                        continue
                    
                    # Execute via strategy
                    results = strategy.execute([signal], self.order_manager)
                    
                    # Record trade in both risk manager and analytics tracker
                    for result in results:
                        if result.get("success"):
                            fee_est = adaptive_size * 0.01  # ~1% fee
                            self.risk_manager.record_trade(adaptive_size, fee_est)
                            self.strategy_tracker.record_trade(
                                strategy=strategy.name,
                                token_id=signal.token_id,
                                market_slug=signal.market_slug,
                                side=signal.side,
                                price=signal.price,
                                size=signal.size,
                                pnl=0.0,  # P&L tracked on exit
                                fees=fee_est,
                                is_exit=False,
                            )
            else:
                cprint(f"   [{strat_name}] No opportunities", "white")
        
        if total_signals == 0:
            cprint(f"   Waiting for opportunities...", "white")
    
    def _build_market_data(self) -> List[MarketData]:
        """Build MarketData objects from cached data."""
        data_list = []
        
        for condition_id, market in self.markets.items():
            try:
                # Gamma API includes bestBid/bestAsk directly in market data!
                best_bid = float(market.get("bestBid", 0) or 0)
                best_ask = float(market.get("bestAsk", 1) or 1)
                
                # Skip if no valid prices
                if best_bid <= 0 or best_ask <= 0 or best_ask >= 1:
                    continue
                
                mid = (best_bid + best_ask) / 2
                spread = best_ask - best_bid
                
                # Get volume - try different field names
                volume = float(
                    market.get("volume24hrClob", 0) or 
                    market.get("volumeClob", 0) or 
                    market.get("volume", 0) or 0
                )
                
                # Get the actual token ID for YES outcome
                # The CLOB API needs the token_id, not the conditionId!
                # clobTokenIds format: ["YES_TOKEN_ID", "NO_TOKEN_ID"]
                clob_tokens = market.get("clobTokenIds", [])
                
                if not clob_tokens or len(clob_tokens) < 1:
                    continue
                
                # Parse clobTokenIds - it might be a string or list
                if isinstance(clob_tokens, str):
                    import json
                    try:
                        clob_tokens = json.loads(clob_tokens)
                    except:
                        continue
                
                # First token is YES, second is NO
                yes_token_id = clob_tokens[0] if len(clob_tokens) > 0 else None
                no_token_id = clob_tokens[1] if len(clob_tokens) > 1 else None
                
                if not yes_token_id:
                    continue
                
                # Create market data for YES outcome
                data = MarketData(
                    token_id=yes_token_id,  # Use actual token ID for CLOB API
                    condition_id=condition_id,
                    market_slug=market.get("slug", ""),
                    question=market.get("question", ""),
                    outcome="YES",  # Primary outcome
                    best_bid=best_bid,
                    best_ask=best_ask,
                    mid_price=mid,
                    spread=spread,
                    volume_24h=volume,
                    liquidity=float(market.get("liquidityClob", 0) or 0),
                    last_price=float(market.get("lastTradePrice", mid) or mid)
                )
                
                data_list.append(data)
                    
            except Exception as e:
                continue
        
        return data_list
    
    def _fetch_markets(self, is_refresh: bool = False):
        """Fetch and filter available markets."""
        try:
            result = self.client.get_markets()
            
            if "error" in result:
                cprint(f"❌ Failed to fetch markets: {result['error']}", "red")
                return
            
            markets = result if isinstance(result, list) else result.get("data", [])
            
            # Filter markets based on settings
            filtered = []
            for market in markets:
                question = market.get("question", "").lower()
                
                # Check if crypto-related (if filter enabled)
                if ONLY_CRYPTO_MARKETS:
                    is_crypto = any(kw in question for kw in CRYPTO_MARKET_KEYWORDS)
                    if not is_crypto:
                        continue
                
                # Check volume
                volume = float(market.get("volume24hr", 0))
                if volume < MIN_VOLUME_USD:
                    continue
                
                # Check not closed
                if market.get("closed"):
                    continue
                
                filtered.append(market)
            
            refreshed_markets: Dict[str, Dict] = {}
            for market in filtered:
                condition_id = market.get("conditionId") or market.get("condition_id") or market.get("id")
                if condition_id:
                    refreshed_markets[condition_id] = market

            if is_refresh:
                self._refresh_feed_subscriptions(refreshed_markets)
                self.markets = refreshed_markets
            else:
                self.markets.update(refreshed_markets)
            
            market_type = "crypto" if ONLY_CRYPTO_MARKETS else "all"
            refresh_label = "Refreshed" if is_refresh else "Found"
            cprint(f"✅ {refresh_label} {len(self.markets)} tradeable markets ({market_type})", "green")
            
            # Subscribe to WebSocket for these markets
            token_ids = []
            for market in self.markets.values():
                for token in market.get("tokens", []):
                    if token.get("token_id"):
                        token_ids.append(token["token_id"])
            
            if token_ids:
                self.feed.subscribe(token_ids[:50])  # Limit subscriptions
                
        except Exception as e:
            cprint(f"❌ Error fetching markets: {e}", "red")

    def _refresh_feed_subscriptions(self, refreshed_markets: Dict[str, Dict]) -> None:
        """Refresh WebSocket subscriptions for updated markets."""
        if not ENABLE_WEBSOCKET_FEED:
            return

        try:
            current_tokens = {
                token.get("token_id")
                for market in self.markets.values()
                for token in market.get("tokens", [])
                if token.get("token_id")
            }
            new_tokens = {
                token.get("token_id")
                for market in refreshed_markets.values()
                for token in market.get("tokens", [])
                if token.get("token_id")
            }

            tokens_to_unsubscribe = list(current_tokens - new_tokens)
            tokens_to_subscribe = list(new_tokens - current_tokens)

            if tokens_to_unsubscribe:
                self.feed.unsubscribe(tokens_to_unsubscribe[:50])
            if tokens_to_subscribe:
                self.feed.subscribe(tokens_to_subscribe[:50])
        except Exception as e:
            cprint(f"❌ WebSocket subscription refresh failed: {e}", "red")

    def _refresh_balance(self, force_log: bool = False) -> None:
        """Fetch latest balance and update risk manager."""
        try:
            balance = self.client.get_balance()
            if balance is None:
                if force_log:
                    cprint("⚠️  Balance unavailable from API", "yellow")
                return

            self.risk_manager.set_balance(balance)
            if force_log:
                label = "PAPER" if PAPER_TRADING else "LIVE"
                cprint(f"💰 {label} balance: ${balance:.2f}", "white")
        except Exception as e:
            if force_log:
                cprint(f"⚠️  Balance refresh failed: {e}", "yellow")
    
    def _on_orderbook_update(self, update):
        """Handle orderbook updates from WebSocket."""
        # Update strategy with new data if needed
        pass
    
    def _on_feed_connect(self):
        """Handle WebSocket connection."""
        cprint("📡 WebSocket connected", "green")
    
    def _on_feed_disconnect(self, reason: str):
        """Handle WebSocket disconnection."""
        cprint(f"📡 WebSocket disconnected: {reason}", "yellow")
    
    def _check_for_fills(self):
        """Poll Polymarket for recent trades and detect fills."""
        try:
            # Get recent trades from Polymarket
            trades = self.client.get_trades(limit=50)
            
            if not trades:
                return
            
            # Track which trades we've already processed (by trade ID)
            if not hasattr(self, '_processed_trades'):
                self._processed_trades = set()
            
            for trade in trades:
                trade_id = trade.get("id") or trade.get("trade_id")
                if not trade_id or trade_id in self._processed_trades:
                    continue
                
                # Mark as processed
                self._processed_trades.add(trade_id)
                
                # Keep set size manageable
                if len(self._processed_trades) > 500:
                    self._processed_trades = set(list(self._processed_trades)[-200:])
                
                # Find matching order in our order manager
                token_id = trade.get("asset_id") or trade.get("token_id")
                side = trade.get("side", "").upper()
                price = float(trade.get("price", 0))
                size = float(trade.get("size", 0))
                
                # Log the fill with strategy info
                order = None
                strategy_name = "unknown"
                
                for oid, o in self.order_manager.orders.items():
                    if o.token_id == token_id and o.side == side:
                        order = o
                        strategy_name = o.metadata.get("strategy", "unknown")
                        break
                
                if order:
                    cprint(f"💰 FILL [{strategy_name.upper()}]: {side} {size:.2f} @ ${price:.3f} | {order.market_slug}", "green", attrs=["bold"])
                    
                    # Trigger fill handler
                    fill_data = {
                        "trade_id": trade_id,
                        "side": side,
                        "price": price,
                        "size": size,
                        "token_id": token_id
                    }
                    self._on_order_fill(order, fill_data)
                    
                    # Telegram fill alert
                    self.telegram.alert_fill(
                        strategy=strategy_name,
                        side=side,
                        price=price,
                        size=size,
                        market=order.market_slug,
                    )
                else:
                    # Still log untracked fills
                    cprint(f"💰 FILL [EXTERNAL]: {side} {size:.2f} @ ${price:.3f}", "cyan")
                    
        except Exception as e:
            # Don't spam errors - fill checking is optional
            pass
    
    def _on_order_fill(self, order, fill_data: Dict):
        """Handle order fills."""
        # Update risk manager
        self.risk_manager.update_position(
            token_id=order.token_id,
            market_slug=order.market_slug,
            side="YES",  # Simplified - would need to track from market
            size_delta=fill_data.get("size", order.size),
            price=fill_data.get("price", order.price),
            is_entry=(order.side == "BUY")
        )

        # Persist trade record
        if self.store:
            trade_id = fill_data.get("trade_id") or f"{order.order_id}_{int(time.time() * 1000)}"
            strategy_name = order.metadata.get("strategy", "unknown") if order.metadata else "unknown"
            trade_record = {
                "trade_id": trade_id,
                "order_id": order.order_id,
                "token_id": order.token_id,
                "market_slug": order.market_slug,
                "side": fill_data.get("side", order.side),
                "price": fill_data.get("price", order.price),
                "size": fill_data.get("size", order.size),
                "strategy": strategy_name,
                "traded_at": datetime.now().isoformat(),
            }
            try:
                self.store.save_trade(trade_record)
            except Exception as e:
                cprint(f"❌ Failed to persist trade {trade_id}: {e}", "red")
        
        # Record exit in analytics tracker
        strategy_name = order.metadata.get("strategy", "unknown") if order.metadata else "unknown"
        if fill_data.get("side") == "SELL" and order.metadata:
            entry_price = order.metadata.get("entry_price", order.price)
            pnl = (fill_data.get("price", 0) - entry_price) * fill_data.get("size", 0)
            self.strategy_tracker.record_trade(
                strategy=strategy_name,
                token_id=fill_data.get("token_id", ""),
                market_slug=order.market_slug,
                side="SELL",
                price=fill_data.get("price", 0),
                size=fill_data.get("size", 0),
                pnl=pnl,
                fees=fill_data.get("size", 0) * fill_data.get("price", 0) * 0.01,
                is_exit=True,
            )
        
        # Notify the correct strategy (not just the first one)
        strategy_name = order.metadata.get("strategy", "") if order.metadata else ""
        notified = False
        for strat in self.strategies:
            if strat.name == strategy_name:
                exit_action = strat.on_order_filled(order.order_id, fill_data)
                notified = True
                break
        
        # Fallback: notify first strategy if no match
        if not notified and self.strategy:
            exit_action = self.strategy.on_order_filled(order.order_id, fill_data)
        else:
            exit_action = None
        
        # Handle exit order if strategy requests one
        if exit_action and exit_action.get("action") == "place_exit":
            self.order_manager.place_limit_order(
                token_id=exit_action["token_id"],
                side=exit_action["side"],
                price=exit_action["price"],
                size=exit_action["size"],
                market_slug=order.market_slug,
                metadata={
                    "reason": exit_action.get("reason", "Exit order"),
                    "strategy": strategy_name,
                    "entry_price": order.price,
                }
            )
    
    def _on_risk_level_change(self, level: RiskLevel, reason: str):
        """Forward risk level changes to Telegram."""
        self.telegram.alert_risk_event(level.value, reason)
        
        # Also check throttle changes
        throttle = self.risk_manager.get_throttle_factor()
        if throttle != self._last_alerted_throttle:
            if self.risk_manager.session_peak_balance > 0:
                dd = (self.risk_manager.session_peak_balance - self.risk_manager.current_balance) / self.risk_manager.session_peak_balance
            else:
                dd = 0
            self.telegram.alert_throttle(throttle, dd)
            self._last_alerted_throttle = throttle
    
    def _on_order_cancel(self, order, reason: str):
        """Handle order cancellations."""
        # Notify the correct strategy
        strategy_name = order.metadata.get("strategy", "") if order.metadata else ""
        for strat in self.strategies:
            if strat.name == strategy_name:
                strat.on_order_cancelled(order.order_id, reason)
                return
        # Fallback
        if self.strategy:
            self.strategy.on_order_cancelled(order.order_id, reason)
    
    def _print_final_stats(self):
        """Print final statistics."""
        cprint("\n" + "="*60, "cyan")
        cprint("📊 Final Statistics", "cyan", attrs=["bold"])
        cprint("="*60, "cyan")
        
        # Strategy stats (all strategies in multi-mode)
        total_signals = 0
        total_trades = 0
        total_pnl = 0.0
        
        for strategy in self.strategies:
            state = strategy.get_state()
            total_signals += state['signals_generated']
            total_trades += state['trades_executed']
            total_pnl += state['pnl']
            
            cprint(f"\n  [{state['name'].upper()}]", "cyan")
            cprint(f"    Signals: {state['signals_generated']} | Trades: {state['trades_executed']} | PnL: ${state['pnl']:.2f}", "white")
        
        if len(self.strategies) > 1:
            cprint(f"\n  TOTAL:", "green", attrs=["bold"])
            cprint(f"    Signals: {total_signals} | Trades: {total_trades}", "white")
            pnl_color = "green" if total_pnl >= 0 else "red"
            cprint(f"    Combined PnL: ${total_pnl:.2f}", pnl_color)
        
        # Order manager stats
        if self.order_manager:
            om_stats = self.order_manager.get_stats()
            cprint(f"\n  Total Orders: {om_stats['total_placed']}", "white")
            cprint(f"  Filled: {om_stats['total_filled']}", "white")
            cprint(f"  Cancelled: {om_stats['total_cancelled']}", "white")
            cprint(f"  Fill Rate: {om_stats['fill_rate']*100:.1f}%", "white")
        
        # Risk manager stats
        self.risk_manager.print_status()
        
        cprint("="*60 + "\n", "cyan")


def main():
    """Entry point for the bot."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Polymarket Trading Bot")
    parser.add_argument(
        "--strategy", "-s",
        default="btc_5min",
        help=(
            "Strategy to use: stink_bid, late_money, cross_asset, "
            "terminal_convergence, orderbook_imbalance, "
            "'btc_5min' (recommended), or 'all' (default: btc_5min)"
        )
    )
    parser.add_argument(
        "--list-strategies", "-l",
        action="store_true",
        help="List available strategies"
    )
    parser.add_argument(
        "--paper", "-p",
        action="store_true",
        help="Force paper trading mode"
    )
    
    args = parser.parse_args()
    
    if args.list_strategies:
        cprint("\n📋 Available Strategies:\n", "cyan", attrs=["bold"])
        for name, info in list_strategies().items():
            cprint(f"  • {name}", "green")
            cprint(f"    {info['description']}\n", "white")
        return
    
    # Create and run bot
    try:
        bot = PolymarketBot(strategy=args.strategy)
        bot.start()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        cprint(f"\n❌ Fatal error: {e}", "red")
        sys.exit(1)


if __name__ == "__main__":
    main()



