import pytest

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


def test_ml_observation_keeps_first_forecast_and_scores_resolution(tmp_path):
    store = SqliteStore(tmp_path / "observer.sqlite")
    record = {"market_slug": "btc-updown-15m-1", "token_id": "up", "probability": 0.7}

    assert store.save_ml_observation(record)
    assert not store.save_ml_observation({**record, "probability": 0.2})
    store.resolve_ml_observation("btc-updown-15m-1", "up", 1.0)

    observation = store.unresolved_ml_observations()
    assert observation == []
    with store._connect() as db:
        row = db.execute("SELECT probability, correct_prediction, brier_score FROM ml_observations").fetchone()
    assert row["probability"] == 0.7
    assert row["correct_prediction"] == 1
    assert row["brier_score"] == pytest.approx(0.09)
