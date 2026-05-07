"""
Polymarket CLOB Client Wrapper

Wraps the official py-clob-client-v2 with additional error handling,
rate limiting, and convenience methods.
"""

import time
import random
import threading
import concurrent.futures
import json
import re
from typing import Dict, List, Optional, Any, Tuple
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


def _call_with_timeout_strict(fn, timeout_s: float = 15, timeout_default=None):
    """
    Run *fn* in a thread.
    - returns timeout_default on timeout
    - re-raises underlying exceptions (needed for auth refresh paths)
    """
    future = _TIMEOUT_POOL.submit(fn)
    try:
        return future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        return timeout_default

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
    ALLOWANCE_DIAGNOSTICS_ENABLED,
    CLOB_HEARTBEAT_ENABLED,
    CLOB_HEARTBEAT_INTERVAL_SECONDS,
    TRADE_FETCH_TIMEOUT_SECONDS,
    BALANCE_FETCH_TIMEOUT_SECONDS,
    POLYMARKET_BUILDER_CODE,
)
from .data_client import get_balance_total, get_trades_by_user, get_positions

# Will be imported when py-clob-client-v2 is installed
try:
    from py_clob_client_v2 import (
        ApiCreds,
        ClobClient,
        OrderArgs,
        OrderType,
        PostOrdersArgs,
        PostOrdersV2Args,
        PartialCreateOrderOptions,
        BalanceAllowanceParams,
        AssetType,
        TradeParams,
        Side,
        OrderPayload,
    )
    try:
        from py_clob_client_v2 import BuilderConfig
    except ImportError:
        BuilderConfig = None
    CLOB_AVAILABLE = True
except ImportError:
    ApiCreds = None
    ClobClient = None
    OrderArgs = None
    OrderType = None
    PostOrdersArgs = None
    PostOrdersV2Args = None
    PartialCreateOrderOptions = None
    BalanceAllowanceParams = None
    AssetType = None
    TradeParams = None
    Side = None
    OrderPayload = None
    BuilderConfig = None
    CLOB_AVAILABLE = False
    cprint("⚠️  py-clob-client-v2 not installed. Run: pip install py-clob-client-v2", "yellow")


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
        self.last_allowance_diag_log = 0.0
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_running = False
        self._heartbeat_warned_unavailable = False
        self._heartbeat_id: str = ""
        self._market_meta_cache: Dict[str, Tuple[str, bool]] = {}
        self._api_creds = None
        
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

    @staticmethod
    def _extract_status_code(error_msg: str) -> Optional[int]:
        msg = error_msg or ""
        marker = "status_code="
        idx = msg.find(marker)
        if idx < 0:
            return None
        num = []
        for ch in msg[idx + len(marker):]:
            if ch.isdigit():
                num.append(ch)
            else:
                break
        if not num:
            return None
        try:
            return int("".join(num))
        except ValueError:
            return None

    @staticmethod
    def _format_error(exc: Exception) -> str:
        """Extract useful API error details from wrapped exceptions."""
        status_code = getattr(exc, "status_code", None)
        error_msg = (
            getattr(exc, "error_msg", None)
            or getattr(exc, "error_message", None)
            or getattr(exc, "msg", None)
        )
        if error_msg is None and getattr(exc, "args", None):
            error_msg = exc.args[0] if len(exc.args) == 1 else exc.args

        if isinstance(error_msg, (dict, list)):
            try:
                error_msg = json.dumps(error_msg, separators=(",", ":"), ensure_ascii=False)
            except Exception:
                error_msg = str(error_msg)
        elif error_msg is not None:
            error_msg = str(error_msg)

        base_name = exc.__class__.__name__
        if status_code is not None or error_msg:
            parts = [base_name]
            if status_code is not None:
                parts.append(f"status_code={status_code}")
            if error_msg:
                parts.append(f"error={error_msg}")
            return " | ".join(parts)

        raw = str(exc)
        return raw if raw and raw != base_name else base_name

    def _refresh_api_creds(self, reason: str = "auth-retry") -> bool:
        """Re-derive L2 API credentials after 401/invalid-key responses."""
        if not self.client or PAPER_TRADING:
            return False
        try:
            cprint(f"Refreshing API credentials ({reason})...", "yellow")
            creds = self.client.create_or_derive_api_key()
            self._api_creds = creds
            if hasattr(self.client, "set_api_creds"):
                self.client.set_api_creds(creds)
            else:
                self.client = self._build_clob_client(creds=creds)
            self.api_creds_set = True
            return True
        except Exception as exc:
            cprint(f"WARNING: API credential refresh failed ({reason}): {exc}", "yellow")
            return False

    def _start_heartbeat_loop(self) -> None:
        """Start periodic CLOB heartbeat loop when supported by SDK."""
        if PAPER_TRADING or not CLOB_HEARTBEAT_ENABLED or self._heartbeat_running:
            return

        self._heartbeat_running = True

        def _loop():
            while self._heartbeat_running and self.is_connected and self.client:
                try:
                    # SDK post_heartbeat uses /v1/heartbeats with heartbeat_id; API returns
                    # "Invalid Heartbeat ID". Use our fallback: POST /heartbeats with empty body.
                    self._retry_call(self._post_heartbeat_fallback, "Post heartbeat")
                except Exception as exc:
                    msg = str(exc)
                    if self._is_auth_error(msg):
                        self._refresh_api_creds(reason="heartbeat")
                    elif "heartbeat endpoint unavailable" in msg.lower():
                        self._heartbeat_running = False
                        return
                    else:
                        cprint(f"WARNING: Heartbeat failed: {exc}", "yellow")
                time.sleep(max(2.0, CLOB_HEARTBEAT_INTERVAL_SECONDS))

        self._heartbeat_thread = threading.Thread(target=_loop, daemon=True, name="clob-heartbeat")
        self._heartbeat_thread.start()

    def stop_background_tasks(self) -> None:
        """Stop client background tasks (heartbeat loop)."""
        self._heartbeat_running = False

    def _extract_heartbeat_id(self, payload: Any) -> str:
        """Best-effort extraction of the next heartbeat id from API payloads/errors."""
        if isinstance(payload, dict):
            for key in ("heartbeat_id", "heartbeatId", "id"):
                value = payload.get(key)
                if value:
                    return str(value)
        try:
            text = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)
        except Exception:
            text = str(payload)
        match = re.search(r'"heartbeat(?:_id|Id)"\s*:\s*"([^"]+)"', text)
        if match:
            return match.group(1)
        match = re.search(r"heartbeat(?:_id|Id)['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9_-]+)", text)
        if match:
            return match.group(1)
        return ""

    def _post_heartbeat_fallback(self) -> Dict[str, Any]:
        """
        POST heartbeat while tracking the rolling heartbeat id required by the API.
        """
        self.client.assert_level_2_auth()
        errors = []
        for path in ("/heartbeats", "/v1/heartbeats"):
            for attempt in range(2):
                body: Dict[str, Any] = {}
                if self._heartbeat_id:
                    body["heartbeat_id"] = self._heartbeat_id
                serialized = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
                try:
                    if not hasattr(self.client, "_l2_headers") or not hasattr(self.client, "_post"):
                        raise Exception("heartbeat endpoint unavailable")
                    headers = self.client._l2_headers("POST", path, body=body, serialized_body=serialized)
                    response = self.client._post(f"{self.client.host}{path}", headers=headers, data=serialized)
                    next_heartbeat_id = self._extract_heartbeat_id(response)
                    if next_heartbeat_id:
                        self._heartbeat_id = next_heartbeat_id
                    return response
                except Exception as exc:
                    errors.append(str(exc))
                    next_heartbeat_id = self._extract_heartbeat_id(exc)
                    if attempt == 0 and next_heartbeat_id and next_heartbeat_id != self._heartbeat_id:
                        self._heartbeat_id = next_heartbeat_id
                        continue
                    break

        if not self._heartbeat_warned_unavailable:
            cprint("WARNING: Heartbeat endpoint unavailable on current API/SDK combination", "yellow")
            self._heartbeat_warned_unavailable = True
        raise Exception("; ".join(errors) if errors else "heartbeat endpoint unavailable")

    def _build_clob_client(self, creds: Optional["ApiCreds"] = None):
        builder_config = None
        if BuilderConfig is not None and POLYMARKET_BUILDER_CODE:
            try:
                builder_config = BuilderConfig(builder_code=POLYMARKET_BUILDER_CODE)
            except TypeError:
                builder_config = None
        return ClobClient(
            host=CLOB_HOST,
            chain_id=CHAIN_ID,
            key=PRIVATE_KEY,
            creds=creds,
            signature_type=SIGNATURE_TYPE,
            funder=PROXY_ADDRESS,
            builder_config=builder_config,
        )

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
            cprint(f"Refreshed CLOB allowance ({reason})", "cyan")
            if ALLOWANCE_DIAGNOSTICS_ENABLED:
                self._log_allowance_diagnostics(f"after-refresh:{reason}", throttle_seconds=10)
            return True
        except Exception as exc:
            cprint(f"WARNING: Allowance refresh failed ({reason}): {exc}", "yellow")
            return False

    def _log_allowance_diagnostics(self, stage: str, throttle_seconds: int = 30) -> None:
        """Log collateral balance/allowance snapshot for debugging spendability issues."""
        if not ALLOWANCE_DIAGNOSTICS_ENABLED:
            return
        if not self.client or not self.is_connected or not self.api_creds_set:
            return
        now = time.time()
        if throttle_seconds > 0 and (now - self.last_allowance_diag_log) < throttle_seconds:
            return
        self.last_allowance_diag_log = now
        try:
            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=SIGNATURE_TYPE,
            )
            snapshot = self.client.get_balance_allowance(params)
            if isinstance(snapshot, dict):
                fields = []
                for key in ("balance", "allowance", "available", "availableBalance", "available_balance"):
                    if key in snapshot:
                        fields.append(f"{key}={snapshot.get(key)}")
                if fields:
                    cprint(f"Allowance diag [{stage}]: " + ", ".join(fields), "dark_grey")
                else:
                    cprint(f"Allowance diag [{stage}]: {json.dumps(snapshot, separators=(',', ':'))[:240]}", "dark_grey")
            else:
                cprint(f"Allowance diag [{stage}]: {str(snapshot)[:240]}", "dark_grey")
        except Exception as exc:
            cprint(f"WARNING: Allowance diag failed [{stage}]: {self._format_error(exc)}", "yellow")
        
    def connect(self) -> bool:
        """
        Initialize connection to Polymarket CLOB.
        
        Returns:
            True if connected successfully
        """
        if PAPER_TRADING:
            self.is_connected = True
            self.api_creds_set = False
            cprint("Paper trading enabled: skipping authenticated CLOB connection", "yellow")
            return True

        if not CLOB_AVAILABLE:
            cprint("Cannot connect: py-clob-client-v2 not installed", "red")
            return False
        
        if not PRIVATE_KEY or not PROXY_ADDRESS:
            cprint("Cannot connect: Missing PRIVATE_KEY or PROXY_ADDRESS in .env", "red")
            return False
        
        try:
            cprint("Connecting to Polymarket CLOB...", "cyan")
            cprint("Setting up API credentials...", "cyan")
            auth_client = self._build_clob_client()
            creds = auth_client.create_or_derive_api_key()
            self._api_creds = creds
            self.client = self._build_clob_client(creds=creds)
            self.api_creds_set = True
            
            self.is_connected = True
            self._refresh_allowance(force=True, reason="startup")
            self._start_heartbeat_loop()
            cprint("Connected to Polymarket CLOB", "green")
            
            return True
            
        except Exception as e:
            cprint(f"Connection failed: {e}", "red")
            self.is_connected = False
            return False

    def get_api_credentials(self) -> Optional[Dict[str, str]]:
        """Expose the current L2 API credentials for authenticated user streams."""
        if not self.client:
            return None
        creds = getattr(self.client, "creds", None)
        if not creds:
            return None
        api_key = getattr(creds, "api_key", None)
        api_secret = getattr(creds, "api_secret", None)
        api_passphrase = getattr(creds, "api_passphrase", None)
        if not (api_key and api_secret and api_passphrase):
            return None
        return {
            "apiKey": str(api_key),
            "secret": str(api_secret),
            "passphrase": str(api_passphrase),
        }

    def _get_order_create_options(self, token_id: str) -> Optional[Dict[str, Any]]:
        """Resolve per-market options required by the current Polymarket SDK."""
        cached = self._market_meta_cache.get(token_id)
        if cached:
            if "PartialCreateOrderOptions" in globals():
                return PartialCreateOrderOptions(tick_size=cached[0], neg_risk=cached[1])
            return {"tick_size": cached[0], "neg_risk": cached[1]}

        if not self.client:
            return None

        try:
            tick_size = self._retry_call(
                lambda: self.client.get_tick_size(token_id),
                f"Fetch tick size {token_id}",
            )
            neg_risk = self._retry_call(
                lambda: self.client.get_neg_risk(token_id),
                f"Fetch neg risk {token_id}",
            )
            if tick_size:
                resolved = (str(tick_size), bool(neg_risk))
                self._market_meta_cache[token_id] = resolved
                if "PartialCreateOrderOptions" in globals():
                    return PartialCreateOrderOptions(tick_size=resolved[0], neg_risk=resolved[1])
                return {"tick_size": resolved[0], "neg_risk": resolved[1]}
        except Exception as exc:
            cprint(
                f"WARNING: Failed to resolve order options for {token_id}: {self._format_error(exc)}",
                "yellow",
            )
        return None

    def invalidate_market_metadata(self, token_id: Optional[str] = None) -> None:
        """Invalidate cached market metadata after live contract/config changes."""
        if token_id:
            self._market_meta_cache.pop(token_id, None)
            return
        self._market_meta_cache.clear()

    @staticmethod
    def _side_value(side: str):
        return Side.BUY if side.upper() == "BUY" else Side.SELL

    def _make_order_args(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        expiration: Optional[int] = None,
    ):
        kwargs = {
            "price": price,
            "size": size,
            "side": self._side_value(side),
            "token_id": token_id,
        }
        if expiration:
            kwargs["expiration"] = int(expiration)
        try:
            return OrderArgs(**kwargs)
        except TypeError:
            kwargs.pop("expiration", None)
            return OrderArgs(**kwargs)
    
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
        max_attempts = RETRY_MAX_ATTEMPTS
        while attempt < max_attempts:
            try:
                return func()
            except Exception as exc:
                last_error = exc
                attempt += 1
                msg = self._format_error(exc)
                status = getattr(exc, "status_code", None) or self._extract_status_code(msg)
                msg_lower = msg.lower()

                is_rate_limited = status == 429 or "too many requests" in msg_lower
                is_engine_restart = status == 425 or "too early" in msg_lower or "matching engine is restarting" in msg_lower
                transient = is_rate_limited or is_engine_restart
                if transient:
                    max_attempts = max(max_attempts, RETRY_MAX_ATTEMPTS + 2)

                # Deterministic 4xx errors - retrying the same signed payload
                # won't help and just wastes time / makes book drift worse.
                non_retryable_markers = (
                    "order crosses book",       # post-only rejection
                    "post-only",
                    "not enough balance",
                    "insufficient balance",
                    "insufficient allowance",
                    "invalid order",
                    "invalid signature",
                    "invalid tick",
                    "invalid price",
                    "invalid size",
                    "market is closed",
                    "market not accepting",
                )
                if status == 400 and any(marker in msg_lower for marker in non_retryable_markers):
                    cprint(f"{action} failed (non-retryable): {msg}", "red")
                    raise exc

                if attempt >= max_attempts:
                    break

                delay = min(
                    RETRY_MAX_DELAY_SECONDS,
                    RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                )
                if transient:
                    delay = max(1.0, delay * 2)
                jitter = delay * 0.1 * random.random()
                cprint(f"WARNING: {action} failed (attempt {attempt}/{max_attempts}): {msg}", "yellow")
                time.sleep(delay + jitter)
        if last_error:
            raise last_error
    
    def get_markets(self, next_cursor: str = "", tag: str = "", max_pages: Optional[int] = None) -> Dict:
        """
        Fetch available markets from Gamma API with automatic pagination.
        
        Args:
            next_cursor: Pagination cursor (used internally)
            tag: Optional tag filter (e.g. "crypto")
            max_pages: Optional page cap override
            
        Returns:
            List of market dicts (paginated automatically)
        """
        if not self.is_connected:
            return {"error": "Not connected"}
        
        try:
            import requests
            
            url = f"{GAMMA_HOST}/markets"
            all_markets: list = []
            start_offset = int(next_cursor) if str(next_cursor).strip() else 0
            page_cap = max_pages or 10

            for page_idx in range(page_cap):
                params = {
                    "closed": "false",
                    "active": "true",
                    "limit": 100,
                    "offset": start_offset + (page_idx * 100),
                }
                if tag:
                    params["tag"] = tag

                def _request(p=dict(params)):
                    response = requests.get(url, params=p, timeout=30)
                    response.raise_for_status()
                    return response.json()

                result = self._retry_call(_request, "Fetch markets")

                if isinstance(result, list):
                    all_markets.extend(result)
                    if len(result) < 100:
                        break
                elif isinstance(result, dict):
                    data = result.get("data", result.get("markets", []))
                    if isinstance(data, list):
                        all_markets.extend(data)
                    if not isinstance(data, list) or len(data) < 100:
                        break
                else:
                    break

            return all_markets
            
        except Exception as e:
            cprint(f"Failed to fetch markets: {e}", "red")
            return {"error": str(e)}

    def get_markets_page(self, next_cursor: str = "", tag: str = "") -> Dict[str, Any]:
        """Fetch a single /markets page using offset pagination metadata."""
        if not self.is_connected:
            return {"error": "Not connected"}

        try:
            import requests

            url = f"{GAMMA_HOST}/markets"
            current_offset = int(next_cursor) if str(next_cursor).strip() else 0
            params = {"closed": "false", "active": "true", "limit": 100, "offset": current_offset}
            if tag:
                params["tag"] = tag

            def _request(p=dict(params)):
                response = requests.get(url, params=p, timeout=30)
                response.raise_for_status()
                return response.json()

            result = self._retry_call(_request, "Fetch markets page")
            if isinstance(result, list):
                next_offset = current_offset + len(result) if len(result) == 100 else ""
                return {"data": result, "next_cursor": str(next_offset) if next_offset != "" else ""}
            if isinstance(result, dict):
                data = result.get("data", result.get("markets", []))
                next_offset = current_offset + len(data) if isinstance(data, list) and len(data) == 100 else ""
                return {
                    "data": data if isinstance(data, list) else [],
                    "next_cursor": str(next_offset) if next_offset != "" else "",
                }
            return {"data": [], "next_cursor": ""}
        except Exception as e:
            cprint(f"Failed to fetch markets page: {e}", "red")
            return {"error": str(e)}

    def get_events(
        self,
        limit: int = 50,
        max_pages: Optional[int] = None,
        offset: int = 0,
        order: str = "startDate",
        ascending: bool = False,
    ) -> List[Dict]:
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
            all_events: List[Dict] = []
            current_offset = offset

            while True:
                params = {
                    "closed": "false",
                    "active": "true",
                    "limit": limit,
                    "offset": current_offset,
                    "order": order,
                    "ascending": "true" if ascending else "false",
                }

                def _request():
                    response = requests.get(url, params=params, timeout=30)
                    response.raise_for_status()
                    return response.json()

                result = self._retry_call(_request, f"Fetch events offset={current_offset}")
                if not isinstance(result, list) or not result:
                    break

                all_events.extend(result)
                if max_pages is not None and max_pages > 0:
                    max_pages -= 1
                    if max_pages <= 0:
                        break
                if len(result) < limit:
                    break
                current_offset += limit

            return all_events
            
        except Exception as e:
            cprint(f"Failed to fetch events: {e}", "yellow")
            return []

    def get_market(self, condition_id: str) -> Optional[Dict]:
        """
        Fetch market by condition ID if supported.

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
            cprint(f"Failed to fetch market {condition_id}: {e}", "red")
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
            cprint(f"Failed to fetch orderbook: {e}", "red")
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
        order_type: str = "GTC",
        expiration: Optional[int] = None,
        post_only: bool = False,
        fee_rate_bps: Optional[int] = None,
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
            cprint(f"[PAPER] {side} {size:.2f} @ ${price:.3f}", "yellow")
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
            
            order_args = self._make_order_args(token_id, side, price, size, expiration)
            
            create_options = self._get_order_create_options(token_id)

            ot = getattr(OrderType, order_type.upper(), OrderType.GTC)
            result = self._retry_call(
                lambda: self.client.create_and_post_order(
                    order_args=order_args,
                    options=create_options,
                    order_type=ot,
                    post_only=post_only,
                ),
                "Create and post order",
            )
            
            cprint(f"Order placed: {side} {size:.2f} @ ${price:.3f}", "green")
            
            return {
                "success": True,
                "order_id": result.get("orderID") or result.get("id"),
                "result": result
            }
            
        except Exception as e:
            error_msg = self._format_error(e)
            if self._is_allowance_error(error_msg):
                self._log_allowance_diagnostics("order-error:before-refresh", throttle_seconds=10)
                cprint("Allowance error detected, forcing allowance refresh and retrying once...", "yellow")
                refreshed = self._refresh_allowance(force=True, reason="order-error")
                if refreshed:
                    self._log_allowance_diagnostics("order-error:after-refresh", throttle_seconds=0)
                    try:
                        self._rate_limit()
                        order_args = self._make_order_args(token_id, side, price, size, expiration)
                        create_options = self._get_order_create_options(token_id)
                        ot = getattr(OrderType, order_type.upper(), OrderType.GTC)
                        result = self._retry_call(
                            lambda: self.client.create_and_post_order(
                                order_args=order_args,
                                options=create_options,
                                order_type=ot,
                                post_only=post_only,
                            ),
                            "Create and post order",
                        )
                        cprint(f"Order placed after allowance refresh: {side} {size:.2f} @ ${price:.3f}", "green")
                        return {
                            "success": True,
                            "order_id": result.get("orderID") or result.get("id"),
                            "result": result,
                        }
                    except Exception as retry_exc:
                        error_msg = self._format_error(retry_exc)
            cprint(f"Order failed: {error_msg}", "red")
            
            return {
                "success": False,
                "error": error_msg
            }

    def place_orders_batch(self, orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Place multiple limit orders in a single API request when possible.

        Args:
            orders: List of order dicts with token_id, side, price, size, order_type

        Returns:
            Per-order result list aligned with input order list.
        """
        if not self.is_connected:
            return [{"success": False, "error": "Not connected"} for _ in orders]
        if not orders:
            return []

        # Paper trading mode
        if PAPER_TRADING:
            now_ms = int(time.time() * 1000)
            results = []
            for idx, order in enumerate(orders):
                results.append(
                    {
                        "success": True,
                        "order_id": f"paper_batch_{now_ms}_{idx}",
                        "paper_trade": True,
                        "side": (order.get("side") or "").upper(),
                        "price": float(order.get("price", 0)),
                        "size": float(order.get("size", 0)),
                    }
                )
            return results

        def _submit_batch() -> List[Dict[str, Any]]:
            self._refresh_allowance(force=False, reason="pre-batch-order")
            self._rate_limit()

            batch_args: List[PostOrdersArgs] = []
            for order in orders:
                side = (order.get("side") or "").upper()
                token_id = order["token_id"]
                order_args = self._make_order_args(
                    token_id=token_id,
                    side=side,
                    price=float(order["price"]),
                    size=float(order["size"]),
                    expiration=order.get("expiration"),
                )
                create_options = self._get_order_create_options(token_id)
                signed_order = self._retry_call(
                    lambda oa=order_args, opts=create_options: self.client.create_order(oa, opts),
                    "Create batch order",
                )
                ot = getattr(OrderType, str(order.get("order_type", "GTC")).upper(), OrderType.GTC)
                post_orders_cls = PostOrdersV2Args or PostOrdersArgs
                batch_args.append(post_orders_cls(order=signed_order, orderType=ot))

            posted = self._retry_call(
                lambda: self.client.post_orders(batch_args),
                "Post batch orders",
            )

            results: List[Dict[str, Any]] = []
            posted_items = posted if isinstance(posted, list) else []
            for idx, req in enumerate(orders):
                item = posted_items[idx] if idx < len(posted_items) else {}
                order_id = (
                    (item.get("orderID") or item.get("id") or item.get("order_id"))
                    if isinstance(item, dict)
                    else None
                )
                item_error = item.get("error") if isinstance(item, dict) else None
                if item_error:
                    results.append({"success": False, "error": str(item_error)})
                else:
                    results.append(
                        {
                            "success": True,
                            "order_id": order_id or f"batch_{int(time.time()*1000)}_{idx}",
                            "result": item if isinstance(item, dict) else {},
                        }
                    )
            return results

        try:
            return _submit_batch()
        except Exception as exc:
            error_msg = self._format_error(exc)
            if self._is_allowance_error(error_msg):
                cprint(
                    "🔁 Batch order allowance error detected, forcing allowance refresh and retrying once...",
                    "yellow",
                )
                if self._refresh_allowance(force=True, reason="batch-order-error"):
                    try:
                        return _submit_batch()
                    except Exception as retry_exc:
                        error_msg = self._format_error(retry_exc)
            return [{"success": False, "error": error_msg} for _ in orders]
    
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
            if hasattr(self.client, "cancel_order"):
                result = self._retry_call(
                    lambda: self.client.cancel_order(OrderPayload(orderID=order_id)),
                    f"Cancel order {order_id}",
                )
            else:
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
            fetch_open_orders = getattr(self.client, "get_open_orders", None) or getattr(self.client, "get_orders")
            orders = self._retry_call(lambda: fetch_open_orders(), "Fetch open orders")
            return orders if orders else []
            
        except Exception as e:
            error_msg = self._format_error(e)
            if self._is_auth_error(error_msg) and self._refresh_api_creds(reason="get_open_orders"):
                try:
                    fetch_open_orders = getattr(self.client, "get_open_orders", None) or getattr(self.client, "get_orders")
                    orders = self._retry_call(lambda: fetch_open_orders(), "Fetch open orders")
                    return orders if orders else []
                except Exception as retry_exc:
                    error_msg = self._format_error(retry_exc)
            cprint(f"❌ Failed to fetch orders: {error_msg}", "red")
            return []
    
    def get_trades(self, limit: int = 100, timeout_s: Optional[float] = None) -> List[Dict]:
        """Get recent trades for our account (bounded timeout to protect scan loop).
        
        Fetches from BOTH CLOB (maker fills) and Data API (maker + taker fills), then merges
        and dedupes. This ensures we never miss taker fills when we take liquidity.
        """
        if not self.is_connected:
            return []
        if PAPER_TRADING:
            return []
        effective_timeout = timeout_s if (timeout_s and timeout_s > 0) else TRADE_FETCH_TIMEOUT_SECONDS
        clob_trades: List[Dict] = []
        data_trades: List[Dict] = []

        def _fetch_clob():
            params = None
            if not PAPER_TRADING and PROXY_ADDRESS and CLOB_AVAILABLE:
                params = TradeParams(maker_address=PROXY_ADDRESS.lower())
            return self._retry_call(lambda: self.client.get_trades(params), "Fetch trades") or []

        def _fetch_data():
            if not PAPER_TRADING and PROXY_ADDRESS:
                return get_trades_by_user(PROXY_ADDRESS, limit=min(limit, 100), taker_only=False)
            return []

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                f_clob = ex.submit(
                    lambda: _call_with_timeout_strict(_fetch_clob, timeout_s=effective_timeout / 2, timeout_default=[])
                )
                f_data = ex.submit(_fetch_data)
                try:
                    clob_trades = f_clob.result(timeout=effective_timeout)
                except Exception:
                    pass
                try:
                    data_trades = f_data.result(timeout=effective_timeout)
                except Exception:
                    pass

            # Merge and dedupe by trade id (prefer CLOB format when both have same trade)
            seen: Dict[str, Dict] = {}
            for t in (clob_trades or []) + (data_trades or []):
                tid = t.get("id") or t.get("trade_id")
                if not tid:
                    aid = t.get("asset_id") or t.get("token_id") or t.get("asset")
                    ts = t.get("timestamp")
                    if aid is not None and ts is not None:
                        tid = f"{aid}_{t.get('side')}_{t.get('price')}_{t.get('size')}_{ts}"
                if tid and tid not in seen:
                    seen[tid] = t
            trades = list(seen.values())
            # Sort by timestamp descending (newest first)
            def _ts(t):
                ts = t.get("timestamp") or t.get("created_at") or 0
                return float(ts) if ts else 0
            trades.sort(key=_ts, reverse=True)
            return trades[:limit]

        except Exception as e:
            error_msg = self._format_error(e)
            if self._is_auth_error(error_msg) and self._refresh_api_creds(reason="get_trades"):
                try:
                    clob_trades = _call_with_timeout_strict(_fetch_clob, timeout_s=effective_timeout, timeout_default=[])
                    data_trades = get_trades_by_user(PROXY_ADDRESS, limit=min(limit, 100), taker_only=False) if PROXY_ADDRESS else []
                    seen = {}
                    for t in (clob_trades or []) + (data_trades or []):
                        tid = t.get("id") or t.get("trade_id") or ""
                        if tid and tid not in seen:
                            seen[tid] = t
                    return list(seen.values())[:limit]
                except Exception as retry_exc:
                    error_msg = self._format_error(retry_exc)
            cprint(f"❌ Failed to fetch trades: {error_msg}", "red")
            return []

    def get_positions(self, limit: int = 100) -> Optional[List[Dict]]:
        """Get current positions from Data API (ground truth for exposure).

        Returns ``None`` on fetch failure so callers can distinguish between
        "API error" and "authoritative empty positions snapshot".
        """
        if PAPER_TRADING or not PROXY_ADDRESS:
            return []
        try:
            positions = get_positions(PROXY_ADDRESS, limit=limit)
            return positions if isinstance(positions, list) else []
        except Exception:
            return None

    def get_balance_diagnostics(self) -> Dict[str, Any]:
        """Resolve live balance and expose which source produced it."""
        if PAPER_TRADING:
            return {"balance": float(PAPER_BALANCE_USD), "source": "paper", "details": {}}

        if not self.is_connected or not self.client:
            return {"balance": None, "source": "disconnected", "details": {}}

        def normalize_balance_value(value: Optional[float]) -> Optional[float]:
            if value is None:
                return None
            if value >= 100000 and abs(value - round(value)) < 1e-9:
                return value / 1_000_000.0
            return value

        def parse_balance(payload) -> Optional[float]:
            if payload is None:
                return None
            if isinstance(payload, (int, float)):
                return normalize_balance_value(float(payload))
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
                            return normalize_balance_value(float(payload[key]))
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

        def _resolve() -> Dict[str, Any]:
            zero_candidate: Optional[float] = None
            zero_source = "unavailable"
            details: Dict[str, Any] = {
                "proxy_address": PROXY_ADDRESS,
                "signature_type": SIGNATURE_TYPE,
            }

            for method_name in ("get_balance", "get_collateral", "get_account"):
                method = getattr(self.client, method_name, None)
                if not method:
                    continue
                try:
                    response = method()
                    bal = parse_balance(response)
                    details[f"method_{method_name}"] = bal
                except Exception as exc:
                    details[f"method_{method_name}_error"] = self._format_error(exc)
                    continue
                if bal is not None:
                    if bal > 0:
                        return {"balance": bal, "source": f"clob:{method_name}", "details": details}
                    zero_candidate = bal
                    zero_source = f"clob:{method_name}"

            if CLOB_AVAILABLE:
                try:
                    params = BalanceAllowanceParams(
                        asset_type=AssetType.COLLATERAL,
                        signature_type=SIGNATURE_TYPE,
                    )
                    allowance_snapshot = self.client.get_balance_allowance(params)
                    bal = parse_balance(allowance_snapshot)
                    details["allowance_balance"] = bal
                    if bal is not None:
                        if bal > 0:
                            return {"balance": bal, "source": "allowance", "details": details}
                        if zero_candidate is None:
                            zero_candidate = bal
                            zero_source = "allowance"
                except Exception as exc:
                    details["allowance_error"] = self._format_error(exc)

            if PROXY_ADDRESS:
                try:
                    fallback = get_balance_total(PROXY_ADDRESS)
                    details["fallback_total"] = fallback
                    if fallback is not None and fallback >= 0:
                        return {"balance": fallback, "source": "fallback_total", "details": details}
                except Exception as exc:
                    details["fallback_total_error"] = self._format_error(exc)

            return {"balance": zero_candidate, "source": zero_source, "details": details}

        return _call_with_timeout(
            _resolve,
            timeout_s=BALANCE_FETCH_TIMEOUT_SECONDS,
            default={"balance": None, "source": "timeout", "details": {}},
        )

    def get_balance(self) -> Optional[float]:
        """
        Get USDC balance.
        
        Note: This may require additional setup depending on API version.
        """
        if PAPER_TRADING:
            return float(PAPER_BALANCE_USD)
        diagnostics = self.get_balance_diagnostics()
        return diagnostics.get("balance")


# Convenience function for quick client creation
def create_client() -> PolymarketClient:
    """Create and connect a Polymarket client."""
    client = PolymarketClient()
    client.connect()
    return client
