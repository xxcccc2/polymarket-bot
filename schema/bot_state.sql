-- Schema extracted from bot_state_chr.sqlite
-- Tables: orders, positions, trades, wallet_copy_state

CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    token_id TEXT NOT NULL,
    market_slug TEXT,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    filled_size REAL NOT NULL,
    status TEXT NOT NULL,
    order_type TEXT,
    created_at TEXT,
    updated_at TEXT,
    metadata_json TEXT
);

CREATE TABLE positions (
    token_id TEXT PRIMARY KEY,
    market_slug TEXT,
    side TEXT,
    size REAL,
    avg_price REAL,
    current_price REAL,
    unrealized_pnl REAL,
    realized_pnl REAL,
    opened_at TEXT,
    updated_at TEXT
);

CREATE TABLE trades (
    trade_id TEXT PRIMARY KEY,
    order_id TEXT,
    token_id TEXT,
    market_slug TEXT,
    side TEXT,
    price REAL,
    size REAL,
    strategy TEXT,
    traded_at TEXT
);

CREATE INDEX idx_orders_token ON orders(token_id);
CREATE INDEX idx_trades_token ON trades(token_id);

CREATE TABLE wallet_copy_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    tracked_wallets_json TEXT NOT NULL,
    removed_at_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
