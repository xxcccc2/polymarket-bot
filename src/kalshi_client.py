"""Kalshi API client for market data and trading."""

from __future__ import annotations

import base64
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Any

import requests

from .logging_utils import cprint
from .config import (
    KALSHI_BASE_URL,
    KALSHI_TRADE_API_PATH,
    KALSHI_ACCESS_KEY,
    KALSHI_PRIVATE_KEY_PATH,
    KALSHI_RATE_LIMIT_PER_SECOND,
    KALSHI_ORDERBOOK_TTL_SECONDS,
    RETRY_MAX_ATTEMPTS,
    RETRY_BASE_DELAY_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
)


@dataclass
class KalshiBestPrices:
    yes_bid: Optional[float]
    yes_ask: Optional[float]
    no_bid: Optional[float]
    no_ask: Optional[float]
    timestamp: datetime


class KalshiClient:
    """Minimal Kalshi REST client with rate limiting and optional auth."""

    def __init__(
        self,
        base_url: str = KALSHI_BASE_URL,
        api_key: Optional[str] = KALSHI_ACCESS_KEY,
        private_key_path: Optional[str] = KALSHI_PRIVATE_KEY_PATH,
        rate_limit_per_second: int = KALSHI_RATE_LIMIT_PER_SECOND,
        timeout: int = 15,
        orderbook_ttl_seconds: int = KALSHI_ORDERBOOK_TTL_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.private_key_path = private_key_path
        self.timeout = timeout
        self.orderbook_ttl_seconds = orderbook_ttl_seconds
        self._session = requests.Session()

        self._min_interval = 1 / max(1, rate_limit_per_second)
        self._lock = threading.Lock()
        self._last_request_at = 0.0
        self._orderbook_cache: Dict[str, Dict[str, Any]] = {}

    def _rate_limit(self) -> None:
        with self._lock:
            now = time.time()
            elapsed = now - self._last_request_at
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request_at = time.time()

    def _retry_call(self, func, action: str):
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
                cprint(f"⚠️  Kalshi {action} failed (attempt {attempt}/{RETRY_MAX_ATTEMPTS}): {exc}", "yellow")
                time.sleep(delay + jitter)
        if last_error:
            raise last_error

    def _load_private_key(self):
        if not self.private_key_path:
            return None
        try:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives import serialization
        except ImportError as exc:
            raise RuntimeError("cryptography is required for Kalshi auth") from exc

        with open(self.private_key_path, "rb") as key_file:
            return serialization.load_pem_private_key(
                key_file.read(),
                password=None,
                backend=default_backend(),
            )

    def _sign(self, method: str, path: str, timestamp_ms: int) -> str:
        private_key = self._load_private_key()
        if not private_key:
            raise RuntimeError("Kalshi private key not configured")

        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError as exc:
            raise RuntimeError("cryptography is required for Kalshi auth") from exc

        message = f"{timestamp_ms}{method.upper()}{path}".encode("utf-8")
        signature = private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    def _headers(self, method: str, path: str) -> Dict[str, str]:
        if not self.api_key or not self.private_key_path:
            return {}

        timestamp_ms = int(time.time() * 1000)
        signature = self._sign(method, path, timestamp_ms)
        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
        }

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._rate_limit()
        if not path.startswith("/"):
            path = "/" + path
        url = f"{self.base_url}{path}"
        headers = self._headers(method, path.split("?")[0])

        def _do_request():
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=self.timeout,
            )
            if response.status_code >= 400:
                raise RuntimeError(f"Kalshi API error {response.status_code}: {response.text}")
            return response.json()

        return self._retry_call(_do_request, f"{method} {path}")

    def get_markets(self, status: str = "open", series_ticker: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
        params: Dict[str, Any] = {"status": status, "limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        path = f"{KALSHI_TRADE_API_PATH}/markets"
        return self._request("GET", path, params=params)

    def get_market_orderbook(self, ticker: str) -> Dict[str, Any]:
        cache = self._orderbook_cache.get(ticker)
        now = time.time()
        if cache and (now - cache["timestamp"] <= self.orderbook_ttl_seconds):
            return cache["data"]

        path = f"{KALSHI_TRADE_API_PATH}/markets/{ticker}/orderbook"
        data = self._request("GET", path)
        self._orderbook_cache[ticker] = {"timestamp": now, "data": data}
        return data

    def get_balance(self) -> Optional[Dict[str, Any]]:
        if not self.api_key or not self.private_key_path:
            cprint("⚠️  Kalshi credentials missing - balance unavailable", "yellow")
            return None

        path = f"{KALSHI_TRADE_API_PATH}/portfolio/balance"
        try:
            return self._request("GET", path)
        except Exception as exc:
            cprint(f"❌ Kalshi balance error: {exc}", "red")
            return None

    def create_order(
        self,
        ticker: str,
        side: str,
        action: str,
        count: int,
        price: float,
        order_type: str = "limit",
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Place a Kalshi order (requires auth)."""
        if not self.api_key or not self.private_key_path:
            raise RuntimeError("Kalshi credentials not configured")

        if price <= 0 or price >= 1:
            raise ValueError("Kalshi price must be between 0 and 1")

        payload: Dict[str, Any] = {
            "ticker": ticker,
            "side": side.lower(),
            "action": action.lower(),
            "count": int(count),
            "price": int(round(price * 100)),
            "type": order_type,
        }
        if client_order_id:
            payload["client_order_id"] = client_order_id

        path = f"{KALSHI_TRADE_API_PATH}/portfolio/orders"
        return self._request("POST", path, json_body=payload)

    def get_best_prices(self, ticker: str) -> Optional[KalshiBestPrices]:
        try:
            response = self.get_market_orderbook(ticker)
        except Exception as exc:
            cprint(f"❌ Kalshi orderbook error for {ticker}: {exc}", "red")
            return None

        orderbook = response.get("orderbook") if isinstance(response, dict) else None
        if not orderbook:
            return None

        yes_bids = orderbook.get("yes", [])
        no_bids = orderbook.get("no", [])

        def best_bid(side_bids: List[List[float]]) -> Optional[float]:
            if not side_bids:
                return None
            return float(side_bids[-1][0]) / 100

        yes_bid = best_bid(yes_bids)
        no_bid = best_bid(no_bids)

        yes_ask = None if no_bid is None else round(1 - no_bid, 4)
        no_ask = None if yes_bid is None else round(1 - yes_bid, 4)

        return KalshiBestPrices(
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            timestamp=datetime.now(),
        )
