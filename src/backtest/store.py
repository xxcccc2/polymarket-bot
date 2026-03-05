"""
Local storage for PolyBackTest backtest data.

SQLite-backed store for markets and snapshots.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import BACKTEST_DB, BACKTEST_DIR


class BacktestStore:
    """
    SQLite-backed storage for PolyBackTest markets and snapshots.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else BACKTEST_DB
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
                CREATE TABLE IF NOT EXISTS markets (
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
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER,
                    market_id TEXT NOT NULL,
                    time TEXT NOT NULL,
                    btc_price REAL,
                    price_up REAL,
                    price_down REAL,
                    orderbook_json TEXT,
                    FOREIGN KEY (market_id) REFERENCES markets(market_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_snapshots_market_time "
                "ON snapshots(market_id, time)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_markets_type_coin "
                "ON markets(market_type, coin)"
            )

    def save_markets(self, markets: List[Dict[str, Any]]) -> None:
        """Save markets to the store."""
        with self._connect() as conn:
            for m in markets:
                market_id = str(m.get("market_id", ""))
                if not market_id:
                    continue
                raw = json.dumps(m) if isinstance(m, dict) else str(m)
                conn.execute(
                    """
                    INSERT INTO markets (
                        market_id, slug, market_type, coin, event_id,
                        start_time, end_time, btc_price_start, btc_price_end,
                        winner, clob_token_up, clob_token_down, condition_id,
                        final_volume, final_liquidity, resolved_at, raw_json
                    ) VALUES (
                        :market_id, :slug, :market_type, :coin, :event_id,
                        :start_time, :end_time, :btc_price_start, :btc_price_end,
                        :winner, :clob_token_up, :clob_token_down, :condition_id,
                        :final_volume, :final_liquidity, :resolved_at, :raw_json
                    )
                    ON CONFLICT(market_id) DO UPDATE SET
                        slug=excluded.slug,
                        market_type=excluded.market_type,
                        coin=excluded.coin,
                        event_id=excluded.event_id,
                        start_time=excluded.start_time,
                        end_time=excluded.end_time,
                        btc_price_start=excluded.btc_price_start,
                        btc_price_end=excluded.btc_price_end,
                        winner=excluded.winner,
                        clob_token_up=excluded.clob_token_up,
                        clob_token_down=excluded.clob_token_down,
                        condition_id=excluded.condition_id,
                        final_volume=excluded.final_volume,
                        final_liquidity=excluded.final_liquidity,
                        resolved_at=excluded.resolved_at,
                        raw_json=excluded.raw_json
                    """,
                    {
                        "market_id": market_id,
                        "slug": m.get("slug", ""),
                        "market_type": m.get("market_type", ""),
                        "coin": m.get("coin", "btc"),
                        "event_id": m.get("event_id"),
                        "start_time": m.get("start_time", ""),
                        "end_time": m.get("end_time", ""),
                        "btc_price_start": m.get("btc_price_start"),
                        "btc_price_end": m.get("btc_price_end"),
                        "winner": m.get("winner"),
                        "clob_token_up": m.get("clob_token_up"),
                        "clob_token_down": m.get("clob_token_down"),
                        "condition_id": m.get("condition_id"),
                        "final_volume": m.get("final_volume"),
                        "final_liquidity": m.get("final_liquidity"),
                        "resolved_at": m.get("resolved_at"),
                        "raw_json": raw,
                    },
                )

    def save_snapshots(
        self,
        market_id: str,
        snapshots: List[Dict[str, Any]],
    ) -> None:
        """Save snapshots for a market. Replaces existing snapshots for that market."""
        with self._connect() as conn:
            conn.execute("DELETE FROM snapshots WHERE market_id = ?", (market_id,))
            for s in snapshots:
                ob = s.get("orderbook_up") or s.get("orderbook_down")
                ob_json = json.dumps(ob) if ob else None
                conn.execute(
                    """
                    INSERT INTO snapshots (
                        snapshot_id, market_id, time, btc_price,
                        price_up, price_down, orderbook_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        s.get("id"),
                        market_id,
                        s.get("time", ""),
                        s.get("btc_price"),
                        s.get("price_up"),
                        s.get("price_down"),
                        ob_json,
                    ),
                )

    def load_markets(
        self,
        market_type: Optional[str] = None,
        coin: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Load markets from store."""
        with self._connect() as conn:
            query = "SELECT * FROM markets WHERE 1=1"
            params: List[Any] = []
            if market_type:
                query += " AND market_type = ?"
                params.append(market_type)
            if coin:
                query += " AND coin = ?"
                params.append(coin)
            query += " ORDER BY start_time DESC"
            if limit:
                query += " LIMIT ?"
                params.append(limit)
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def load_snapshots(
        self,
        market_id: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Load snapshots for a market, optionally filtered by time."""
        with self._connect() as conn:
            query = "SELECT * FROM snapshots WHERE market_id = ?"
            params: List[Any] = [market_id]
            if start_time:
                query += " AND time >= ?"
                params.append(start_time)
            if end_time:
                query += " AND time <= ?"
                params.append(end_time)
            query += " ORDER BY time ASC"
            rows = conn.execute(query, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("orderbook_json"):
                try:
                    d["orderbook"] = json.loads(d["orderbook_json"])
                except json.JSONDecodeError:
                    pass
            result.append(d)
        return result

    def get_market_count(self, market_type: Optional[str] = None) -> int:
        """Count stored markets."""
        with self._connect() as conn:
            if market_type:
                row = conn.execute(
                    "SELECT COUNT(*) FROM markets WHERE market_type = ?",
                    (market_type,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM markets").fetchone()
        return row[0] if row else 0
