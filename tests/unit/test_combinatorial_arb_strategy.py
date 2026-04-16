from __future__ import annotations

from datetime import datetime, timezone

from src.strategies.base_strategy import MarketData
from src.strategies.combinatorial_arb_strategy import CombinatorialArbStrategy


def _market(token_id: str, question: str, price: float, *, event_slug: str, end_offset: float = 3600) -> MarketData:
    now = datetime.now(timezone.utc)
    return MarketData(
        token_id=token_id,
        condition_id=f"cond-{token_id}",
        market_slug=token_id,
        question=question,
        outcome="YES",
        best_bid=price,
        best_ask=min(price + 0.02, 0.99),
        mid_price=price + 0.01,
        spread=0.02,
        volume_24h=50000.0,
        liquidity=10000.0,
        last_price=price,
        has_real_quotes=True,
        accepting_orders=True,
        data_source_quality="live_quotes",
        event_slug=event_slug,
        timestamp=now,
        end_date_ts=now.timestamp() + end_offset,
    )


def test_combo_groups_only_within_same_event_family():
    strategy = CombinatorialArbStrategy({"combo_min_edge_cents": 1})
    same_family = [
        _market("btc-90", "Will Bitcoin go above $90,000?", 0.40, event_slug="btc-april"),
        _market("btc-100", "Will Bitcoin go above $100,000?", 0.55, event_slug="btc-april"),
    ]
    other_family = _market("btc-95-next", "Will Bitcoin go above $95,000?", 0.10, event_slug="btc-may")

    signals = strategy.analyze(same_family + [other_family])

    assert len(signals) == 1
    assert signals[0].token_id == "btc-90"
    assert signals[0].metadata["family_key"] == "btc-april|above"


def test_combo_skips_markets_without_real_quotes():
    strategy = CombinatorialArbStrategy({"combo_min_edge_cents": 1})
    bad_market = _market("btc-90", "Will Bitcoin go above $90,000?", 0.40, event_slug="btc-april")
    bad_market.has_real_quotes = False
    good_market = _market("btc-100", "Will Bitcoin go above $100,000?", 0.55, event_slug="btc-april")

    assert strategy.analyze([bad_market, good_market]) == []
