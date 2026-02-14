"""
Micro-Spread Farming Strategy

This strategy profits from small price movements by:
1. Identifying markets with wide bid-ask spreads
2. Placing limit buy orders at/near the bid
3. Placing limit sell orders at bid + spread target
4. Capturing the spread minus fees

Target: 1-2 cent spreads, executed hundreds of times daily.
Example: Buy at 5¢, sell at 6¢ = 20% return per trade.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from ..logging_utils import cprint

from .base_strategy import (
    BaseStrategy, 
    Signal, 
    SignalType, 
    MarketData
)
from ..config import (
    MIN_SPREAD_CENTS,
    ORDER_SIZE_USD,
    MAX_POSITION_USD,
    MIN_PRICE_CENTS,
    MAX_PRICE_CENTS,
    TRADING_FEE_RATE,
    MIN_PROFIT_MARGIN,
    CRYPTO_MARKET_KEYWORDS,
    ONLY_CRYPTO_MARKETS,
    VOL_HIGH_THRESHOLD,
    VOL_LOW_THRESHOLD,
    VOL_HIGH_SPREAD_MULT,
    VOL_LOW_SPREAD_MULT,
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
    description = "Micro-spread farming - buy low, sell high on small price movements"
    version = "1.0.0"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        
        # Strategy parameters (can be overridden via config)
        self.min_spread_cents = self.config.get("min_spread_cents", MIN_SPREAD_CENTS)
        self.target_spread_cents = self.config.get("target_spread_cents", self.min_spread_cents + 1)
        self.order_size_usd = self.config.get("order_size_usd", ORDER_SIZE_USD)
        self.max_position_usd = self.config.get("max_position_usd", MAX_POSITION_USD)
        self.price_improvement = self.config.get("price_improvement", 0)  # cents
        self.only_crypto = self.config.get("only_crypto", ONLY_CRYPTO_MARKETS)
        
        # Binance feed for volatility-regime awareness (optional)
        self.binance_feed = self.config.get("binance_feed")
        
        # Track active positions and orders per market
        self.positions: Dict[str, float] = {}  # token_id -> position size
        self.pending_orders: Dict[str, Dict] = {}  # order_id -> order details
        self.last_trade_time: Dict[str, datetime] = {}  # token_id -> last trade
        
        # Cooldown between trades on same market (seconds)
        self.trade_cooldown = self.config.get("trade_cooldown_seconds", 10)
        
        # Vol-regime state
        self._vol_regime = "normal"  # "low", "normal", "high"
        self._effective_min_spread = self.min_spread_cents
        
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
            is_crypto = any(kw in question_lower for kw in CRYPTO_MARKET_KEYWORDS)
            if not is_crypto:
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

    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        """
        Analyze markets and generate spread trading signals.
        
        For each market with sufficient spread:
        1. Generate BUY signal at best_bid (or slightly better)
        2. Pre-calculate exit SELL at target price
        """
        # Update vol regime before analysis
        self._update_vol_regime()

        signals = []
        
        # Debug: show best spread found
        if market_data:
            best = max(market_data, key=lambda x: x.spread_cents)
            cprint(
                f"      Best spread: {best.spread_cents:.2f}¢ @ {best.question[:35]}... (bid={best.best_bid:.3f}, ask={best.best_ask:.3f})",
                "white"
            )
        
        for data in market_data:
            # Apply market filter with debug logging
            if not self.should_trade_market(data):
                # Debug: Why was it filtered?
                mid_cents = data.mid_price * 100
                is_crypto = any(kw in data.question.lower() for kw in CRYPTO_MARKET_KEYWORDS)
                
                if not is_crypto and self.only_crypto:
                    pass  # Expected filter
                elif mid_cents < MIN_PRICE_CENTS or mid_cents > MAX_PRICE_CENTS:
                    cprint(f"      ⚠️ {data.question[:35]}... filtered: price {mid_cents:.1f}¢ out of range ({MIN_PRICE_CENTS}-{MAX_PRICE_CENTS})", "yellow")
                elif data.spread_cents < self.min_spread_cents:
                    pass  # Normal - spread too tight
                else:
                    cprint(f"      ⚠️ {data.question[:35]}... filtered: cooldown active", "yellow")
                continue
            
            # Check position limits (filled positions)
            current_position = self.positions.get(data.token_id, 0)
            if current_position >= self.max_position_usd:
                cprint(f"      ⚠️ {data.question[:35]}... filtered: position limit ${current_position:.0f} >= ${self.max_position_usd:.0f}", "yellow")
                continue
            
            # Check for pending orders on this market (prevent duplicate orders!)
            pending_value = sum(
                o["size"] * o["entry_price"] 
                for o in self.pending_orders.values() 
                if o.get("token_id") == data.token_id
            )
            total_exposure = current_position + pending_value
            if total_exposure >= self.max_position_usd:
                cprint(f"      ⚠️ {data.question[:35]}... filtered: pending orders ${pending_value:.0f} + position ${current_position:.0f} >= ${self.max_position_usd:.0f}", "yellow")
                continue
            
            # Calculate entry and exit prices
            # Buy at bid, sell at ask (capture the spread)
            entry_price = data.best_bid + (self.price_improvement / 100)
            exit_price = data.best_ask  # Target the ask price
            
            # Calculate expected profit after fees
            gross_profit_pct = (exit_price - entry_price) / entry_price if entry_price > 0 else 0
            net_profit_pct = gross_profit_pct - (2 * TRADING_FEE_RATE)  # Buy + sell fees
            
            # For paper trading, be more lenient with profit requirements
            min_profit = MIN_PROFIT_MARGIN
            if net_profit_pct < min_profit:
                # Log why it was skipped
                cprint(
                    f"      ⚠️ {data.question[:30]}... spread={data.spread_cents:.1f}¢ but profit={net_profit_pct*100:.1f}% < {min_profit*100:.1f}%",
                    "yellow"
                )
                continue
            
            # Debug: This market passed all filters!
            cprint(f"      ✅ {data.question[:35]}... PASSED ALL FILTERS! spread={data.spread_cents:.1f}¢, profit={net_profit_pct*100:.1f}%", "green")
            
            # Calculate position size
            remaining_capacity = self.max_position_usd - current_position
            size_usd = min(self.order_size_usd, remaining_capacity)
            size_shares = size_usd / entry_price
            
            # Generate BUY signal
            buy_signal = Signal(
                signal_type=SignalType.BUY,
                token_id=data.token_id,
                market_slug=data.market_slug,
                side=data.outcome,
                price=round(entry_price, 3),
                size=round(size_shares, 2),
                confidence=min(net_profit_pct * 10, 1.0),  # Higher profit = higher confidence
                reason=f"Spread opportunity: {data.spread_cents:.1f}¢ spread, "
                       f"expected {net_profit_pct*100:.1f}% net profit",
                metadata={
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "spread_cents": data.spread_cents,
                    "gross_profit_pct": gross_profit_pct,
                    "net_profit_pct": net_profit_pct,
                    "best_bid": data.best_bid,
                    "best_ask": data.best_ask,
                }
            )
            
            signals.append(buy_signal)
            self.signals_generated += 1
            
            cprint(f"📊 {buy_signal}", "cyan")
        
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
                    cprint(f"❌ Order failed: {order_result.get('error')}", "red")
                
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
        })
        return state



