"""
Late Money Strategy

Follows informed traders who tend to trade near market expiration.
Based on academic research showing 40% of volume occurs in the final minute,
and this "late money" is systematically more informed.

Key insights from Gramm & McKinney (2009) and Asch, Malkiel & Quandt (1982):
- Late price movements signal informed trader activity
- Sharp movements near expiration should be FOLLOWED, not faded
- 3-8% improved predictive accuracy over early prices

Strategy:
- Track price changes over time windows
- Detect significant price momentum (>X% in Y minutes)
- Generate signals to follow the momentum direction
- Weight signals by proximity to expiration
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from collections import defaultdict
from ..logging_utils import cprint

from .base_strategy import (
    BaseStrategy,
    Signal,
    SignalType,
    MarketData
)
from ..config import ORDER_SIZE_USD, TRADING_FEE_RATE


class LateMoneyStrategy(BaseStrategy):
    """
    Late Money Strategy - Follow informed traders near expiration.
    
    Tracks price momentum and generates signals when detecting
    significant directional movement, especially near market close.
    
    Configuration options:
        - momentum_threshold_pct: Min price change to trigger (default: 3%)
        - lookback_minutes: Time window to measure momentum (default: 30)
        - min_data_points: Minimum price samples needed (default: 5)
        - expiry_boost_hours: Hours before expiry to boost signal weight (default: 24)
        - order_size_usd: USD per trade (default: from config)
        - max_positions: Maximum concurrent positions (default: 10)
        - cooldown_minutes: Minutes between signals on same market (default: 60)
    """
    
    name = "late_money"
    description = "Follow informed late money - track price momentum near expiration"
    version = "1.0.0"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        
        # Momentum detection parameters
        self.momentum_threshold_pct = self.config.get("momentum_threshold_pct", 3.0)  # 3% move
        self.lookback_minutes = self.config.get("lookback_minutes", 30)
        self.min_data_points = self.config.get("min_data_points", 5)
        
        # Expiry weighting
        self.expiry_boost_hours = self.config.get("expiry_boost_hours", 24)
        
        # Trading parameters
        self.order_size_usd = self.config.get("order_size_usd", ORDER_SIZE_USD)
        self.max_positions = self.config.get("max_positions", 10)
        self.cooldown_minutes = self.config.get("cooldown_minutes", 60)
        
        # Price history cache: token_id -> [(timestamp, price), ...]
        self.price_history: Dict[str, List[tuple]] = defaultdict(list)
        
        # Track active positions
        self.positions: Dict[str, Dict] = {}
        
        # Last signal time per market
        self.last_signal_time: Dict[str, datetime] = {}
        
        # Stats
        self.momentum_signals = 0
        self.total_pnl = 0.0
    
    def _update_price_history(self, market_data: MarketData):
        """Record current price in history."""
        now = datetime.now()
        token_id = market_data.token_id
        
        # Add current price
        self.price_history[token_id].append((now, market_data.mid_price))
        
        # Prune old data (keep last 2 hours)
        cutoff = now - timedelta(hours=2)
        self.price_history[token_id] = [
            (ts, price) for ts, price in self.price_history[token_id]
            if ts > cutoff
        ]
    
    def _calculate_momentum(self, token_id: str) -> Optional[Dict]:
        """
        Calculate price momentum for a market.
        
        Returns:
            Dict with momentum metrics or None if insufficient data
        """
        history = self.price_history.get(token_id, [])
        
        if len(history) < self.min_data_points:
            return None
        
        now = datetime.now()
        lookback_cutoff = now - timedelta(minutes=self.lookback_minutes)
        
        # Get prices in lookback window
        recent_prices = [
            (ts, price) for ts, price in history
            if ts > lookback_cutoff
        ]
        
        if len(recent_prices) < 2:
            return None
        
        # Calculate momentum
        oldest_ts, oldest_price = recent_prices[0]
        newest_ts, newest_price = recent_prices[-1]
        
        if oldest_price == 0:
            return None
        
        price_change = newest_price - oldest_price
        price_change_pct = (price_change / oldest_price) * 100
        
        # Calculate velocity (change per minute)
        time_diff = (newest_ts - oldest_ts).total_seconds() / 60
        velocity = price_change_pct / time_diff if time_diff > 0 else 0
        
        # Determine direction
        if price_change_pct > 0:
            direction = "UP"
        elif price_change_pct < 0:
            direction = "DOWN"
        else:
            direction = "FLAT"
        
        return {
            "direction": direction,
            "price_change": price_change,
            "price_change_pct": price_change_pct,
            "velocity": velocity,  # % per minute
            "oldest_price": oldest_price,
            "newest_price": newest_price,
            "time_window_minutes": time_diff,
            "data_points": len(recent_prices),
        }
    
    def _is_significant_momentum(self, momentum: Dict) -> bool:
        """Check if momentum is significant enough to trade."""
        return abs(momentum["price_change_pct"]) >= self.momentum_threshold_pct
    
    def _check_cooldown(self, token_id: str) -> bool:
        """Check if market is in cooldown period."""
        if token_id not in self.last_signal_time:
            return False
        
        elapsed = (datetime.now() - self.last_signal_time[token_id]).seconds / 60
        return elapsed < self.cooldown_minutes
    
    def should_trade_market(self, market_data: MarketData) -> bool:
        """Filter markets for late money strategy."""
        # Check position limit
        if len(self.positions) >= self.max_positions:
            return False
        
        # Check if already positioned
        if market_data.token_id in self.positions:
            return False
        
        # Check cooldown
        if self._check_cooldown(market_data.token_id):
            return False
        
        # Need reasonable liquidity
        if market_data.volume_24h < 5000:  # $5k min volume
            return False
        
        return True
    
    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        """
        Analyze markets for late money momentum signals.
        
        Generates signals when detecting significant price momentum,
        following the direction of the move (not fading it).
        """
        signals = []
        momentum_found = 0
        
        for data in market_data:
            # Update price history
            self._update_price_history(data)
            
            # Apply filters
            if not self.should_trade_market(data):
                continue
            
            # Calculate momentum
            momentum = self._calculate_momentum(data.token_id)
            
            if not momentum:
                continue
            
            # Check if momentum is significant
            if not self._is_significant_momentum(momentum):
                continue
            
            momentum_found += 1
            
            # Determine trade direction (FOLLOW the momentum)
            if momentum["direction"] == "UP":
                signal_type = SignalType.BUY
                side = "YES"
                price = data.best_ask  # Buy at ask
                reason = f"Late money UP: {momentum['price_change_pct']:.1f}% in {momentum['time_window_minutes']:.0f}min"
            elif momentum["direction"] == "DOWN":
                # For DOWN momentum, we'd want to sell YES or buy NO
                # Since we primarily buy YES, we skip down momentum for now
                # (Could implement selling if we had positions to exit)
                cprint(
                    f"📉 LATE MONEY DOWN: {data.market_slug[:40]}... | "
                    f"{momentum['price_change_pct']:.1f}% | (no action - would need to sell)",
                    "yellow"
                )
                continue
            else:
                continue
            
            # Calculate confidence based on momentum strength
            confidence = min(abs(momentum["velocity"]) * 10, 0.95)
            
            # Calculate position size
            size_shares = self.order_size_usd / price if price > 0 else 0
            
            signal = Signal(
                signal_type=signal_type,
                token_id=data.token_id,
                market_slug=data.market_slug,
                side=side,
                price=price,
                size=size_shares,
                confidence=confidence,
                reason=reason,
                metadata={
                    "strategy": "late_money",
                    "momentum_pct": momentum["price_change_pct"],
                    "velocity": momentum["velocity"],
                    "direction": momentum["direction"],
                    "time_window": momentum["time_window_minutes"],
                    "data_points": momentum["data_points"],
                }
            )
            
            signals.append(signal)
            self.signals_generated += 1
            
            cprint(
                f"🚀 LATE MONEY: {data.market_slug[:40]}... | "
                f"{momentum['direction']} {abs(momentum['price_change_pct']):.1f}% in {momentum['time_window_minutes']:.0f}min | "
                f"Velocity: {momentum['velocity']:.2f}%/min",
                "magenta", attrs=["bold"]
            )
        
        # Summary logging
        if momentum_found > 0:
            cprint(f"   📊 Detected {momentum_found} momentum moves, {len(signals)} tradeable", "cyan")
        
        return signals
    
    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        """Execute late money trades."""
        results = []
        
        for signal in signals:
            if signal.signal_type != SignalType.BUY:
                continue
            
            try:
                # Place order following momentum
                order_result = order_manager.place_limit_order(
                    token_id=signal.token_id,
                    side="BUY",
                    price=signal.price,
                    size=signal.size,
                    order_type="GTC",
                    market_slug=signal.market_slug,
                    metadata={
                        "strategy": "late_money",
                        "momentum_pct": signal.metadata.get("momentum_pct"),
                        "direction": signal.metadata.get("direction"),
                    }
                )
                
                if order_result.get("success"):
                    order_id = order_result.get("order_id")
                    
                    # Track position
                    self.positions[signal.token_id] = {
                        "order_id": order_id,
                        "market_slug": signal.market_slug,
                        "entry_price": signal.price,
                        "size": signal.size,
                        "momentum_pct": signal.metadata.get("momentum_pct"),
                        "entered_at": datetime.now()
                    }
                    
                    # Update cooldown
                    self.last_signal_time[signal.token_id] = datetime.now()
                    self.momentum_signals += 1
                    
                    cprint(
                        f"✅ Late money position: {signal.market_slug[:40]}... @ {signal.price*100:.1f}¢",
                        "green"
                    )
                else:
                    cprint(f"❌ Order failed: {order_result.get('error')}", "red")
                
                results.append(order_result)
                
            except Exception as e:
                cprint(f"❌ Execute error: {e}", "red")
                results.append({"success": False, "error": str(e)})
        
        return results
    
    def on_order_filled(self, order_id: str, fill_data: Dict):
        """Track fills."""
        super().on_order_filled(order_id, fill_data)
        
        for token_id, pos in self.positions.items():
            if pos.get("order_id") == order_id:
                pos["filled"] = True
                pos["fill_price"] = float(fill_data.get("price", pos["entry_price"]))
                pos["filled_at"] = datetime.now()
                
                cprint(
                    f"✅ Late money filled: {pos['market_slug'][:40]}... @ {pos['fill_price']*100:.1f}¢",
                    "green"
                )
                break
    
    def on_order_cancelled(self, order_id: str, reason: str):
        """Clean up cancelled orders."""
        super().on_order_cancelled(order_id, reason)
        
        for token_id, pos in list(self.positions.items()):
            if pos.get("order_id") == order_id:
                del self.positions[token_id]
                break
    
    def get_state(self) -> Dict[str, Any]:
        """Get strategy state."""
        state = super().get_state()
        state.update({
            "positions": len(self.positions),
            "momentum_signals": self.momentum_signals,
            "tracked_markets": len(self.price_history),
            "momentum_threshold": f"{self.momentum_threshold_pct}%",
            "lookback_minutes": self.lookback_minutes,
            "total_pnl": self.total_pnl,
        })
        return state
    
    def print_status(self):
        """Print current strategy status."""
        cprint("\n" + "="*55, "magenta")
        cprint("🚀 Late Money Strategy Status", "magenta", attrs=["bold"])
        cprint("="*55, "magenta")
        
        cprint(f"  Active Positions: {len(self.positions)}/{self.max_positions}", "white")
        cprint(f"  Momentum Signals: {self.momentum_signals}", "white")
        cprint(f"  Tracking Markets: {len(self.price_history)}", "white")
        cprint(f"  Total PnL: ${self.total_pnl:.2f}", "green" if self.total_pnl >= 0 else "red")
        
        cprint(f"\n  Settings:", "cyan")
        cprint(f"    Momentum Threshold: {self.momentum_threshold_pct}%", "white")
        cprint(f"    Lookback Window: {self.lookback_minutes} minutes", "white")
        cprint(f"    Cooldown: {self.cooldown_minutes} minutes", "white")
        
        if self.positions:
            cprint(f"\n  Active Positions:", "cyan")
            for token_id, pos in list(self.positions.items())[:5]:
                status = "✅" if pos.get("filled") else "⏳"
                momentum = pos.get("momentum_pct", 0)
                cprint(
                    f"    {status} {pos['market_slug'][:35]}... | "
                    f"Momentum: {momentum:+.1f}%",
                    "white"
                )
        
        cprint("="*55 + "\n", "magenta")

