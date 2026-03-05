"""
Wallet activity and trade-profile metrics.

Computes n_trades, crypto_pct, shortterm_pct, trades_per_day,
days_since_last_trade from recent trades.
"""

import time
from collections import defaultdict
from typing import Dict, Optional

from ..data_client import get_trades_by_user

# Fast-market horizons (resolve within ~24h)
FAST_HORIZONS = ("5min", "15min", "1h", "daily")


def _categorize_market(trade: dict) -> str:
    """Infer market category from trade metadata."""
    title = (trade.get("title") or "").lower()
    slug = (trade.get("slug") or trade.get("eventSlug") or "").lower()
    text = f"{title} {slug}"
    if any(kw in text for kw in ["bitcoin", "btc"]):
        if any(kw in text for kw in ["up or down", "updown", "5m", "15m", "1h"]):
            return "btc_shortterm"
        return "btc"
    if any(kw in text for kw in ["ethereum", "eth", "solana", "sol", "xrp"]):
        return "crypto"
    if any(kw in text for kw in ["trump", "biden", "election", "president"]):
        return "politics"
    if any(kw in text for kw in ["sport", "nfl", "nba", "mlb", "game"]):
        return "sports"
    return "other"


def _infer_horizon(trade: dict) -> str:
    """Infer time horizon from slug."""
    slug = (trade.get("slug") or trade.get("eventSlug") or "").lower()
    if "5m" in slug or "5 min" in slug:
        return "5min"
    if "15m" in slug or "15 min" in slug:
        return "15min"
    if "1h" in slug or "1 hour" in slug:
        return "1h"
    if "day" in slug or "daily" in slug:
        return "daily"
    if "week" in slug or "month" in slug or "year" in slug or "2025" in slug or "2026" in slug:
        return "longterm"
    return "unknown"


def compute_wallet_metrics(
    addr: str,
    max_trades: int = 200,
    sleep_between_pages: float = 0.2,
) -> Optional[Dict]:
    """
    Fetch trades and compute metrics for scoring.

    Args:
        addr: Proxy wallet address (0x...)
        max_trades: Max trades to fetch
        sleep_between_pages: Sleep between API pagination calls

    Returns:
        Dict with address, n_trades, crypto_pct, shortterm_pct, avg_buy_usd,
        trades_per_day, days_since_last_trade, categories, horizons.
        None if no trades or fetch failed.
    """
    addr = addr.strip()
    if not addr or not addr.startswith("0x"):
        return None

    trades = []
    offset = 0
    while len(trades) < max_trades:
        try:
            batch = get_trades_by_user(addr, limit=100, offset=offset, taker_only=False)
        except Exception:
            return None
        if not batch:
            break
        trades.extend(batch)
        if len(batch) < 100:
            break
        offset += len(batch)
        time.sleep(sleep_between_pages)
    trades = trades[:max_trades]

    if not trades:
        return None

    categories = defaultdict(int)
    horizons = defaultdict(int)
    buy_values = []
    last_ts = 0.0

    for t in trades:
        cat = _categorize_market(t)
        categories[cat] += 1
        hor = _infer_horizon(t)
        horizons[hor] += 1
        ts_val = t.get("timestamp", 0)
        if ts_val:
            try:
                ts_num = float(ts_val)
                if ts_num > 1e12:
                    ts_num /= 1000
                last_ts = max(last_ts, ts_num)
            except (TypeError, ValueError):
                pass
        if (t.get("side") or "").upper() == "BUY":
            size = float(t.get("size", 0) or 0)
            price = float(t.get("price", 0) or 0)
            buy_values.append(size * price)

    crypto_cats = ("btc_shortterm", "btc", "crypto")
    crypto_count = sum(categories.get(c, 0) for c in crypto_cats)
    crypto_pct = (crypto_count / len(trades)) * 100 if trades else 0

    shortterm_count = sum(horizons.get(h, 0) for h in FAST_HORIZONS)
    shortterm_pct = (shortterm_count / len(trades)) * 100 if trades else 0

    days_span = max(7, len(trades) / 10)
    trades_per_day = len(trades) / days_span if days_span else 0

    days_since_last = (time.time() - last_ts) / 86400 if last_ts else 999

    avg_buy_usd = sum(buy_values) / len(buy_values) if buy_values else 0
    unique_tokens = len(set((t.get("asset") or t.get("asset_id") or "") for t in trades if (t.get("asset") or t.get("asset_id"))))
    n_buys = sum(1 for t in trades if (t.get("side") or "").upper() == "BUY")
    n_sells = len(trades) - n_buys
    sell_ratio = n_sells / n_buys if n_buys else 0

    mm_like, vf_like = _detect_mm_or_volume_farmer(
        sell_ratio=sell_ratio,
        trades_per_day=trades_per_day,
        avg_buy_usd=avg_buy_usd,
        unique_tokens=unique_tokens,
        n_trades=len(trades),
    )

    return {
        "address": addr,
        "n_trades": len(trades),
        "crypto_pct": crypto_pct,
        "shortterm_pct": shortterm_pct,
        "avg_buy_usd": avg_buy_usd,
        "trades_per_day": trades_per_day,
        "days_since_last_trade": days_since_last,
        "categories": dict(categories),
        "horizons": dict(horizons),
        "sell_ratio": sell_ratio,
        "unique_tokens": unique_tokens,
        "mm_like": mm_like,
        "volume_farmer_like": vf_like,
    }


def _detect_mm_or_volume_farmer(
    sell_ratio: float,
    trades_per_day: float,
    avg_buy_usd: float,
    unique_tokens: int,
    n_trades: int,
    *,
    mm_sell_ratio_lo: float = 0.35,
    mm_sell_ratio_hi: float = 0.65,
    mm_trades_per_day_min: float = 15,
    vf_trades_per_day_min: float = 25,
    vf_avg_buy_max: float = 10,
    vf_unique_tokens_min: int = 20,
) -> tuple[bool, bool]:
    """
    Detect spread/MM or airdrop volume-farmer patterns.

    MM/Spread: balanced buys/sells (35–65% sell), very high activity.
    Volume farmer: very high trades/day, small avg size, many different tokens.
    """
    mm_like = (
        mm_sell_ratio_lo <= sell_ratio <= mm_sell_ratio_hi
        and trades_per_day >= mm_trades_per_day_min
        and n_trades >= 50
    )
    vf_like = (
        trades_per_day >= vf_trades_per_day_min
        and avg_buy_usd <= vf_avg_buy_max
        and unique_tokens >= vf_unique_tokens_min
        and n_trades >= 50
    )
    return mm_like, vf_like


def is_wallet_copyable(analysis: Optional[Dict]) -> bool:
    """
    True if wallet looks like a directional alpha trader, not MM/volume-farmer.
    """
    if not analysis:
        return False
    if analysis.get("mm_like") or analysis.get("volume_farmer_like"):
        return False
    return True
