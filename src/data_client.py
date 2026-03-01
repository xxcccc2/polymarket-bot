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


def get_positions(user: str, limit: int = 100) -> List[Dict]:
    """
    Fetch current open positions for a wallet from Data API.
    Returns list of position dicts with asset, size, avgPrice, currentValue, etc.
    Use this for ground-truth exposure instead of reconstructing from fills.
    """
    if not user or not user.startswith("0x"):
        return []
    try:
        params = {"user": user, "limit": min(limit, 500)}
        data = _request("GET", "/positions", params=params)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def get_portfolio_value(user: str) -> Optional[float]:
    """
    Get total portfolio value for a wallet (proxy address) from Data API /value.
    Note: /value returns POSITION value (market exposure) only — NOT free USDC.
    If you have mostly cash, use get_balance_total() instead.
    """
    if not user or not user.startswith("0x"):
        return None
    try:
        params = {"user": user}
        data = _request("GET", "/value", params=params)
        if isinstance(data, list) and len(data) > 0:
            # Sum all values in case API returns multiple entries (e.g. per-market)
            total = 0.0
            for item in data:
                if isinstance(item, dict):
                    v = item.get("value")
                    if v is not None:
                        try:
                            total += float(v)
                        except (TypeError, ValueError):
                            pass
            if total >= 0:
                return total
        if isinstance(data, dict) and "value" in data:
            return float(data["value"])
    except Exception:
        pass
    return None


# USDC.e (bridged) on Polygon - used by Polymarket
_USDC_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
_POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"


def get_usdc_balance_on_chain(address: str) -> Optional[float]:
    """
    Read USDC balance from Polygon for a wallet (proxy address).
    Returns balance in USD (6 decimals). Use when Data API /value is too low
    (it returns position value only, not free USDC).
    """
    if not address or not address.startswith("0x") or len(address) != 42:
        return None
    try:
        import requests
        # balanceOf(address) selector
        selector = "0x70a08231"
        # Pad address to 32 bytes (lowercase, no 0x for the param)
        addr = address.lower().replace("0x", "").zfill(64)
        data_hex = selector + addr
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [
                {"to": _USDC_POLYGON, "data": data_hex},
                "latest",
            ],
            "id": 1,
        }
        resp = requests.post(_POLYGON_RPC, json=payload, timeout=10)
        if resp.status_code != 200:
            return None
        j = resp.json()
        result = j.get("result")
        if not result or result == "0x":
            return 0.0
        return int(result, 16) / 1_000_000  # USDC has 6 decimals
    except Exception:
        return None


def get_balance_total(user: str) -> Optional[float]:
    """
    Get best estimate of total account value (free USDC + positions).
    - On-chain USDC = proxy wallet balance (source of truth for available cash)
    - Data API /value = position value only (market exposure, NOT free USDC)
    When /value shows $0.02 but real balance is $77+, we use on-chain USDC.
    Fetches both in parallel to reduce latency.
    """
    if not user or not user.startswith("0x"):
        return None
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_on_chain = ex.submit(get_usdc_balance_on_chain, user)
        f_position = ex.submit(get_portfolio_value, user)
        try:
            on_chain = f_on_chain.result(timeout=10)
        except concurrent.futures.TimeoutError:
            on_chain = None
        try:
            position_val = f_position.result(timeout=10)
        except concurrent.futures.TimeoutError:
            position_val = None
    # Prefer on-chain when substantial (fixes /value showing $0.02 when balance is $77+)
    if on_chain is not None and on_chain >= 1.0:
        pv = position_val if position_val is not None and position_val > 0 else 0
        return on_chain + pv
    # Fallback: /value when on-chain fails, returns 0, or RPC unavailable
    return position_val
