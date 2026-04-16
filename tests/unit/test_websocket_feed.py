from __future__ import annotations

import json

from src.websocket_feed import WebSocketFeed, UserWebSocketFeed


class DummyWS:
    def __init__(self):
        self.messages = []

    def send(self, message: str):
        self.messages.append(json.loads(message))


def test_market_feed_batches_subscription_with_custom_features():
    feed = WebSocketFeed()
    feed.ws = DummyWS()

    feed._send_subscription([f"token-{idx}" for idx in range(51)])

    assert len(feed.ws.messages) == 2
    assert feed.ws.messages[0]["type"] == "market"
    assert feed.ws.messages[0]["custom_feature_enabled"] is True


def test_market_feed_parses_best_bid_ask_and_resolution_events():
    feed = WebSocketFeed()
    feed._on_message(
        None,
        json.dumps(
            [
                {"event_type": "best_bid_ask", "asset_id": "token-1", "best_bid": 0.45, "best_ask": 0.47},
                {"event_type": "market_resolved", "condition_id": "cond-1", "winner": "YES"},
                {"event_type": "tick_size_change", "asset_id": "token-1", "new_tick_size": 0.01},
            ]
        ),
    )

    assert feed.orderbooks["token-1"].best_bid == 0.45
    assert feed.resolved_markets["cond-1"].winning_outcome == "YES"
    assert feed.tick_size_by_token["token-1"] == 0.01


def test_user_feed_subscription_includes_markets():
    feed = UserWebSocketFeed(lambda: {"apiKey": "k"}, lambda: ["cond-1", "cond-2"])
    feed.ws = DummyWS()

    feed._send_subscription()

    assert feed.ws.messages[0]["operation"] == "subscribe"
    assert feed.ws.messages[0]["markets"] == ["cond-1", "cond-2"]


def test_user_feed_ignores_bytes_pong_without_json_error():
    feed = UserWebSocketFeed(lambda: {"apiKey": "k"}, lambda: ["cond-1"])
    before = feed.messages_received
    feed._on_message(None, b"PONG")
    assert feed.messages_received == before


def test_user_feed_ignores_non_json_without_error():
    feed = UserWebSocketFeed(lambda: {"apiKey": "k"}, lambda: ["cond-1"])
    before = feed.messages_received
    feed._on_message(None, " ")
    feed._on_message(None, "not-json")
    assert feed.messages_received == before


def test_user_feed_parses_json_from_utf8_bytes():
    feed = UserWebSocketFeed(lambda: {"apiKey": "k"}, lambda: ["cond-1"])
    received = []
    feed.on_order(lambda u: received.append(u))
    payload = {"event_type": "order", "id": "oid", "status": "open"}
    feed._on_message(None, json.dumps(payload).encode("utf-8"))
    assert len(received) == 1
    assert received[0].order_id == "oid"


def test_user_feed_open_authenticates_then_subscribes():
    feed = UserWebSocketFeed(lambda: {"apiKey": "k", "secret": "s", "passphrase": "p"}, lambda: ["cond-1"])
    feed.ws = DummyWS()
    feed._start_heartbeat = lambda: None

    feed._on_open(feed.ws)

    assert feed.ws.messages[0]["type"] == "user"
    assert feed.ws.messages[1]["operation"] == "subscribe"
    assert feed.ws.messages[1]["markets"] == ["cond-1"]
