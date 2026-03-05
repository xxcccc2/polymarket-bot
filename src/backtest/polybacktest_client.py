"""
PolyBackTest API client.

HTTP client for PolyBackTest API with rate limiting and error handling.
"""

from __future__ import annotations

import time
import random
from typing import Any, Dict, List, Optional

import requests

from ..config import POLYBACKTEST_API_KEY, POLYBACKTEST_BASE_URL
from ..logging_utils import cprint

# Rate limit: 2000 req/min, 100 req/sec burst. Use conservative 30 req/sec.
_MIN_REQUEST_INTERVAL = 1.0 / 30


class PolyBackTestError(Exception):
    """PolyBackTest API error."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class PolyBackTestClient:
    """
    HTTP client for PolyBackTest API.

    Usage:
        client = PolyBackTestClient()
        markets = client.list_markets(coin="btc", market_type="5m", limit=50)
        snapshots = client.get_snapshots(market_id="123", limit=1000)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key or POLYBACKTEST_API_KEY
        self.base_url = (base_url or POLYBACKTEST_BASE_URL).rstrip("/")
        self._last_request_time = 0.0

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise PolyBackTestError("POLYBACKTEST_API_KEY not set")
        return {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Any:
        url = f"{self.base_url}{path}"
        self._rate_limit()

        for attempt in range(max_retries):
            try:
                resp = requests.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params or {},
                    timeout=60,
                )

                if resp.status_code == 401:
                    raise PolyBackTestError(
                        "Invalid or missing API key", status_code=401
                    )
                if resp.status_code == 402:
                    detail = resp.json().get("detail", {}) if resp.text else {}
                    msg = detail.get("message", "Upgrade required")
                    raise PolyBackTestError(msg, status_code=402)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    if attempt < max_retries - 1:
                        cprint(
                            f"  Rate limited, retrying in {retry_after}s...",
                            "yellow",
                        )
                        time.sleep(retry_after + random.uniform(0, 2))
                        continue
                    raise PolyBackTestError(
                        "Rate limit exceeded", status_code=429
                    )
                if resp.status_code >= 400:
                    try:
                        err = resp.json()
                        msg = err.get("detail", str(err))
                    except Exception:
                        msg = resp.text or f"HTTP {resp.status_code}"
                    raise PolyBackTestError(msg, status_code=resp.status_code)

                return resp.json() if resp.content else {}

            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    delay = 2 ** attempt + random.uniform(0, 1)
                    cprint(f"  Request failed, retrying in {delay:.1f}s: {e}", "yellow")
                    time.sleep(delay)
                else:
                    raise PolyBackTestError(str(e)) from e

        raise PolyBackTestError("Max retries exceeded")

    def get_limits(self) -> Dict[str, Any]:
        """
        Get current plan limits.

        Returns:
            Dict with plan, limits (5m_markets, 15m_markets, etc.)
        """
        return self._request("GET", "/v2/limits")

    def list_markets(
        self,
        coin: str = "btc",
        market_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        resolved: Optional[bool] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List markets with pagination.

        Args:
            coin: btc or eth
            market_type: 5m, 15m, 1hr, 4hr, 24hr
            limit: Max results (max 100)
            offset: Pagination offset
            resolved: Filter by resolution status
            start_time: Filter markets starting after (ms epoch or ISO8601)
            end_time: Filter markets starting before

        Returns:
            {markets: [...], total: int, limit: int, offset: int, warning?: str}
        """
        params: Dict[str, Any] = {
            "coin": coin,
            "limit": min(limit, 100),
            "offset": offset,
        }
        if market_type:
            params["market_type"] = market_type
        if resolved is not None:
            params["resolved"] = resolved
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time

        return self._request("GET", "/v2/markets", params=params)

    def get_market(
        self,
        market_id: str,
        coin: str = "btc",
    ) -> Dict[str, Any]:
        """Get single market by ID."""
        return self._request(
            "GET",
            f"/v2/markets/{market_id}",
            params={"coin": coin},
        )

    def get_snapshots(
        self,
        market_id: str,
        coin: str = "btc",
        limit: int = 1000,
        offset: int = 0,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        include_orderbook: bool = False,
    ) -> Dict[str, Any]:
        """
        Get snapshots for a market.

        Args:
            market_id: Market ID
            coin: btc or eth
            limit: Max 1000 per request
            offset: Pagination offset
            start_time: Filter snapshots after (ms epoch or ISO8601)
            end_time: Filter snapshots before
            include_orderbook: Include full orderbook (larger payloads)

        Returns:
            {market: {...}, snapshots: [...], total: int, limit: int, offset: int}
        """
        params: Dict[str, Any] = {
            "coin": coin,
            "limit": min(limit, 1000),
            "offset": offset,
            "include_orderbook": include_orderbook,
        }
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time

        return self._request(
            "GET",
            f"/v2/markets/{market_id}/snapshots",
            params=params,
        )
