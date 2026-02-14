"""Unit tests for RiskManager."""

from __future__ import annotations

from pathlib import Path

from src.risk_manager import RiskManager
from src.persistence import SqliteStore


def test_can_open_position_allows_valid_trade():
    manager = RiskManager()
    allowed, reason = manager.can_open_position("token-1", size_usd=10, price=0.5)
    assert allowed, reason


def test_positions_persist_across_restarts(tmp_path: Path):
    db_path = tmp_path / "state.sqlite"
    store = SqliteStore(db_path)
    manager = RiskManager(store=store)

    manager.update_position(
        token_id="token-1",
        market_slug="btc-2025",
        side="YES",
        size_delta=12,
        price=0.6,
        is_entry=True,
    )

    assert "token-1" in manager.positions

    restored = RiskManager(store=SqliteStore(db_path))
    assert "token-1" in restored.positions
    assert restored.positions["token-1"].size == 12
