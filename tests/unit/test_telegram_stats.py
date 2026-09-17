import sqlite3

from src import telegram_bot


def test_stats_reads_isolated_paper_database(tmp_path):
    db_path = tmp_path / "paper.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE orders (id INTEGER, status TEXT)")
        db.execute("CREATE TABLE trades (id INTEGER, side TEXT, price REAL, size REAL)")
        db.execute("CREATE TABLE positions (current_price REAL, size REAL)")
        db.execute("INSERT INTO orders VALUES (1, 'open'), (2, 'cancelled')")
        db.execute("INSERT INTO trades VALUES (1, 'BUY', 0.4, 10), (2, 'SELL', 0.6, 10)")
    telegram_bot.DATABASES["ml"] = db_path

    assert telegram_bot.stats("ml") == (1, 2, 0, 102.0, 2.0)


def test_observer_stats_reads_resolved_forecasts(tmp_path):
    db_path = tmp_path / "observer.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE ml_observations (resolved_at REAL, brier_score REAL)")
        db.execute("INSERT INTO ml_observations VALUES (1, 0.09), (NULL, NULL)")
    telegram_bot.DATABASES["ml"] = db_path

    assert telegram_bot.observer_stats() == (2, 1, 0.09)
