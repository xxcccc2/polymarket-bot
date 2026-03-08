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
