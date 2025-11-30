"""
Polymarket WebSocket Feed

Real-time market data feed via WebSocket for:
- Orderbook updates
- Price changes
- Trade notifications

Provides streaming data to strategies for instant opportunity detection.
"""

import json
import time
import threading
from typing import Dict, List, Callable, Optional, Set
from datetime import datetime
from dataclasses import dataclass
from termcolor import cprint
import websocket

from .config import WS_URL

# Polymarket WebSocket endpoints
WS_LIVE_ACTIVITY = "wss://ws-subscriptions-clob.polymarket.com/ws/activity"
WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@dataclass
class OrderbookUpdate:
    """Represents an orderbook update."""
    token_id: str
    bids: List[Dict]  # [{"price": 0.50, "size": 100}, ...]
    asks: List[Dict]
    timestamp: datetime
    
    @property
    def best_bid(self) -> float:
        return self.bids[0]["price"] if self.bids else 0
    
    @property
    def best_ask(self) -> float:
        return self.asks[0]["price"] if self.asks else 1
    
    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid
    
    @property
    def spread_cents(self) -> float:
        return self.spread * 100


@dataclass
class TradeUpdate:
    """Represents a trade notification."""
    token_id: str
    price: float
    size: float
    side: str  # "buy" or "sell"
    timestamp: datetime


class WebSocketFeed:
    """
    Real-time WebSocket feed for Polymarket market data.
    
    Usage:
        feed = WebSocketFeed()
        feed.on_orderbook(my_orderbook_handler)
        feed.on_trade(my_trade_handler)
        feed.subscribe(["token_id_1", "token_id_2"])
        feed.start()
    """
    
    def __init__(self, url: str = WS_URL):
        self.url = url
        self.ws: Optional[websocket.WebSocketApp] = None
        self.is_connected = False
        self.is_running = False
        
        # Subscribed tokens
        self.subscribed_tokens: Set[str] = set()
        
        # Callbacks
        self._orderbook_callbacks: List[Callable[[OrderbookUpdate], None]] = []
        self._trade_callbacks: List[Callable[[TradeUpdate], None]] = []
        self._connect_callbacks: List[Callable[[], None]] = []
        self._disconnect_callbacks: List[Callable[[str], None]] = []
        
        # Latest data cache
        self.orderbooks: Dict[str, OrderbookUpdate] = {}
        self.last_trades: Dict[str, TradeUpdate] = {}
        
        # Stats
        self.messages_received = 0
        self.last_message_time: Optional[datetime] = None
        
        # Thread for running WebSocket
        self._ws_thread: Optional[threading.Thread] = None
        
        # Reconnection settings
        self.reconnect_delay = 5
        self.max_reconnect_attempts = 10
        self.reconnect_attempts = 0
    
    def on_orderbook(self, callback: Callable[[OrderbookUpdate], None]):
        """Register callback for orderbook updates."""
        self._orderbook_callbacks.append(callback)
    
    def on_trade(self, callback: Callable[[TradeUpdate], None]):
        """Register callback for trade updates."""
        self._trade_callbacks.append(callback)
    
    def on_connect(self, callback: Callable[[], None]):
        """Register callback for connection events."""
        self._connect_callbacks.append(callback)
    
    def on_disconnect(self, callback: Callable[[str], None]):
        """Register callback for disconnection events."""
        self._disconnect_callbacks.append(callback)
    
    def subscribe(self, token_ids: List[str]):
        """
        Subscribe to market updates for given tokens.
        
        Args:
            token_ids: List of token IDs to subscribe to
        """
        for token_id in token_ids:
            self.subscribed_tokens.add(token_id)
        
        # If already connected, send subscription
        if self.is_connected and self.ws:
            self._send_subscription(token_ids)
    
    def unsubscribe(self, token_ids: List[str]):
        """Unsubscribe from market updates."""
        for token_id in token_ids:
            self.subscribed_tokens.discard(token_id)
        
        if self.is_connected and self.ws:
            self._send_unsubscription(token_ids)
    
    def _send_subscription(self, token_ids: List[str]):
        """Send subscription message to WebSocket."""
        if not self.ws or not token_ids:
            return
        
        try:
            # Polymarket WebSocket subscription format (batch subscribe)
            # Format: {"assets_ids": ["token1", "token2"], "type": "market"}
            msg = {
                "assets_ids": token_ids[:50],  # Limit to 50 at a time
                "type": "market"
            }
            self.ws.send(json.dumps(msg))
            cprint(f"📡 Subscribed to {len(token_ids)} tokens", "cyan")
                
        except Exception as e:
            cprint(f"❌ Subscription error: {e}", "red")
    
    def _send_unsubscription(self, token_ids: List[str]):
        """Send unsubscription message."""
        if not self.ws or not token_ids:
            return
        
        try:
            for token_id in token_ids:
                msg = {
                    "type": "unsubscribe",
                    "channel": "market",
                    "assets_id": token_id
                }
                self.ws.send(json.dumps(msg))
                
        except Exception as e:
            cprint(f"❌ Unsubscription error: {e}", "red")
    
    def _on_message(self, ws, message: str):
        """Handle incoming WebSocket message."""
        try:
            self.messages_received += 1
            self.last_message_time = datetime.now()
            
            data = json.loads(message)
            msg_type = data.get("type") or data.get("event_type")
            
            # Handle different message types
            if msg_type == "book" or "book" in str(data.get("channel", "")):
                self._handle_orderbook(data)
                
            elif msg_type == "trade" or "trade" in str(data.get("channel", "")):
                self._handle_trade(data)
                
            elif msg_type == "subscribed":
                cprint(f"✅ Subscription confirmed", "green")
                
            elif msg_type == "pong":
                pass  # Heartbeat response
                
            elif msg_type == "error":
                cprint(f"⚠️ WebSocket error: {data.get('message')}", "yellow")
                
        except json.JSONDecodeError:
            pass
        except Exception as e:
            cprint(f"❌ Message handling error: {e}", "red")
    
    def _handle_orderbook(self, data: Dict):
        """Process orderbook update."""
        try:
            token_id = data.get("asset_id") or data.get("market")
            if not token_id:
                return
            
            update = OrderbookUpdate(
                token_id=token_id,
                bids=data.get("bids", []),
                asks=data.get("asks", []),
                timestamp=datetime.now()
            )
            
            # Cache latest
            self.orderbooks[token_id] = update
            
            # Notify callbacks
            for callback in self._orderbook_callbacks:
                try:
                    callback(update)
                except Exception as e:
                    cprint(f"❌ Orderbook callback error: {e}", "red")
                    
        except Exception as e:
            cprint(f"❌ Orderbook parse error: {e}", "red")
    
    def _handle_trade(self, data: Dict):
        """Process trade notification."""
        try:
            token_id = data.get("asset_id") or data.get("market")
            if not token_id:
                return
            
            update = TradeUpdate(
                token_id=token_id,
                price=float(data.get("price", 0)),
                size=float(data.get("size", 0)),
                side=data.get("side", "unknown"),
                timestamp=datetime.now()
            )
            
            # Cache latest
            self.last_trades[token_id] = update
            
            # Notify callbacks
            for callback in self._trade_callbacks:
                try:
                    callback(update)
                except Exception as e:
                    cprint(f"❌ Trade callback error: {e}", "red")
                    
        except Exception as e:
            cprint(f"❌ Trade parse error: {e}", "red")
    
    def _on_error(self, ws, error):
        """Handle WebSocket error."""
        cprint(f"❌ WebSocket error: {error}", "red")
    
    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket close."""
        self.is_connected = False
        reason = f"{close_status_code}: {close_msg}" if close_status_code else "Unknown"
        cprint(f"🔌 WebSocket disconnected: {reason}", "yellow")
        
        # Notify callbacks
        for callback in self._disconnect_callbacks:
            try:
                callback(reason)
            except:
                pass
        
        # Attempt reconnection if still running
        if self.is_running:
            self._reconnect()
    
    def _on_open(self, ws):
        """Handle WebSocket open."""
        self.is_connected = True
        self.reconnect_attempts = 0
        cprint("✅ WebSocket connected!", "green")
        
        # Subscribe to tokens
        if self.subscribed_tokens:
            self._send_subscription(list(self.subscribed_tokens))
        
        # Start heartbeat
        self._start_heartbeat()
        
        # Notify callbacks
        for callback in self._connect_callbacks:
            try:
                callback()
            except:
                pass
    
    def _start_heartbeat(self):
        """Start heartbeat thread to keep connection alive."""
        def heartbeat():
            while self.is_connected and self.is_running:
                try:
                    if self.ws:
                        self.ws.send(json.dumps({"type": "ping"}))
                except:
                    break
                time.sleep(30)
        
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
    
    def _reconnect(self):
        """Attempt to reconnect."""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            cprint("❌ Max reconnection attempts reached", "red")
            self.is_running = False
            return
        
        self.reconnect_attempts += 1
        delay = self.reconnect_delay * self.reconnect_attempts
        
        cprint(f"🔄 Reconnecting in {delay}s (attempt {self.reconnect_attempts})...", "yellow")
        time.sleep(delay)
        
        if self.is_running:
            self._connect()
    
    def _connect(self):
        """Establish WebSocket connection."""
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        
        self.ws.run_forever()
    
    def start(self, blocking: bool = False):
        """
        Start the WebSocket feed.
        
        Args:
            blocking: If True, run in current thread (blocks). 
                     If False, run in background thread.
        """
        if self.is_running:
            cprint("⚠️ WebSocket feed already running", "yellow")
            return
        
        self.is_running = True
        cprint(f"🚀 Starting WebSocket feed: {self.url}", "cyan")
        
        if blocking:
            self._connect()
        else:
            self._ws_thread = threading.Thread(target=self._connect, daemon=True)
            self._ws_thread.start()
    
    def stop(self):
        """Stop the WebSocket feed."""
        self.is_running = False
        
        if self.ws:
            self.ws.close()
            self.ws = None
        
        cprint("🛑 WebSocket feed stopped", "yellow")
    
    def get_latest_orderbook(self, token_id: str) -> Optional[OrderbookUpdate]:
        """Get cached orderbook for a token."""
        return self.orderbooks.get(token_id)
    
    def get_stats(self) -> Dict:
        """Get feed statistics."""
        return {
            "is_connected": self.is_connected,
            "is_running": self.is_running,
            "messages_received": self.messages_received,
            "last_message": self.last_message_time.isoformat() if self.last_message_time else None,
            "subscribed_tokens": len(self.subscribed_tokens),
            "cached_orderbooks": len(self.orderbooks),
            "reconnect_attempts": self.reconnect_attempts
        }


