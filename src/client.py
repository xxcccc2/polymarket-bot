"""
Polymarket CLOB Client Wrapper

Wraps the official py-clob-client with additional error handling,
rate limiting, and convenience methods.
"""

import time
import random
import concurrent.futures
from typing import Dict, List, Optional, Any
from datetime import datetime
from .logging_utils import cprint

# Thread pool for bounded HTTP calls (prevents main loop hanging)
_TIMEOUT_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="api")

def _call_with_timeout(fn, timeout_s: float = 15, default=None):
    """Run *fn* in a thread and return default if it exceeds *timeout_s*."""
    try:
        future = _TIMEOUT_POOL.submit(fn)
        return future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        return default
    except Exception:
        return default

from .config import (
    CLOB_HOST,
    GAMMA_HOST,
    CHAIN_ID,
    PRIVATE_KEY,
    PROXY_ADDRESS,
    SIGNATURE_TYPE,
    PAPER_TRADING,
    PAPER_BALANCE_USD,
    ORDER_RATE_LIMIT_SUSTAINED,
    RETRY_MAX_ATTEMPTS,
    RETRY_BASE_DELAY_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
    AUTO_ALLOWANCE_REFRESH_ENABLED,
    ALLOWANCE_REFRESH_SECONDS,
)
from .data_client import get_balance_total

# Will be imported when py-clob-client is installed
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import (
        OrderArgs,
        OrderType,
        BalanceAllowanceParams,
        AssetType,
    )
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
        self.last_allowance_refresh = 0.0
        
        # Track rate limits
        self.min_order_interval = 1.0 / ORDER_RATE_LIMIT_SUSTAINED

    @staticmethod
    def _is_allowance_error(error_msg: str) -> bool:
        msg = (error_msg or "").lower()
        return "allowance" in msg or "not enough balance / allowance" in msg

    @staticmethod
    def _is_auth_error(error_msg: str) -> bool:
        msg = (error_msg or "").lower()
        return "unauthorized" in msg or "invalid api key" in msg or "status_code=401" in msg

    def _refresh_api_creds(self, reason: str = "auth-retry") -> bool:
        """Re-derive L2 API credentials after 401/invalid-key responses."""
        if not self.client or PAPER_TRADING:
            return False
        try:
            cprint(f"🔑 Refreshing API credentials ({reason})...", "yellow")
            self.client.set_api_creds(self.client.create_or_derive_api_creds())
            self.api_creds_set = True
            return True
        except Exception as exc:
            cprint(f"⚠️  API credential refresh failed ({reason}): {exc}", "yellow")
            return False

    def _refresh_allowance(self, force: bool = False, reason: str = "periodic") -> bool:
        """
        Refresh CLOB collateral allowance.
        This is safe to call repeatedly; uses interval gating unless forced.
        """
        if PAPER_TRADING or not AUTO_ALLOWANCE_REFRESH_ENABLED:
            return False
        if not self.is_connected or not self.client or not self.api_creds_set:
            return False

        now = time.time()
        if not force and (now - self.last_allowance_refresh) < ALLOWANCE_REFRESH_SECONDS:
            return True

        try:
            collateral_params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=SIGNATURE_TYPE,
            )

            self._retry_call(
                lambda: self.client.update_balance_allowance(collateral_params),
                "Update collateral allowance",
            )

            self.last_allowance_refresh = now
            cprint(f"🔐 Refreshed CLOB allowance ({reason})", "cyan")
            return True
        except Exception as exc:
            cprint(f"⚠️  Allowance refresh failed ({reason}): {exc}", "yellow")
            return False
        
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
            self._refresh_allowance(force=True, reason="startup")
            
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

    def _retry_call(self, func, action: str):
        """Retry wrapper with exponential backoff."""
        attempt = 0
        last_error: Optional[Exception] = None
        while attempt < RETRY_MAX_ATTEMPTS:
            try:
                return func()
            except Exception as exc:
                last_error = exc
                attempt += 1
                if attempt >= RETRY_MAX_ATTEMPTS:
                    break
                delay = min(
                    RETRY_MAX_DELAY_SECONDS,
                    RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                )
                jitter = delay * 0.1 * random.random()
                cprint(f"⚠️  {action} failed (attempt {attempt}/{RETRY_MAX_ATTEMPTS}): {exc}", "yellow")
                time.sleep(delay + jitter)
        if last_error:
            raise last_error
    
    def get_markets(self, next_cursor: str = "", tag: str = "") -> Dict:
        """
        Fetch available markets from Gamma API with automatic pagination.
        
        Args:
            next_cursor: Pagination cursor (used internally)
            tag: Optional tag filter (e.g. "crypto")
            
        Returns:
            List of market dicts (paginated automatically)
        """
        if not self.is_connected:
            return {"error": "Not connected"}
        
        try:
            import requests
            
            url = f"{GAMMA_HOST}/markets"
            all_markets: list = []
            cursor = next_cursor
            max_pages = 10  # safety cap

            for _ in range(max_pages):
                params = {"closed": "false", "limit": 100}
                if cursor:
                    params["next_cursor"] = cursor
                if tag:
                    params["tag"] = tag

                def _request(p=dict(params)):
                    response = requests.get(url, params=p, timeout=30)
                    response.raise_for_status()
                    return response.json()

                result = self._retry_call(_request, "Fetch markets")

                if isinstance(result, list):
                    all_markets.extend(result)
                    break  # no pagination info — single page
                elif isinstance(result, dict):
                    data = result.get("data", result.get("markets", []))
                    if isinstance(data, list):
                        all_markets.extend(data)
                    cursor = result.get("next_cursor", "")
                    if not cursor:
                        break
                else:
                    break

            return all_markets
            
        except Exception as e:
            cprint(f"❌ Failed to fetch markets: {e}", "red")
            return {"error": str(e)}
    
    def get_events(self, limit: int = 50) -> List[Dict]:
        """
        Fetch active events from Gamma API (includes 5-min crypto markets).
        
        Returns:
            List of event dicts, each containing a 'markets' sub-list.
        """
        if not self.is_connected:
            return []
        
        try:
            import requests
            
            url = f"{GAMMA_HOST}/events"
            params = {
                "closed": "false",
                "active": "true",
                "limit": limit,
                "order": "startDate",
                "ascending": "false",
            }

            def _request():
                response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()
                return response.json()

            result = self._retry_call(_request, "Fetch events")
            return result if isinstance(result, list) else []
            
        except Exception as e:
            cprint(f"⚠️ Failed to fetch events: {e}", "yellow")
            return []

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

            def _request():
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                return response.json()

            return self._retry_call(_request, f"Fetch market {condition_id}")
            
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
            return self._retry_call(
                lambda: self.client.get_order_book(token_id),
                f"Fetch orderbook {token_id}",
            )
            
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
            price = self._retry_call(lambda: self.client.get_price(token_id), f"Fetch price {token_id}")
            if price and (price.get("bid") or price.get("ask")):
                return price
        except:
            pass
        
        # Fallback: get from orderbook
        try:
            book = self._retry_call(
                lambda: self.client.get_order_book(token_id),
                f"Fetch orderbook {token_id}",
            )
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
            self._refresh_allowance(force=False, reason="pre-order")

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
            signed_order = self._retry_call(
                lambda: self.client.create_order(order_args),
                "Create order",
            )

            # Post order
            ot = OrderType.GTC if order_type == "GTC" else OrderType.FOK
            result = self._retry_call(
                lambda: self.client.post_order(signed_order, ot),
                "Post order",
            )
            
            cprint(f"✅ Order placed: {side} {size:.2f} @ ${price:.3f}", "green")
            
            return {
                "success": True,
                "order_id": result.get("orderID") or result.get("id"),
                "result": result
            }
            
        except Exception as e:
            error_msg = str(e)
            if self._is_allowance_error(error_msg):
                cprint("🔁 Allowance error detected, forcing allowance refresh and retrying once...", "yellow")
                refreshed = self._refresh_allowance(force=True, reason="order-error")
                if refreshed:
                    try:
                        self._rate_limit()
                        order_args = OrderArgs(
                            price=price,
                            size=size,
                            side=BUY if side.upper() == "BUY" else SELL,
                            token_id=token_id
                        )
                        signed_order = self._retry_call(
                            lambda: self.client.create_order(order_args),
                            "Create order",
                        )
                        ot = OrderType.GTC if order_type == "GTC" else OrderType.FOK
                        result = self._retry_call(
                            lambda: self.client.post_order(signed_order, ot),
                            "Post order",
                        )
                        cprint(f"✅ Order placed after allowance refresh: {side} {size:.2f} @ ${price:.3f}", "green")
                        return {
                            "success": True,
                            "order_id": result.get("orderID") or result.get("id"),
                            "result": result,
                        }
                    except Exception as retry_exc:
                        error_msg = str(retry_exc)
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
            result = self._retry_call(lambda: self.client.cancel(order_id), f"Cancel order {order_id}")
            
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
            result = self._retry_call(lambda: self.client.cancel_all(), "Cancel all orders")
            cprint("🚫 All orders cancelled", "yellow")
            return {"success": True, "result": result}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_open_orders(self) -> List[Dict]:
        """Get all open orders."""
        if not self.is_connected:
            return []
        
        try:
            orders = self._retry_call(lambda: self.client.get_orders(), "Fetch open orders")
            return orders if orders else []
            
        except Exception as e:
            error_msg = str(e)
            if self._is_auth_error(error_msg) and self._refresh_api_creds(reason="get_open_orders"):
                try:
                    orders = self._retry_call(lambda: self.client.get_orders(), "Fetch open orders")
                    return orders if orders else []
                except Exception as retry_exc:
                    error_msg = str(retry_exc)
            cprint(f"❌ Failed to fetch orders: {error_msg}", "red")
            return []
    
    def get_trades(self, limit: int = 100) -> List[Dict]:
        """Get recent trades (bounded to 15s timeout)."""
        if not self.is_connected:
            return []

        try:
            trades = _call_with_timeout(
                lambda: self._retry_call(lambda: self.client.get_trades(), "Fetch trades"),
                timeout_s=15,
                default=[],
            )
            return trades[:limit] if trades else []

        except Exception as e:
            error_msg = str(e)
            if self._is_auth_error(error_msg) and self._refresh_api_creds(reason="get_trades"):
                try:
                    trades = _call_with_timeout(
                        lambda: self._retry_call(lambda: self.client.get_trades(), "Fetch trades"),
                        timeout_s=15,
                        default=[],
                    )
                    return trades[:limit] if trades else []
                except Exception as retry_exc:
                    error_msg = str(retry_exc)
            cprint(f"❌ Failed to fetch trades: {error_msg}", "red")
            return []
    
    def get_balance(self) -> Optional[float]:
        """
        Get USDC balance.
        
        Note: This may require additional setup depending on API version.
        """
        if PAPER_TRADING:
            return float(PAPER_BALANCE_USD)

        if not self.is_connected or not self.client:
            return None

        def parse_balance(payload) -> Optional[float]:
            if payload is None:
                return None
            if isinstance(payload, (int, float)):
                return float(payload)
            if isinstance(payload, dict):
                for key in (
                    "balance",
                    "availableBalance",
                    "available_balance",
                    "totalBalance",
                    "total_balance",
                    "cashBalance",
                    "collateral",
                    "usdc",
                    "USDC",
                ):
                    if key in payload:
                        try:
                            return float(payload[key])
                        except (TypeError, ValueError):
                            return None
                if "data" in payload:
                    return parse_balance(payload.get("data"))
                return None
            if isinstance(payload, list):
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    asset = str(item.get("asset") or item.get("token") or item.get("symbol") or "").upper()
                    if asset in {"USDC", "USD"}:
                        return parse_balance(item)
                    value = parse_balance(item)
                    if value is not None:
                        return value
            return None

        def _try_methods():
            for method_name in ("get_balance", "get_collateral", "get_account"):
                method = getattr(self.client, method_name, None)
                if not method:
                    continue
                try:
                    response = method()
                except Exception:
                    continue
                bal = parse_balance(response)
                if bal is not None:
                    return bal
            # Fallback: on-chain USDC + Data API position value (total portfolio)
            # Data API /value returns position value only — use get_balance_total
            if PROXY_ADDRESS:
                try:
                    fallback = get_balance_total(PROXY_ADDRESS)
                    if fallback is not None and fallback >= 0:
                        return fallback
                except Exception:
                    pass
            return None

        return _call_with_timeout(_try_methods, timeout_s=15, default=None)


# Convenience function for quick client creation
def create_client() -> PolymarketClient:
    """Create and connect a Polymarket client."""
    client = PolymarketClient()
    client.connect()
    return client



