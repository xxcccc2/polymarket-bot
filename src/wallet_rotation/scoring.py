"""
Wallet scoring for rotation candidates.

Hard filters (must pass) and weighted score components.
Anti-lucky-shot: requires combined DAY+MONTH evidence for high score.
"""

from typing import Dict, List, Optional, Tuple


def score_wallet(
    candidate: Dict,
    analysis: Optional[Dict],
    *,
    min_trades: int = 20,
    min_crypto_pct: float = 70,
    min_shortterm_pct: float = 40,
    max_days_since_last_trade: float = 7,
    min_trades_per_day: float = 0.5,
    w_rank_daily: float = 0.35,
    w_rank_monthly: float = 0.35,
    w_crypto_pct: float = 0.15,
    w_shortterm_pct: float = 0.15,
) -> Tuple[float, List[str]]:
    """
    Compute score 0–1 and list of reasons.

    Hard filters: min trades, crypto %, short-term %, recency, trades/day.
    Score: rank_daily, rank_monthly, crypto_pct, shortterm_pct (weighted).
    Anti-lucky-shot: rank scores require both daily and monthly presence.

    Returns:
        (score, reasons) — score in [0, 1], reasons for logging.
    """
    reasons = []
    score = 0.0

    if not analysis:
        return 0.0, ["No trade data"]

    if analysis["n_trades"] < min_trades:
        return 0.0, [f"Too few trades ({analysis['n_trades']} < {min_trades})"]

    if analysis["crypto_pct"] < min_crypto_pct:
        return 0.0, [f"Low crypto % ({analysis['crypto_pct']:.0f}% < {min_crypto_pct}%)"]

    if analysis["shortterm_pct"] < min_shortterm_pct:
        return 0.0, [f"Low short-term % ({analysis['shortterm_pct']:.0f}% < {min_shortterm_pct}%)"]

    if analysis["days_since_last_trade"] > max_days_since_last_trade:
        return 0.0, [
            f"Inactive ({analysis['days_since_last_trade']:.0f}d since last trade)"
        ]

    if analysis["trades_per_day"] < min_trades_per_day:
        return 0.0, [
            f"Low activity ({analysis['trades_per_day']:.1f} trades/day < {min_trades_per_day})"
        ]

    # Exclude MM/spread and volume-farmer wallets
    if analysis.get("mm_like"):
        return 0.0, ["MM/Spread-like (balanced buys/sells, high frequency)"]
    if analysis.get("volume_farmer_like"):
        return 0.0, ["Volume-farmer-like (high trades, small size, many tokens)"]

    # Rank score (daily) — cap single-period influence
    r_daily = candidate.get("rank_daily")
    if r_daily is not None:
        rank_score_daily = max(0, 1.0 - (r_daily - 1) / 50)
        score += w_rank_daily * rank_score_daily
        reasons.append(f"daily#{r_daily}")
    else:
        reasons.append("no daily rank")

    # Rank score (monthly) — stability proxy
    r_monthly = candidate.get("rank_monthly")
    if r_monthly is not None:
        rank_score_monthly = max(0, 1.0 - (r_monthly - 1) / 50)
        score += w_rank_monthly * rank_score_monthly
        reasons.append(f"month#{r_monthly}")
    else:
        reasons.append("no month rank")

    crypto_norm = min(1.0, analysis["crypto_pct"] / 100)
    score += w_crypto_pct * crypto_norm

    shortterm_norm = min(1.0, analysis["shortterm_pct"] / 100)
    score += w_shortterm_pct * shortterm_norm

    return min(1.0, score), reasons
