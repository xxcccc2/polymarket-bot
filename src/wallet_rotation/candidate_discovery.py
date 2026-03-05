"""
Candidate wallet discovery from leaderboard.

Fetches DAY and MONTH leaderboards, unions by wallet, and returns
candidates with rank/pnl/vol metadata for scoring.
"""

import time
from typing import Dict, List

from ..data_client import get_leaderboard


def fetch_candidate_wallets(
    category: str = "CRYPTO",
    top_n: int = 30,
) -> List[Dict]:
    """
    Fetch leaderboard from DAY and MONTH, union by wallet.

    Args:
        category: Leaderboard category (OVERALL, CRYPTO, etc.)
        top_n: Max entries per period

    Returns:
        List of candidate dicts with address, rank_daily, rank_monthly,
        pnl_daily, pnl_monthly, vol_daily, vol_monthly, userName.
    """
    by_addr: Dict[str, Dict] = {}

    for period in ("DAY", "MONTH"):
        try:
            entries = get_leaderboard(
                category=category,
                time_period=period,
                order_by="PNL",
                limit=top_n,
            )
            for i, e in enumerate(entries):
                addr = (e.get("proxyWallet") or e.get("proxy_wallet") or "").strip()
                if not addr or not addr.startswith("0x"):
                    continue
                key = addr.lower()
                if key not in by_addr:
                    by_addr[key] = {
                        "address": addr,
                        "userName": e.get("userName") or e.get("user_name") or addr[:12],
                        "rank_daily": None,
                        "rank_monthly": None,
                        "pnl_daily": None,
                        "pnl_monthly": None,
                        "vol_daily": None,
                        "vol_monthly": None,
                    }
                c = by_addr[key]
                if period == "DAY":
                    c["rank_daily"] = i + 1
                    c["pnl_daily"] = float(e.get("pnl", 0) or 0)
                    c["vol_daily"] = float(e.get("vol", 0) or 0)
                else:
                    c["rank_monthly"] = i + 1
                    c["pnl_monthly"] = float(e.get("pnl", 0) or 0)
                    c["vol_monthly"] = float(e.get("vol", 0) or 0)
            time.sleep(0.3)
        except Exception as e:
            # Log but continue; caller can handle partial results
            pass  # Avoid import cycle; caller logs

    return list(by_addr.values())
