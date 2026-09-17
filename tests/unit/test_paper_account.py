from src.persistence import SqliteStore


def test_paper_account_uses_fills_and_open_market_value(tmp_path):
    store = SqliteStore(tmp_path / "paper.sqlite")
    store.save_trade({
        "trade_id": "buy", "side": "BUY", "price": 0.4, "size": 10,
        "token_id": "token", "market_slug": "market",
    })
    store.save_position({
        "token_id": "token", "market_slug": "market", "side": "UP",
        "size": 10, "avg_price": 0.4, "current_price": 0.6,
        "unrealized_pnl": 2, "realized_pnl": 0,
    })

    assert store.paper_account(100) == {
        "cash": 96.0,
        "equity": 102.0,
        "pnl": 2.0,
        "unrealized_pnl": 2.0,
    }
