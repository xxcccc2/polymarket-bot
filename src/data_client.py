"""
Polymarket Data API Client

Fetches public data: trades by wallet, leaderboard, etc.
Used by wallet_copy strategy to track and copy top traders.
Base URL: https://data-api.polymarket.com
"""

import time
import random
from typing import Dict, List, Optional, Any

from .config import RETRY_MAX_ATTEMPTS, RETRY_BASE_DELAY_SECONDS, RETRY_MAX_DELAY_SECONDS

DATA_API_BASE = "https://data-api.polymarket.com"


def _request(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 15,
) -> Optional[Any]:
    """Make HTTP request to Data API with retries."""
    import requests
    url = f"{DATA_API_BASE}{path}"
    attempt = 0
    last_error = None
    while attempt < RETRY_MAX_ATTEMPTS:
        try:
            resp = requests.request(
                method,
                url,
                params=params or {},
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_error = e
            attempt += 1
            if attempt < RETRY_MAX_ATTEMPTS:
                delay = min(
                    RETRY_MAX_DELAY_SECONDS,
                    RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                )
                time.sleep(delay + 0.1 * random.random())
    raise last_error


def get_trades_by_user(
    user: str,
    limit: int = 50,
    offset: int = 0,
    side: Optional[str] = None,
    taker_only: bool = True,
) -> List[Dict]:
    """
    Fetch recent trades for a wallet (proxy address).
    
    Args:
        user: Proxy wallet address (0x...)
        limit: Max trades to return (1-10000)
        offset: Pagination offset
        side: "BUY" or "SELL" to filter
        taker_only: If True, only taker fills (default: True)
    
    Returns:
        List of trade dicts with: asset, side, price, size, timestamp, 
        conditionId, title, slug, eventSlug, outcome, etc.
    """
    params: Dict[str, Any] = {
        "user": user,
        "limit": min(limit, 1000),
        "offset": offset,
        "takerOnly": str(taker_only).lower(),
    }
    if side:
        params["side"] = side
    data = _request("GET", "/trades", params=params)
    return data if isinstance(data, list) else []


def get_leaderboard(
    category: str = "OVERALL",
    time_period: str = "MONTH",
    order_by: str = "PNL",
    limit: int = 25,
    offset: int = 0,
) -> List[Dict]:
    """
    Fetch trader leaderboard rankings.
    
    Args:
        category: OVERALL, CRYPTO, POLITICS, SPORTS, etc.
        time_period: DAY, WEEK, MONTH, ALL
        order_by: PNL (profit) or VOL (volume)
        limit: Max entries (1-50)
        offset: Pagination
    
    Returns:
        List of TraderLeaderboardEntry: rank, proxyWallet, userName, vol, pnl, etc.
    """
    params = {
        "category": category,
        "timePeriod": time_period,
        "orderBy": order_by,
        "limit": min(limit, 50),
        "offset": offset,
    }
    data = _request("GET", "/v1/leaderboard", params=params)
    return data if isinstance(data, list) else []


def get_portfolio_value(user: str) -> Optional[float]:
    """
    Get total portfolio value for a wallet (proxy address).
    Returns total USDC value (positions + available) — used when CLOB balance is unavailable.
    """
    if not user or not user.startswith("0x"):
        return None
    try:
        params = {"user": user}
        data = _request("GET", "/value", params=params)
        if isinstance(data, list) and len(data) > 0:
            val = data[0].get("value")
            if val is not None:
                return float(val)
        if isinstance(data, dict) and "value" in data:
            return float(data["value"])
    except Exception:
        pass
    return None
