"""
Binance WebSocket Feed — Real-time BTC price, VWAP, and volatility.

Streams BTC/USDT trades from Binance and computes rolling metrics
that the cross-asset latency strategy consumes to generate signals.

Runs as a background daemon thread so the main bot loop is not blocked.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Deque, Dict, List, Optional

import websocket  # websocket-client library (already in requirements)

from ..logging_utils import cprint
from ..config import (
    BINANCE_WS_URL,
    BINANCE_SYMBOL,
    VOL_WINDOW_SECONDS,
)


@dataclass
class BinanceTrade:
    """Single trade tick from Binance."""
    price: float
    qty: float
    timestamp: float  # unix seconds
    is_buyer_maker: bool


@dataclass
class BinanceState:
    """Aggregated real-time state derived from Binance trade stream."""
    last_price: float = 0.0
    vwap_5m: float = 0.0
    volume_5m: float = 0.0
    volatility_5m: float = 0.0           # annualized σ
    price_change_pct_10s: float = 0.0     # % change over last 10 seconds
    price_change_pct_30s: float = 0.0     # % change over last 30 seconds
    price_change_pct_60s: float = 0.0     # % change over last 60 seconds
    price_velocity: float = 0.0           # $/second over last 10s
    bid_pressure: float = 0.5             # ratio of buy volume to total (0-1)
    last_update: float = 0.0              # unix timestamp
    connected: bool = False
    trade_count_5m: int = 0


class BinanceFeed:
    """
    Background WebSocket feed streaming BTC/USDT trades from Binance.

    Usage:
        feed = BinanceFeed()
        feed.start()
        ...
        state = feed.get_state()   # BinanceState snapshot
        price = feed.last_price    # quick accessor
        ...
        feed.stop()
    """

    def __init__(
        self,
        symbol: str = BINANCE_SYMBOL,
        ws_url: str = BINANCE_WS_URL,
        window_seconds: int = VOL_WINDOW_SECONDS,
        on_state_update: Optional[Callable[["BinanceState"], None]] = None,
    ) -> None:
        self.symbol = symbol.lower()
        self.ws_url = f"{ws_url}/{self.symbol}@trade"
        self.window_seconds = window_seconds
        self.on_state_update = on_state_update

        # Rolling trade buffer (kept for window_seconds)
        self._trades: Deque[BinanceTrade] = deque()
        # Price snapshots every second for velocity/change calculations
        self._price_snapshots: Deque[tuple] = deque()  # (timestamp, price)
        self._lock = threading.Lock()

        # Current aggregated state
        self._state = BinanceState()

        # WebSocket internals
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def last_price(self) -> float:
        return self._state.last_price

    @property
    def connected(self) -> bool:
        return self._state.connected

    def get_state(self) -> BinanceState:
        """Return a snapshot of the current aggregated state."""
        with self._lock:
            # Return a copy so callers can't mutate internal state
            return BinanceState(
                last_price=self._state.last_price,
                vwap_5m=self._state.vwap_5m,
                volume_5m=self._state.volume_5m,
                volatility_5m=self._state.volatility_5m,
                price_change_pct_10s=self._state.price_change_pct_10s,
                price_change_pct_30s=self._state.price_change_pct_30s,
                price_change_pct_60s=self._state.price_change_pct_60s,
                price_velocity=self._state.price_velocity,
                bid_pressure=self._state.bid_pressure,
                last_update=self._state.last_update,
                connected=self._state.connected,
                trade_count_5m=self._state.trade_count_5m,
            )

    def start(self) -> None:
        """Start the background feed thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="binance-feed", daemon=True
        )
        self._thread.start()
        cprint(f"🟢 Binance feed started: {self.symbol.upper()}", "green")

    def stop(self) -> None:
        """Stop the feed gracefully."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._state.connected = False
        cprint("🔴 Binance feed stopped", "yellow")

    # ------------------------------------------------------------------
    # WebSocket callbacks
    # ------------------------------------------------------------------

    def _on_message(self, ws, message: str) -> None:
        try:
            data = json.loads(message)
            trade = BinanceTrade(
                price=float(data["p"]),
                qty=float(data["q"]),
                timestamp=data["T"] / 1000.0,  # ms → s
                is_buyer_maker=data["m"],
            )
            self._ingest_trade(trade)
        except (KeyError, ValueError, TypeError) as exc:
            cprint(f"⚠️  Binance parse error: {exc}", "yellow")

    def _on_open(self, ws) -> None:
        self._state.connected = True
        self._reconnect_delay = 1.0
        cprint(f"🔗 Binance WS connected: {self.symbol.upper()}", "green")

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        self._state.connected = False
        cprint(f"🔌 Binance WS closed (code={close_status_code})", "yellow")

    def _on_error(self, ws, error) -> None:
        cprint(f"❌ Binance WS error: {error}", "red")

    # ------------------------------------------------------------------
    # Background loop with auto-reconnect
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        while self._running:
            try:
                self._ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_message=self._on_message,
                    on_open=self._on_open,
                    on_close=self._on_close,
                    on_error=self._on_error,
                )
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                cprint(f"❌ Binance WS exception: {exc}", "red")

            if not self._running:
                break

            cprint(
                f"♻️  Binance reconnecting in {self._reconnect_delay:.0f}s...",
                "yellow",
            )
            time.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 2, self._max_reconnect_delay
            )

    # ------------------------------------------------------------------
    # Trade ingestion & metric computation
    # ------------------------------------------------------------------

    def _ingest_trade(self, trade: BinanceTrade) -> None:
        now = trade.timestamp

        with self._lock:
            # Add trade to rolling buffer
            self._trades.append(trade)

            # Snapshot price every ~1s for velocity calculations
            if (
                not self._price_snapshots
                or now - self._price_snapshots[-1][0] >= 1.0
            ):
                self._price_snapshots.append((now, trade.price))

            # Prune old data outside window
            cutoff = now - self.window_seconds
            while self._trades and self._trades[0].timestamp < cutoff:
                self._trades.popleft()
            while (
                self._price_snapshots
                and self._price_snapshots[0][0] < cutoff
            ):
                self._price_snapshots.popleft()

            # Recompute metrics
            self._recompute(trade.price, now)

        # Fire callback (outside lock)
        if self.on_state_update:
            try:
                self.on_state_update(self._state)
            except Exception:
                pass

    def _recompute(self, current_price: float, now: float) -> None:
        """Recompute all rolling metrics. Caller must hold _lock."""
        trades = self._trades
        snaps = self._price_snapshots

        self._state.last_price = current_price
        self._state.last_update = now
        self._state.trade_count_5m = len(trades)

        if not trades:
            return

        # --- VWAP ---
        total_pq = sum(t.price * t.qty for t in trades)
        total_q = sum(t.qty for t in trades)
        self._state.vwap_5m = total_pq / total_q if total_q > 0 else current_price
        self._state.volume_5m = total_q

        # --- Bid pressure (buy volume / total volume) ---
        buy_vol = sum(t.qty for t in trades if not t.is_buyer_maker)
        self._state.bid_pressure = buy_vol / total_q if total_q > 0 else 0.5

        # --- Price changes over different windows ---
        for seconds, attr in [
            (10, "price_change_pct_10s"),
            (30, "price_change_pct_30s"),
            (60, "price_change_pct_60s"),
        ]:
            ref_price = self._price_at_seconds_ago(snaps, now, seconds)
            if ref_price and ref_price > 0:
                setattr(
                    self._state,
                    attr,
                    ((current_price - ref_price) / ref_price) * 100,
                )
            else:
                setattr(self._state, attr, 0.0)

        # --- Price velocity ($/sec over last 10s) ---
        ref_10 = self._price_at_seconds_ago(snaps, now, 10)
        if ref_10:
            self._state.price_velocity = (current_price - ref_10) / 10.0
        else:
            self._state.price_velocity = 0.0

        # --- Volatility (annualized σ from 1-second returns) ---
        if len(snaps) >= 10:
            returns: List[float] = []
            snap_list = list(snaps)
            for i in range(1, len(snap_list)):
                p0 = snap_list[i - 1][1]
                p1 = snap_list[i][1]
                if p0 > 0:
                    returns.append(math.log(p1 / p0))
            if len(returns) >= 5:
                mean_r = sum(returns) / len(returns)
                var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
                # Annualize: σ_annual = σ_1s * sqrt(seconds_per_year)
                # ~31.5M seconds/year → sqrt ≈ 5612
                self._state.volatility_5m = math.sqrt(var_r) * 5612
            else:
                self._state.volatility_5m = 0.0
        else:
            self._state.volatility_5m = 0.0

    @staticmethod
    def _price_at_seconds_ago(
        snaps: Deque[tuple], now: float, seconds: int
    ) -> Optional[float]:
        """Find the price snapshot closest to `seconds` ago."""
        target = now - seconds
        best: Optional[tuple] = None
        for snap in snaps:
            if snap[0] <= target:
                best = snap
            else:
                break
        return best[1] if best else None
