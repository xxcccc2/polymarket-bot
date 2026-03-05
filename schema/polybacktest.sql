-- Schema extracted from data/backtest/polybacktest.db
-- Tables: markets, snapshots

CREATE TABLE markets (
    market_id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    market_type TEXT NOT NULL,
    coin TEXT NOT NULL,
    event_id TEXT,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    btc_price_start REAL,
    btc_price_end REAL,
    winner TEXT,
    clob_token_up TEXT,
    clob_token_down TEXT,
    condition_id TEXT,
    final_volume REAL,
    final_liquidity REAL,
    resolved_at TEXT,
    raw_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER,
    market_id TEXT NOT NULL,
    time TEXT NOT NULL,
    btc_price REAL,
    price_up REAL,
    price_down REAL,
    orderbook_json TEXT,
    FOREIGN KEY (market_id) REFERENCES markets(market_id)
);

CREATE INDEX idx_snapshots_market_time ON snapshots(market_id, time);
CREATE INDEX idx_markets_type_coin ON markets(market_type, coin);
