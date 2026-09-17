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

from .logging_utils import cprint

from .config import (
    MAX_ACTIVE_ORDERS,
    ORDER_TIMEOUT_SECONDS,
    PAPER_IMMEDIATE_FILL,
    PAPER_TRADING,
)
from .client import PolymarketClient
from .persistence import SqliteStore


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
    
    def __init__(self, client: PolymarketClient, store: Optional[SqliteStore] = None):
        self.client = client
        self.store = store
        
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
        self.total_fill_events = 0  # every process_fill call (includes partials)
        
        # Cleanup thread
        self._cleanup_running = False
        self._cleanup_thread: Optional[threading.Thread] = None

        # Load persisted orders if available
        if self.store:
            self._load_persisted_orders()

    def _parse_datetime(self, value: Optional[str]) -> datetime:
        if not value:
            return datetime.now()
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.now()

    def _order_to_record(self, order: Order) -> Dict:
        return {
            "order_id": order.order_id,
            "token_id": order.token_id,
            "market_slug": order.market_slug,
            "side": order.side,
            "price": order.price,
            "size": order.size,
            "filled_size": order.filled_size,
            "status": order.status.value,
            "order_type": order.order_type,
            "created_at": order.created_at.isoformat(),
            "updated_at": order.updated_at.isoformat(),
            "metadata": order.metadata,
        }

    def _persist_order(self, order: Order) -> None:
        if not self.store:
            return
        try:
            self.store.save_order(self._order_to_record(order))
        except Exception as e:
            cprint(f"❌ Failed to persist order {order.order_id}: {e}", "red")

    def _load_persisted_orders(self) -> None:
        try:
            records = self.store.load_orders()
        except Exception as e:
            cprint(f"❌ Failed to load persisted orders: {e}", "red")
            return

        for record in records:
            try:
                order = Order(
                    order_id=record["order_id"],
                    token_id=record["token_id"],
                    market_slug=record.get("market_slug") or "",
                    side=record["side"],
                    price=float(record["price"]),
                    size=float(record["size"]),
                    filled_size=float(record.get("filled_size") or 0),
                    status=OrderStatus(record.get("status", OrderStatus.OPEN.value)),
                    order_type=record.get("order_type") or "GTC",
                    created_at=self._parse_datetime(record.get("created_at")),
                    updated_at=self._parse_datetime(record.get("updated_at")),
                    metadata=record.get("metadata") or {},
                )
            except Exception as e:
                cprint(f"❌ Failed to load persisted order: {e}", "red")
                continue

            self.orders[order.order_id] = order
            self.orders_by_token.setdefault(order.token_id, []).append(order.order_id)

        if self.orders:
            self.total_orders_placed = len(self.orders)
            self.total_orders_filled = sum(1 for o in self.orders.values() if o.status == OrderStatus.FILLED)
            self.total_orders_cancelled = sum(1 for o in self.orders.values() if o.status == OrderStatus.CANCELLED)
            cprint(f"📦 Loaded {len(self.orders)} persisted orders", "cyan")

    @staticmethod
    def _norm_id(value) -> str:
        if not value:
            return ""
        return str(value).lower().replace("0x", "").strip()

    def _trade_matches_order(self, order: Order, trade: Dict) -> bool:
        token_id = trade.get("asset_id") or trade.get("token_id") or trade.get("asset")
        side = (trade.get("side") or "").upper()
        try:
            price = float(trade.get("price", 0) or 0)
        except (TypeError, ValueError):
            price = 0.0
        try:
            size = float(trade.get("size", 0) or 0)
        except (TypeError, ValueError):
            size = 0.0

        if not token_id or not side or price <= 0 or size <= 0:
            return False

        if self._norm_id(order.token_id) != self._norm_id(token_id):
            return False
        if (order.side or "").upper() != side:
            return False
        if abs(order.price - price) >= 0.02:
            return False

        remaining = max(order.remaining_size, 0.0)
        if remaining > 0 and abs(remaining - size) < 0.5:
            return True
        if abs(order.size - size) < 0.5:
            return True
        if size <= remaining + 0.01:
            return True
        return False

    def _best_trade_for_order(self, order: Order, trades: List[Dict]) -> Optional[Dict]:
        candidates = [trade for trade in trades if self._trade_matches_order(order, trade)]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda trade: abs(float(trade.get("size", 0) or 0) - max(order.remaining_size, 0.0))
            + abs(float(trade.get("price", 0) or 0) - order.price) * 10,
        )
    
    def on_fill(self, callback: Callable[[Order, Dict], None]):
        """Register callback for order fills."""
        self._fill_callbacks.append(callback)
    
    def on_cancel(self, callback: Callable[[Order, str], None]):
        """Register callback for order cancellations."""
        self._cancel_callbacks.append(callback)

    def _notify_cancel(self, order: Order, reason: str) -> None:
        for callback in self._cancel_callbacks:
            try:
                callback(order, reason)
            except Exception as e:
                cprint(f"❌ Cancel callback error: {e}", "red")
    
    def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_type: str = "GTC",
        market_slug: str = "",
        metadata: Optional[Dict] = None,
        expiration: Optional[int] = None,
        post_only: bool = False,
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
        
        # PREVENT DUPLICATE ORDERS: Check if we already have an active order for this token
        existing_orders = self.get_orders_for_token(token_id)
        active_for_token = [o for o in existing_orders if o.is_active and o.side == side.upper()]
        if active_for_token:
            return {
                "success": False,
                "error": f"Already have {len(active_for_token)} active {side} order(s) for this token"
            }
        
        metadata = dict(metadata or {})
        if "outcome_side" not in metadata:
            metadata["outcome_side"] = metadata.get("side")
        metadata.setdefault("post_only", bool(post_only))
        metadata.setdefault("expiration", expiration)
        metadata.setdefault("order_type", order_type)

        # Place order via client
        try:
            result = self.client.place_order(
                token_id=token_id,
                side=side.upper(),
                price=price,
                size=size,
                order_type=order_type,
                expiration=expiration,
                post_only=post_only,
            )
        except TypeError:
            # Backward-compatible path for mocks/older client signatures.
            result = self.client.place_order(
                token_id=token_id,
                side=side.upper(),
                price=price,
                size=size,
                order_type=order_type,
            )
        
        if result.get("success"):
            order_id = result.get("order_id", f"unknown_{int(time.time()*1000)}")
            metadata.setdefault("submitted_expiration", expiration)
            metadata.setdefault("submitted_order_type", order_type)
            if result.get("result") is not None:
                metadata["placement_result"] = result.get("result")
            
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
                metadata=metadata
            )
            
            # Store order
            self.orders[order_id] = order
            
            if token_id not in self.orders_by_token:
                self.orders_by_token[token_id] = []
            self.orders_by_token[token_id].append(order_id)
            
            self.total_orders_placed += 1
            
            cprint(f"📝 {order}", "cyan")
            
            result["order"] = order
            self._persist_order(order)
            
            # Paper trading: simulate immediate fill
            if PAPER_TRADING and PAPER_IMMEDIATE_FILL:
                self.process_fill(order_id, {
                    "price": price,
                    "size": size,
                    "trade_id": f"paper_fill_{int(time.time()*1000)}",
                    "side": side.upper(),
                    "token_id": token_id,
                })
        
        return result

    def place_limit_orders_batch(self, orders: List[Dict]) -> List[Dict]:
        """
        Place multiple limit orders in a single request when supported.

        Each order dict supports:
          token_id, side, price, size, order_type, market_slug, metadata
        """
        if not orders:
            return []

        results: List[Dict] = []
        valid_orders: List[Dict] = []
        valid_indices: List[int] = []
        staged_by_token_side: set[tuple[str, str]] = set()

        current_active_count = len(self.get_active_orders())
        allowed_new = max(0, MAX_ACTIVE_ORDERS - current_active_count)

        for idx, req in enumerate(orders):
            token_id = req.get("token_id")
            side = (req.get("side") or "").upper()
            price = float(req.get("price", 0))
            size = float(req.get("size", 0))
            order_type = req.get("order_type", "GTC")

            if not token_id:
                results.append({"success": False, "error": "Missing token_id"})
                continue
            if side not in {"BUY", "SELL"}:
                results.append({"success": False, "error": f"Invalid side {side}"})
                continue
            if price <= 0 or price >= 1:
                results.append({"success": False, "error": f"Invalid price {price} - must be between 0 and 1"})
                continue
            if size <= 0:
                results.append({"success": False, "error": f"Invalid size {size} - must be positive"})
                continue
            if len(valid_orders) >= allowed_new:
                results.append({"success": False, "error": f"Max active orders ({MAX_ACTIVE_ORDERS}) reached"})
                continue

            existing_orders = self.get_orders_for_token(token_id)
            active_for_token = [o for o in existing_orders if o.is_active and o.side == side]
            if active_for_token:
                results.append(
                    {
                        "success": False,
                        "error": f"Already have {len(active_for_token)} active {side} order(s) for this token",
                    }
                )
                continue

            staged_key = (token_id, side)
            if staged_key in staged_by_token_side:
                results.append(
                    {
                        "success": False,
                        "error": f"Duplicate {side} order in batch for token {token_id}",
                    }
                )
                continue

            staged_by_token_side.add(staged_key)
            valid_orders.append(
                {
                    "token_id": token_id,
                    "side": side,
                    "price": price,
                    "size": size,
                    "order_type": order_type,
                    "market_slug": req.get("market_slug", ""),
                    "metadata": req.get("metadata") or {},
                }
            )
            valid_indices.append(idx)
            results.append({"success": None})  # placeholder to preserve index order

        if not valid_orders:
            return results

        batch_results = self.client.place_orders_batch(valid_orders)
        for i, idx in enumerate(valid_indices):
            result = batch_results[i] if i < len(batch_results) else {"success": False, "error": "Missing batch result"}
            req = valid_orders[i]

            if result.get("success"):
                order_id = result.get("order_id", f"unknown_{int(time.time()*1000)}_{i}")
                order = Order(
                    order_id=order_id,
                    token_id=req["token_id"],
                    market_slug=req["market_slug"],
                    side=req["side"],
                    price=req["price"],
                    size=req["size"],
                    order_type=req["order_type"],
                    status=OrderStatus.OPEN,
                    metadata=req["metadata"],
                )
                self.orders[order_id] = order
                self.orders_by_token.setdefault(req["token_id"], []).append(order_id)
                self.total_orders_placed += 1
                cprint(f"📝 {order}", "cyan")
                result["order"] = order
                self._persist_order(order)

                if PAPER_TRADING and PAPER_IMMEDIATE_FILL:
                    self.process_fill(
                        order_id,
                        {
                            "price": req["price"],
                            "size": req["size"],
                            "trade_id": f"paper_fill_{int(time.time()*1000)}",
                            "side": req["side"],
                            "token_id": req["token_id"],
                        },
                    )

            results[idx] = result

        return results
    
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
            if order.metadata is None:
                order.metadata = {}
            order.metadata["cancel_reason"] = reason
            self.total_orders_cancelled += 1
            
            self._notify_cancel(order, reason)
            
            cprint(f"🚫 Cancelled: {order} - {reason}", "yellow")
            self._persist_order(order)
        
        return result

    def mark_order_cancelled(self, order_id: str, reason: str = "Exchange reported cancellation") -> Dict:
        """Mark an order cancelled locally without sending another exchange request."""
        order = self.orders.get(order_id)
        if not order:
            return {"success": False, "error": f"Unknown order: {order_id}"}
        if not order.is_active:
            return {"success": False, "error": f"Order not active: {order.status.value}"}
        order.status = OrderStatus.CANCELLED
        order.updated_at = datetime.now()
        if order.metadata is None:
            order.metadata = {}
        order.metadata["cancel_reason"] = reason
        self.total_orders_cancelled += 1
        self._persist_order(order)
        self._notify_cancel(order, reason)
        cprint(f"🚫 Cancelled: {order} - {reason}", "yellow")
        return {"success": True}
    
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

        try:
            fill_size = float(fill_data.get("size", 0) or 0)
        except (TypeError, ValueError):
            fill_size = 0.0
        try:
            fill_price = float(fill_data.get("price", order.price) or order.price)
        except (TypeError, ValueError):
            fill_price = order.price
        metadata = order.metadata if isinstance(order.metadata, dict) else {}
        try:
            provisional_matched = float(metadata.get("user_ws_size_matched", 0.0) or 0.0)
            provisional_consumed = float(metadata.get("user_ws_size_matched_consumed", 0.0) or 0.0)
        except (TypeError, ValueError):
            provisional_matched = 0.0
            provisional_consumed = 0.0
        provisional_available = max(0.0, provisional_matched - provisional_consumed)
        if provisional_available > 0 and fill_size > 0:
            provisional_reversal = min(fill_size, provisional_available)
            order.filled_size = max(0.0, order.filled_size - provisional_reversal)
            metadata["user_ws_size_matched_consumed"] = provisional_consumed + provisional_reversal
            order.metadata = metadata
        remaining_before = max(order.remaining_size, 0.0)
        if remaining_before <= 0:
            return
        if fill_size <= 0:
            return
        fill_size = min(fill_size, remaining_before)
        fill_data = dict(fill_data)
        fill_data["size"] = fill_size
        fill_data["price"] = fill_price
        self.total_fill_events += 1
        
        order.filled_size += fill_size
        order.updated_at = datetime.now()
        
        if order.filled_size >= order.size:
            order.status = OrderStatus.FILLED
            self.total_orders_filled += 1
            cprint(f"✅ FILLED: {order}", "green")
        else:
            order.status = OrderStatus.PARTIAL
            cprint(f"📊 Partial fill: {order}", "cyan")

        self._persist_order(order)
        
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
            "total_fill_events": self.total_fill_events,
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
        
        Uses heuristic: if an order disappeared from the exchange and
        we see a matching trade, it was filled. Otherwise, assume cancelled.
        """
        try:
            exchange_orders = self.client.get_open_orders()

            exchange_ids = set()
            for o in exchange_orders:
                for k in ("id", "orderID", "order_id"):
                    v = o.get(k)
                    if v:
                        exchange_ids.add(str(v))
                        exchange_ids.add(self._norm_id(v))
            
            # Also fetch recent trades to distinguish fills from cancels
            recent_trades = []
            try:
                recent_trades = self.client.get_trades(limit=100)
            except Exception:
                pass
            def _trade_order_ids(t: dict) -> set:
                ids = set()
                for k in ("order_id", "orderID", "maker_order_id", "makerOrderId", "taker_order_id", "takerOrderId"):
                    v = t.get(k)
                    if v:
                        ids.add(str(v))
                        ids.add(str(v).lower().replace("0x", ""))
                return ids

            recent_trade_order_ids = set()
            for t in recent_trades:
                recent_trade_order_ids |= _trade_order_ids(t)

            # Mark orders as filled/cancelled if not on exchange
            for order_id, order in self.orders.items():
                norm = self._norm_id(order_id)
                on_exchange = order_id in exchange_ids or norm in exchange_ids
                if order.is_active and not on_exchange:
                    if PAPER_TRADING:
                        continue

                    has_fill = order_id in recent_trade_order_ids or norm in recent_trade_order_ids
                    matched_trade = None if has_fill else self._best_trade_for_order(order, recent_trades)
                    if has_fill:
                        self.process_fill(order_id, {
                            "trade_id": f"sync_{order_id}",
                            "side": order.side,
                            "price": order.price,
                            "size": order.size,
                            "token_id": order.token_id,
                        })
                        cprint(f"🔄 Synced FILL: {order}", "green")
                    elif matched_trade:
                        fill_size = float(matched_trade.get("size", 0) or 0)
                        fill_price = float(matched_trade.get("price", order.price) or order.price)
                        trade_id = matched_trade.get("id") or matched_trade.get("trade_id") or f"sync_{order_id}"
                        self.process_fill(order_id, {
                            "trade_id": trade_id,
                            "side": (matched_trade.get("side") or order.side),
                            "price": fill_price,
                            "size": fill_size,
                            "token_id": matched_trade.get("asset_id") or matched_trade.get("token_id") or matched_trade.get("asset") or order.token_id,
                        })
                        cprint(f"🔄 Synced FILL (heuristic): {order}", "green")
                    else:
                        # No matching trade → likely cancelled by exchange.
                        self.mark_order_cancelled(order_id, "Exchange sync: removed from open orders")
            
        except Exception as e:
            cprint(f"❌ Sync error: {e}", "red")

    def cancel_expired_market_orders(self, expired_condition_ids: set) -> int:
        """
        Cancel all active orders for markets that have expired/resolved.
        
        Args:
            expired_condition_ids: Set of condition IDs for expired markets
            
        Returns:
            Number of orders cancelled
        """
        cancelled = 0
        for order_id, order in list(self.orders.items()):
            if not order.is_active:
                continue
            # Check if token belongs to an expired market
            token_id = order.token_id
            # Token IDs are associated with condition IDs in market data
            if token_id in expired_condition_ids or order.market_slug in expired_condition_ids:
                result = self.cancel_order(order_id, "Market expired/resolved")
                if result.get("success"):
                    cancelled += 1
        
        if cancelled:
            cprint(f"🧹 Cancelled {cancelled} orders for expired markets", "yellow")
        return cancelled
