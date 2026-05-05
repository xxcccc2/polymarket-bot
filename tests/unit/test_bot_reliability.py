from __future__ import annotations

from types import SimpleNamespace

from src.bot import PolymarketBot
from src.order_manager import Order, OrderManager, OrderStatus


def test_user_trade_update_ignores_non_terminal_status():
    bot = PolymarketBot.__new__(PolymarketBot)
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
    handled = []
    bot.order_manager = SimpleNamespace(orders={"order-1": order}, get_order=lambda oid: order)
    bot._find_order_for_trade = lambda **kwargs: order
    bot._handle_fill_event = lambda order_obj, fill: handled.append((order_obj.order_id, fill["trade_id"]))

    bot._on_user_trade_update(
        SimpleNamespace(
            trade_id="trade-1",
            raw={
                "id": "trade-1",
                "status": "MATCHED",
                "asset_id": "token-1",
                "side": "BUY",
                "price": 0.5,
                "size": 10,
                "taker_order_id": "order-1",
            },
        )
    )

    assert handled == []


def test_user_trade_update_handles_confirmed_trade():
    bot = PolymarketBot.__new__(PolymarketBot)
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
    handled = []
    bot.order_manager = SimpleNamespace(orders={"order-1": order}, get_order=lambda oid: order)
    bot._find_order_for_trade = lambda **kwargs: order
    bot._handle_fill_event = lambda order_obj, fill: handled.append((order_obj.order_id, fill["trade_id"]))

    bot._on_user_trade_update(
        SimpleNamespace(
            trade_id="trade-1",
            raw={
                "id": "trade-1",
                "status": "CONFIRMED",
                "asset_id": "token-1",
                "side": "BUY",
                "price": 0.5,
                "size": 10,
                "taker_order_id": "order-1",
            },
        )
    )

    assert handled == [("order-1", "trade-1")]


def test_strategy_detail_row_formats_priority_strategy_state():
    bot = PolymarketBot.__new__(PolymarketBot)

    terminal = bot._strategy_detail_row(
        "terminal_convergence",
        {"status": "1 live", "best_edge_cents": 3.5, "nearest_expiry_s": 42, "fill_mode": "GTD"},
    )
    combo = bot._strategy_detail_row(
        "combinatorial_arb",
        {"status": "idle", "parsed_markets": 7, "valid_parsed": 5, "active_cooldowns": 2},
    )

    assert terminal.summary == "1 live"
    assert "best edge 3.5c" in terminal.detail
    assert "eligible" in terminal.detail
    assert "parsed 7" in combo.detail


def test_user_order_update_marks_partial_fill_without_waiting_for_trade_confirmation():
    bot = PolymarketBot.__new__(PolymarketBot)
    persisted = []
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
    bot.order_manager = SimpleNamespace(
        get_order=lambda oid: order,
        _persist_order=lambda o: persisted.append((o.order_id, o.status.value, o.filled_size)),
        mark_order_cancelled=lambda order_id, reason: None,
    )

    bot._on_user_order_update(
        SimpleNamespace(
            order_id="order-1",
            status="LIVE",
            raw={"type": "UPDATE", "status": "LIVE", "size_matched": "4"},
        )
    )

    assert order.status == OrderStatus.PARTIAL
    assert order.filled_size == 4
    assert persisted[-1] == ("order-1", "partial", 4.0)


def test_user_order_update_keeps_full_match_active_until_trade_confirmation():
    bot = PolymarketBot.__new__(PolymarketBot)
    persisted = []
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
    bot.order_manager = SimpleNamespace(
        get_order=lambda oid: order,
        _persist_order=lambda o: persisted.append((o.order_id, o.status.value, o.filled_size)),
        mark_order_cancelled=lambda order_id, reason: None,
    )

    bot._on_user_order_update(
        SimpleNamespace(
            order_id="order-1",
            status="LIVE",
            raw={"type": "UPDATE", "status": "LIVE", "size_matched": "10"},
        )
    )

    assert order.status == OrderStatus.PARTIAL
    assert order.is_active
    assert order.metadata["user_ws_size_matched"] == 10.0


def test_trade_confirmation_after_user_order_match_does_not_overfill():
    bot = PolymarketBot.__new__(PolymarketBot)
    manager = OrderManager(SimpleNamespace())
    order = Order(
        order_id="order-1",
        token_id="token-1",
        market_slug="btc-market",
        side="BUY",
        price=0.5,
        size=10,
        filled_size=10,
        status=OrderStatus.PARTIAL,
        metadata={"strategy": "ml_directional", "user_ws_size_matched": 10.0},
    )
    manager.orders[order.order_id] = order
    manager.orders_by_token[order.token_id] = [order.order_id]
    bot.order_manager = manager
    bot._refresh_balance = lambda: None
    bot.telegram = SimpleNamespace(alert_fill=lambda **kwargs: None)

    bot._handle_fill_event(
        order,
        {"trade_id": "trade-1", "side": "BUY", "price": 0.5, "size": 10, "token_id": "token-1"},
    )

    assert order.status == OrderStatus.FILLED
    assert order.filled_size == 10
    assert order.metadata["user_ws_size_matched_consumed"] == 10.0
