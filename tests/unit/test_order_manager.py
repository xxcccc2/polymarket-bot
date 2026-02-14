"""Unit tests for OrderManager."""

from __future__ import annotations

from pathlib import Path

from src.order_manager import OrderManager
from src.persistence import SqliteStore


class DummyClient:
    def place_order(self, token_id, side, price, size, order_type="GTC"):
        return {"success": True, "order_id": "order-123"}

    def cancel_order(self, order_id):
        return {"success": True}

    def get_orders(self):
        return []


def test_order_is_persisted_on_place(tmp_path: Path):
    store = SqliteStore(tmp_path / "state.sqlite")
    manager = OrderManager(DummyClient(), store=store)

    result = manager.place_limit_order(
        token_id="token-1",
        side="BUY",
        price=0.42,
        size=10,
        market_slug="btc-2025",
    )

    assert result["success"] is True

    restored = OrderManager(DummyClient(), store=SqliteStore(tmp_path / "state.sqlite"))
    assert "order-123" in restored.orders
    assert restored.orders["order-123"].token_id == "token-1"
