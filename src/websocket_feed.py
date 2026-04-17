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
from .logging_utils import cprint
import websocket

from .config import WS_URL

# Polymarket WebSocket endpoints
WS_LIVE_ACTIVITY = "wss://ws-subscriptions-clob.polymarket.com/ws/activity"
WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WS_USER = "wss://ws-subscriptions-clob.polymarket.com/ws/user"


def _normalize_ws_message_text(message) -> str:
    """Decode WebSocket frames to str. websocket-client may pass str or bytes."""
    if message is None:
        return ""
    if isinstance(message, bytes):
        return message.decode("utf-8", errors="replace").strip().lstrip("\ufeff")
    if isinstance(message, (bytearray, memoryview)):
        return bytes(message).decode("utf-8", errors="replace").strip().lstrip("\ufeff")
    s = str(message).strip()
    return s.lstrip("\ufeff")


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


@dataclass
class UserOrderUpdate:
    """Authenticated order lifecycle update."""
    order_id: str
    status: str
    raw: Dict
    timestamp: datetime


@dataclass
class UserTradeUpdate:
    """Authenticated trade update."""
    trade_id: str
    raw: Dict
    timestamp: datetime


@dataclass
class MarketResolvedUpdate:
    """Market resolution event from the market channel."""
    condition_id: str
    winning_outcome: Optional[str]
    raw: Dict
    timestamp: datetime


@dataclass
class TickSizeChangeUpdate:
    """Tick-size update from the market channel."""
    token_id: str
    tick_size: Optional[float]
    raw: Dict
    timestamp: datetime


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
        self._resolved_callbacks: List[Callable[[MarketResolvedUpdate], None]] = []
        self._tick_size_callbacks: List[Callable[[TickSizeChangeUpdate], None]] = []
        
        # Latest data cache
        self.orderbooks: Dict[str, OrderbookUpdate] = {}
        self.last_trades: Dict[str, TradeUpdate] = {}
        
        # Stats
        self.messages_received = 0
        self.last_message_time: Optional[datetime] = None
        self.last_event_time: Optional[datetime] = None
        self.last_event_type: str = ""
        self.feature_events_received: Dict[str, int] = {}
        
        # Thread for running WebSocket
        self._ws_thread: Optional[threading.Thread] = None
        
        # Reconnection settings
        self.reconnect_delay = 5
        self.max_reconnect_attempts = 10
        self.reconnect_attempts = 0
        self._reconnect_lock = threading.Lock()
        self._reconnect_scheduled = False
        self.resolved_markets: Dict[str, MarketResolvedUpdate] = {}
        self.tick_size_by_token: Dict[str, float] = {}
    
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

    def on_market_resolved(self, callback: Callable[[MarketResolvedUpdate], None]):
        """Register callback for market resolution events."""
        self._resolved_callbacks.append(callback)

    def on_tick_size_change(self, callback: Callable[[TickSizeChangeUpdate], None]):
        """Register callback for tick size change events."""
        self._tick_size_callbacks.append(callback)
    
    def subscribe(self, token_ids: List[str]):
        """
        Subscribe to market updates for given tokens.
        
        Args:
            token_ids: List of token IDs to subscribe to
        """
        new_token_ids: List[str] = []
        for token_id in token_ids:
            if token_id not in self.subscribed_tokens:
                self.subscribed_tokens.add(token_id)
                new_token_ids.append(token_id)
        
        # If already connected, send subscription
        if self.is_connected and self.ws and new_token_ids:
            self._send_subscription(new_token_ids)

    def unsubscribe(self, token_ids: List[str]):
        """Unsubscribe from market updates."""
        removed_token_ids: List[str] = []
        for token_id in token_ids:
            if token_id in self.subscribed_tokens:
                self.subscribed_tokens.discard(token_id)
                removed_token_ids.append(token_id)
        
        if self.is_connected and self.ws and removed_token_ids:
            self._send_unsubscription(removed_token_ids)
    
    def _send_subscription(self, token_ids: List[str]):
        """Send subscription message to WebSocket."""
        if not self.ws or not token_ids:
            return
        
        try:
            unique_ids = list(dict.fromkeys(token_ids))
            for start in range(0, len(unique_ids), 50):
                batch = unique_ids[start:start + 50]
                msg = {
                    "assets_ids": batch,
                    "type": "market",
                    "custom_feature_enabled": True,
                }
                self.ws.send(json.dumps(msg))
            cprint(f"📡 Subscribed to {len(unique_ids)} tokens", "cyan")
                
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
            
            raw = json.loads(message)
            # Polymarket may send a single dict or a list of updates
            payloads = raw if isinstance(raw, list) else [raw]
            for data in payloads:
                if not isinstance(data, dict):
                    continue
                msg_type = str(data.get("type") or data.get("event_type") or "").lower()
                self.last_event_time = datetime.now()
                self.last_event_type = msg_type or str(data.get("channel") or "")
                if self.last_event_type:
                    self.feature_events_received[self.last_event_type] = (
                        self.feature_events_received.get(self.last_event_type, 0) + 1
                    )
                # Handle different message types
                channel = str(data.get("channel", "")).lower()
                if msg_type == "book" or "book" in channel:
                    self._handle_orderbook(data)
                elif msg_type in {"best_bid_ask", "price_change"}:
                    self._handle_orderbook(data)
                elif msg_type == "trade" or "trade" in channel:
                    self._handle_trade(data)
                elif msg_type == "market_resolved":
                    self._handle_market_resolved(data)
                elif msg_type == "tick_size_change":
                    self._handle_tick_size_change(data)
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
            token_id = data.get("asset_id") or data.get("asset") or data.get("market")
            if not token_id:
                return
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            if not bids and data.get("best_bid") is not None:
                bids = [{"price": data.get("best_bid"), "size": data.get("best_bid_size", 0)}]
            if not asks and data.get("best_ask") is not None:
                asks = [{"price": data.get("best_ask"), "size": data.get("best_ask_size", 0)}]
            update = OrderbookUpdate(
                token_id=token_id,
                bids=[
                    {"price": _to_float(level.get("price")), "size": _to_float(level.get("size"))}
                    for level in bids
                    if isinstance(level, dict)
                ],
                asks=[
                    {"price": _to_float(level.get("price")), "size": _to_float(level.get("size"))}
                    for level in asks
                    if isinstance(level, dict)
                ],
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
            token_id = data.get("asset_id") or data.get("asset") or data.get("market")
            if not token_id:
                return
            
            update = TradeUpdate(
                token_id=token_id,
                price=_to_float(data.get("price", 0)),
                size=_to_float(data.get("size", 0)),
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

    def _handle_market_resolved(self, data: Dict):
        """Process market resolution event."""
        try:
            condition_id = str(
                data.get("condition_id")
                or data.get("conditionId")
                or data.get("market")
                or ""
            )
            if not condition_id:
                return
            winning_outcome = data.get("winning_outcome") or data.get("winner") or data.get("outcome")
            update = MarketResolvedUpdate(
                condition_id=condition_id,
                winning_outcome=str(winning_outcome).upper() if winning_outcome not in (None, "") else None,
                raw=data,
                timestamp=datetime.now(),
            )
            self.resolved_markets[condition_id] = update
            for callback in self._resolved_callbacks:
                try:
                    callback(update)
                except Exception as e:
                    cprint(f"❌ Market resolved callback error: {e}", "red")
        except Exception as e:
            cprint(f"❌ Market resolved parse error: {e}", "red")

    def _handle_tick_size_change(self, data: Dict):
        """Process tick size change event."""
        try:
            token_id = str(data.get("asset_id") or data.get("asset") or data.get("market") or "")
            if not token_id:
                return
            tick_size_raw = data.get("new_tick_size") or data.get("tick_size") or data.get("tickSize")
            tick_size = _to_float(tick_size_raw, default=0.0)
            update = TickSizeChangeUpdate(
                token_id=token_id,
                tick_size=tick_size if tick_size > 0 else None,
                raw=data,
                timestamp=datetime.now(),
            )
            if update.tick_size is not None:
                self.tick_size_by_token[token_id] = update.tick_size
            for callback in self._tick_size_callbacks:
                try:
                    callback(update)
                except Exception as e:
                    cprint(f"❌ Tick size callback error: {e}", "red")
        except Exception as e:
            cprint(f"❌ Tick size parse error: {e}", "red")
    
    def _on_error(self, ws, error):
        """Handle WebSocket error."""
        cprint(f"❌ WebSocket error: {error}", "red")
    
    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket close. Returns quickly to avoid blocking the library."""
        self.is_connected = False
        reason = f"{close_status_code}: {close_msg}" if close_status_code else "Unknown"
        cprint(f"🔌 WebSocket disconnected: {reason}", "yellow")
        
        # Notify callbacks
        for callback in self._disconnect_callbacks:
            try:
                callback(reason)
            except Exception:
                pass
        
        # Schedule reconnect in a separate thread — do NOT block this callback.
        # Blocking here causes websocket-client to spin (CPU spike) while waiting.
        if self.is_running:
            with self._reconnect_lock:
                if self._reconnect_scheduled:
                    return
                self._reconnect_scheduled = True
            threading.Thread(target=self._reconnect, daemon=True).start()
    
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
                time.sleep(10)
        
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
    
    def _reconnect(self):
        """Attempt to reconnect. Runs in a daemon thread so we don't block the WS callback."""
        try:
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
        finally:
            with self._reconnect_lock:
                self._reconnect_scheduled = False
    
    def _connect(self):
        """Establish WebSocket connection."""
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        # skip_utf8_validation=True reduces CPU when connection is flaky (partial frames).
        # ping_interval=None: disable library ping — we use Polymarket-specific heartbeat.
        # Avoids ping/pong timeout recursion (websocket-client#858) on disconnect.
        self.ws.run_forever(
            skip_utf8_validation=True,
            ping_interval=None,
        )
    
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
            "last_event": self.last_event_time.isoformat() if self.last_event_time else None,
            "last_event_type": self.last_event_type or None,
            "subscribed_tokens": len(self.subscribed_tokens),
            "cached_orderbooks": len(self.orderbooks),
            "reconnect_attempts": self.reconnect_attempts,
            "resolved_markets": len(self.resolved_markets),
        }


class UserWebSocketFeed:
    """Authenticated user stream for real-time order and trade updates."""

    def __init__(
        self,
        auth_provider: Callable[[], Optional[Dict[str, str]]],
        markets_provider: Optional[Callable[[], List[str]]] = None,
        url: str = WS_USER,
    ):
        self.url = url
        self._auth_provider = auth_provider
        self._markets_provider = markets_provider
        self.ws: Optional[websocket.WebSocketApp] = None
        self.is_connected = False
        self.is_running = False
        self._ws_thread: Optional[threading.Thread] = None
        self._order_callbacks: List[Callable[[UserOrderUpdate], None]] = []
        self._trade_callbacks: List[Callable[[UserTradeUpdate], None]] = []
        self._disconnect_callbacks: List[Callable[[str], None]] = []
        self.reconnect_delay = 5
        self.max_reconnect_attempts = 10
        self.reconnect_attempts = 0
        self._reconnect_lock = threading.Lock()
        self._reconnect_scheduled = False
        self.last_message_time: Optional[datetime] = None
        self.last_event_time: Optional[datetime] = None
        self.last_event_type: str = ""
        self.messages_received = 0
        self.subscribed_markets: List[str] = []
        self._heartbeat_thread: Optional[threading.Thread] = None

    def on_order(self, callback: Callable[[UserOrderUpdate], None]):
        self._order_callbacks.append(callback)

    def on_trade(self, callback: Callable[[UserTradeUpdate], None]):
        self._trade_callbacks.append(callback)

    def on_disconnect(self, callback: Callable[[str], None]):
        self._disconnect_callbacks.append(callback)

    def set_markets(self, condition_ids: List[str]):
        self.subscribed_markets = list(dict.fromkeys([mid for mid in condition_ids if mid]))
        if self.is_connected and self.ws:
            self._send_subscription()

    def _send_subscription(self):
        if not self.ws:
            return
        markets = (
            list(dict.fromkeys(self._markets_provider() or []))
            if self._markets_provider
            else list(self.subscribed_markets)
        )
        self.subscribed_markets = markets
        self.ws.send(json.dumps({"operation": "subscribe", "markets": markets}))

    def _start_heartbeat(self):
        def heartbeat():
            while self.is_running and self.is_connected:
                try:
                    if self.ws:
                        self.ws.send("PING")
                except Exception:
                    break
                time.sleep(10)

        self._heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        self._heartbeat_thread.start()

    def _on_open(self, ws):
        creds = self._auth_provider() or {}
        if not creds:
            cprint("⚠️ User WebSocket missing API credentials; closing connection", "yellow")
            ws.close()
            return
        if self._markets_provider:
            self.subscribed_markets = list(dict.fromkeys(self._markets_provider() or []))
        ws.send(json.dumps({"auth": creds, "type": "user"}))
        if self.subscribed_markets:
            ws.send(json.dumps({"operation": "subscribe", "markets": self.subscribed_markets}))
        self.is_connected = True
        self.reconnect_attempts = 0
        self._start_heartbeat()
        cprint("✅ User WebSocket connected!", "green")

    def _on_message(self, ws, message):
        try:
            text = _normalize_ws_message_text(message)
            if not text or text.upper() in {"PONG", "PING"} or text == "{}":
                self.last_message_time = datetime.now()
                return
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                self.last_message_time = datetime.now()
                return
            self.messages_received += 1
            self.last_message_time = datetime.now()
            payloads = raw if isinstance(raw, list) else [raw]
            for data in payloads:
                if not isinstance(data, dict):
                    continue
                event_type = str(data.get("event_type") or "").lower()
                self.last_event_time = datetime.now()
                self.last_event_type = event_type
                if event_type == "order":
                    update = UserOrderUpdate(
                        order_id=str(data.get("id") or ""),
                        status=str(data.get("status") or data.get("type") or ""),
                        raw=data,
                        timestamp=datetime.now(),
                    )
                    for callback in self._order_callbacks:
                        callback(update)
                elif event_type == "trade":
                    update = UserTradeUpdate(
                        trade_id=str(data.get("id") or ""),
                        raw=data,
                        timestamp=datetime.now(),
                    )
                    for callback in self._trade_callbacks:
                        callback(update)
        except Exception as exc:
            cprint(f"❌ User WebSocket message handling error: {exc}", "red")

    def _on_error(self, ws, error):
        cprint(f"❌ User WebSocket error: {error}", "red")

    def _on_close(self, ws, close_status_code, close_msg):
        self.is_connected = False
        reason = f"{close_status_code}: {close_msg}" if close_status_code else "Unknown"
        cprint(f"🔌 User WebSocket disconnected: {reason}", "yellow")
        for callback in self._disconnect_callbacks:
            try:
                callback(reason)
            except Exception:
                pass
        if self.is_running:
            with self._reconnect_lock:
                if self._reconnect_scheduled:
                    return
                self._reconnect_scheduled = True
            threading.Thread(target=self._reconnect, daemon=True).start()

    def _reconnect(self):
        try:
            if self.reconnect_attempts >= self.max_reconnect_attempts:
                cprint("❌ Max user WebSocket reconnection attempts reached", "red")
                self.is_running = False
                return
            self.reconnect_attempts += 1
            time.sleep(self.reconnect_delay * self.reconnect_attempts)
            if self.is_running:
                self._connect()
        finally:
            with self._reconnect_lock:
                self._reconnect_scheduled = False

    def _connect(self):
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self.ws.run_forever(skip_utf8_validation=True, ping_interval=None)

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self._ws_thread = threading.Thread(target=self._connect, daemon=True)
        self._ws_thread.start()

    def stop(self):
        self.is_running = False
        if self.ws:
            self.ws.close()
            self.ws = None

    def get_stats(self) -> Dict[str, Optional[str]]:
        return {
            "is_connected": self.is_connected,
            "is_running": self.is_running,
            "messages_received": self.messages_received,
            "last_message": self.last_message_time.isoformat() if self.last_message_time else None,
            "last_event": self.last_event_time.isoformat() if self.last_event_time else None,
            "last_event_type": self.last_event_type or None,
            "subscribed_markets": len(self.subscribed_markets),
            "reconnect_attempts": self.reconnect_attempts,
        }
