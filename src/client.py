"""
Polymarket CLOB Client Wrapper

Wraps the official py-clob-client with additional error handling,
rate limiting, and convenience methods.
"""

import time
from typing import Dict, List, Optional, Any
from datetime import datetime
from termcolor import cprint

from .config import (
    CLOB_HOST,
    GAMMA_HOST,
    CHAIN_ID,
    PRIVATE_KEY,
    PROXY_ADDRESS,
    SIGNATURE_TYPE,
    PAPER_TRADING,
    ORDER_RATE_LIMIT_SUSTAINED,
)

# Will be imported when py-clob-client is installed
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False
    cprint("⚠️  py-clob-client not installed. Run: pip install py-clob-client", "yellow")


class PolymarketClient:
    """
    Wrapper around Polymarket CLOB client with rate limiting and error handling.
    
    Usage:
        client = PolymarketClient()
        if client.connect():
            markets = client.get_markets()
            client.place_order(token_id, "BUY", 0.50, 10)
    """
    
    def __init__(self):
        self.client: Optional[ClobClient] = None
        self.is_connected = False
        self.last_order_time = 0
        self.orders_this_second = 0
        self.api_creds_set = False
        
        # Track rate limits
        self.min_order_interval = 1.0 / ORDER_RATE_LIMIT_SUSTAINED
        
    def connect(self) -> bool:
        """
        Initialize connection to Polymarket CLOB.
        
        Returns:
            True if connected successfully
        """
        if not CLOB_AVAILABLE:
            cprint("❌ Cannot connect: py-clob-client not installed", "red")
            return False
        
        if not PRIVATE_KEY or not PROXY_ADDRESS:
            cprint("❌ Cannot connect: Missing PRIVATE_KEY or PROXY_ADDRESS in .env", "red")
            return False
        
        try:
            cprint("🔌 Connecting to Polymarket CLOB...", "cyan")
            
            self.client = ClobClient(
                host=CLOB_HOST,
                key=PRIVATE_KEY,
                chain_id=CHAIN_ID,
                signature_type=SIGNATURE_TYPE,
                funder=PROXY_ADDRESS
            )
            
            # Derive API credentials
            cprint("🔑 Setting up API credentials...", "cyan")
            self.client.set_api_creds(self.client.create_or_derive_api_creds())
            self.api_creds_set = True
            
            self.is_connected = True
            cprint("✅ Connected to Polymarket CLOB", "green")
            
            return True
            
        except Exception as e:
            cprint(f"❌ Connection failed: {e}", "red")
            self.is_connected = False
            return False
    
    def _rate_limit(self):
        """Enforce rate limiting between orders."""
        now = time.time()
        elapsed = now - self.last_order_time
        
        if elapsed < self.min_order_interval:
            sleep_time = self.min_order_interval - elapsed
            time.sleep(sleep_time)
        
        self.last_order_time = time.time()
    
    def get_markets(self, next_cursor: str = "") -> Dict:
        """
        Fetch available markets from Gamma API.
        
        Args:
            next_cursor: Pagination cursor
            
        Returns:
            Dict with markets data
        """
        if not self.is_connected:
            return {"error": "Not connected"}
        
        try:
            import requests
            
            url = f"{GAMMA_HOST}/markets"
            params = {"closed": "false", "limit": 100}
            if next_cursor:
                params["next_cursor"] = next_cursor
            
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            cprint(f"❌ Failed to fetch markets: {e}", "red")
            return {"error": str(e)}
    
    def get_market(self, condition_id: str) -> Optional[Dict]:
        """
        Get details for a specific market.
        
        Args:
            condition_id: The market's condition ID
            
        Returns:
            Market details dict or None
        """
        if not self.is_connected:
            return None
        
        try:
            import requests
            
            url = f"{GAMMA_HOST}/markets/{condition_id}"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            cprint(f"❌ Failed to fetch market {condition_id}: {e}", "red")
            return None
    
    def get_orderbook(self, token_id: str) -> Optional[Dict]:
        """
        Get orderbook for a specific token.
        
        Args:
            token_id: The token ID
            
        Returns:
            Orderbook dict with bids and asks
        """
        if not self.is_connected:
            return None
        
        try:
            book = self.client.get_order_book(token_id)
            return book
            
        except Exception as e:
            cprint(f"❌ Failed to fetch orderbook: {e}", "red")
            return None
    
    def get_price(self, token_id: str) -> Optional[Dict]:
        """
        Get current price info for a token.
        
        Args:
            token_id: The token ID
            
        Returns:
            Dict with bid, ask, mid prices
        """
        if not self.is_connected:
            return None
        
        try:
            # Try the price endpoint first
            price = self.client.get_price(token_id)
            if price and (price.get("bid") or price.get("ask")):
                return price
        except:
            pass
        
        # Fallback: get from orderbook
        try:
            book = self.client.get_order_book(token_id)
            if book:
                bids = book.get("bids", [])
                asks = book.get("asks", [])
                
                best_bid = float(bids[0].get("price", 0)) if bids else 0
                best_ask = float(asks[0].get("price", 1)) if asks else 1
                
                return {
                    "bid": best_bid,
                    "ask": best_ask,
                    "mid": (best_bid + best_ask) / 2
                }
        except:
            pass
        
        return None
    
    def place_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_type: str = "GTC"
    ) -> Dict:
        """
        Place a limit order.
        
        Args:
            token_id: Token to trade
            side: "BUY" or "SELL"
            price: Limit price (0.01 to 0.99)
            size: Number of shares
            order_type: "GTC" (Good Till Cancelled) or "FOK" (Fill or Kill)
            
        Returns:
            Order result dict
        """
        if not self.is_connected:
            return {"success": False, "error": "Not connected"}
        
        # Paper trading mode
        if PAPER_TRADING:
            cprint(f"📝 [PAPER] {side} {size:.2f} @ ${price:.3f}", "yellow")
            return {
                "success": True,
                "order_id": f"paper_{int(time.time()*1000)}",
                "paper_trade": True,
                "side": side,
                "price": price,
                "size": size
            }
        
        try:
            # Rate limiting
            self._rate_limit()
            
            # Build order args
            order_args = OrderArgs(
                price=price,
                size=size,
                side=BUY if side.upper() == "BUY" else SELL,
                token_id=token_id
            )
            
            # Create and sign order
            signed_order = self.client.create_order(order_args)
            
            # Post order
            ot = OrderType.GTC if order_type == "GTC" else OrderType.FOK
            result = self.client.post_order(signed_order, ot)
            
            cprint(f"✅ Order placed: {side} {size:.2f} @ ${price:.3f}", "green")
            
            return {
                "success": True,
                "order_id": result.get("orderID") or result.get("id"),
                "result": result
            }
            
        except Exception as e:
            error_msg = str(e)
            cprint(f"❌ Order failed: {error_msg}", "red")
            
            return {
                "success": False,
                "error": error_msg
            }
    
    def cancel_order(self, order_id: str) -> Dict:
        """
        Cancel an open order.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            Cancellation result
        """
        if not self.is_connected:
            return {"success": False, "error": "Not connected"}
        
        if PAPER_TRADING:
            cprint(f"📝 [PAPER] Cancel order {order_id}", "yellow")
            return {"success": True, "paper_trade": True}
        
        try:
            self._rate_limit()
            result = self.client.cancel(order_id)
            
            cprint(f"🚫 Order cancelled: {order_id}", "yellow")
            return {"success": True, "result": result}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def cancel_all_orders(self) -> Dict:
        """Cancel all open orders."""
        if not self.is_connected:
            return {"success": False, "error": "Not connected"}
        
        if PAPER_TRADING:
            cprint("📝 [PAPER] Cancel all orders", "yellow")
            return {"success": True, "paper_trade": True}
        
        try:
            result = self.client.cancel_all()
            cprint("🚫 All orders cancelled", "yellow")
            return {"success": True, "result": result}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_open_orders(self) -> List[Dict]:
        """Get all open orders."""
        if not self.is_connected:
            return []
        
        try:
            orders = self.client.get_orders()
            return orders if orders else []
            
        except Exception as e:
            cprint(f"❌ Failed to fetch orders: {e}", "red")
            return []
    
    def get_trades(self, limit: int = 100) -> List[Dict]:
        """Get recent trades."""
        if not self.is_connected:
            return []
        
        try:
            trades = self.client.get_trades()
            return trades[:limit] if trades else []
            
        except Exception as e:
            cprint(f"❌ Failed to fetch trades: {e}", "red")
            return []
    
    def get_balance(self) -> Optional[float]:
        """
        Get USDC balance.
        
        Note: This may require additional setup depending on API version.
        """
        # Balance fetching depends on proxy wallet setup
        # For now, return None and let user check manually
        return None


# Convenience function for quick client creation
def create_client() -> PolymarketClient:
    """Create and connect a Polymarket client."""
    client = PolymarketClient()
    client.connect()
    return client



