"""Unit tests for OrderManager."""

from __future__ import annotations

from pathlib import Path

from src.order_manager import Order, OrderManager, OrderStatus
from src.persistence import SqliteStore


class DummyClient:
    def place_order(self, token_id, side, price, size, order_type="GTC"):
        return {"success": True, "order_id": "order-123"}

    def place_orders_batch(self, orders):
        return [
            {"success": True, "order_id": f"order-batch-{idx}"}
            for idx, _ in enumerate(orders, start=1)
        ]

    def cancel_order(self, order_id):
        return {"success": True}

    def get_orders(self):
        return []

    def get_open_orders(self):
        return []

    def get_trades(self, limit=100):
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


def test_batch_orders_are_persisted(tmp_path: Path):
    store = SqliteStore(tmp_path / "state.sqlite")
    manager = OrderManager(DummyClient(), store=store)

    results = manager.place_limit_orders_batch(
        [
            {
                "token_id": "token-1",
                "side": "BUY",
                "price": 0.42,
                "size": 10,
                "market_slug": "btc-2025",
            },
            {
                "token_id": "token-2",
                "side": "BUY",
                "price": 0.38,
                "size": 8,
                "market_slug": "eth-2025",
            },
        ]
    )

    assert len(results) == 2
    assert all(r["success"] for r in results)

    restored = OrderManager(DummyClient(), store=SqliteStore(tmp_path / "state.sqlite"))
    assert "order-batch-1" in restored.orders
    assert "order-batch-2" in restored.orders


def test_exchange_sync_cancel_notifies_callbacks(monkeypatch):
    monkeypatch.setattr("src.order_manager.PAPER_TRADING", False)
    manager = OrderManager(DummyClient())
    order = Order(
        order_id="order-1",
        token_id="token-1",
        market_slug="btc-market",
        side="BUY",
        price=0.5,
        size=10,
        status=OrderStatus.OPEN,
        metadata={"strategy": "ml_directional"},
    )
    manager.orders[order.order_id] = order
    manager.orders_by_token[order.token_id] = [order.order_id]
    cancelled = []
    manager.on_cancel(lambda order_obj, reason: cancelled.append((order_obj.order_id, reason)))

    manager.sync_with_exchange()

    assert order.status == OrderStatus.CANCELLED
    assert cancelled == [("order-1", "Exchange sync: removed from open orders")]


def test_process_fill_caps_at_remaining_size():
    manager = OrderManager(DummyClient())
    order = Order(
        order_id="order-1",
        token_id="token-1",
        market_slug="btc-market",
        side="BUY",
        price=0.5,
        size=10,
        filled_size=6,
        status=OrderStatus.PARTIAL,
    )
    manager.orders[order.order_id] = order
    seen = []
    manager.on_fill(lambda order_obj, fill: seen.append(fill["size"]))

    manager.process_fill("order-1", {"price": 0.5, "size": 8, "side": "BUY", "token_id": "token-1"})

    assert order.filled_size == 10
    assert seen == [4]


def test_process_fill_accounts_for_provisional_user_ws_match():
    manager = OrderManager(DummyClient())
    order = Order(
        order_id="order-1",
        token_id="token-1",
        market_slug="btc-market",
        side="BUY",
        price=0.5,
        size=10,
        filled_size=10,
        status=OrderStatus.PARTIAL,
        metadata={"user_ws_size_matched": 10.0},
    )
    manager.orders[order.order_id] = order
    seen = []
    manager.on_fill(lambda order_obj, fill: seen.append(fill["size"]))

    manager.process_fill("order-1", {"price": 0.5, "size": 10, "side": "BUY", "token_id": "token-1"})

    assert order.status == OrderStatus.FILLED
    assert order.filled_size == 10
    assert seen == [10]
    assert order.metadata["user_ws_size_matched_consumed"] == 10.0
