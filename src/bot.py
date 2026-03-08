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
from dataclasses import replace
from typing import Dict, List, Optional, Type
from datetime import datetime

from .logging_utils import cprint, set_dashboard_mode
from .dashboard import (
    Dashboard, DashboardState, BinanceSnapshot,
    StrategyRow, PortfolioSnapshot, log as dash_log,
)

from .config import (
    SCAN_INTERVAL_SECONDS,
    BALANCE_REFRESH_SECONDS,
    MARKET_REFRESH_SECONDS,
    MAX_HOURS_TO_EXPIRY,
    SHORTTERM_NEAREST_CYCLE_ONLY,
    SHORTTERM_MAX_HOURS_AHEAD,
    ENABLE_WEBSOCKET_FEED,
    ENABLE_BTC_5MIN,
    TERMINAL_CONVERGENCE_1H_ONLY,
    PAPER_TRADING,
    CRYPTO_MARKET_KEYWORDS,
    ONLY_CRYPTO_MARKETS,
    BTC_5MIN_KEYWORDS,
    DISABLED_STRATEGIES,
    ADAPTIVE_RISK_ENABLED,
    ADAPTIVE_MAX_POSITION_PCT,
    ADAPTIVE_MAX_EXPOSURE_PCT,
    ADAPTIVE_MAX_SINGLE_TRADE_PCT,
    ADAPTIVE_DAILY_LOSS_LIMIT_PCT,
    ADAPTIVE_DRAWDOWN_THROTTLE_PCT,
    ADAPTIVE_DRAWDOWN_HALT_PCT,
    ADAPTIVE_MIN_BALANCE_FLOOR_PCT,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_ALERTS_ENABLED,
    MIN_VOLUME_USD,
    TRADING_FEE_RATE,
    BALANCE_STALE_BLOCK_BUYS,
    BALANCE_STALE_MAX_SECONDS,
    POLYMARKET_MIN_ORDER_SIZE,
    print_config,
    validate_config,
    ENABLE_STRATEGY_ANALYTICS,
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
from .strategies.base_strategy import MarketData, SignalType


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
            adaptive_config={
                "enabled": ADAPTIVE_RISK_ENABLED,
                "max_position_pct": ADAPTIVE_MAX_POSITION_PCT,
                "max_exposure_pct": ADAPTIVE_MAX_EXPOSURE_PCT,
                "max_single_trade_pct": ADAPTIVE_MAX_SINGLE_TRADE_PCT,
                "daily_loss_limit_pct": ADAPTIVE_DAILY_LOSS_LIMIT_PCT,
                "drawdown_throttle_pct": ADAPTIVE_DRAWDOWN_THROTTLE_PCT,
                "drawdown_halt_pct": ADAPTIVE_DRAWDOWN_HALT_PCT,
                "min_balance_floor_pct": ADAPTIVE_MIN_BALANCE_FLOOR_PCT,
            },
        )
        self.analytics_enabled = ENABLE_STRATEGY_ANALYTICS
        self.strategy_tracker = StrategyTracker() if self.analytics_enabled else None
        
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
        
        # Strategy config — inject shared resources into strategies
        merged_config = dict(strategy_config or {})
        merged_config["client"] = self.client
        merged_config["risk_manager"] = self.risk_manager
        merged_config["store"] = self.store
        if self.binance_feed:
            merged_config["binance_feed"] = self.binance_feed
        merged_config["1h_only"] = TERMINAL_CONVERGENCE_1H_ONLY
        
        # Multi-strategy mode
        self.multi_strategy_mode = (strategy == "all")
        self.strategies: List[BaseStrategy] = []
        
        # BTC 5-min strategies that need Binance feed
        _BTC_5MIN_STRATEGIES = ["cross_asset", "terminal_convergence", "orderbook_imbalance"]
        # ML strategies use Binance feed but target slower horizons, so keep them separate
        _ML_STRATEGIES = ["ml_directional"]
        # Phase 5 advanced strategies (work on all markets)
        _ADVANCED_STRATEGIES = ["vpin", "sentiment", "combinatorial_arb", "wallet_copy"]
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
                strat_names.extend(_ML_STRATEGIES)
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
            for extra in ["stink_bid", "arbitrage", "vpin", "sentiment", "combinatorial_arb", "wallet_copy"]:
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
        self._scan_count = 0
        self._last_balance_refresh_success_ts = 0.0
        self._last_positions_sync_ts = 0.0
        
        # TUI dashboard
        self.dashboard = Dashboard()
        
        # Threads
        self._main_thread: Optional[threading.Thread] = None
        self._market_fetch_thread: Optional[threading.Thread] = None
        
        # Register signal handlers
        self._shutdown_requested = False
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        
        cprint("✅ Bot initialized!\n", "green")
    
    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully. Keep handler minimal so it returns quickly."""
        if self._shutdown_requested:
            # Second Ctrl+C: force exit immediately
            import os
            os._exit(0)
        self._shutdown_requested = True
        set_dashboard_mode(False)
        self.dashboard.stop()
        cprint("\n\n⚠️  Shutdown signal received... (Ctrl+C again to force quit)", "yellow")
        self.is_running = False
        # Main loop will exit and call stop() to cancel orders, etc.
    
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
        for strat in self.strategies:
            strat.config["order_manager"] = self.order_manager

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
            syms = getattr(self.binance_feed, "symbols", ["btcusdt"])
            sym_str = ",".join(s.upper() for s in syms)
            cprint(f"   Binance Feed: ✅ {sym_str}", "cyan")
        if self.risk_manager.adaptive_enabled:
            cprint(f"   Adaptive Risk: ✅ bankroll-proportional", "cyan")
        cprint("\n   Press Ctrl+C to stop\n", "yellow")
        
        # Start TUI dashboard — takes over the terminal
        time.sleep(0.5)  # let final startup messages flush
        self.dashboard.start()
        set_dashboard_mode(True)
        dash_log("✅ Bot started — dashboard active")
        
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
        
        # Restore normal terminal before printing shutdown info
        set_dashboard_mode(False)
        self.dashboard.stop()
        
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

        # Stop client background tasks (e.g. CLOB heartbeat loop)
        try:
            self.client.stop_background_tasks()
        except Exception:
            pass
        
        # Print final stats
        self._print_final_stats()
        if self.analytics_enabled and self.strategy_tracker:
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
        last_sync = 0
        fill_check_interval = 10  # Check for fills every 10 seconds
        sync_interval = 120  # Sync order state with exchange every 2 min (catches missed fills)
        self._recent_trades_cache: List[Dict] = []  # shared with VPIN
        self._first_scan_done = False
        
        while self.is_running:
            try:
                now = time.time()
                
                # Check if it's time to scan
                if now - last_scan >= SCAN_INTERVAL_SECONDS:
                    t0 = time.time()
                    self._scan_and_trade()
                    dt = time.time() - t0
                    if dt > 5:
                        cprint(f"   ⏱️  scan_and_trade took {dt:.1f}s (slow!)", "yellow")
                    last_scan = time.time()  # use actual time, not stale `now`
                
                # Check for fills periodically
                if time.time() - last_fill_check >= fill_check_interval:
                    t0 = time.time()
                    self._check_for_fills()
                    dt = time.time() - t0
                    if dt > 5:
                        cprint(f"   ⏱️  fill_check took {dt:.1f}s (slow!)", "yellow")
                    last_fill_check = time.time()

                # Refresh balance periodically
                if time.time() - last_balance_check >= BALANCE_REFRESH_SECONDS:
                    t0 = time.time()
                    self._refresh_balance()
                    dt = time.time() - t0
                    if dt > 5:
                        cprint(f"   ⏱️  balance_refresh took {dt:.1f}s (slow!)", "yellow")
                    last_balance_check = time.time()

                # Refresh market universe periodically
                if time.time() - last_market_refresh >= MARKET_REFRESH_SECONDS:
                    t0 = time.time()
                    self._fetch_markets(is_refresh=True)
                    dt = time.time() - t0
                    if dt > 5:
                        cprint(f"   ⏱️  market_refresh took {dt:.1f}s (slow!)", "yellow")
                    last_market_refresh = time.time()

                # Sync order state + positions from exchange/API (catches missed fills)
                if time.time() - last_sync >= sync_interval and not PAPER_TRADING:
                    try:
                        if self.order_manager.orders:
                            self.order_manager.sync_with_exchange()
                        # Sync exposure from Data API positions (ground truth, not fill-derived)
                        positions = self.client.get_positions(limit=150)
                        if positions:
                            self.risk_manager.sync_positions_from_api(positions)
                        last_sync = time.time()
                        self._last_positions_sync_ts = time.time()
                    except Exception as e:
                        cprint(f"   ⚠️  sync error: {e}", "yellow")
                
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
        from .logging_utils import _DASHBOARD_MODE
        
        self._scan_count += 1
        
        # Check if trading is allowed
        can_trade, reason = self.risk_manager.can_trade()
        if not can_trade:
            cprint(f"⏸️  Trading paused: {reason}", "yellow")
            self._push_dashboard_state()
            return
        
        # Build market data for strategy
        market_data_list = self._build_market_data()
        
        if not market_data_list:
            if not _DASHBOARD_MODE:
                cprint(f"📊 Scanning {len(self.markets)} markets... (no price data yet)", "white")
            self._push_dashboard_state()
            return
        
        # Binance state (logged to panel, not spammed to log)
        if not _DASHBOARD_MODE:
            cprint(f"\n📊 Analyzed {len(market_data_list)} tokens @ {datetime.now().strftime('%H:%M:%S')}", "cyan")
            if hasattr(self, 'binance_feed') and self.binance_feed:
                bs = self.binance_feed.get_state()
                if bs.connected and bs.last_price > 0:
                    cprint(
                        f"   📡 BTC ${bs.last_price:,.0f} | "
                        f"10s={bs.price_change_pct_10s:+.4f}% "
                        f"30s={bs.price_change_pct_30s:+.4f}% "
                        f"60s={bs.price_change_pct_60s:+.4f}% | "
                        f"vol={bs.volatility_5m:.2f}σ press={bs.bid_pressure:.2f}",
                        "dark_grey",
                    )
                elif not bs.connected:
                    cprint("   📡 Binance: NOT CONNECTED", "red")
        
        # First-scan diagnostic: show market matching stats
        if not self._first_scan_done:
            self._first_scan_done = True
            self._log_market_diagnostics(market_data_list)
        
        # Show adaptive risk state (only log if throttled — that's important)
        if self.risk_manager.adaptive_enabled:
            throttle = self.risk_manager.get_throttle_factor()
            if throttle < 1.0:
                cprint(f"⚠️  Throttle: {throttle*100:.0f}% (drawdown protection active)", "yellow")
        
        # Run each strategy
        total_signals = 0
        for strategy in self.strategies:
            strat_name = strategy.name.upper()
            
            # Check strategy health before running
            if self.analytics_enabled and self.strategy_tracker:
                healthy, health_reason = self.strategy_tracker.is_strategy_healthy(strategy.name)
                if not healthy:
                    cprint(f"[{strat_name}] ⏸️  Disabled: {health_reason}", "yellow")
                    continue
            
            # Run strategy analysis
            signals = strategy.analyze(market_data_list)
            
            if signals:
                total_signals += len(signals)
                
                # Execute signals
                n_blocked = 0
                n_executed = 0
                block_reason = ""
                for sig in signals:
                    is_sell = sig.signal_type == SignalType.SELL
                    trade_value = sig.size * sig.price
                    adaptive_size = self.risk_manager.get_adaptive_order_size(trade_value)
                    exec_signal = sig
                    resized = False

                    if (
                        BALANCE_STALE_BLOCK_BUYS
                        and not is_sell
                        and self._last_balance_refresh_success_ts > 0
                    ):
                        age = time.time() - self._last_balance_refresh_success_ts
                        if age > BALANCE_STALE_MAX_SECONDS:
                            n_blocked += 1
                            block_reason = (
                                f"Balance data stale ({int(age)}s > {BALANCE_STALE_MAX_SECONDS}s)"
                            )
                            continue
                    
                    # For BUY: check risk limits. For SELL (position close): skip
                    if not is_sell:
                        can_open, reason = self.risk_manager.can_open_position(
                            sig.token_id,
                            adaptive_size,
                            sig.price,
                            strategy=strategy.name,
                        )
                        if not can_open:
                            n_blocked += 1
                            block_reason = reason
                            continue

                        # Adaptive risk should resize actual execution, not just checks.
                        if sig.price > 0:
                            adaptive_shares = adaptive_size / sig.price
                            if adaptive_shares < POLYMARKET_MIN_ORDER_SIZE:
                                n_blocked += 1
                                block_reason = f"Size below Polymarket minimum ({POLYMARKET_MIN_ORDER_SIZE:.0f} shares)"
                                continue
                            exec_signal = replace(sig, size=round(max(POLYMARKET_MIN_ORDER_SIZE, adaptive_shares), 4))
                            resized = abs(exec_signal.size - sig.size) > 0.0001

                            # Visibility: show when adaptive risk materially resizes BUYs.
                            if resized and not _DASHBOARD_MODE:
                                original_notional = sig.size * sig.price
                                resized_notional = exec_signal.size * exec_signal.price
                                rel_delta = (
                                    abs(resized_notional - original_notional) / original_notional
                                    if original_notional > 0
                                    else 0.0
                                )
                                if rel_delta >= 0.05:
                                    import time as _t
                                    _arl = getattr(self, "_adaptive_resize_log", {})
                                    _now = _t.time()
                                    key = f"{strategy.name}:{sig.token_id}"
                                    if _now - _arl.get(key, 0) >= 15:
                                        _arl[key] = _now
                                        self._adaptive_resize_log = _arl
                                        throttle = self.risk_manager.get_throttle_factor()
                                        cprint(
                                            f"🔧 [{strat_name}] adaptive resize: "
                                            f"${original_notional:.2f} -> ${resized_notional:.2f} "
                                            f"({sig.size:.2f} -> {exec_signal.size:.2f} shares, throttle {throttle*100:.0f}%)",
                                            "cyan",
                                        )
                    
                    # Execute via strategy
                    results = strategy.execute([exec_signal], self.order_manager)
                    
                    # Record trade in both risk manager and analytics tracker
                    for result in results:
                        if result.get("success"):
                            n_executed += 1
                            executed_notional = exec_signal.size * exec_signal.price
                            order = result.get("order")
                            if order:
                                try:
                                    executed_notional = float(order.size) * float(order.price)
                                except Exception:
                                    pass
                            fee_est = executed_notional * TRADING_FEE_RATE
                            self.risk_manager.record_trade(executed_notional, fee_est)
                            if self.analytics_enabled and self.strategy_tracker:
                                self.strategy_tracker.record_trade(
                                    strategy=strategy.name,
                                    token_id=exec_signal.token_id,
                                    market_slug=exec_signal.market_slug,
                                    side=exec_signal.side,
                                    price=exec_signal.price,
                                    size=exec_signal.size,
                                    pnl=0.0,  # P&L tracked on exit
                                    fees=fee_est,
                                    is_exit=is_sell,
                                )
                
                # Log summary: executed trades always, risk blocks throttled
                if n_executed > 0:
                    cprint(f"🎯 [{strat_name}] {n_executed}/{len(signals)} signal(s) executed!", "green", attrs=["bold"])
                if n_blocked > 0:
                    import time as _t
                    _rb_log = getattr(self, '_risk_block_log', {})
                    _now = _t.time()
                    if _now - _rb_log.get(strat_name, 0) >= 30:
                        _rb_log[strat_name] = _now
                        self._risk_block_log = _rb_log
                        cprint(f"⚠️  [{strat_name}] {n_blocked} blocked: {block_reason}", "yellow")
            else:
                # "No opportunities" is noise — skip in dashboard mode
                if not _DASHBOARD_MODE:
                    cprint(f"   [{strat_name}] No opportunities", "white")
        
        if total_signals == 0 and not _DASHBOARD_MODE:
            cprint(f"   Waiting for opportunities...", "white")
        
        # Update the TUI dashboard
        self._push_dashboard_state()
    
    def _push_dashboard_state(self) -> None:
        """Build a DashboardState snapshot and push it to the TUI."""
        from .logging_utils import _DASHBOARD_MODE
        if not _DASHBOARD_MODE:
            return
        
        # Binance snapshot
        bs = BinanceSnapshot()
        if self.binance_feed:
            b = self.binance_feed.get_state()
            bs = BinanceSnapshot(
                connected=b.connected,
                price=b.last_price,
                chg_10s=b.price_change_pct_10s,
                chg_30s=b.price_change_pct_30s,
                chg_60s=b.price_change_pct_60s,
                volatility=b.volatility_5m,
                pressure=b.bid_pressure,
            )
        
        # Strategy rows
        strat_rows = []
        for strat in self.strategies:
            state = strat.get_state()
            if self.analytics_enabled and self.strategy_tracker:
                healthy, _ = self.strategy_tracker.is_strategy_healthy(strat.name)
            else:
                healthy = True
            
            # Last signal time
            last_ts = ""
            if hasattr(strat, 'last_signal_time') and strat.last_signal_time:
                most_recent = max(strat.last_signal_time.values()) if strat.last_signal_time else 0
                if most_recent > 0:
                    ago = time.time() - most_recent
                    if ago < 60:
                        last_ts = f"{ago:.0f}s ago"
                    elif ago < 3600:
                        last_ts = f"{ago/60:.0f}m ago"
                    else:
                        last_ts = f"{ago/3600:.1f}h ago"
            
            strat_rows.append(StrategyRow(
                name=strat.name,
                signals=state['signals_generated'],
                trades=state['trades_executed'],
                pnl=state['pnl'] if self.analytics_enabled else 0.0,
                healthy=healthy,
                last_signal=last_ts,
                status=state.get('status', ''),
            ))
        
        # Portfolio snapshot
        rm = self.risk_manager
        active_orders = 0
        om_filled = 0
        om_cancelled = 0
        om_fill_rate = 0.0
        max_orders = 10
        if self.order_manager:
            active_orders = len(self.order_manager.get_active_orders())
            om_stats = self.order_manager.get_stats()
            om_filled = om_stats.get('total_filled', 0)
            om_cancelled = om_stats.get('total_cancelled', 0)
            om_fill_rate = om_stats.get('fill_rate', 0) * 100
            max_orders = getattr(self.order_manager, 'max_active_orders', 10)
        
        total_exp = rm.get_total_exposure()
        rm_status = rm.get_status()
        balance_age = time.time() - self._last_balance_refresh_success_ts if self._last_balance_refresh_success_ts > 0 else 0.0
        portfolio = PortfolioSnapshot(
            balance=rm.current_balance,
            start_balance=rm.starting_balance,
            exposure=total_exp,
            exposure_pct=(total_exp / rm.current_balance * 100) if rm.current_balance > 0 else 0,
            daily_pnl=rm_status.get('daily_pnl', 0.0),
            positions=len(rm.positions),
            active_orders=active_orders,
            max_orders=max_orders,
            filled=om_filled,
            cancelled=om_cancelled,
            fill_rate=om_fill_rate,
            throttle=rm.get_throttle_factor() if rm.adaptive_enabled else 1.0,
            balance_age_seconds=balance_age,
            balance_stale_block_buys=BALANCE_STALE_BLOCK_BUYS,
            balance_stale_max_seconds=float(BALANCE_STALE_MAX_SECONDS),
            last_balance_sync_ts=self._last_balance_refresh_success_ts,
            last_positions_sync_ts=self._last_positions_sync_ts,
        )
        
        # Build Risk Engine snapshot
        from .dashboard import RiskEngineSnapshot
        try:
            from .native.pmkernel import NATIVE_AVAILABLE
            self.risk_manager.compute_portfolio_greeks()
            re_snap = RiskEngineSnapshot(
                native_available=NATIVE_AVAILABLE,
                engine_label="NATIVE" if NATIVE_AVAILABLE else "PYTHON",
                net_delta=self.risk_manager.net_delta,
                net_gamma=self.risk_manager.net_gamma,
                active_markets=len(self.risk_manager.positions),
            )
        except Exception:
            re_snap = RiskEngineSnapshot()

        state = DashboardState(
            paper=PAPER_TRADING,
            n_markets=len(self.markets),
            scan_number=self._scan_count,
            binance=bs,
            strategies=strat_rows,
            portfolio=portfolio,
            risk_engine=re_snap,
        )
        self.dashboard.update(state)
    
    def _parse_market_end_ts(self, m: dict) -> Optional[float]:
        """Parse market end/resolution timestamp. Returns Unix sec or None."""
        for key in ("endDate", "end_date", "end_date_iso", "closeTime", "resolutionDate"):
            val = m.get(key)
            if not val:
                continue
            if isinstance(val, (int, float)):
                v = float(val)
                if v > 1e12:
                    return v / 1000
                if v > 1e9:
                    return v
                return None
            if isinstance(val, str):
                try:
                    parsed = datetime.fromisoformat(val.replace("Z", "+00:00"))
                    return parsed.timestamp()
                except Exception:
                    pass
        slug = m.get("slug", "")
        parts = slug.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return float(parts[1])
        return None

    def _build_market_data(self) -> List[MarketData]:
        """Build MarketData objects from cached data."""
        data_list = []

        for condition_id, market in self.markets.items():
            try:
                # Gamma API includes bestBid/bestAsk directly in market data!
                best_bid = float(market.get("bestBid", 0) or 0)
                best_ask = float(market.get("bestAsk", 1) or 1)
                
                # Check if this is a crypto short-term market (5m, 15m, 1h, 4h from events)
                q_lower = market.get("question", "").lower()
                slug_lower = market.get("slug", "").lower()
                mtext = f"{q_lower} {slug_lower}"
                is_crypto_st = (
                    any(kw in mtext for kw in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp"])
                    and any(kw in mtext for kw in BTC_5MIN_KEYWORDS)
                )
                
                # Skip if no valid prices (non-crypto). Crypto short-term: use synthetic so spread sees them.
                # Gamma/events often lack bestBid/bestAsk; WebSocket will override per-token if available.
                # Also allow threshold/combo_arb markets (above/below) through with synthetic fallback.
                is_threshold = any(kw in mtext for kw in ["above", "below", "over", "under", "exceed", "reach"])
                if best_bid <= 0 or best_ask <= 0 or best_ask >= 1:
                    if is_crypto_st or is_threshold:
                        best_bid = 0.50 if best_bid <= 0 else best_bid
                        best_ask = 0.52 if best_ask <= 0 or best_ask >= 1 else best_ask
                    else:
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
                
                # Parse outcomes — e.g. ["Up","Down"], ["Yes","No"]
                raw_outcomes = market.get("outcomes", [])
                if isinstance(raw_outcomes, str):
                    import json as _json
                    try:
                        raw_outcomes = _json.loads(raw_outcomes)
                    except Exception:
                        raw_outcomes = ["Yes", "No"]
                if not raw_outcomes:
                    raw_outcomes = ["Yes", "No"]
                
                # Build one MarketData per outcome token
                for idx, token_id in enumerate(clob_tokens):
                    if not token_id:
                        continue
                    outcome_label = raw_outcomes[idx] if idx < len(raw_outcomes) else "Yes"
                    
                    # For the first token, use API bid/ask directly
                    # For the second token, invert (complement pricing)
                    if idx == 0:
                        t_bid, t_ask = best_bid, best_ask
                    else:
                        t_bid = round(max(0.01, 1.0 - best_ask), 4)
                        t_ask = round(min(0.99, 1.0 - best_bid), 4)
                    
                    t_mid = (t_bid + t_ask) / 2
                    t_spread = t_ask - t_bid
                    
                    # Get cached recent trades for this token (for VPIN)
                    token_trades = [
                        t for t in getattr(self, '_recent_trades_cache', [])
                        if (t.get("asset_id") or t.get("token_id")) == token_id
                    ]

                    end_ts = self._parse_market_end_ts(market) if is_crypto_st else None

                    # Prefer WebSocket orderbook for best_bid/best_ask when available (real-time)
                    ob_data = None
                    if hasattr(self, 'feed') and self.feed:
                        ws_ob = self.feed.get_latest_orderbook(token_id)
                        if ws_ob is not None:
                            ob_data = {"bids": ws_ob.bids, "asks": ws_ob.asks}
                            # Override Gamma prices with WebSocket for latency-sensitive strategies
                            t_bid = float(ws_ob.best_bid) if ws_ob.bids else t_bid
                            t_ask = float(ws_ob.best_ask) if ws_ob.asks else t_ask
                            t_mid = (t_bid + t_ask) / 2
                            t_spread = t_ask - t_bid

                    data = MarketData(
                        token_id=token_id,
                        condition_id=condition_id,
                        market_slug=market.get("slug", ""),
                        question=market.get("question", ""),
                        outcome=outcome_label,
                        best_bid=t_bid,
                        best_ask=t_ask,
                        mid_price=t_mid,
                        spread=t_spread,
                        volume_24h=volume,
                        liquidity=float(market.get("liquidityClob", 0) or 0),
                        last_price=float(market.get("lastTradePrice", t_mid) or t_mid),
                        orderbook=ob_data,
                        recent_trades=token_trades if token_trades else None,
                        end_date_ts=end_ts,
                    )
                    
                    data_list.append(data)
                    
            except Exception as e:
                continue
        
        return data_list

    def _log_market_diagnostics(self, market_data_list: List[MarketData]) -> None:
        """Log how many markets match each strategy's filters (first scan only)."""
        btc_count = 0
        btc_5min_count = 0
        crypto_count = 0
        with_orderbook = 0
        with_trades = 0
        sample_all: List[str] = []  # first few market questions for debugging

        for md in market_data_list:
            q = md.question.lower()
            slug = md.market_slug.lower()
            text = f"{q} {slug}"  # search both fields
            is_btc = any(kw in text for kw in ["bitcoin", "btc"])
            is_5min = any(kw in text for kw in BTC_5MIN_KEYWORDS)
            is_crypto = any(kw in text for kw in CRYPTO_MARKET_KEYWORDS)

            if is_btc:
                btc_count += 1
            if is_btc and is_5min:
                btc_5min_count += 1
            if is_crypto:
                crypto_count += 1
            if md.orderbook:
                with_orderbook += 1
            if md.recent_trades:
                with_trades += 1
            if len(sample_all) < 5:
                sample_all.append(f"Q={q[:60]} | slug={slug[:40]}")

        cprint(f"\n   🔍 Market Diagnostics (first scan):", "cyan", attrs=["bold"])
        cprint(f"      Total tokens: {len(market_data_list)}", "white")
        cprint(f"      Crypto markets: {crypto_count}", "white")
        cprint(f"      BTC markets: {btc_count}", "white")
        cprint(f"      BTC short-term markets: {btc_5min_count}", "white")
        cprint(f"      With orderbook data: {with_orderbook}", "white")
        cprint(f"      With recent trades: {with_trades}", "white")

        # Combinatorial arb: threshold markets (above/below + numeric)
        threshold_count = sum(
            1 for md in market_data_list
            if any(kw in (md.question or "").lower() for kw in ["above", "below", "over", "under", "exceed", "reach"])
            and any(kw in (md.question or "").lower() for kw in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol"])
        )
        cprint(f"      Threshold markets (combo_arb): {threshold_count}", "white")

        if btc_5min_count == 0:
            cprint(f"      ⚠️  No BTC short-term markets matched!", "yellow")
            cprint(f"      Sample markets (first 5):", "yellow")
            for s in sample_all:
                cprint(f"        {s}", "yellow")

    def _fetch_markets(self, is_refresh: bool = False):
        """Fetch and filter available markets."""
        try:
            result = self.client.get_markets()
            
            if isinstance(result, dict) and "error" in result:
                cprint(f"❌ Failed to fetch markets: {result['error']}", "red")
                return
            
            markets = result if isinstance(result, list) else result.get("data", [])
            
            # Also fetch events (5-min BTC markets live here, not in /markets)
            if ENABLE_BTC_5MIN:
                events = self.client.get_events(limit=100)
                event_market_count = 0
                for event in events:
                    slug = event.get("slug", "").lower()
                    title = event.get("title", "").lower()
                    text = f"{title} {slug}"
                    # Only extract crypto up/down event sub-markets
                    is_crypto_event = any(
                        kw in text for kw in ["bitcoin", "btc", "ethereum", "eth",
                                              "solana", "sol", "xrp", "up or down", "updown"]
                    )
                    if not is_crypto_event:
                        continue
                    for sub_market in event.get("markets", []):
                        if sub_market.get("closed"):
                            continue
                        if not sub_market.get("acceptingOrders"):
                            continue
                        # Enrich with event context (Gamma may omit question/slug on sub-markets)
                        sm = dict(sub_market)
                        if not sm.get("question"):
                            sm["question"] = event.get("title", "")
                        if not sm.get("slug"):
                            sm["slug"] = event.get("slug", "")
                        markets.append(sm)
                        event_market_count += 1
                if event_market_count > 0:
                    cprint(f"📡 Found {event_market_count} crypto event markets from /events", "cyan")
            
            # Filter markets based on settings
            filtered = []
            seen_ids = set()
            now_ts = time.time()
            max_expiry_sec = MAX_HOURS_TO_EXPIRY * 3600 if MAX_HOURS_TO_EXPIRY > 0 else 0
            # Strict short-term duration markers (exclude generic "up or down" without timeframe).
            shortterm_duration_markers = [
                "5m", "15m", "1h", "4h",
                "5 min", "5-min", "5min", "5-minute", "5 minute",
                "15 min", "15-min", "15min",
                "1 hour", "4 hour", "4-hour",
                "updown-5m", "updown-15m", "updown-1h", "updown-4h",
                "up or down - 5 min", "up or down - 15 min",
                "up or down - 1 hour", "up or down - 1h",
                "up or down - 4 hour", "up or down - 4h",
            ]

            def _market_end_ts(m: dict) -> Optional[float]:
                """Parse market end/resolution timestamp. Returns Unix sec or None."""
                for key in ("endDate", "end_date", "end_date_iso", "closeTime", "resolutionDate"):
                    val = m.get(key)
                    if not val:
                        continue
                    if isinstance(val, (int, float)):
                        v = float(val)
                        if v > 1e12:
                            return v / 1000  # milliseconds
                        if v > 1e9:
                            return v  # seconds
                        return None
                    if isinstance(val, str):
                        try:
                            from datetime import datetime as dt
                            # ISO or similar
                            parsed = dt.fromisoformat(val.replace("Z", "+00:00"))
                            return parsed.timestamp()
                        except Exception:
                            pass
                # Fallback: slug like btc-updown-15m-1772402400 has Unix timestamp
                slug = m.get("slug", "")
                parts = slug.rsplit("-", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    return float(parts[1])
                return None

            for market in markets:
                question = (market.get("question") or market.get("title") or "").lower()
                slug = (market.get("slug") or market.get("market_slug") or "").lower()
                text = f"{question} {slug}"
                is_crypto_shortterm = (
                    any(kw in text for kw in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp"])
                    and any(kw in text for kw in shortterm_duration_markers)
                )
                
                # Deduplicate by conditionId
                cid = market.get("conditionId") or market.get("condition_id") or market.get("id")
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                
                # Skip markets resolving in more than MAX_HOURS_TO_EXPIRY
                # Exempt threshold markets (combo_arb). Filter far-dated non-threshold (e.g. up/down buckets).
                if max_expiry_sec > 0:
                    is_threshold = any(kw in text for kw in ["above", "below", "over", "under", "exceed", "reach"])
                    # Short-term cycle mode applies a tighter per-bucket nearest filter below.
                    if not is_threshold and not (SHORTTERM_NEAREST_CYCLE_ONLY and is_crypto_shortterm):
                        end_ts = _market_end_ts(market)
                        if end_ts is not None and (end_ts - now_ts) > max_expiry_sec:
                            continue
                
                # Check if crypto-related (if filter enabled)
                if ONLY_CRYPTO_MARKETS:
                    is_crypto = any(kw in text for kw in CRYPTO_MARKET_KEYWORDS)
                    if not is_crypto:
                        continue
                
                # Check not closed
                if market.get("closed"):
                    continue
                
                # Crypto short-term (5m/15m/1h/4h from events) often have 0 volume — bypass
                # Threshold markets (combo_arb) also bypass volume floor — many have low 24h volume
                is_threshold_mkt = any(kw in text for kw in ["above", "below", "over", "under", "exceed", "reach"])
                volume = float(market.get("volume24hr", 0) or market.get("volume24hrClob", 0) or 0)
                min_vol = 0 if (is_crypto_shortterm or is_threshold_mkt) else MIN_VOLUME_USD
                if volume < min_vol:
                    continue
                
                filtered.append(market)

            if SHORTTERM_NEAREST_CYCLE_ONLY and filtered:
                shortterm_max_sec = (
                    SHORTTERM_MAX_HOURS_AHEAD * 3600
                    if SHORTTERM_MAX_HOURS_AHEAD > 0
                    else max_expiry_sec
                )
                def _shortterm_bucket(text: str) -> Optional[str]:
                    asset = None
                    if "bitcoin" in text or "btc" in text:
                        asset = "btc"
                    elif "ethereum" in text or "eth" in text:
                        asset = "eth"
                    elif "solana" in text or "sol" in text:
                        asset = "sol"
                    elif "xrp" in text:
                        asset = "xrp"
                    if not asset:
                        return None

                    if any(k in text for k in ["5m", "5 min", "5-min", "5min", "5-minute", "updown-5m"]):
                        dur = "5m"
                    elif any(k in text for k in ["15m", "15 min", "15-min", "15min", "updown-15m"]):
                        dur = "15m"
                    elif any(k in text for k in ["1h", "1 hour", "updown-1h"]):
                        dur = "1h"
                    elif any(k in text for k in ["4h", "4 hour", "4-hour", "updown-4h"]):
                        dur = "4h"
                    else:
                        return None
                    return f"{asset}:{dur}"

                nearest_by_bucket: Dict[str, tuple] = {}
                kept_non_shortterm = []
                shortterm_seen = 0

                for market in filtered:
                    question = (market.get("question") or market.get("title") or "").lower()
                    slug = (market.get("slug") or market.get("market_slug") or "").lower()
                    text = f"{question} {slug}"
                    bucket = _shortterm_bucket(text)
                    if not bucket:
                        kept_non_shortterm.append(market)
                        continue

                    end_ts = _market_end_ts(market)
                    if end_ts is None:
                        # In nearest short-term mode, we require a parseable expiry.
                        # Otherwise far-future buckets can leak through.
                        continue

                    remaining = end_ts - now_ts
                    if remaining < 0:
                        continue
                    if shortterm_max_sec > 0 and remaining > shortterm_max_sec:
                        continue
                    shortterm_seen += 1
                    current = nearest_by_bucket.get(bucket)
                    if current is None or remaining < current[0]:
                        nearest_by_bucket[bucket] = (remaining, market)

                nearest_shortterm = [v[1] for v in nearest_by_bucket.values()]
                filtered = kept_non_shortterm + nearest_shortterm
                cprint(
                    f"⏱️ Short-term nearest-cycle filter: {len(nearest_shortterm)} kept "
                    f"across {len(nearest_by_bucket)} buckets (from {shortterm_seen})",
                    "cyan",
                )
            
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
                # Fallback: clobTokenIds (used by Gamma / events)
                for tid in market.get("clobTokenIds", []) or []:
                    if isinstance(tid, str) and tid:
                        token_ids.append(tid)
                    elif isinstance(tid, dict) and tid.get("token_id"):
                        token_ids.append(tid["token_id"])
            
            if token_ids:
                self.feed.subscribe(list(dict.fromkeys(token_ids))[:50])  # Dedupe, limit 50
                
        except Exception as e:
            cprint(f"❌ Error fetching markets: {e}", "red")

    def _refresh_feed_subscriptions(self, refreshed_markets: Dict[str, Dict]) -> None:
        """Refresh WebSocket subscriptions for updated markets."""
        if not ENABLE_WEBSOCKET_FEED:
            return

        try:
            def _collect_token_ids(markets_dict):
                out = set()
                for market in markets_dict.values():
                    for token in market.get("tokens", []):
                        if token.get("token_id"):
                            out.add(token["token_id"])
                    for tid in market.get("clobTokenIds", []) or []:
                        if isinstance(tid, str) and tid:
                            out.add(tid)
                return out

            current_tokens = _collect_token_ids(self.markets)
            new_tokens = _collect_token_ids(refreshed_markets)

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
            self._last_balance_refresh_success_ts = time.time()
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
            # Get recent trades from Polymarket (filtered by our address when PROXY_ADDRESS set)
            trades = self.client.get_trades(limit=50)
            
            if not trades:
                return

            # Cache trades for VPIN strategy
            self._recent_trades_cache = trades
            
            # Track which trades we've already processed (by trade ID)
            if not hasattr(self, '_processed_trades'):
                self._processed_trades = set()
            
            for trade in trades:
                trade_id = trade.get("id") or trade.get("trade_id")
                if not trade_id:
                    # Data API may not include id; build dedup key from content
                    aid = trade.get("asset_id") or trade.get("token_id") or trade.get("asset")
                    ts = trade.get("timestamp")
                    if aid and ts is not None:
                        trade_id = f"{aid}_{trade.get('side')}_{trade.get('price')}_{trade.get('size')}_{ts}"
                    else:
                        continue
                if trade_id in self._processed_trades:
                    continue
                
                # Mark as processed
                self._processed_trades.add(trade_id)
                
                # Keep set size manageable
                if len(self._processed_trades) > 500:
                    self._processed_trades = set(list(self._processed_trades)[-200:])
                
                # Find matching order in our order manager
                token_id = trade.get("asset_id") or trade.get("token_id") or trade.get("asset")
                side = (trade.get("side") or "").upper()
                price = float(trade.get("price", 0))
                size = float(trade.get("size", 0))
                trade_order_id = (
                    trade.get("order_id") or trade.get("orderID")
                    or trade.get("maker_order_id") or trade.get("makerOrderId")
                    or trade.get("taker_order_id") or trade.get("takerOrderId")
                )

                def _norm_id(oid: str) -> str:
                    if not oid or not isinstance(oid, str):
                        return ""
                    return oid.lower().replace("0x", "").strip()

                order = None
                strategy_name = "unknown"

                if trade_order_id:
                    # Direct lookup, then try normalized (APIs may use different casing/0x)
                    order = self.order_manager.orders.get(trade_order_id)
                    if not order:
                        norm = _norm_id(trade_order_id)
                        for oid, o in self.order_manager.orders.items():
                            if _norm_id(oid) == norm:
                                order = o
                                break
                    if order:
                        strategy_name = order.metadata.get("strategy", "unknown")
                if not order and token_id and side and price > 0 and size > 0:
                    # Fallback: match by token+side; pick best by size+price when multiple
                    candidates = [
                        o for o in self.order_manager.orders.values()
                        if o.is_active
                        and (o.token_id == token_id or _norm_id(o.token_id or "") == _norm_id(str(token_id or "")))
                        and (o.side or "").upper() == side
                        and abs(o.size - size) < 0.5
                        and abs(o.price - price) < 0.02
                    ]
                    if len(candidates) == 1:
                        order = candidates[0]
                        strategy_name = order.metadata.get("strategy", "unknown")
                    elif len(candidates) > 1:
                        # Pick best match by smallest combined size+price diff
                        best = min(candidates, key=lambda o: abs(o.size - size) + abs(o.price - price) * 10)
                        order = best
                        strategy_name = order.metadata.get("strategy", "unknown")
                
                if order and order.is_active:
                    cprint(f"💰 FILL [{strategy_name.upper()}]: {side} {size:.2f} @ ${price:.3f} | {order.market_slug}", "green", attrs=["bold"])
                    
                    fill_data = {
                        "trade_id": trade_id,
                        "side": side,
                        "price": price,
                        "size": size,
                        "token_id": token_id
                    }
                    self.order_manager.process_fill(order.order_id, fill_data)
                    
                    # Refresh balance immediately after fill (don't wait for next scheduled refresh)
                    self._refresh_balance()
                    
                    # Telegram fill alert
                    self.telegram.alert_fill(
                        strategy=strategy_name,
                        side=side,
                        price=price,
                        size=size,
                        market=order.market_slug,
                    )
                # External trades (other users) — don't log, just cache for VPIN
                    
        except Exception as e:
            cprint(f"   ⚠️  Fill check error: {e}", "yellow")
    
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
        if fill_data.get("side") == "SELL" and order.metadata and self.analytics_enabled and self.strategy_tracker:
            entry_price = order.metadata.get("entry_price") or order.price
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
            if self.analytics_enabled:
                total_pnl += state['pnl']
            
            cprint(f"\n  [{state['name'].upper()}]", "cyan")
            if self.analytics_enabled:
                cprint(f"    Signals: {state['signals_generated']} | Trades: {state['trades_executed']} | PnL: ${state['pnl']:.2f}", "white")
            else:
                cprint(f"    Signals: {state['signals_generated']} | Trades: {state['trades_executed']} | PnL: disabled", "white")
        
        if len(self.strategies) > 1:
            cprint(f"\n  TOTAL:", "green", attrs=["bold"])
            cprint(f"    Signals: {total_signals} | Trades: {total_trades}", "white")
            if self.analytics_enabled:
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
            "terminal_convergence, orderbook_imbalance, wallet_copy, "
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



