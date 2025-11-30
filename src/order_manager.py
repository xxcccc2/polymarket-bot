"""
Order Manager

Handles order lifecycle:
- Placing new orders (with validation)
- Tracking active orders
- Cancelling stale orders
- Managing order fills
"""

import time
import threading
from typing import Dict, List, Optional, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
from termcolor import cprint

from .config import (
    MAX_ACTIVE_ORDERS,
    ORDER_TIMEOUT_SECONDS,
    PAPER_TRADING,
)
from .client import PolymarketClient


class OrderStatus(Enum):
    """Order status enum."""
    PENDING = "pending"      # Submitted, awaiting confirmation
    OPEN = "open"            # Active in orderbook
    PARTIAL = "partial"      # Partially filled
    FILLED = "filled"        # Completely filled
    CANCELLED = "cancelled"  # Cancelled by user or system
    EXPIRED = "expired"      # Timed out
    REJECTED = "rejected"    # Rejected by exchange


@dataclass
class Order:
    """Represents an order."""
    order_id: str
    token_id: str
    market_slug: str
    side: str  # "BUY" or "SELL"
    price: float
    size: float
    filled_size: float = 0
    status: OrderStatus = OrderStatus.PENDING
    order_type: str = "GTC"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: Dict = field(default_factory=dict)
    
    @property
    def remaining_size(self) -> float:
        return self.size - self.filled_size
    
    @property
    def is_active(self) -> bool:
        return self.status in [OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIAL]
    
    @property
    def age_seconds(self) -> float:
        return (datetime.now() - self.created_at).total_seconds()
    
    def __str__(self):
        return (
            f"Order({self.order_id[:8]}... {self.side} {self.size:.2f} @ ${self.price:.3f} "
            f"[{self.status.value}] age={self.age_seconds:.0f}s)"
        )


class OrderManager:
    """
    Manages order lifecycle with tracking and automatic cleanup.
    
    Features:
    - Order placement with validation
    - Active order tracking
    - Automatic timeout/cancellation of stale orders
    - Fill callbacks for strategy notification
    """
    
    def __init__(self, client: PolymarketClient):
        self.client = client
        
        # Active orders by order_id
        self.orders: Dict[str, Order] = {}
        
        # Orders by token_id for quick lookup
        self.orders_by_token: Dict[str, List[str]] = {}  # token_id -> [order_ids]
        
        # Callbacks
        self._fill_callbacks: List[Callable[[Order, Dict], None]] = []
        self._cancel_callbacks: List[Callable[[Order, str], None]] = []
        
        # Stats
        self.total_orders_placed = 0
        self.total_orders_filled = 0
        self.total_orders_cancelled = 0
        
        # Cleanup thread
        self._cleanup_running = False
        self._cleanup_thread: Optional[threading.Thread] = None
    
    def on_fill(self, callback: Callable[[Order, Dict], None]):
        """Register callback for order fills."""
        self._fill_callbacks.append(callback)
    
    def on_cancel(self, callback: Callable[[Order, str], None]):
        """Register callback for order cancellations."""
        self._cancel_callbacks.append(callback)
    
    def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_type: str = "GTC",
        market_slug: str = "",
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Place a limit order with validation.
        
        Args:
            token_id: Token to trade
            side: "BUY" or "SELL"
            price: Limit price
            size: Order size
            order_type: "GTC" or "FOK"
            market_slug: Human-readable market name
            metadata: Additional data to store with order
            
        Returns:
            Dict with success status and order details
        """
        # Validate
        if len(self.get_active_orders()) >= MAX_ACTIVE_ORDERS:
            return {
                "success": False,
                "error": f"Max active orders ({MAX_ACTIVE_ORDERS}) reached"
            }
        
        if price <= 0 or price >= 1:
            return {
                "success": False,
                "error": f"Invalid price {price} - must be between 0 and 1"
            }
        
        if size <= 0:
            return {
                "success": False,
                "error": f"Invalid size {size} - must be positive"
            }
        
        # Place order via client
        result = self.client.place_order(
            token_id=token_id,
            side=side.upper(),
            price=price,
            size=size,
            order_type=order_type
        )
        
        if result.get("success"):
            order_id = result.get("order_id", f"unknown_{int(time.time()*1000)}")
            
            # Create order record
            order = Order(
                order_id=order_id,
                token_id=token_id,
                market_slug=market_slug,
                side=side.upper(),
                price=price,
                size=size,
                order_type=order_type,
                status=OrderStatus.OPEN,
                metadata=metadata or {}
            )
            
            # Store order
            self.orders[order_id] = order
            
            if token_id not in self.orders_by_token:
                self.orders_by_token[token_id] = []
            self.orders_by_token[token_id].append(order_id)
            
            self.total_orders_placed += 1
            
            cprint(f"📝 {order}", "cyan")
            
            result["order"] = order
        
        return result
    
    def cancel_order(self, order_id: str, reason: str = "User requested") -> Dict:
        """
        Cancel an order.
        
        Args:
            order_id: Order to cancel
            reason: Cancellation reason
            
        Returns:
            Cancellation result
        """
        order = self.orders.get(order_id)
        if not order:
            return {"success": False, "error": "Order not found"}
        
        if not order.is_active:
            return {"success": False, "error": f"Order not active: {order.status.value}"}
        
        # Cancel via client
        result = self.client.cancel_order(order_id)
        
        if result.get("success") or PAPER_TRADING:
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.now()
            self.total_orders_cancelled += 1
            
            # Notify callbacks
            for callback in self._cancel_callbacks:
                try:
                    callback(order, reason)
                except Exception as e:
                    cprint(f"❌ Cancel callback error: {e}", "red")
            
            cprint(f"🚫 Cancelled: {order} - {reason}", "yellow")
        
        return result
    
    def cancel_all_orders(self, reason: str = "Cancel all requested") -> int:
        """
        Cancel all active orders.
        
        Returns:
            Number of orders cancelled
        """
        cancelled = 0
        
        for order_id in list(self.orders.keys()):
            order = self.orders[order_id]
            if order.is_active:
                result = self.cancel_order(order_id, reason)
                if result.get("success"):
                    cancelled += 1
        
        return cancelled
    
    def cancel_orders_for_token(self, token_id: str, reason: str = "Token cleanup") -> int:
        """Cancel all orders for a specific token."""
        cancelled = 0
        
        order_ids = self.orders_by_token.get(token_id, [])
        for order_id in order_ids:
            order = self.orders.get(order_id)
            if order and order.is_active:
                result = self.cancel_order(order_id, reason)
                if result.get("success"):
                    cancelled += 1
        
        return cancelled
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        return self.orders.get(order_id)
    
    def get_active_orders(self) -> List[Order]:
        """Get all active orders."""
        return [o for o in self.orders.values() if o.is_active]
    
    def get_orders_for_token(self, token_id: str) -> List[Order]:
        """Get all orders for a specific token."""
        order_ids = self.orders_by_token.get(token_id, [])
        return [self.orders[oid] for oid in order_ids if oid in self.orders]
    
    def process_fill(self, order_id: str, fill_data: Dict):
        """
        Process an order fill.
        
        Args:
            order_id: Filled order ID
            fill_data: Fill details (price, size, etc.)
        """
        order = self.orders.get(order_id)
        if not order:
            cprint(f"⚠️ Fill for unknown order: {order_id}", "yellow")
            return
        
        fill_size = float(fill_data.get("size", 0))
        fill_price = float(fill_data.get("price", order.price))
        
        order.filled_size += fill_size
        order.updated_at = datetime.now()
        
        if order.filled_size >= order.size:
            order.status = OrderStatus.FILLED
            self.total_orders_filled += 1
            cprint(f"✅ FILLED: {order}", "green")
        else:
            order.status = OrderStatus.PARTIAL
            cprint(f"📊 Partial fill: {order}", "cyan")
        
        # Notify callbacks
        for callback in self._fill_callbacks:
            try:
                callback(order, fill_data)
            except Exception as e:
                cprint(f"❌ Fill callback error: {e}", "red")
    
    def _cleanup_stale_orders(self):
        """Cancel orders that have exceeded timeout."""
        now = datetime.now()
        timeout = timedelta(seconds=ORDER_TIMEOUT_SECONDS)
        
        for order_id, order in list(self.orders.items()):
            if order.is_active and (now - order.created_at) > timeout:
                self.cancel_order(order_id, f"Timeout ({ORDER_TIMEOUT_SECONDS}s)")
    
    def start_cleanup_loop(self, interval: int = 60):
        """
        Start background thread to cleanup stale orders.
        
        Args:
            interval: Seconds between cleanup checks
        """
        if self._cleanup_running:
            return
        
        self._cleanup_running = True
        
        def cleanup_loop():
            while self._cleanup_running:
                try:
                    self._cleanup_stale_orders()
                except Exception as e:
                    cprint(f"❌ Cleanup error: {e}", "red")
                time.sleep(interval)
        
        self._cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        self._cleanup_thread.start()
        cprint(f"🧹 Order cleanup started (every {interval}s)", "cyan")
    
    def stop_cleanup_loop(self):
        """Stop the cleanup background thread."""
        self._cleanup_running = False
    
    def get_stats(self) -> Dict:
        """Get order manager statistics."""
        active = self.get_active_orders()
        
        return {
            "total_orders": len(self.orders),
            "active_orders": len(active),
            "total_placed": self.total_orders_placed,
            "total_filled": self.total_orders_filled,
            "total_cancelled": self.total_orders_cancelled,
            "fill_rate": (
                self.total_orders_filled / self.total_orders_placed 
                if self.total_orders_placed > 0 else 0
            )
        }
    
    def sync_with_exchange(self):
        """
        Sync local order state with exchange.
        Call periodically to ensure consistency.
        """
        try:
            exchange_orders = self.client.get_open_orders()
            exchange_ids = {o.get("id") or o.get("orderID") for o in exchange_orders}
            
            # Mark orders as filled/cancelled if not on exchange
            for order_id, order in self.orders.items():
                if order.is_active and order_id not in exchange_ids:
                    # Order no longer on exchange - assume filled or cancelled
                    if not PAPER_TRADING:
                        order.status = OrderStatus.FILLED
                        order.updated_at = datetime.now()
                        cprint(f"🔄 Synced: {order} (removed from exchange)", "cyan")
            
        except Exception as e:
            cprint(f"❌ Sync error: {e}", "red")



