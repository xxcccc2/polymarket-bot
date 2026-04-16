"""Unit tests for MLDirectionalStrategy."""

from __future__ import annotations

import pickle
from datetime import datetime, timezone

from src.ml.model import ModelArtifact
from src.strategies.base_strategy import MarketData
from src.strategies.ml_directional_strategy import MLDirectionalStrategy


class DeterministicEstimator:
    def predict_proba(self, frame):
        rows = len(frame)
        return [[0.2, 0.8] for _ in range(rows)]


class DummyBinanceState:
    last_price = 100000.0
    vwap_5m = 99980.0
    volatility_5m = 0.3
    price_change_pct_10s = 0.05
    price_change_pct_30s = 0.08
    price_change_pct_60s = 0.12
    price_velocity = 2.0
    bid_pressure = 0.6
    last_update = 2_000_000_000.0
    connected = True


class DummyBinanceFeed:
    connected = True

    def get_state(self, symbol="btc"):
        return DummyBinanceState()

    def get_recent_ohlcv(self, symbol="btc", *, timeframe="15m", limit=128, cache_seconds=30):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        step_minutes = {"15m": 15, "1h": 60, "4h": 240}.get(timeframe, 15)
        rows = 80 if timeframe == "15m" else 40
        data = []
        price = 100.0
        for idx in range(rows):
            ts = start.timestamp() + (step_minutes * 60 * idx)
            open_price = price
            close_price = price + 1.0
            data.append(
                {
                    "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc),
                    "open": open_price,
                    "high": close_price + 0.5,
                    "low": open_price - 0.5,
                    "close": close_price,
                    "volume": 1000.0 + idx,
                }
            )
            price += 1.0
        import pandas as pd

        return pd.DataFrame(data)


class DummyRiskPosition:
    def __init__(self, size, current_price):
        self.size = size
        self.current_price = current_price


class DummyRiskManager:
    def __init__(self, positions=None):
        self.positions = positions or {}


def _write_artifact(path):
    artifact = ModelArtifact(
        version="unit-test-artifact",
        feature_columns=[
            "market_mid_price",
            "market_spread",
            "market_volume_24h",
            "market_liquidity",
            "market_orderbook_imbalance",
            "market_time_to_expiry_s",
            "market_time_to_expiry_m",
            "binance_last_price",
            "binance_vwap_5m",
            "binance_volatility_5m",
            "binance_price_change_pct_10s",
            "binance_price_change_pct_30s",
            "binance_price_change_pct_60s",
            "binance_price_velocity",
            "binance_bid_pressure",
        ],
        threshold_probability=0.53,
    )
    payload = {
        "model": DeterministicEstimator(),
        "artifact": artifact,
        "backend": "deterministic",
        "params": {},
    }
    with path.open("wb") as handle:
        pickle.dump(payload, handle)


def _write_training_artifact(path, feature_columns, *, version="unit-test-training-artifact"):
    artifact = ModelArtifact(
        version=version,
        feature_columns=feature_columns,
        threshold_probability=0.53,
    )
    payload = {
        "model": DeterministicEstimator(),
        "artifact": artifact,
        "backend": "deterministic",
        "params": {},
    }
    with path.open("wb") as handle:
        pickle.dump(payload, handle)


def _market(outcome: str, mid_price: float) -> MarketData:
    return MarketData(
        token_id=f"token-{outcome.lower()}",
        condition_id="cond-1",
        market_slug="btc-updown-15m-demo",
        question="Bitcoin Up or Down - 15 min",
        outcome=outcome,
        best_bid=max(0.01, mid_price - 0.01),
        best_ask=min(0.99, mid_price + 0.01),
        mid_price=mid_price,
        spread=0.02,
        volume_24h=100000.0,
        liquidity=25000.0,
        last_price=mid_price,
        has_real_quotes=True,
        accepting_orders=True,
        data_source_quality="live_quotes",
        timestamp=datetime.now(timezone.utc),
        orderbook={"bids": [[mid_price - 0.01, 20]], "asks": [[mid_price + 0.01, 10]]},
        end_date_ts=2_000_000_600.0,
    )


def _market_with_horizon(outcome: str, mid_price: float, horizon: str) -> MarketData:
    label = "15 min" if horizon == "15m" else "1 hour"
    return MarketData(
        token_id=f"token-{horizon}-{outcome.lower()}",
        condition_id=f"cond-{horizon}",
        market_slug=f"btc-updown-{horizon}-demo",
        question=f"Bitcoin Up or Down - {label}",
        outcome=outcome,
        best_bid=max(0.01, mid_price - 0.01),
        best_ask=min(0.99, mid_price + 0.01),
        mid_price=mid_price,
        spread=0.02,
        volume_24h=100000.0,
        liquidity=25000.0,
        last_price=mid_price,
        has_real_quotes=True,
        accepting_orders=True,
        data_source_quality="live_quotes",
        timestamp=datetime.now(timezone.utc),
        orderbook={"bids": [[mid_price - 0.01, 20]], "asks": [[mid_price + 0.01, 10]]},
        end_date_ts=2_000_000_600.0,
    )


def test_strategy_emits_signal_for_matching_outcome(tmp_path):
    artifact_path = tmp_path / "ml_directional.pkl"
    _write_artifact(artifact_path)

    strategy = MLDirectionalStrategy(
        {
            "binance_feed": DummyBinanceFeed(),
            "model_path": artifact_path,
            "min_edge": 0.02,
        }
    )

    signals = strategy.analyze([_market("Up", 0.60), _market("Down", 0.40)])

    assert len(signals) == 1
    assert signals[0].side == "Up"
    assert signals[0].metadata["model_version"] == "unit-test-artifact"


def test_strategy_stays_idle_without_connected_feed(tmp_path):
    artifact_path = tmp_path / "ml_directional.pkl"
    _write_artifact(artifact_path)

    class OfflineFeed(DummyBinanceFeed):
        def get_state(self, symbol="btc"):
            state = DummyBinanceState()
            state.connected = False
            return state

    strategy = MLDirectionalStrategy(
        {
            "binance_feed": OfflineFeed(),
            "model_path": artifact_path,
        }
    )

    signals = strategy.analyze([_market("Up", 0.60)])
    assert signals == []


def test_extract_horizon_detects_hourly_clock_window_titles():
    text = "bitcoin up or down - april 12, 7-8pm et"

    assert MLDirectionalStrategy._extract_horizon(text) == "1h"


def test_extract_horizon_detects_single_hour_titles():
    text = "bitcoin up or down - april 13, 11am et"

    assert MLDirectionalStrategy._extract_horizon(text) == "1h"


def test_strategy_does_not_signal_both_sides_same_condition(tmp_path):
    artifact_path = tmp_path / "ml_directional.pkl"
    _write_artifact(artifact_path)

    strategy = MLDirectionalStrategy(
        {
            "binance_feed": DummyBinanceFeed(),
            "model_path": artifact_path,
            "min_edge": 0.02,
        }
    )

    signals = strategy.analyze([_market("Up", 0.60), _market("Down", 0.10)])

    assert len(signals) == 1


def test_strategy_uses_risk_manager_inventory_for_sizing(tmp_path):
    artifact_path = tmp_path / "ml_directional.pkl"
    _write_artifact(artifact_path)
    risk_manager = DummyRiskManager({"token-up": DummyRiskPosition(size=20, current_price=0.6)})

    strategy = MLDirectionalStrategy(
        {
            "binance_feed": DummyBinanceFeed(),
            "model_path": artifact_path,
            "risk_manager": risk_manager,
            "order_size_usd": 10,
            "min_edge": 0.02,
        }
    )

    without_inventory = MLDirectionalStrategy(
        {
            "binance_feed": DummyBinanceFeed(),
            "model_path": artifact_path,
            "order_size_usd": 10,
            "min_edge": 0.02,
        }
    )

    risk_sized = strategy.analyze([_market("Up", 0.60)])[0].size
    base_sized = without_inventory.analyze([_market("Up", 0.60)])[0].size

    assert risk_sized <= base_sized


def test_resolution_tracking_uses_resolved_outcome_not_mid_price(tmp_path):
    artifact_path = tmp_path / "ml_directional.pkl"
    _write_artifact(artifact_path)
    strategy = MLDirectionalStrategy(
        {
            "binance_feed": DummyBinanceFeed(),
            "model_path": artifact_path,
            "min_edge": 0.02,
        }
    )

    strategy._pending_resolutions["token-up"] = {
        "predicted_prob": 0.8,
        "condition_id": "cond-1",
        "end_date_ts": 1.0,
    }
    resolved_market = _market("Up", 0.9)
    resolved_market.is_resolved = True
    resolved_market.resolution_outcome = "DOWN"
    resolved_market.end_date_ts = 1.0

    strategy._update_resolution_tracking([resolved_market])

    assert list(strategy._resolved_accuracy) == [0.0]
    assert "token-up" not in strategy._pending_resolutions


def test_strategy_state_reports_horizon_stats(tmp_path):
    artifact_15m = tmp_path / "ml_directional_15m.pkl"
    artifact_1h = tmp_path / "ml_directional_1h.pkl"
    _write_artifact(artifact_15m)
    _write_artifact(artifact_1h)

    strategy = MLDirectionalStrategy(
        {
            "binance_feed": DummyBinanceFeed(),
            "model_path": artifact_15m,
            "model_path_15m": artifact_15m,
            "model_path_1h": artifact_1h,
            "min_edge": 0.02,
        }
    )

    signals = strategy.analyze([
        _market_with_horizon("Up", 0.60, "15m"),
        _market_with_horizon("Up", 0.60, "1h"),
    ])

    assert len(signals) == 2
    state = strategy.get_state()

    assert state["horizon_stats"]["15m"]["signals"] >= 1
    assert state["horizon_stats"]["1h"]["signals"] >= 1


def test_strategy_supports_training_schema_artifact_with_live_ohlc_reconstruction(tmp_path):
    artifact_path = tmp_path / "ml_directional_training.pkl"
    _write_training_artifact(
        artifact_path,
        ["15m_body_ratio", "1h_momentum_3", "4h_volatility_6", "hour_sin"],
    )

    strategy = MLDirectionalStrategy(
        {
            "binance_feed": DummyBinanceFeed(),
            "model_path": artifact_path,
            "min_edge": 0.02,
        }
    )

    signals = strategy.analyze([_market("Up", 0.60), _market("Down", 0.40)])

    assert len(signals) == 1
    assert strategy.model_error is None


def test_strategy_blocks_microstructure_artifact_without_live_micro_parity(tmp_path):
    artifact_path = tmp_path / "ml_directional_micro.pkl"
    _write_training_artifact(
        artifact_path,
        ["15m_body_ratio", "micro_cvd_5m"],
    )

    strategy = MLDirectionalStrategy(
        {
            "binance_feed": DummyBinanceFeed(),
            "model_path": artifact_path,
            "min_edge": 0.02,
        }
    )

    signals = strategy.analyze([_market("Up", 0.60)])

    assert signals == []
    assert strategy.model_error is not None
    assert "microstructure parity" in strategy.model_error


def test_strategy_uses_horizon_specific_artifacts_in_single_run(tmp_path):
    artifact_15m = tmp_path / "ml_directional_15m.pkl"
    artifact_1h = tmp_path / "ml_directional_1h.pkl"
    _write_training_artifact(
        artifact_15m,
        ["15m_body_ratio", "1h_momentum_3", "4h_volatility_6", "hour_sin"],
        version="artifact-15m",
    )
    _write_training_artifact(
        artifact_1h,
        ["1h_body_ratio", "4h_momentum_3", "1d_volatility_6", "hour_sin"],
        version="artifact-1h",
    )

    strategy = MLDirectionalStrategy(
        {
            "binance_feed": DummyBinanceFeed(),
            "model_path_15m": artifact_15m,
            "model_path_1h": artifact_1h,
            "enabled_horizons": ["15m", "1h"],
            "min_edge": 0.02,
        }
    )

    signals = strategy.analyze(
        [
            _market_with_horizon("Up", 0.60, "15m"),
            _market_with_horizon("Up", 0.60, "1h"),
        ]
    )

    assert len(signals) == 2
    versions = {signal.metadata["horizon"]: signal.metadata["model_version"] for signal in signals}
    assert versions["15m"] == "artifact-15m"
    assert versions["1h"] == "artifact-1h"
