"""
Stink Bid Strategy

Places extremely low (1¢) limit orders on markets with thin orderbooks,
waiting for someone to accidentally nuke the book with a market sell.

Concept:
- Find markets with high volume but thin liquidity
- Place 1¢ bids that sit waiting
- When panic sellers or fat-finger trades wipe the book, you get filled
- $10 bet at 1¢ = $1000 potential payout (100x)

This is an asymmetric bet strategy - small losses, huge potential wins.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from termcolor import cprint

from .base_strategy import (
    BaseStrategy,
    Signal,
    SignalType,
    MarketData
)
from ..config import ORDER_SIZE_USD


class StinkBidStrategy(BaseStrategy):
    """
    Stink Bid Strategy - Place 1¢ orders waiting for orderbook nukes.
    
    Configuration options:
        - bid_price: Price to bid at (default: 0.01 = 1¢)
        - min_volume_24h: Minimum 24h volume to consider (default: 50000)
        - max_depth_ratio: Max orderbook depth as % of volume (default: 0.05 = 5%)
        - order_size_usd: USD per stink bid (default: 10)
        - max_bids_per_market: Max concurrent bids per market (default: 1)
        - max_total_bids: Max total stink bids active (default: 20)
        - refresh_hours: Hours before refreshing/replacing bid (default: 168 = 7 days)
    """
    
    name = "stink_bid"
    description = "Place 1¢ bids on thin orderbooks waiting for 100x opportunities"
    version = "1.0.0"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        
        # Strategy parameters
        self.bid_price = self.config.get("bid_price", 0.01)  # 1 cent
        self.min_volume_24h = self.config.get("min_volume_24h", 50000)  # $50k volume
        self.max_depth_ratio = self.config.get("max_depth_ratio", 0.05)  # 5% of volume
        self.order_size_usd = self.config.get("order_size_usd", 10)  # $10 per bid
        self.max_bids_per_market = self.config.get("max_bids_per_market", 1)
        self.max_total_bids = self.config.get("max_total_bids", 20)
        self.refresh_hours = self.config.get("refresh_hours", 168)  # 7 days
        
        # Track active stink bids
        self.active_bids: Dict[str, Dict] = {}  # token_id -> bid info
        self.filled_bids: List[Dict] = []  # History of filled bids
        
        # Stats
        self.opportunities_found = 0
        self.bids_placed = 0
        self.bids_filled = 0
        self.total_profit = 0.0
    
    def should_trade_market(self, market_data: MarketData) -> bool:
        """
        Filter markets suitable for stink bidding.
        
        Good candidates have:
        - High volume (activity)
        - Thin orderbook (vulnerable to nukes)
        - Not too close to resolution
        """
        # Check volume threshold
        if market_data.volume_24h < self.min_volume_24h:
            return False
        
        # Check we don't already have max bids on this market
        active_for_market = sum(
            1 for bid in self.active_bids.values()
            if bid.get("token_id") == market_data.token_id
        )
        if active_for_market >= self.max_bids_per_market:
            return False
        
        # Check total bid limit
        if len(self.active_bids) >= self.max_total_bids:
            return False
        
        return True
    
    def _calculate_depth_ratio(self, market_data: MarketData) -> float:
        """
        Calculate orderbook depth as ratio of 24h volume.
        
        Lower ratio = thinner book = better opportunity.
        """
        if not market_data.orderbook or market_data.volume_24h == 0:
            return 1.0  # Assume thick if no data
        
        # Sum up bid-side liquidity
        bids = market_data.orderbook.get("bids", [])
        total_bid_depth = sum(
            float(b.get("size", 0)) * float(b.get("price", 0))
            for b in bids
        )
        
        return total_bid_depth / market_data.volume_24h
    
    def _is_thin_orderbook(self, market_data: MarketData) -> tuple[bool, float]:
        """
        Check if orderbook is thin enough for stink bidding.
        
        Returns:
            (is_thin: bool, depth_ratio: float)
        """
        depth_ratio = self._calculate_depth_ratio(market_data)
        is_thin = depth_ratio <= self.max_depth_ratio
        return is_thin, depth_ratio
    
    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        """
        Find thin orderbook markets and generate stink bid signals.
        """
        signals = []
        
        for data in market_data:
            # Apply filters
            if not self.should_trade_market(data):
                continue
            
            # Check orderbook thinness
            is_thin, depth_ratio = self._is_thin_orderbook(data)
            
            if not is_thin:
                continue
            
            self.opportunities_found += 1
            
            # Calculate potential return
            # If we buy at 1¢ and it resolves to $1, that's 100x
            potential_multiplier = 1.0 / self.bid_price
            
            # Calculate size in shares
            size_shares = self.order_size_usd / self.bid_price
            
            signal = Signal(
                signal_type=SignalType.BUY,
                token_id=data.token_id,
                market_slug=data.market_slug,
                side=data.outcome,
                price=self.bid_price,
                size=size_shares,
                confidence=min(0.3 + (1 - depth_ratio) * 0.5, 0.8),  # Higher confidence for thinner books
                reason=f"Thin orderbook: {depth_ratio*100:.1f}% depth ratio, {potential_multiplier:.0f}x potential",
                metadata={
                    "strategy": "stink_bid",
                    "depth_ratio": depth_ratio,
                    "volume_24h": data.volume_24h,
                    "potential_multiplier": potential_multiplier,
                    "current_best_bid": data.best_bid,
                    "current_best_ask": data.best_ask,
                }
            )
            
            signals.append(signal)
            self.signals_generated += 1
            
            cprint(
                f"🎯 Stink bid opportunity: {data.market_slug[:50]} | "
                f"Depth: {depth_ratio*100:.1f}% | Potential: {potential_multiplier:.0f}x",
                "magenta"
            )
        
        return signals
    
    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        """
        Place stink bid orders.
        """
        results = []
        
        for signal in signals:
            if signal.signal_type != SignalType.BUY:
                continue
            
            # Double-check we're not over limit
            if len(self.active_bids) >= self.max_total_bids:
                cprint(f"⚠️ Max stink bids ({self.max_total_bids}) reached", "yellow")
                break
            
            try:
                # Place the stink bid
                order_result = order_manager.place_limit_order(
                    token_id=signal.token_id,
                    side="BUY",
                    price=signal.price,
                    size=signal.size,
                    order_type="GTC",  # Good till cancelled
                    market_slug=signal.market_slug,
                    metadata={
                        "strategy": "stink_bid",
                        "potential_multiplier": signal.metadata.get("potential_multiplier"),
                    }
                )
                
                if order_result.get("success"):
                    order_id = order_result.get("order_id")
                    
                    # Track this stink bid
                    self.active_bids[order_id] = {
                        "token_id": signal.token_id,
                        "market_slug": signal.market_slug,
                        "price": signal.price,
                        "size": signal.size,
                        "potential_multiplier": signal.metadata.get("potential_multiplier"),
                        "placed_at": datetime.now(),
                        "expires_at": datetime.now() + timedelta(hours=self.refresh_hours)
                    }
                    
                    self.bids_placed += 1
                    
                    cprint(
                        f"💰 Stink bid placed: ${self.order_size_usd} @ {self.bid_price*100:.0f}¢ | "
                        f"{signal.market_slug[:40]} | Potential: {signal.metadata.get('potential_multiplier'):.0f}x",
                        "green"
                    )
                else:
                    cprint(f"❌ Stink bid failed: {order_result.get('error')}", "red")
                
                results.append(order_result)
                
            except Exception as e:
                cprint(f"❌ Execute error: {e}", "red")
                results.append({"success": False, "error": str(e)})
        
        return results
    
    def on_order_filled(self, order_id: str, fill_data: Dict):
        """Handle stink bid fills - THIS IS THE JACKPOT!"""
        super().on_order_filled(order_id, fill_data)
        
        if order_id not in self.active_bids:
            return
        
        bid = self.active_bids[order_id]
        fill_price = float(fill_data.get("price", bid["price"]))
        fill_size = float(fill_data.get("size", bid["size"]))
        
        # Calculate profit (assuming it resolves to $1)
        cost = fill_price * fill_size
        potential_value = fill_size  # $1 per share if YES wins
        potential_profit = potential_value - cost
        multiplier = potential_value / cost if cost > 0 else 0
        
        self.bids_filled += 1
        
        # Record the fill
        self.filled_bids.append({
            "order_id": order_id,
            "market_slug": bid["market_slug"],
            "fill_price": fill_price,
            "size": fill_size,
            "cost": cost,
            "potential_profit": potential_profit,
            "multiplier": multiplier,
            "filled_at": datetime.now()
        })
        
        # Remove from active
        del self.active_bids[order_id]
        
        # ALERT! This is a big deal!
        cprint("\n" + "🎰" * 20, "green")
        cprint(f"💎 STINK BID FILLED! 💎", "green", attrs=["bold"])
        cprint(f"   Market: {bid['market_slug']}", "white")
        cprint(f"   Fill Price: {fill_price*100:.1f}¢", "white")
        cprint(f"   Cost: ${cost:.2f}", "white")
        cprint(f"   Potential Value: ${potential_value:.2f}", "cyan")
        cprint(f"   Potential Profit: ${potential_profit:.2f} ({multiplier:.0f}x)", "green", attrs=["bold"])
        cprint("🎰" * 20 + "\n", "green")
        
        # TODO: Send Telegram/Discord alert here
        
        return None  # No exit order needed - hold until resolution
    
    def on_order_cancelled(self, order_id: str, reason: str):
        """Clean up cancelled stink bids."""
        super().on_order_cancelled(order_id, reason)
        
        if order_id in self.active_bids:
            del self.active_bids[order_id]
    
    def refresh_expired_bids(self, order_manager) -> int:
        """
        Cancel and replace expired stink bids.
        Call this periodically to refresh old bids.
        
        Returns:
            Number of bids refreshed
        """
        refreshed = 0
        now = datetime.now()
        
        for order_id, bid in list(self.active_bids.items()):
            if bid.get("expires_at") and now > bid["expires_at"]:
                # Cancel old bid
                order_manager.cancel_order(order_id, "Stink bid refresh")
                refreshed += 1
                
                cprint(f"🔄 Refreshing stink bid: {bid['market_slug'][:40]}", "cyan")
        
        return refreshed
    
    def get_state(self) -> Dict[str, Any]:
        """Get strategy state including active stink bids."""
        state = super().get_state()
        state.update({
            "active_bids": len(self.active_bids),
            "bids_placed": self.bids_placed,
            "bids_filled": self.bids_filled,
            "opportunities_found": self.opportunities_found,
            "bid_price": self.bid_price,
            "max_total_bids": self.max_total_bids,
            "filled_history": self.filled_bids[-10:],  # Last 10 fills
        })
        return state
    
    def print_status(self):
        """Print current stink bid status."""
        cprint("\n" + "="*50, "magenta")
        cprint("🎯 Stink Bid Strategy Status", "magenta", attrs=["bold"])
        cprint("="*50, "magenta")
        
        cprint(f"  Active Bids: {len(self.active_bids)}/{self.max_total_bids}", "white")
        cprint(f"  Total Placed: {self.bids_placed}", "white")
        cprint(f"  Total Filled: {self.bids_filled}", "green" if self.bids_filled > 0 else "white")
        cprint(f"  Opportunities Found: {self.opportunities_found}", "white")
        
        if self.active_bids:
            cprint("\n  Active Stink Bids:", "cyan")
            for order_id, bid in list(self.active_bids.items())[:5]:
                expires_in = (bid["expires_at"] - datetime.now()).days if bid.get("expires_at") else "?"
                cprint(
                    f"    • {bid['market_slug'][:35]}... | "
                    f"{bid['potential_multiplier']:.0f}x potential | "
                    f"expires in {expires_in}d",
                    "white"
                )
        
        if self.filled_bids:
            cprint("\n  💎 Recent Fills:", "green")
            for fill in self.filled_bids[-3:]:
                cprint(
                    f"    • {fill['market_slug'][:35]}... | "
                    f"{fill['multiplier']:.0f}x | "
                    f"${fill['potential_profit']:.2f} potential",
                    "green"
                )
        
        cprint("="*50 + "\n", "magenta")

