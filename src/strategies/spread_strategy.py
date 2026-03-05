"""
Micro-Spread Farming Strategy (Avellaneda-Stoikov Enhanced)

When the bs-p native engine is available, this strategy uses
``calculate_quotes_logit`` to compute inventory-aware, theoretically
optimal bid/ask quotes.  The quoting kernel accounts for:

  - Current inventory (q_t) — skews reservation price
  - Belief volatility (sigma_b) — bootstrapped from market spreads
  - Risk aversion (gamma) — wider when bankroll is small
  - Time to resolution (tau) — tighter near expiry
  - Market depth (k) — tighter in liquid markets

When the native engine is unavailable, falls back to the original
bid+improvement / ask logic.
"""

import time as _time
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from ..logging_utils import cprint

from .base_strategy import (
    BaseStrategy,
    Signal,
    SignalType,
    MarketData,
)
from ..config import (
    MIN_SPREAD_CENTS,
    ORDER_SIZE_USD,
    MAX_POSITION_USD,
    MIN_PRICE_CENTS,
    MAX_PRICE_CENTS,
    MAKER_FEE_RATE,
    MIN_PROFIT_MARGIN,
    CRYPTO_MARKET_KEYWORDS,
    SPREAD_ONLY_CRYPTO_MARKETS,
    SPREAD_ONLY_SHORTTERM_CRYPTO,
    SPREAD_LOG_VERBOSE,
    VOL_HIGH_THRESHOLD,
    VOL_LOW_THRESHOLD,
    VOL_HIGH_SPREAD_MULT,
    VOL_LOW_SPREAD_MULT,
    QUOTING_GAMMA,
    QUOTING_K,
    QUOTING_TAU_DEFAULT,
    BTC_5MIN_KEYWORDS,
)


class SpreadStrategy(BaseStrategy):
    """
    Micro-spread farming strategy for Polymarket.

    Configuration options (pass in config dict):
        - min_spread_cents: Minimum spread to trade (default: from config)
        - target_spread_cents: Target spread to capture (default: min_spread + 1)
        - order_size_usd: Size per order in USD (default: from config)
        - max_position_usd: Max position per market (default: from config)
        - price_improvement: Cents to improve on best bid (default: 0)
        - only_crypto: Only trade crypto price markets (default: True)
    """

    name = "spread"
    description = "Avellaneda-Stoikov spread farming with inventory penalty"
    version = "2.0.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

        self.min_spread_cents = self.config.get("min_spread_cents", MIN_SPREAD_CENTS)
        self.target_spread_cents = self.config.get("target_spread_cents", self.min_spread_cents + 1)
        self.order_size_usd = self.config.get("order_size_usd", ORDER_SIZE_USD)
        self.max_position_usd = self.config.get("max_position_usd", MAX_POSITION_USD)
        self.price_improvement = self.config.get("price_improvement", 0)
        self.only_crypto = self.config.get("only_crypto", SPREAD_ONLY_CRYPTO_MARKETS)
        self.only_shortterm_crypto = self.config.get("only_shortterm_crypto", SPREAD_ONLY_SHORTTERM_CRYPTO)

        self.binance_feed = self.config.get("binance_feed")
        self.risk_manager = self.config.get("risk_manager")

        self.positions: Dict[str, float] = {}
        self.pending_orders: Dict[str, Dict] = {}
        self.last_trade_time: Dict[str, datetime] = {}
        self.trade_cooldown = self.config.get("trade_cooldown_seconds", 10)

        self._vol_regime = "normal"
        self._effective_min_spread = self.min_spread_cents

        # Throttle repeated order-failure logs (once per 15s per error type)
        self._order_fail_log_ts: Dict[str, float] = {}
        self._order_fail_throttle_sec = 15
        # Throttle "filtered: cooldown" to one summary line per 30s
        self._cooldown_log_ts: float = 0.0
        self._cooldown_log_interval_sec: float = 30.0

        # Per-market implied vol cache {token_id: sigma_b}
        self._sigma_cache: Dict[str, float] = {}
        self._use_native: bool = False
        try:
            from ..native.pmkernel import NATIVE_AVAILABLE
            self._use_native = NATIVE_AVAILABLE
        except Exception:
            pass
        
    def should_trade_market(self, market_data: MarketData) -> bool:
        """
        Filter markets for spread strategy.
        
        Only trade markets that:
        1. Are crypto price predictions (if only_crypto=True)
        2. Have prices in safe range (not near resolution)
        3. Have sufficient volume
        4. Have a wide enough spread
        """
        # Check if crypto market (if filter enabled)
        if self.only_crypto:
            question_lower = market_data.question.lower()
            slug_lower = market_data.market_slug.lower()
            text = f"{question_lower} {slug_lower}"
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
            is_crypto = any(kw in text for kw in CRYPTO_MARKET_KEYWORDS)
            if not is_crypto:
                return False
            # When only_shortterm_crypto: exclude MegaETH, airdrop, etc. — only 5m/15m/1h/4h up/down
            if self.only_shortterm_crypto:
                is_shortterm = any(kw in text for kw in shortterm_duration_markers)
                if not is_shortterm:
                    return False
        
        # Check price is in safe range
        mid_cents = market_data.mid_price * 100
        if mid_cents < MIN_PRICE_CENTS or mid_cents > MAX_PRICE_CENTS:
            return False
        
        # Check spread is wide enough
        # Use small epsilon (0.001) for floating point tolerance
        # This fixes: 0.12 - 0.11 = 0.00999... which is < 1.0 due to float precision
        EPSILON = 0.001
        if market_data.spread_cents < (self.min_spread_cents - EPSILON):
            return False
        
        # Check cooldown
        token_id = market_data.token_id
        if token_id in self.last_trade_time:
            elapsed = (datetime.now() - self.last_trade_time[token_id]).seconds
            if elapsed < self.trade_cooldown:
                return False
        
        return True
    
    def _update_vol_regime(self) -> None:
        """Adjust spread parameters based on Binance volatility regime."""
        if not self.binance_feed:
            self._vol_regime = "normal"
            self._effective_min_spread = self.min_spread_cents
            return

        state = self.binance_feed.get_state()
        if not state.connected or state.volatility_5m <= 0:
            self._vol_regime = "normal"
            self._effective_min_spread = self.min_spread_cents
            return

        vol = state.volatility_5m

        if vol >= VOL_HIGH_THRESHOLD:
            self._vol_regime = "high"
            self._effective_min_spread = self.min_spread_cents * VOL_HIGH_SPREAD_MULT
        elif vol <= VOL_LOW_THRESHOLD:
            self._vol_regime = "low"
            self._effective_min_spread = self.min_spread_cents * VOL_LOW_SPREAD_MULT
        else:
            self._vol_regime = "normal"
            self._effective_min_spread = self.min_spread_cents

    # ------------------------------------------------------------------
    # Avellaneda-Stoikov quoting helpers
    # ------------------------------------------------------------------

    def _get_tau(self, data: MarketData) -> float:
        """Fraction of a day remaining until resolution."""
        if data.end_date_ts and data.end_date_ts > 0:
            remaining = max(0.0, data.end_date_ts - _time.time())
            return max(0.001, remaining / 86400.0)
        return QUOTING_TAU_DEFAULT

    def _get_inventory(self, token_id: str) -> float:
        """Current inventory in shares for *token_id*."""
        if self.risk_manager:
            pos = self.risk_manager.positions.get(token_id)
            if pos:
                return pos.size
        return self.positions.get(token_id, 0.0)

    def _get_sigma(self, data: MarketData) -> float:
        """Get or bootstrap belief volatility for a market."""
        cached = self._sigma_cache.get(data.token_id)
        if cached is not None:
            return cached

        if self._use_native:
            try:
                from ..native.pmkernel import implied_belief_vol
                sigma = implied_belief_vol(
                    bid_p=data.best_bid, ask_p=data.best_ask,
                    q_t=0.0, gamma=QUOTING_GAMMA,
                    tau=self._get_tau(data), k=QUOTING_K,
                )
                sigma = max(0.05, min(5.0, sigma)) if sigma > 0 else 0.5
                self._sigma_cache[data.token_id] = sigma
                return sigma
            except Exception:
                pass

        self._sigma_cache[data.token_id] = 0.5
        return 0.5

    def _compute_quotes(self, data: MarketData) -> tuple:
        """Compute (entry_price, exit_price, used_native) for a market.

        When the native engine is available, uses Avellaneda-Stoikov.
        Otherwise, falls back to bid + price_improvement / ask.
        """
        if self._use_native:
            try:
                from ..native.pmkernel import calculate_quotes, logit
                x_t = logit(data.mid_price)
                q_t = self._get_inventory(data.token_id)
                sigma = self._get_sigma(data)
                tau = self._get_tau(data)

                result = calculate_quotes(
                    x_t=x_t, q_t=q_t, sigma_b=sigma,
                    gamma=QUOTING_GAMMA, tau=tau, k=QUOTING_K,
                )
                entry = round(max(0.01, min(0.99, result.bid_p)), 3)
                exit_ = round(max(0.01, min(0.99, result.ask_p)), 3)
                return entry, exit_, True
            except Exception:
                pass

        entry = data.best_bid + (self.price_improvement / 100)
        exit_ = data.best_ask
        return entry, exit_, False

    # ------------------------------------------------------------------
    # Core analysis
    # ------------------------------------------------------------------

    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        """
        Analyze markets and generate spread trading signals.

        For each market with sufficient spread:
        1. Compute optimal bid/ask via Avellaneda-Stoikov (or fallback)
        2. Generate BUY signal at the computed bid
        3. Pre-calculate exit SELL at the computed ask
        """
        self._update_vol_regime()
        signals = []
        verbose = self.config.get("log_verbose", SPREAD_LOG_VERBOSE)

        if market_data and verbose:
            best = max(market_data, key=lambda x: x.spread_cents)
            cprint(
                f"      Best spread: {best.spread_cents:.2f}¢ @ {best.question[:35]}... "
                f"(bid={best.best_bid:.3f}, ask={best.best_ask:.3f})",
                "white",
            )

        cooldown_filtered = 0
        for data in market_data:
            if not self.should_trade_market(data):
                mid_cents = data.mid_price * 100
                is_crypto = any(kw in data.question.lower() for kw in CRYPTO_MARKET_KEYWORDS)
                if not is_crypto and self.only_crypto:
                    pass
                elif mid_cents < MIN_PRICE_CENTS or mid_cents > MAX_PRICE_CENTS:
                    cprint(f"      {data.question[:35]}... filtered: price {mid_cents:.1f}¢ out of range", "yellow")
                elif data.spread_cents < self.min_spread_cents:
                    pass
                else:
                    cooldown_filtered += 1
                continue

            current_position = self.positions.get(data.token_id, 0)
            if current_position >= self.max_position_usd:
                continue

            # Skip if we already have an active BUY for this token (order manager allows only 1 per token)
            if any(o.get("token_id") == data.token_id for o in self.pending_orders.values()):
                continue

            pending_value = sum(
                o["size"] * o["entry_price"]
                for o in self.pending_orders.values()
                if o.get("token_id") == data.token_id
            )
            if current_position + pending_value >= self.max_position_usd:
                continue

            entry_price, exit_price, native_used = self._compute_quotes(data)

            gross_profit_pct = (exit_price - entry_price) / entry_price if entry_price > 0 else 0
            # Makers pay zero fees on Polymarket (2026+). MAKER_FEE_RATE=0 by default.
            net_profit_pct = gross_profit_pct - (2 * MAKER_FEE_RATE)

            if net_profit_pct < MIN_PROFIT_MARGIN:
                continue

            engine_tag = "AS" if native_used else "manual"
            if verbose:
                cprint(
                    f"      [{engine_tag}] {data.question[:35]}... spread={data.spread_cents:.1f}¢, "
                    f"profit={net_profit_pct*100:.1f}%, bid={entry_price:.3f}, ask={exit_price:.3f}",
                    "green",
                )

            remaining_capacity = self.max_position_usd - current_position
            size_usd = min(self.order_size_usd, remaining_capacity)
            size_shares = size_usd / entry_price if entry_price > 0 else 0

            buy_signal = Signal(
                signal_type=SignalType.BUY,
                token_id=data.token_id,
                market_slug=data.market_slug,
                side=data.outcome,
                price=round(entry_price, 3),
                size=round(size_shares, 2),
                confidence=min(net_profit_pct * 10, 1.0),
                reason=(
                    f"Spread [{engine_tag}]: {data.spread_cents:.1f}¢, "
                    f"net {net_profit_pct*100:.1f}%"
                ),
                metadata={
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "spread_cents": data.spread_cents,
                    "gross_profit_pct": gross_profit_pct,
                    "net_profit_pct": net_profit_pct,
                    "best_bid": data.best_bid,
                    "best_ask": data.best_ask,
                    "native_engine": native_used,
                    "sigma_b": self._sigma_cache.get(data.token_id, 0),
                    "q_t": self._get_inventory(data.token_id),
                },
            )

            signals.append(buy_signal)
            self.signals_generated += 1
            if verbose:
                cprint(f"  {buy_signal}", "cyan")

        # Throttled single-line summary for cooldown-filtered (max once per 30s)
        if cooldown_filtered > 0:
            now_ts = _time.time()
            if now_ts - self._cooldown_log_ts >= self._cooldown_log_interval_sec:
                self._cooldown_log_ts = now_ts
                cprint(f"      Spread: {cooldown_filtered} market(s) skipped (cooldown)", "yellow")

        # Summary mode: one line per scan when we have signals
        if not verbose and signals:
            markets = set(s.market_slug for s in signals)
            by_dur = {}
            for s in signals:
                slug = s.market_slug.lower()
                if "5m" in slug or "5-min" in slug:
                    by_dur["5m"] = by_dur.get("5m", 0) + 1
                elif "15m" in slug or "15-min" in slug:
                    by_dur["15m"] = by_dur.get("15m", 0) + 1
                elif "1h" in slug or "1-hour" in slug:
                    by_dur["1h"] = by_dur.get("1h", 0) + 1
                elif "4h" in slug or "4-hour" in slug:
                    by_dur["4h"] = by_dur.get("4h", 0) + 1
                else:
                    by_dur["other"] = by_dur.get("other", 0) + 1
            dur_parts = [f"{k}:{v}" for k, v in sorted(by_dur.items())]
            avg_profit = sum(s.metadata.get("net_profit_pct", 0) for s in signals) / len(signals) * 100
            cprint(
                f"Spread: {len(signals)} signals ({len(markets)} markets) | "
                f"{', '.join(dur_parts)} | avg profit {avg_profit:.1f}%",
                "green",
            )

        return signals
    
    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        """
        Execute spread trades.
        
        For each BUY signal:
        1. Place limit buy order
        2. Store exit target for when buy fills
        """
        results = []
        
        for signal in signals:
            if signal.signal_type != SignalType.BUY:
                continue
            
            try:
                # Place buy order
                order_result = order_manager.place_limit_order(
                    token_id=signal.token_id,
                    side="BUY",
                    price=signal.price,
                    size=signal.size,
                    order_type="GTC"  # Good till cancelled
                )
                
                if order_result.get("success"):
                    order_id = order_result.get("order_id")
                    
                    # Track pending order with exit target
                    self.pending_orders[order_id] = {
                        "token_id": signal.token_id,
                        "market_slug": signal.market_slug,
                        "side": signal.side,
                        "entry_price": signal.price,
                        "exit_price": signal.metadata.get("exit_price"),
                        "size": signal.size,
                        "created_at": datetime.now()
                    }
                    
                    # Update last trade time
                    self.last_trade_time[signal.token_id] = datetime.now()
                    
                    cprint(f"✅ Order placed: {order_id}", "green")
                else:
                    err = order_result.get("error") or ""
                    # Throttle repeated "Max active orders" / "Already have" logs
                    throttle_key = "max_active" if "Max active orders" in err else ("already_have" if "Already have" in err else None)
                    now = _time.time()
                    if throttle_key:
                        last = self._order_fail_log_ts.get(throttle_key, 0)
                        if now - last < self._order_fail_throttle_sec:
                            pass  # skip log
                        else:
                            self._order_fail_log_ts[throttle_key] = now
                            cprint(f"❌ Order failed: {err}", "red")
                    else:
                        cprint(f"❌ Order failed: {err}", "red")
                
                results.append(order_result)
                
            except Exception as e:
                cprint(f"❌ Execute error: {e}", "red")
                results.append({"success": False, "error": str(e)})
        
        return results
    
    def on_order_filled(self, order_id: str, fill_data: Dict):
        """Handle order fills - place exit order when entry fills."""
        super().on_order_filled(order_id, fill_data)
        
        if order_id not in self.pending_orders:
            return
        
        order = self.pending_orders[order_id]
        
        # If this was a BUY fill, place the SELL exit order
        if fill_data.get("side") == "BUY":
            cprint(f"🎯 Buy filled @ ${fill_data.get('price'):.3f}, placing exit...", "yellow")
            
            # Update position tracking
            self.positions[order["token_id"]] = self.positions.get(order["token_id"], 0) + order["size"]
            
            # Return exit order details for order_manager to place
            return {
                "action": "place_exit",
                "token_id": order["token_id"],
                "side": "SELL",
                "price": order["exit_price"],
                "size": order["size"],
                "reason": "Spread exit order"
            }
        
        # If this was a SELL fill, position closed
        elif fill_data.get("side") == "SELL":
            entry = order.get("entry_price", 0)
            exit_price = fill_data.get("price", 0)
            profit = (exit_price - entry) * order["size"]
            
            cprint(f"💰 Spread captured! Profit: ${profit:.2f}", "green")
            
            # Update tracking
            self.positions[order["token_id"]] = max(0, self.positions.get(order["token_id"], 0) - order["size"])
            self.pnl += profit
            del self.pending_orders[order_id]
    
    def on_order_cancelled(self, order_id: str, reason: str):
        """Clean up cancelled orders."""
        super().on_order_cancelled(order_id, reason)
        
        if order_id in self.pending_orders:
            del self.pending_orders[order_id]
            cprint(f"🚫 Order {order_id} cancelled: {reason}", "yellow")
    
    def get_state(self) -> Dict[str, Any]:
        """Get strategy state including positions."""
        state = super().get_state()
        state.update({
            "positions": self.positions,
            "pending_orders_count": len(self.pending_orders),
            "min_spread_cents": self.min_spread_cents,
            "target_spread_cents": self.target_spread_cents,
            "vol_regime": self._vol_regime,
            "effective_min_spread": self._effective_min_spread,
            "native_engine": self._use_native,
            "sigma_cache_size": len(self._sigma_cache),
        })
        return state



