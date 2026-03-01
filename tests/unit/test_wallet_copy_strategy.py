"""Unit tests for WalletCopyStrategy hardening paths."""

from __future__ import annotations

import time
from types import SimpleNamespace

from src.order_manager import Order, OrderStatus
from src.strategies.base_strategy import MarketData, SignalType
from src.strategies import wallet_copy_strategy as wallet_copy_module
from src.strategies.wallet_copy_strategy import WalletCopyStrategy


def _make_trade(trade_id: str, timestamp: object) -> dict:
    return {
        "transactionHash": trade_id,
        "asset": "token-1",
        "side": "BUY",
        "size": 10,
        "price": 0.5,
        "timestamp": timestamp,
    }


def _make_market(token_id: str = "token-1") -> MarketData:
    return MarketData(
        token_id=token_id,
        condition_id="cond-1",
        market_slug="btc-2025",
        question="Will BTC be above $100k?",
        outcome="YES",
        best_bid=0.49,
        best_ask=0.51,
        mid_price=0.50,
        spread=0.02,
        volume_24h=100000,
        liquidity=5000,
        last_price=0.50,
    )


class FakeOrderManager:
    def __init__(self):
        self._fill_callbacks = []
        self.placed = []

    def on_fill(self, callback):
        self._fill_callbacks.append(callback)

    def place_limit_order(self, **kwargs):
        self.placed.append(kwargs)
        return {"success": True, "order_id": f"o-{len(self.placed)}"}

    def emit_fill(self, side: str, token_id: str, metadata: dict):
        order = Order(
            order_id=f"fill-{len(self.placed)}",
            token_id=token_id,
            market_slug="btc-2025",
            side=side,
            price=0.5,
            size=5,
            status=OrderStatus.FILLED,
            metadata=metadata,
        )
        for callback in self._fill_callbacks:
            callback(order, {"size": 5, "price": 0.5})


def test_trade_deduplication_is_deterministic():
    strategy = WalletCopyStrategy()
    strategy._max_ids_per_wallet = 2
    wallet = "0xabc"

    t1 = _make_trade("tx-1", int(time.time()))
    t2 = _make_trade("tx-2", int(time.time()))
    t3 = _make_trade("tx-3", int(time.time()))

    assert strategy._is_new_trade(wallet, t1) is True
    assert strategy._is_new_trade(wallet, t2) is True
    assert strategy._is_new_trade(wallet, t3) is True
    assert strategy._is_new_trade(wallet, t3) is False
    # Oldest ID should be evicted first when bounded.
    assert strategy._is_new_trade(wallet, t1) is True


def test_trade_too_old_handles_malformed_timestamp():
    strategy = WalletCopyStrategy({"max_copy_delay_seconds": 120})
    assert strategy._trade_too_old({"timestamp": "not-a-number"}) is True


def test_trade_too_old_accepts_recent_millisecond_timestamp():
    strategy = WalletCopyStrategy({"max_copy_delay_seconds": 120})
    now_ms = int(time.time() * 1000)
    assert strategy._trade_too_old({"timestamp": now_ms}) is False


def test_fill_callback_tracks_copy_provenance_and_mirrored_exit():
    strategy = WalletCopyStrategy()
    buy_order = Order(
        order_id="o1",
        token_id="token-1",
        market_slug="m1",
        side="BUY",
        price=0.55,
        size=5,
        status=OrderStatus.FILLED,
        metadata={"strategy": "wallet_copy", "trader_wallet": "0xleader"},
    )
    strategy._on_order_fill(buy_order, {})

    assert strategy.copies_executed == 1
    assert strategy.copied_from["token-1"] == {"0xleader"}

    sell_order = Order(
        order_id="o2",
        token_id="token-1",
        market_slug="m1",
        side="SELL",
        price=0.60,
        size=5,
        status=OrderStatus.FILLED,
        metadata={"strategy": "wallet_copy", "trader_wallet": "0xleader"},
    )
    strategy._on_order_fill(sell_order, {})

    assert strategy.sells_mirrored == 1
    assert "token-1" not in strategy.copied_from


def test_prune_stale_copied_from():
    strategy = WalletCopyStrategy()
    strategy.copied_from["token-keep"] = {"0x1"}
    strategy.copied_from["token-drop"] = {"0x2"}
    strategy.risk_manager = SimpleNamespace(
        positions={
            "token-keep": SimpleNamespace(size=1),
            "token-drop": SimpleNamespace(size=0),
        }
    )

    strategy._prune_stale_copied_from()

    assert "token-keep" in strategy.copied_from
    assert "token-drop" not in strategy.copied_from


def test_end_to_end_buy_fill_then_mirrored_sell_signal(monkeypatch):
    now = int(time.time())
    buy_trade = {
        "transactionHash": "tx-buy-1",
        "asset": "token-1",
        "side": "BUY",
        "size": 10,
        "price": 0.52,
        "timestamp": now,
        "slug": "btc-2025",
        "userName": "leader",
    }
    sell_trade = {
        "transactionHash": "tx-sell-1",
        "asset": "token-1",
        "side": "SELL",
        "size": 10,
        "price": 0.60,
        "timestamp": now,
        "slug": "btc-2025",
        "userName": "leader",
    }
    phase = {"mode": "buy"}

    def fake_get_trades_by_user(user, limit=20, taker_only=True):  # noqa: ARG001
        return [buy_trade] if phase["mode"] == "buy" else [sell_trade]

    monkeypatch.setattr(wallet_copy_module, "get_trades_by_user", fake_get_trades_by_user)

    rm = SimpleNamespace(
        positions={"token-1": SimpleNamespace(size=5, side="YES", market_slug="btc-2025")}
    )
    strategy = WalletCopyStrategy(
        {
            "use_leaderboard": False,
            "tracked_wallets": ["0xleader"],
            "risk_manager": rm,
            "min_tracked_trade_usd": 1,
            "min_wallet_poll_seconds": 0,
        }
    )
    market = _make_market("token-1")
    order_manager = FakeOrderManager()

    buy_signals = strategy.analyze([market])
    assert len(buy_signals) == 1
    assert buy_signals[0].signal_type == SignalType.BUY

    strategy.execute(buy_signals, order_manager)
    # Provenance is fill-based, so no copied mapping before fill.
    assert "token-1" not in strategy.copied_from

    buy_metadata = order_manager.placed[0]["metadata"]
    order_manager.emit_fill("BUY", "token-1", buy_metadata)
    assert strategy.copied_from["token-1"] == {"0xleader"}

    phase["mode"] = "sell"
    sell_signals = strategy.analyze([market])
    assert len(sell_signals) == 1
    assert sell_signals[0].signal_type == SignalType.SELL


def test_multi_leader_same_token_sell_is_selective(monkeypatch):
    now = int(time.time())
    phase = {"mode": "buy"}
    wallet_trades = {
        "0xleader1": {
            "buy": [
                {
                    "transactionHash": "tx-l1-buy",
                    "asset": "token-1",
                    "side": "BUY",
                    "size": 20,
                    "price": 0.50,
                    "timestamp": now,
                    "slug": "btc-2025",
                    "userName": "leader1",
                }
            ],
            "sell": [
                {
                    "transactionHash": "tx-l1-sell",
                    "asset": "token-1",
                    "side": "SELL",
                    "size": 20,
                    "price": 0.62,
                    "timestamp": now + 1,
                    "slug": "btc-2025",
                    "userName": "leader1",
                }
            ],
        },
        "0xleader2": {
            "buy": [
                {
                    "transactionHash": "tx-l2-buy",
                    "asset": "token-1",
                    "side": "BUY",
                    "size": 15,
                    "price": 0.51,
                    "timestamp": now,
                    "slug": "btc-2025",
                    "userName": "leader2",
                }
            ],
            "sell": [
                {
                    "transactionHash": "tx-l2-sell",
                    "asset": "token-1",
                    "side": "SELL",
                    "size": 15,
                    "price": 0.63,
                    "timestamp": now + 1,
                    "slug": "btc-2025",
                    "userName": "leader2",
                }
            ],
        },
    }

    def fake_get_trades_by_user(user, limit=20, taker_only=True):  # noqa: ARG001
        return wallet_trades[user][phase["mode"]]

    monkeypatch.setattr(wallet_copy_module, "get_trades_by_user", fake_get_trades_by_user)

    rm = SimpleNamespace(
        positions={"token-1": SimpleNamespace(size=6, side="YES", market_slug="btc-2025")}
    )
    strategy = WalletCopyStrategy(
        {
            "use_leaderboard": False,
            "tracked_wallets": ["0xleader1", "0xleader2"],
            "risk_manager": rm,
            "min_tracked_trade_usd": 1,
            "cooldown_seconds": 0,
            "min_wallet_poll_seconds": 0,
        }
    )
    market = _make_market("token-1")
    order_manager = FakeOrderManager()

    buy_signals = strategy.analyze([market])
    assert len(buy_signals) == 2
    strategy.execute(buy_signals, order_manager)

    # Simulate fills from both copied leaders.
    for placed in order_manager.placed:
        order_manager.emit_fill("BUY", "token-1", placed["metadata"])

    assert strategy.copied_from["token-1"] == {"0xleader1", "0xleader2"}

    # Only leader1 sells first -> still keep leader2 provenance.
    phase["mode"] = "sell"
    sell_signals = strategy.analyze([market])
    assert len(sell_signals) == 2
    strategy.execute(sell_signals[:1], order_manager)
    order_manager.emit_fill("SELL", "token-1", sell_signals[0].metadata)
    assert strategy.copied_from["token-1"] == {"0xleader2"}
