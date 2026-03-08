"""Unit tests for ML data loading and microstructure aggregation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.ml.data_loader import MicrostructureLoader


def test_microstructure_loader_aggregates_training_features(tmp_path: Path):
    db_path = tmp_path / "micro.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE depth_snapshots (event_time REAL, symbol TEXT, bids_json TEXT, asks_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE agg_trades (event_time REAL, symbol TEXT, price REAL, quantity REAL, is_buyer_maker INTEGER)"
        )
        conn.execute(
            "CREATE TABLE liquidations (event_time REAL, symbol TEXT, side TEXT, price REAL, quantity REAL)"
        )
        conn.execute(
            "CREATE TABLE funding_open_interest (sampled_at REAL, symbol TEXT, funding_rate REAL, open_interest REAL)"
        )
        conn.execute(
            "INSERT INTO depth_snapshots VALUES (1700000000, 'btcusdt', '[[100, 10], [99, 5]]', '[[101, 4], [102, 2]]')"
        )
        conn.execute(
            "INSERT INTO agg_trades VALUES (1700000005, 'btcusdt', 100000, 2.0, 0)"
        )
        conn.execute(
            "INSERT INTO liquidations VALUES (1700000010, 'btcusdt', 'SELL', 99950, 1.5)"
        )
        conn.execute(
            "INSERT INTO funding_open_interest VALUES (1700000020, 'btcusdt', 0.0001, 12345)"
        )

    loader = MicrostructureLoader(db_path)
    dataset = loader.load_symbol("btcusdt")

    assert not dataset.frame.empty
    row = dataset.frame.iloc[-1]
    assert "micro_obi_l1" in dataset.frame.columns
    assert "micro_cvd_1m" in dataset.frame.columns
    assert "micro_liq_delta_1m" in dataset.frame.columns
    assert "micro_funding_rate" in dataset.frame.columns
    assert row["micro_open_interest"] == 12345
