"""Unit tests for SpreadStrategy."""

from __future__ import annotations

from src.strategies.spread_strategy import SpreadStrategy
from src.strategies.base_strategy import MarketData


def _make_market(spread_cents: float) -> MarketData:
    best_bid = 0.5
    best_ask = best_bid + (spread_cents / 100)
    return MarketData(
        token_id="token-1",
        condition_id="cond-1",
        market_slug="btc-2025",
        question="Will BTC be above $100k?",
        outcome="YES",
        best_bid=best_bid,
        best_ask=best_ask,
        mid_price=(best_bid + best_ask) / 2,
        spread=best_ask - best_bid,
        volume_24h=100000,
        liquidity=5000,
        last_price=best_bid,
    )


def test_generate_signal_when_spread_wide_enough():
    strategy = SpreadStrategy({"only_crypto": False, "min_spread_cents": 1})
    market = _make_market(2)

    signals = strategy.analyze([market])
    assert signals, "Expected a signal for a wide spread"


def test_no_signal_when_spread_too_tight():
    strategy = SpreadStrategy({"only_crypto": False, "min_spread_cents": 3})
    market = _make_market(1)

    signals = strategy.analyze([market])
    assert signals == []
