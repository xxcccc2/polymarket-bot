"""
SQLite persistence layer for bot state.

Stores orders, positions, and trades to survive restarts.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import BOT_STATE_DB

DEFAULT_DB_PATH = BOT_STATE_DB


class SqliteStore:
    """Lightweight SQLite-backed storage for bot state."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
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
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
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
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    order_id TEXT,
                    token_id TEXT,
                    market_slug TEXT,
                    side TEXT,
                    price REAL,
                    size REAL,
                    strategy TEXT,
                    traded_at TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_token ON orders(token_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_token ON trades(token_id)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet_copy_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    tracked_wallets_json TEXT NOT NULL,
                    removed_at_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def save_order(self, record: Dict[str, Any]) -> None:
        metadata = record.get("metadata") or {}
        payload = {
            "order_id": record["order_id"],
            "token_id": record["token_id"],
            "market_slug": record.get("market_slug"),
            "side": record["side"],
            "price": record["price"],
            "size": record["size"],
            "filled_size": record.get("filled_size", 0),
            "status": record["status"],
            "order_type": record.get("order_type"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "metadata_json": json.dumps(metadata),
        }

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, token_id, market_slug, side, price, size, filled_size,
                    status, order_type, created_at, updated_at, metadata_json
                ) VALUES (
                    :order_id, :token_id, :market_slug, :side, :price, :size, :filled_size,
                    :status, :order_type, :created_at, :updated_at, :metadata_json
                )
                ON CONFLICT(order_id) DO UPDATE SET
                    token_id=excluded.token_id,
                    market_slug=excluded.market_slug,
                    side=excluded.side,
                    price=excluded.price,
                    size=excluded.size,
                    filled_size=excluded.filled_size,
                    status=excluded.status,
                    order_type=excluded.order_type,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                payload,
            )

    def load_orders(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM orders").fetchall()

        records = []
        for row in rows:
            record = dict(row)
            record["metadata"] = json.loads(record.get("metadata_json") or "{}")
            record.pop("metadata_json", None)
            records.append(record)
        return records

    def save_position(self, record: Dict[str, Any]) -> None:
        payload = {
            "token_id": record["token_id"],
            "market_slug": record.get("market_slug"),
            "side": record.get("side"),
            "size": record.get("size"),
            "avg_price": record.get("avg_price"),
            "current_price": record.get("current_price"),
            "unrealized_pnl": record.get("unrealized_pnl"),
            "realized_pnl": record.get("realized_pnl"),
            "opened_at": record.get("opened_at"),
            "updated_at": record.get("updated_at"),
        }

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO positions (
                    token_id, market_slug, side, size, avg_price, current_price,
                    unrealized_pnl, realized_pnl, opened_at, updated_at
                ) VALUES (
                    :token_id, :market_slug, :side, :size, :avg_price, :current_price,
                    :unrealized_pnl, :realized_pnl, :opened_at, :updated_at
                )
                ON CONFLICT(token_id) DO UPDATE SET
                    market_slug=excluded.market_slug,
                    side=excluded.side,
                    size=excluded.size,
                    avg_price=excluded.avg_price,
                    current_price=excluded.current_price,
                    unrealized_pnl=excluded.unrealized_pnl,
                    realized_pnl=excluded.realized_pnl,
                    opened_at=excluded.opened_at,
                    updated_at=excluded.updated_at
                """,
                payload,
            )

    def load_positions(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM positions").fetchall()

        return [dict(row) for row in rows]

    def delete_position(self, token_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM positions WHERE token_id = ?", (token_id,))

    def save_trade(self, record: Dict[str, Any]) -> None:
        payload = {
            "trade_id": record["trade_id"],
            "order_id": record.get("order_id"),
            "token_id": record.get("token_id"),
            "market_slug": record.get("market_slug"),
            "side": record.get("side"),
            "price": record.get("price"),
            "size": record.get("size"),
            "strategy": record.get("strategy"),
            "traded_at": record.get("traded_at"),
        }

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO trades (
                    trade_id, order_id, token_id, market_slug, side,
                    price, size, strategy, traded_at
                ) VALUES (
                    :trade_id, :order_id, :token_id, :market_slug, :side,
                    :price, :size, :strategy, :traded_at
                )
                """,
                payload,
            )

    def paper_account(self, starting_balance: float) -> Dict[str, float]:
        """Derive paper cash, equity and PnL from persisted fills and positions."""
        with self._connect() as conn:
            buys, sells = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN side = 'BUY' THEN price * size END), 0),
                    COALESCE(SUM(CASE WHEN side = 'SELL' THEN price * size END), 0)
                FROM trades
                """
            ).fetchone()
            market_value, unrealized = conn.execute(
                """
                SELECT COALESCE(SUM(current_price * size), 0),
                       COALESCE(SUM(unrealized_pnl), 0)
                FROM positions
                """
            ).fetchone()
        cash = float(starting_balance) - float(buys) + float(sells)
        equity = cash + float(market_value)
        return {
            "cash": cash,
            "equity": equity,
            "pnl": equity - float(starting_balance),
            "unrealized_pnl": float(unrealized),
        }

    def save_wallet_copy_state(
        self,
        tracked_wallets: List[str],
        removed_at: Dict[str, float],
    ) -> None:
        """Persist wallet_copy tracked wallets and rotation cooldown (removed_at)."""
        from datetime import datetime
        payload = {
            "tracked_wallets_json": json.dumps(tracked_wallets),
            "removed_at_json": json.dumps(removed_at),
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO wallet_copy_state (id, tracked_wallets_json, removed_at_json, updated_at)
                VALUES (1, :tracked_wallets_json, :removed_at_json, :updated_at)
                ON CONFLICT(id) DO UPDATE SET
                    tracked_wallets_json = excluded.tracked_wallets_json,
                    removed_at_json = excluded.removed_at_json,
                    updated_at = excluded.updated_at
                """,
                payload,
            )

    def load_wallet_copy_state(
        self,
    ) -> Optional[tuple[List[str], Dict[str, float]]]:
        """
        Load persisted wallet_copy state.

        Returns:
            (tracked_wallets, removed_at) or None if no persisted state.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT tracked_wallets_json, removed_at_json FROM wallet_copy_state WHERE id = 1"
            ).fetchone()
        if not row:
            return None
        try:
            tracked = json.loads(row["tracked_wallets_json"] or "[]")
            removed = json.loads(row["removed_at_json"] or "{}")
            removed = {k: float(v) for k, v in removed.items()}
            return (list(tracked), removed)
        except (json.JSONDecodeError, TypeError):
            return None
