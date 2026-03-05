"""
Mappers for converting PolyBackTest data to bot types.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from ..strategies.base_strategy import MarketData


def _parse_end_ts(market: dict) -> Optional[float]:
    """Parse market end_time to Unix seconds."""
    val = market.get("end_time")
    if not val:
        return None
    if isinstance(val, (int, float)):
        v = float(val)
        if v > 1e12:
            return v / 1000
        if v > 1e9:
            return v
        return None
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            pass
    return None


def _parse_snapshot_time(snapshot: dict) -> datetime:
    """Parse snapshot time to datetime."""
    val = snapshot.get("time")
    if isinstance(val, datetime):
        return val
    if isinstance(val, (int, float)):
        if val > 1e12:
            val = val / 1000
        return datetime.utcfromtimestamp(val)
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.utcnow()


def snapshot_to_market_data(
    snapshot: dict,
    market: dict,
    outcome: str = "Up",
) -> Optional[MarketData]:
    """
    Convert a snapshot + market to MarketData for strategy.analyze().

    Args:
        snapshot: Snapshot dict with price_up, price_down, btc_price, time
        market: Market dict with slug, clob_token_up, clob_token_down, etc.
        outcome: "Up" or "Down" — which outcome token to build MarketData for

    Returns:
        MarketData or None if missing required fields
    """
    outcome = outcome or "Up"
    is_up = outcome.lower() == "up"

    token_id = market.get("clob_token_up") if is_up else market.get("clob_token_down")
    if not token_id:
        return None

    price_up = float(snapshot.get("price_up") or 0)
    price_down = float(snapshot.get("price_down") or 0)

    if is_up:
        mid = price_up
    else:
        mid = price_down

    if mid <= 0 or mid >= 1:
        return None

    # Assume 1c spread each side for backtest
    spread = 0.02
    best_bid = max(0.01, min(0.99, mid - 0.01))
    best_ask = max(0.01, min(0.99, mid + 0.01))

    condition_id = market.get("condition_id") or ""
    slug = market.get("slug") or ""
    question = market.get("question") or f"{outcome} or Down"
    if "question" not in market and slug:
        question = slug.replace("-", " ").title()

    end_ts = _parse_end_ts(market)
    ts = _parse_snapshot_time(snapshot)

    volume = float(market.get("final_volume") or market.get("volume") or 0)
    liquidity = float(market.get("final_liquidity") or market.get("liquidity") or 0)

    orderbook = None
    if snapshot.get("orderbook_up") or snapshot.get("orderbook_down"):
        ob = snapshot.get("orderbook_up") if is_up else snapshot.get("orderbook_down")
        if ob:
            orderbook = ob

    return MarketData(
        token_id=str(token_id),
        condition_id=str(condition_id),
        market_slug=slug,
        question=question,
        outcome=outcome,
        best_bid=best_bid,
        best_ask=best_ask,
        mid_price=mid,
        spread=best_ask - best_bid,
        volume_24h=volume,
        liquidity=liquidity,
        last_price=mid,
        timestamp=ts,
        orderbook=orderbook,
        recent_trades=None,
        end_date_ts=end_ts,
    )


def snapshots_to_market_data_list(
    snapshot: dict,
    market: dict,
) -> List[MarketData]:
    """
    Build MarketData for both Up and Down outcomes from one snapshot.

    Returns:
        List of 2 MarketData (Up, Down) for strategy.analyze().
    """
    result = []
    for outcome in ("Up", "Down"):
        md = snapshot_to_market_data(snapshot, market, outcome)
        if md:
            result.append(md)
    return result
