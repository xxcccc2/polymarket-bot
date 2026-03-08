"""
Binance microstructure collector for the ML research pipeline.

This collector is intentionally separate from the live strategy feed. It writes
raw depth, liquidation, trade, funding, and open-interest snapshots to a local
SQLite database so the offline trainer can replay them deterministically.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

import websocket

from ...config import (
    BINANCE_FUTURES_REST_URL,
    BINANCE_SYMBOL,
    BINANCE_WS_COMBINED_URL,
    ML_BINANCE_COLLECTOR_DB,
    ML_COLLECTOR_DEPTH_LEVELS,
    ML_COLLECTOR_REST_POLL_SECONDS,
)
from ...logging_utils import cprint


class BinanceMicrostructureCollector:
    """Persist Binance market microstructure events for offline feature building."""

    def __init__(
        self,
        *,
        symbol: str = BINANCE_SYMBOL,
        db_path: str | Path = ML_BINANCE_COLLECTOR_DB,
        depth_levels: int = ML_COLLECTOR_DEPTH_LEVELS,
        rest_poll_seconds: int = ML_COLLECTOR_REST_POLL_SECONDS,
    ) -> None:
        self.symbol = symbol.lower()
        self.db_path = Path(db_path)
        self.depth_levels = depth_levels
        self.rest_poll_seconds = rest_poll_seconds
        self._running = False
        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None

        streams = "/".join(
            [
                f"{self.symbol}@depth{self.depth_levels}@100ms",
                f"{self.symbol}@aggTrade",
                f"{self.symbol}@forceOrder",
            ]
        )
        self._ws_url = f"{BINANCE_WS_COMBINED_URL}?streams={streams}"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def start(self) -> None:
        """Start websocket and REST polling threads."""
        if self._running:
            return
        self._running = True
        self._ws_thread = threading.Thread(target=self._run_ws_loop, name="ml-binance-ws", daemon=True)
        self._poll_thread = threading.Thread(target=self._poll_rest_loop, name="ml-binance-rest", daemon=True)
        self._ws_thread.start()
        self._poll_thread.start()
        cprint(f"🟢 ML Binance collector started for {self.symbol.upper()}", "green")

    def stop(self) -> None:
        """Stop the collector gracefully."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)
        cprint(f"🔴 ML Binance collector stopped for {self.symbol.upper()}", "yellow")

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS depth_snapshots (
                    event_time REAL NOT NULL,
                    symbol TEXT NOT NULL,
                    bids_json TEXT NOT NULL,
                    asks_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agg_trades (
                    event_time REAL NOT NULL,
                    symbol TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    is_buyer_maker INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS liquidations (
                    event_time REAL NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS funding_open_interest (
                    sampled_at REAL NOT NULL,
                    symbol TEXT NOT NULL,
                    funding_rate REAL,
                    open_interest REAL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _run_ws_loop(self) -> None:
        while self._running:
            try:
                self._ws = websocket.WebSocketApp(
                    self._ws_url,
                    on_message=self._on_message,
                    on_open=lambda ws: cprint(f"🔗 ML Binance WS connected: {self.symbol.upper()}", "green"),
                    on_close=lambda ws, code, msg: cprint(f"🔌 ML Binance WS closed ({code})", "yellow"),
                    on_error=lambda ws, err: cprint(f"❌ ML Binance WS error: {err}", "red"),
                )
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                cprint(f"❌ ML Binance collector loop error: {exc}", "red")
            if self._running:
                time.sleep(5)

    def _on_message(self, ws, message: str) -> None:
        payload = json.loads(message)
        stream = payload.get("stream", "")
        data = payload.get("data", {})
        event_time = float((data.get("E") or time.time() * 1000) / 1000.0)

        if stream.endswith("@aggTrade"):
            self._save_agg_trade(
                event_time=event_time,
                price=float(data.get("p") or 0),
                quantity=float(data.get("q") or 0),
                is_buyer_maker=1 if data.get("m") else 0,
            )
            return

        if "@forceOrder" in stream:
            order = data.get("o") or {}
            self._save_liquidation(
                event_time=event_time,
                side=str(order.get("S") or ""),
                price=float(order.get("ap") or 0),
                quantity=float(order.get("q") or 0),
            )
            return

        if "@depth" in stream:
            self._save_depth_snapshot(
                event_time=event_time,
                bids=data.get("b") or [],
                asks=data.get("a") or [],
            )

    def _save_depth_snapshot(self, *, event_time: float, bids, asks) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO depth_snapshots (event_time, symbol, bids_json, asks_json)
                VALUES (?, ?, ?, ?)
                """,
                (event_time, self.symbol, json.dumps(bids), json.dumps(asks)),
            )

    def _save_agg_trade(self, *, event_time: float, price: float, quantity: float, is_buyer_maker: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agg_trades (event_time, symbol, price, quantity, is_buyer_maker)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_time, self.symbol, price, quantity, is_buyer_maker),
            )

    def _save_liquidation(self, *, event_time: float, side: str, price: float, quantity: float) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO liquidations (event_time, symbol, side, price, quantity)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_time, self.symbol, side, price, quantity),
            )

    def _poll_rest_loop(self) -> None:
        while self._running:
            sampled_at = time.time()
            funding_rate = self._fetch_funding_rate()
            open_interest = self._fetch_open_interest()
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO funding_open_interest (sampled_at, symbol, funding_rate, open_interest)
                    VALUES (?, ?, ?, ?)
                    """,
                    (sampled_at, self.symbol, funding_rate, open_interest),
                )
            time.sleep(self.rest_poll_seconds)

    def _fetch_funding_rate(self) -> Optional[float]:
        url = f"{BINANCE_FUTURES_REST_URL}/fapi/v1/premiumIndex?symbol={self.symbol.upper()}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                payload = json.loads(response.read().decode())
            return float(payload.get("lastFundingRate")) if payload.get("lastFundingRate") is not None else None
        except Exception as exc:
            cprint(f"⚠️  Funding rate poll failed: {exc}", "yellow")
            return None

    def _fetch_open_interest(self) -> Optional[float]:
        url = f"{BINANCE_FUTURES_REST_URL}/fapi/v1/openInterest?symbol={self.symbol.upper()}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                payload = json.loads(response.read().decode())
            return float(payload.get("openInterest")) if payload.get("openInterest") is not None else None
        except Exception as exc:
            cprint(f"⚠️  Open interest poll failed: {exc}", "yellow")
            return None
