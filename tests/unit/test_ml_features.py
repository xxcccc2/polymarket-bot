"""Unit tests for ML feature engineering helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from src.ml.features import FeatureBuilder, compute_orderbook_imbalance


def _frame(start: datetime, minutes: int, rows: int) -> pd.DataFrame:
    data = []
    price = 100.0
    for idx in range(rows):
        ts = start + timedelta(minutes=minutes * idx)
        open_price = price
        close_price = price + 1.0
        high_price = close_price + 0.5
        low_price = open_price - 0.5
        data.append(
            {
                "timestamp": ts,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": 1000 + idx,
            }
        )
        price += 1.0
    return pd.DataFrame(data)


def test_compute_orderbook_imbalance_handles_level_arrays():
    orderbook = {
        "bids": [[0.51, 20], [0.50, 10]],
        "asks": [[0.52, 5], [0.53, 5]],
    }
    imbalance = compute_orderbook_imbalance(orderbook)
    assert round(imbalance, 3) == 0.5


def test_build_ohlcv_feature_frame_is_time_safe():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    frames = {
        "15m": _frame(start, 15, 40),
        "1h": _frame(start, 60, 20),
    }
    builder = FeatureBuilder()

    features = builder.build_ohlcv_feature_frame(frames, target_timeframe="15m")

    assert not features.empty
    assert "15m_body_ratio" in features.columns
    assert "1h_body_ratio" in features.columns
    assert "target_up" in features.columns
    assert (features["available_ts"] <= features["as_of_ts"]).all()


def test_runtime_feature_row_contains_expected_keys():
    builder = FeatureBuilder()
    market = type(
        "DummyMarket",
        (),
        {
            "mid_price": 0.52,
            "spread": 0.02,
            "volume_24h": 50000,
            "liquidity": 12000,
            "orderbook": {"bids": [[0.51, 15]], "asks": [[0.53, 5]]},
            "end_date_ts": 1_700_000_100,
            "timestamp": datetime.fromtimestamp(1_700_000_000, tz=timezone.utc),
        },
    )()
    binance_state = type(
        "DummyBinance",
        (),
        {
            "last_price": 100000.0,
            "vwap_5m": 99900.0,
            "volatility_5m": 0.4,
            "price_change_pct_10s": 0.05,
            "price_change_pct_30s": 0.10,
            "price_change_pct_60s": 0.15,
            "price_velocity": 3.0,
            "bid_pressure": 0.6,
        },
    )()

    row = builder.build_runtime_feature_row(market, binance_state, now_ts=1_700_000_000)

    assert row["market_mid_price"] == 0.52
    assert row["market_orderbook_imbalance"] > 0
    assert row["binance_price_change_pct_30s"] == 0.10


def test_build_training_schema_runtime_row_returns_latest_feature_values():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    frames = {
        "15m": _frame(start, 15, 80),
        "1h": _frame(start, 60, 40),
        "4h": _frame(start, 240, 20),
    }
    builder = FeatureBuilder()

    row = builder.build_training_schema_runtime_row(frames, target_timeframe="15m")

    assert row
    assert "15m_body_ratio" in row
    assert "1h_momentum_3" in row
    assert "4h_volatility_6" in row
    assert "hour_sin" in row
    assert "target_up" not in row


def test_build_ohlcv_feature_frame_normalizes_mixed_timestamp_precisions():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    frames = {
        "15m": _frame(start, 15, 40),
        "1h": _frame(start, 60, 20),
    }
    micro = pd.DataFrame(
        {
            "timestamp": pd.Series(
                pd.date_range(start=start, periods=40, freq="15min", tz="UTC"),
                dtype="datetime64[us, UTC]",
            ),
            "micro_cvd_5m": [float(idx) for idx in range(40)],
        }
    )
    builder = FeatureBuilder()

    features = builder.build_ohlcv_feature_frame(
        frames,
        target_timeframe="15m",
        microstructure_frame=micro,
    )

    assert not features.empty
    assert str(features["timestamp"].dtype) == "datetime64[ns, UTC]"
    assert "micro_cvd_5m" in features.columns
