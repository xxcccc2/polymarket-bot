"""
Kelly Criterion Position Sizing Engine

Calculates optimal bet size based on estimated edge and odds.
Supports full, half, and quarter Kelly fractions for risk control.

When the bs-p native engine is available, ``kelly_size`` delegates to
``adaptive_kelly_clip_batch`` which adds inventory-aware scaling:
the bet shrinks as existing position ``|q_t|`` grows, preventing
over-concentration in a single market.

References:
    - Kelly (1956): "A New Interpretation of Information Rate"
    - Thorp (2006): "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..config import (
    KELLY_FRACTION_MODE,
    KELLY_MAX_BET_FRACTION,
    KELLY_MIN_EDGE,
    ORDER_SIZE_USD,
    QUOTING_GAMMA,
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
    # Inventory-aware fields (populated when native engine is used)
    inventory_q: float = 0.0         # current position size passed in
    inventory_scale: float = 1.0     # 1/(1+gamma*|q|) — how much inventory reduced the bet
    maker_size_usd: float = 0.0     # conservative (maker) bet in USD
    taker_size_usd: float = 0.0     # aggressive (taker) bet in USD
    native_sized: bool = False       # True if bs-p engine was used


def kelly_fraction(
    estimated_prob: float,
    market_price: float,
    mode: str = KELLY_FRACTION_MODE,
) -> float:
    """
    Calculate Kelly bet fraction (classic, no inventory awareness).

    Args:
        estimated_prob: Your estimated probability of the outcome (0-1).
        market_price: Current market price of the contract (0-1).
        mode: "full", "half", "quarter", or "third".

    Returns:
        Optimal fraction of bankroll to bet (0 if no edge).
    """
    if market_price <= 0 or market_price >= 1:
        return 0.0
    if estimated_prob <= 0 or estimated_prob >= 1:
        return 0.0

    b = (1.0 - market_price) / market_price
    p = estimated_prob
    q = 1.0 - p

    f_star = (b * p - q) / b

    if f_star <= 0:
        return 0.0

    multiplier = _FRACTION_MAP.get(mode, 0.5)
    adjusted = f_star * multiplier

    return min(adjusted, KELLY_MAX_BET_FRACTION)


def kelly_size(
    estimated_prob: float,
    market_price: float,
    bankroll: float,
    mode: str = KELLY_FRACTION_MODE,
    min_bet_usd: float = 1.0,
    max_bet_usd: Optional[float] = None,
    inventory_q: float = 0.0,
    gamma: Optional[float] = None,
    risk_limit_usd: Optional[float] = None,
) -> KellyResult:
    """
    Calculate the optimal dollar bet size using Kelly Criterion.

    When the bs-p native engine is available and ``inventory_q`` is provided,
    the sizing is inventory-aware: bets shrink as existing position grows.

    Args:
        estimated_prob: Your estimated probability (0-1).
        market_price: Current contract price / implied probability (0-1).
        bankroll: Total available capital in USD.
        mode: Kelly fraction mode.
        min_bet_usd: Minimum bet (below this -> don't trade).
        max_bet_usd: Hard cap on bet size in USD.
        inventory_q: Current position in this market (shares).  Positive = long.
        gamma: Risk aversion parameter (defaults to QUOTING_GAMMA).
        risk_limit_usd: Max allowed position in USD (defaults to bankroll * MAX_BET_FRACTION * 5).
    """
    if max_bet_usd is None:
        max_bet_usd = ORDER_SIZE_USD * 5
    if gamma is None:
        gamma = QUOTING_GAMMA

    raw_f = kelly_fraction(estimated_prob, market_price, mode="full")
    adj_f = kelly_fraction(estimated_prob, market_price, mode=mode)

    edge = estimated_prob - market_price
    b = (1.0 - market_price) / market_price if 0 < market_price < 1 else 0.0

    # Try native inventory-aware sizing
    native_sized = False
    maker_usd = 0.0
    taker_usd = 0.0
    inv_scale = 1.0

    try:
        from ..native.pmkernel import NATIVE_AVAILABLE, adaptive_kelly_clip

        if NATIVE_AVAILABLE and edge >= KELLY_MIN_EDGE:
            rl_usd = risk_limit_usd if risk_limit_usd is not None else bankroll * KELLY_MAX_BET_FRACTION * 5
            rl_contracts = rl_usd / market_price if market_price > 0 else 0.0
            mc_contracts = max_bet_usd / market_price if market_price > 0 else 0.0

            clip = adaptive_kelly_clip(
                belief_p=estimated_prob,
                market_p=market_price,
                q_t=inventory_q,
                gamma=gamma,
                risk_limit=rl_contracts,
                max_clip=mc_contracts,
            )

            taker_usd = abs(clip.taker_clip) * market_price
            maker_usd = abs(clip.maker_clip) * market_price
            inv_scale = 1.0 / (1.0 + gamma * abs(inventory_q)) if gamma > 0 else 1.0
            native_sized = True
    except Exception:
        pass

    if native_sized:
        multiplier = _FRACTION_MAP.get(mode, 0.5)
        bet_usd = taker_usd * multiplier
        bet_usd = min(bet_usd, max_bet_usd)
    else:
        bet_usd = adj_f * bankroll
        bet_usd = min(bet_usd, max_bet_usd)

    if edge < KELLY_MIN_EDGE or bet_usd < min_bet_usd:
        bet_usd = 0.0
        maker_usd = 0.0
        taker_usd = 0.0

    return KellyResult(
        raw_fraction=raw_f,
        adjusted_fraction=adj_f,
        bet_size_usd=round(bet_usd, 2),
        edge=round(edge, 4),
        implied_prob=market_price,
        estimated_prob=estimated_prob,
        odds=round(b, 4),
        mode=mode,
        inventory_q=inventory_q,
        inventory_scale=round(inv_scale, 4),
        maker_size_usd=round(maker_usd, 2),
        taker_size_usd=round(taker_usd, 2),
        native_sized=native_sized,
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
