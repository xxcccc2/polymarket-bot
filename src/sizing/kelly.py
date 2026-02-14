"""
Kelly Criterion Position Sizing Engine

Calculates optimal bet size based on estimated edge and odds.
Supports full, half, and quarter Kelly fractions for risk control.

References:
    - Kelly (1956): "A New Interpretation of Information Rate"
    - Thorp (2006): "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..config import (
    KELLY_FRACTION_MODE,
    KELLY_MAX_BET_FRACTION,
    KELLY_MIN_EDGE,
    ORDER_SIZE_USD,
)


_FRACTION_MAP = {
    "full": 1.0,
    "half": 0.5,
    "quarter": 0.25,
    "third": 1.0 / 3.0,
}


@dataclass
class KellyResult:
    """Result of a Kelly sizing calculation."""
    raw_fraction: float        # full Kelly fraction of bankroll
    adjusted_fraction: float   # after applying mode (half/quarter) + cap
    bet_size_usd: float        # dollar amount to bet
    edge: float                # estimated edge (p*b - q) / b
    implied_prob: float        # market-implied probability
    estimated_prob: float      # your estimated probability
    odds: float                # net odds (payout/cost - 1)
    mode: str                  # fraction mode used


def kelly_fraction(
    estimated_prob: float,
    market_price: float,
    mode: str = KELLY_FRACTION_MODE,
) -> float:
    """
    Calculate Kelly bet fraction.

    Args:
        estimated_prob: Your estimated probability of the outcome (0-1).
        market_price: Current market price of the contract (0-1).
                      This is also the implied probability.
        mode: "full", "half", "quarter", or "third".

    Returns:
        Optimal fraction of bankroll to bet (0 if no edge).
    """
    if market_price <= 0 or market_price >= 1:
        return 0.0
    if estimated_prob <= 0 or estimated_prob >= 1:
        return 0.0

    # Net odds: payout / cost - 1
    # If you buy at 0.60, payout is 1.00, so b = (1 - 0.60) / 0.60 = 0.667
    b = (1.0 - market_price) / market_price
    p = estimated_prob
    q = 1.0 - p

    # Kelly formula: f* = (b*p - q) / b
    f_star = (b * p - q) / b

    if f_star <= 0:
        return 0.0

    # Apply fractional Kelly
    multiplier = _FRACTION_MAP.get(mode, 0.5)
    adjusted = f_star * multiplier

    # Safety cap
    adjusted = min(adjusted, KELLY_MAX_BET_FRACTION)

    return adjusted


def kelly_size(
    estimated_prob: float,
    market_price: float,
    bankroll: float,
    mode: str = KELLY_FRACTION_MODE,
    min_bet_usd: float = 1.0,
    max_bet_usd: Optional[float] = None,
) -> KellyResult:
    """
    Calculate the optimal dollar bet size using Kelly Criterion.

    Args:
        estimated_prob: Your estimated probability (0-1).
        market_price: Current contract price / implied probability (0-1).
        bankroll: Total available capital in USD.
        mode: Kelly fraction mode.
        min_bet_usd: Minimum bet (below this → don't trade).
        max_bet_usd: Hard cap on bet size in USD.

    Returns:
        KellyResult with all sizing details.
    """
    if max_bet_usd is None:
        max_bet_usd = ORDER_SIZE_USD * 5  # sensible default cap

    raw_f = kelly_fraction(estimated_prob, market_price, mode="full")
    adj_f = kelly_fraction(estimated_prob, market_price, mode=mode)

    bet_usd = adj_f * bankroll
    bet_usd = min(bet_usd, max_bet_usd)

    # Edge = estimated_prob - market_price (simplified)
    edge = estimated_prob - market_price

    # If edge below threshold or bet too small, zero it out
    if edge < KELLY_MIN_EDGE or bet_usd < min_bet_usd:
        bet_usd = 0.0

    b = (1.0 - market_price) / market_price if market_price > 0 and market_price < 1 else 0

    return KellyResult(
        raw_fraction=raw_f,
        adjusted_fraction=adj_f,
        bet_size_usd=round(bet_usd, 2),
        edge=round(edge, 4),
        implied_prob=market_price,
        estimated_prob=estimated_prob,
        odds=round(b, 4),
        mode=mode,
    )


def kelly_size_from_odds(
    win_prob: float,
    net_odds: float,
    bankroll: float,
    mode: str = KELLY_FRACTION_MODE,
) -> float:
    """
    Simplified Kelly sizing from win probability and net odds.

    Args:
        win_prob: Probability of winning (0-1).
        net_odds: Net odds (payout/cost - 1).
        bankroll: Available capital.
        mode: Kelly mode.

    Returns:
        Dollar amount to bet.
    """
    if net_odds <= 0 or win_prob <= 0 or win_prob >= 1:
        return 0.0

    q = 1.0 - win_prob
    f_star = (net_odds * win_prob - q) / net_odds

    if f_star <= 0:
        return 0.0

    multiplier = _FRACTION_MAP.get(mode, 0.5)
    adjusted = min(f_star * multiplier, KELLY_MAX_BET_FRACTION)

    return round(adjusted * bankroll, 2)
