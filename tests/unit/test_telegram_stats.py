import sqlite3

from src import telegram_bot


def test_stats_reads_isolated_paper_database(tmp_path):
    db_path = tmp_path / "paper.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE orders (id INTEGER)")
        db.execute("CREATE TABLE trades (id INTEGER)")
        db.execute("CREATE TABLE positions (realized_pnl REAL, unrealized_pnl REAL)")
        db.execute("INSERT INTO orders VALUES (1)")
        db.execute("INSERT INTO trades VALUES (1)")
        db.execute("INSERT INTO positions VALUES (2.5, -0.5)")
    telegram_bot.DATABASES["ml"] = db_path

    assert telegram_bot.stats("ml") == (1, 1, 1, 2.0)
