from src.bot import (
    PolymarketBot,
    _extract_asset_from_bucket,
    _extract_horizon_from_bucket,
    _market_text_payload,
    _normalize_market_outcome_label,
    _parse_market_end_ts,
    _shortterm_bucket_key,
)
from src.websocket_feed import OrderbookUpdate

from datetime import datetime, timezone
from types import SimpleNamespace


def test_shortterm_bucket_key_detects_btc_15m_from_event_slug_and_title():
    market = {
        "question": "Bitcoin Up or Down - 15 min",
        "slug": "bitcoin-up-or-down-15m",
        "event_title": "Bitcoin Up or Down - 15 min",
        "event_slug": "btc-updown-15m-1772402400",
    }

    assert _shortterm_bucket_key(_market_text_payload(market)) == "btc:15m"


def test_shortterm_bucket_key_detects_btc_1h_from_slug():
    market = {
        "question": "BTC Up or Down - 1 hour",
        "slug": "btc-updown-1h-1772406000",
    }

    assert _shortterm_bucket_key(_market_text_payload(market)) == "btc:1h"


def test_shortterm_bucket_key_detects_btc_1h_from_hourly_clock_window_title():
    market = {
        "question": "Bitcoin Up or Down - April 12, 7-8PM ET",
        "slug": "bitcoin-up-or-down-april-12-7-8pm-et",
    }

    assert _shortterm_bucket_key(_market_text_payload(market)) == "btc:1h"


def test_shortterm_bucket_key_detects_btc_1h_from_single_hour_title_and_slug():
    market = {
        "question": "Bitcoin Up or Down - April 13, 11AM ET",
        "slug": "bitcoin-up-or-down-april-13-2026-11am-et",
    }

    assert _shortterm_bucket_key(_market_text_payload(market)) == "btc:1h"


def test_parse_market_end_ts_uses_event_metadata_when_submarket_missing_times():
    market = {
        "slug": "btc-updown-15m-market",
        "event_end_date": "2026-04-12T19:15:00Z",
    }

    parsed = _parse_market_end_ts(market)

    assert parsed is not None
    assert parsed > 0


def test_parse_market_end_ts_prefers_slug_timestamp_for_shortterm_markets_over_event_date():
    market = {
        "question": "BTC Up or Down - 15 Minutes",
        "slug": "btc-updown-15m-1776093300",
        "event_end_date": "2026-04-14T11:00:00Z",
    }

    # Slug unix is window start; resolution/end is start + 15m.
    assert _parse_market_end_ts(market) == 1776093300.0 + 15 * 60


def test_parse_market_end_ts_falls_back_to_slug_timestamp():
    market = {
        "event_slug": "btc-updown-1h-1772406000",
    }

    assert _parse_market_end_ts(market) == 1772406000.0 + 60 * 60


def test_extract_asset_from_bucket_returns_asset_prefix():
    assert _extract_asset_from_bucket("btc:15m") == "btc"


def test_extract_horizon_from_bucket_returns_duration_suffix():
    assert _extract_horizon_from_bucket("btc:1h") == "1h"


def test_normalize_market_outcome_label_maps_yes_no_to_up_down_for_shortterm_crypto():
    market = {
        "question": "Bitcoin Up or Down - 15 Minutes",
        "slug": "btc-updown-15m-1772402400",
    }

    assert _normalize_market_outcome_label(market, "Yes", 0) == "UP"
    assert _normalize_market_outcome_label(market, "No", 1) == "DOWN"


def test_normalize_market_outcome_label_preserves_non_shortterm_markets():
    market = {
        "question": "Will BTC be above $80k by Friday?",
        "slug": "btc-above-80k-friday",
    }

    assert _normalize_market_outcome_label(market, "Yes", 0) == "Yes"


def test_build_market_data_keeps_quote_quality_per_token():
    bot = PolymarketBot.__new__(PolymarketBot)
    bot._recent_trades_cache = []
    bot.markets = {
        "cond-1": {
            "conditionId": "cond-1",
            "question": "Bitcoin Up or Down - 15 Minutes",
            "slug": "btc-updown-15m-1776093300",
            "clobTokenIds": ["token-up", "token-down"],
            "outcomes": ["Up", "Down"],
            "bestBid": 0,
            "bestAsk": 0,
            "acceptingOrders": True,
        }
    }
    bot.feed = SimpleNamespace(
        resolved_markets={},
        tick_size_by_token={},
        get_latest_orderbook=lambda token_id: (
            OrderbookUpdate(
                token_id="token-up",
                bids=[{"price": 0.51, "size": 10}],
                asks=[{"price": 0.53, "size": 10}],
                timestamp=datetime.now(timezone.utc),
            )
            if token_id == "token-up"
            else None
        ),
    )

    rows = bot._build_market_data()
    by_token = {row.token_id: row for row in rows}

    assert by_token["token-up"].has_real_quotes is True
    assert by_token["token-up"].data_source_quality == "live_quotes"
    assert by_token["token-down"].has_real_quotes is False
    assert by_token["token-down"].data_source_quality == "missing_quotes"
