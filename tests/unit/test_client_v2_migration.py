from __future__ import annotations

from types import SimpleNamespace

import src.client as client_module
from src.client import PolymarketClient


class DummyOrderArgs:
    instances = []

    def __init__(self, **kwargs):
        if "fee_rate_bps" in kwargs:
            raise TypeError("fee_rate_bps is not supported")
        self.kwargs = kwargs
        DummyOrderArgs.instances.append(kwargs)


class DummyOrderType:
    GTC = "GTC"
    FOK = "FOK"


class DummySide:
    BUY = "BUY"
    SELL = "SELL"


class DummyOrderPayload:
    def __init__(self, orderID):
        self.orderID = orderID


class DummyPostOrdersArgs:
    def __init__(self, order, orderType):
        self.order = order
        self.orderType = orderType


class DummyClobClient:
    constructed = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.creds = kwargs.get("creds")
        self.posted_orders = []
        self.cancel_payload = None
        DummyClobClient.constructed.append(kwargs)

    def create_or_derive_api_key(self):
        return SimpleNamespace(api_key="key", api_secret="secret", api_passphrase="pass")

    def update_balance_allowance(self, params):
        return {"balance": 1000000, "allowance": 1000000}

    def get_tick_size(self, token_id):
        return "0.01"

    def get_neg_risk(self, token_id):
        return False

    def create_and_post_order(self, order_args, options=None, order_type=None, post_only=False):
        self.posted_orders.append((order_args, options, order_type, post_only))
        return {"orderID": "order-v2"}

    def create_order(self, order_args, options=None):
        return {"signed": order_args.kwargs}

    def post_orders(self, orders):
        return [{"orderID": f"batch-{idx}"} for idx, _ in enumerate(orders, start=1)]

    def cancel_order(self, payload):
        self.cancel_payload = payload
        return {"cancelled": payload.orderID}


class DummyBalanceAllowanceParams:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class DummyAssetType:
    COLLATERAL = "COLLATERAL"


def patch_client(monkeypatch):
    DummyClobClient.constructed = []
    DummyOrderArgs.instances = []
    monkeypatch.setattr(client_module, "CLOB_AVAILABLE", True)
    monkeypatch.setattr(client_module, "PAPER_TRADING", False)
    monkeypatch.setattr(client_module, "PRIVATE_KEY", "0xabc")
    monkeypatch.setattr(client_module, "PROXY_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setattr(client_module, "ClobClient", DummyClobClient)
    monkeypatch.setattr(client_module, "OrderArgs", DummyOrderArgs)
    monkeypatch.setattr(client_module, "OrderType", DummyOrderType)
    monkeypatch.setattr(client_module, "Side", DummySide)
    monkeypatch.setattr(client_module, "OrderPayload", DummyOrderPayload)
    monkeypatch.setattr(client_module, "PostOrdersArgs", DummyPostOrdersArgs)
    monkeypatch.setattr(client_module, "PostOrdersV2Args", DummyPostOrdersArgs)
    monkeypatch.setattr(client_module, "BalanceAllowanceParams", DummyBalanceAllowanceParams)
    monkeypatch.setattr(client_module, "AssetType", DummyAssetType)
    monkeypatch.setattr(client_module, "AUTO_ALLOWANCE_REFRESH_ENABLED", False)
    monkeypatch.setattr(client_module, "CLOB_HEARTBEAT_ENABLED", False)


def test_connect_rebuilds_client_with_v2_api_creds(monkeypatch):
    patch_client(monkeypatch)
    client = PolymarketClient()

    assert client.connect() is True

    assert client.api_creds_set is True
    assert len(DummyClobClient.constructed) == 2
    assert DummyClobClient.constructed[0]["creds"] is None
    assert DummyClobClient.constructed[1]["creds"].api_key == "key"


def test_connect_skips_clob_auth_in_paper_mode(monkeypatch):
    patch_client(monkeypatch)
    monkeypatch.setattr(client_module, "PAPER_TRADING", True)
    client = PolymarketClient()

    assert client.connect() is True

    assert client.is_connected is True
    assert client.api_creds_set is False
    assert DummyClobClient.constructed == []


def test_get_trades_skips_live_client_in_paper_mode(monkeypatch):
    patch_client(monkeypatch)
    monkeypatch.setattr(client_module, "PAPER_TRADING", True)
    client = PolymarketClient()
    assert client.connect() is True

    assert client.get_trades() == []


def test_place_order_uses_v2_order_args_without_fee_rate_bps(monkeypatch):
    patch_client(monkeypatch)
    client = PolymarketClient()
    assert client.connect() is True

    result = client.place_order("token-1", "BUY", 0.42, 10, post_only=True)

    assert result["success"] is True
    assert result["order_id"] == "order-v2"
    assert DummyOrderArgs.instances[-1]["side"] == DummySide.BUY
    assert "fee_rate_bps" not in DummyOrderArgs.instances[-1]


def test_cancel_order_uses_v2_order_payload(monkeypatch):
    patch_client(monkeypatch)
    client = PolymarketClient()
    assert client.connect() is True

    result = client.cancel_order("order-v2")

    assert result["success"] is True
    assert client.client.cancel_payload.orderID == "order-v2"
