"""
Binance WebSocket Feed — Real-time crypto price, VWAP, and volatility.

Streams BTC/ETH/SOL/XRP-USDT trades from Binance via a single combined WebSocket
and computes rolling metrics that cross-asset strategies consume.

Runs as a background daemon thread so the main bot loop is not blocked.
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional

import pandas as pd
import websocket  # websocket-client library (already in requirements)

from ..logging_utils import cprint
from ..config import (
    BINANCE_WS_URL,
    BINANCE_WS_COMBINED_URL,
    BINANCE_REST_URL,
    BINANCE_SYMBOL,
    BINANCE_SYMBOLS,
    VOL_WINDOW_SECONDS,
)

# Map short asset names to Binance symbols
ASSET_TO_SYMBOL: Dict[str, str] = {
    "btc": "btcusdt",
    "eth": "ethusdt",
    "sol": "solusdt",
    "xrp": "xrpusdt",
}
SYMBOL_TO_ASSET: Dict[str, str] = {v: k for k, v in ASSET_TO_SYMBOL.items()}
KLINE_INTERVAL_SECONDS: Dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


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


def _copy_state(s: BinanceState) -> BinanceState:
    return BinanceState(
        last_price=s.last_price,
        vwap_5m=s.vwap_5m,
        volume_5m=s.volume_5m,
        volatility_5m=s.volatility_5m,
        price_change_pct_10s=s.price_change_pct_10s,
        price_change_pct_30s=s.price_change_pct_30s,
        price_change_pct_60s=s.price_change_pct_60s,
        price_velocity=s.price_velocity,
        bid_pressure=s.bid_pressure,
        last_update=s.last_update,
        connected=s.connected,
        trade_count_5m=s.trade_count_5m,
    )


class BinanceFeed:
    """
    Background WebSocket feed streaming crypto/USDT trades from Binance.

    Supports single-symbol (legacy) or multi-symbol mode via Binance combined
    stream. One connection, one thread — not heavy.

    Usage:
        feed = BinanceFeed()  # uses BINANCE_SYMBOLS from config
        feed.start()
        state = feed.get_state()        # BTC (default)
        state = feed.get_state("eth")   # ETH-specific state
        price = feed.last_price         # BTC last price
        feed.stop()
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        ws_url: str = BINANCE_WS_URL,
        ws_combined_url: str = BINANCE_WS_COMBINED_URL,
        window_seconds: int = VOL_WINDOW_SECONDS,
        on_state_update: Optional[Callable[[str, BinanceState], None]] = None,
    ) -> None:
        self.symbols = (symbols or BINANCE_SYMBOLS or [BINANCE_SYMBOL])
        if isinstance(self.symbols, str):
            self.symbols = [self.symbols.lower()]
        self.symbols = [s.lower() for s in self.symbols if s]
        if not self.symbols:
            self.symbols = [BINANCE_SYMBOL.lower()]

        self._multi = len(self.symbols) > 1
        self.ws_url = ws_url
        self.ws_combined_url = ws_combined_url
        self.window_seconds = window_seconds
        self.on_state_update = on_state_update

        # Per-symbol state (multi) or single state (legacy)
        self._states: Dict[str, BinanceState] = {}
        self._trades: Dict[str, Deque[BinanceTrade]] = {}
        self._price_snapshots: Dict[str, Deque[tuple]] = {}
        for sym in self.symbols:
            self._states[sym] = BinanceState()
            self._trades[sym] = deque()
            self._price_snapshots[sym] = deque()

        self._lock = threading.Lock()
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._reconnect_delay = 5.0
        self._max_reconnect_delay = 60.0
        self._min_reconnect_interval = 10.0

        self._ohlcv_cache: Dict[str, tuple[pd.DataFrame, float]] = {}
        # 1h candle open tracking: sym -> (hour_bucket_ts, open_price)
        # hour_bucket_ts = Unix timestamp of the start of the current UTC hour
        self._1h_candle: Dict[str, tuple] = {}  # sym -> (bucket_ts, open_price)
        self._1h_candle_cache: Dict[str, tuple] = {}  # REST cache: cache_key -> (price, ts)

        # Build WebSocket URL
        if self._multi:
            streams = "/".join(f"{s}@trade" for s in self.symbols)
            self._ws_url = f"{self.ws_combined_url}?streams={streams}"
        else:
            self._ws_url = f"{self.ws_url}/{self.symbols[0]}@trade"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def last_price(self) -> float:
        """BTC last price (backward compat)."""
        return self.get_state("btc").last_price

    @property
    def connected(self) -> bool:
        """True if any symbol is connected."""
        with self._lock:
            return any(s.connected for s in self._states.values())

    def get_state(self, symbol: Optional[str] = None) -> BinanceState:
        """
        Return a snapshot of aggregated state for the given symbol.

        Args:
            symbol: "btc", "eth", "sol", "xrp" or full "btcusdt". Default: btc.
        """
        sym = self._resolve_symbol(symbol)
        with self._lock:
            s = self._states.get(sym)
            if not s:
                return BinanceState()
            return _copy_state(s)

    def get_recent_ohlcv(
        self,
        symbol: Optional[str] = None,
        *,
        timeframe: str = "15m",
        limit: int = 128,
        cache_seconds: int = 30,
    ) -> pd.DataFrame:
        sym = self._resolve_symbol(symbol)
        sym_upper = sym.upper()
        interval_seconds = KLINE_INTERVAL_SECONDS.get(timeframe)
        if interval_seconds is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        cache_key = f"{sym_upper}:{timeframe}:{limit}"
        now = time.time()
        with self._lock:
            cached = self._ohlcv_cache.get(cache_key)
        if cached is not None:
            cached_frame, cached_ts = cached
            if now - cached_ts < cache_seconds:
                return cached_frame.copy()

        url = f"{BINANCE_REST_URL}/api/v3/klines?symbol={sym_upper}&interval={timeframe}&limit={limit + 1}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            payload = json.loads(resp.read().decode())

        rows = []
        for item in payload:
            if len(item) < 6:
                continue
            open_ts = float(item[0]) / 1000.0
            rows.append(
                {
                    "timestamp": pd.to_datetime(open_ts, unit="s", utc=True),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                }
            )

        frame = pd.DataFrame(rows)
        if not frame.empty:
            latest_open_ts = frame.iloc[-1]["timestamp"].timestamp()
            if now < (latest_open_ts + interval_seconds):
                frame = frame.iloc[:-1]
        frame = frame.tail(limit).reset_index(drop=True)

        with self._lock:
            self._ohlcv_cache[cache_key] = (frame.copy(), now)
        return frame

    def get_1h_candle_open(self, symbol: Optional[str] = None) -> Optional[float]:
        """
        Get the open price of the current 1h candle (Price to Beat for 1h Up/Down).

        Primary: WS-tracked open (set on first trade of each UTC hour, zero latency).
        Fallback: Binance REST klines (cached 60s). REST may be slow/blocked from some regions.
        Returns None only if both sources fail.
        """
        sym = self._resolve_symbol(symbol)
        sym_upper = sym.upper()
        now = time.time()
        # Current UTC hour bucket
        current_bucket = int(now // 3600) * 3600

        # 1. WS-tracked open (preferred — no network call)
        with self._lock:
            ws_entry = self._1h_candle.get(sym)
        if ws_entry is not None:
            bucket_ts, open_price = ws_entry
            if bucket_ts == current_bucket and open_price > 0:
                return open_price

        # 2. REST fallback (cached 60s)
        cache_key = f"1h_open_{sym_upper}"
        with self._lock:
            cached = self._1h_candle_cache.get(cache_key)
        if cached is not None:
            val, ts = cached
            if now - ts < 60:
                return val
        try:
            import urllib.request
            url = f"{BINANCE_REST_URL}/api/v3/klines?symbol={sym_upper}&interval=1h&limit=1"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            if data and len(data[0]) >= 2:
                open_price = float(data[0][1])
                with self._lock:
                    self._1h_candle_cache[cache_key] = (open_price, now)
                    # Also seed WS tracker if not set for this hour
                    if self._1h_candle.get(sym, (0,))[0] != current_bucket:
                        self._1h_candle[sym] = (current_bucket, open_price)
                return open_price
        except Exception as e:
            cprint(f"⚠️  Binance 1h kline REST failed ({sym_upper}): {e}", "yellow")
        return None

    def _resolve_symbol(self, symbol: Optional[str]) -> str:
        if not symbol:
            return self.symbols[0]
        s = symbol.lower().strip()
        if s in ASSET_TO_SYMBOL:
            sym = ASSET_TO_SYMBOL[s]
            return sym if sym in self._states else self.symbols[0]
        if s in self._states:
            return s
        return self.symbols[0]

    def start(self) -> None:
        """Start the background feed thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="binance-feed", daemon=True
        )
        self._thread.start()
        syms = ",".join(s.upper() for s in self.symbols)
        cprint(f"🟢 Binance feed started: {syms}", "green")

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
        with self._lock:
            for s in self._states.values():
                s.connected = False
        cprint("🔴 Binance feed stopped", "yellow")

    # ------------------------------------------------------------------
    # WebSocket callbacks
    # ------------------------------------------------------------------

    def _on_message(self, ws, message: str) -> None:
        try:
            data = json.loads(message)
            if self._multi:
                # Combined stream: {"stream": "btcusdt@trade", "data": {...}}
                stream = data.get("stream", "")
                payload = data.get("data", data)
                # Extract symbol from "btcusdt@trade"
                sym = stream.split("@")[0] if stream else None
                if not sym or sym not in self._states:
                    return
            else:
                sym = self.symbols[0]
                payload = data

            trade = BinanceTrade(
                price=float(payload["p"]),
                qty=float(payload["q"]),
                timestamp=payload["T"] / 1000.0,
                is_buyer_maker=payload["m"],
            )
            self._ingest_trade(sym, trade)
        except (KeyError, ValueError, TypeError) as exc:
            cprint(f"⚠️  Binance parse error: {exc}", "yellow")

    def _on_open(self, ws) -> None:
        with self._lock:
            for s in self._states.values():
                s.connected = True
        self._reconnect_delay = 5.0
        syms = ",".join(s.upper() for s in self.symbols)
        cprint(f"🔗 Binance WS connected: {syms}", "green")

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        with self._lock:
            for s in self._states.values():
                s.connected = False
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
                    self._ws_url,
                    on_message=self._on_message,
                    on_open=self._on_open,
                    on_close=self._on_close,
                    on_error=self._on_error,
                )
                self._ws.run_forever(
                    ping_interval=0,
                    skip_utf8_validation=True,
                )
            except Exception as exc:
                cprint(f"❌ Binance WS exception: {exc}", "red")

            if not self._running:
                break

            sleep_time = max(
                self._reconnect_delay, self._min_reconnect_interval
            )
            cprint(
                f"♻️  Binance reconnecting in {sleep_time:.0f}s...",
                "yellow",
            )
            time.sleep(sleep_time)
            self._reconnect_delay = min(
                self._reconnect_delay * 2, self._max_reconnect_delay
            )

    # ------------------------------------------------------------------
    # Trade ingestion & metric computation
    # ------------------------------------------------------------------

    def _ingest_trade(self, sym: str, trade: BinanceTrade) -> None:
        now = trade.timestamp
        trades = self._trades[sym]
        snaps = self._price_snapshots[sym]
        state = self._states[sym]

        with self._lock:
            # Track 1h candle open: record the first price of each UTC hour
            current_bucket = int(now // 3600) * 3600
            existing = self._1h_candle.get(sym)
            if existing is None or existing[0] != current_bucket:
                self._1h_candle[sym] = (current_bucket, trade.price)

            trades.append(trade)
            if not snaps or now - snaps[-1][0] >= 1.0:
                snaps.append((now, trade.price))

            cutoff = now - self.window_seconds
            while trades and trades[0].timestamp < cutoff:
                trades.popleft()
            while snaps and snaps[0][0] < cutoff:
                snaps.popleft()

            self._recompute(sym, trade.price, now)

        if self.on_state_update:
            try:
                self.on_state_update(sym, state)
            except Exception:
                pass

    def _recompute(self, sym: str, current_price: float, now: float) -> None:
        """Recompute rolling metrics for symbol. Caller must hold _lock."""
        trades = self._trades[sym]
        snaps = self._price_snapshots[sym]
        state = self._states[sym]

        state.last_price = current_price
        state.last_update = now
        state.trade_count_5m = len(trades)

        if not trades:
            return

        total_pq = sum(t.price * t.qty for t in trades)
        total_q = sum(t.qty for t in trades)
        state.vwap_5m = total_pq / total_q if total_q > 0 else current_price
        state.volume_5m = total_q

        buy_vol = sum(t.qty for t in trades if not t.is_buyer_maker)
        state.bid_pressure = buy_vol / total_q if total_q > 0 else 0.5

        for seconds, attr in [
            (10, "price_change_pct_10s"),
            (30, "price_change_pct_30s"),
            (60, "price_change_pct_60s"),
        ]:
            ref = self._price_at_seconds_ago(snaps, now, seconds)
            if ref and ref > 0:
                setattr(state, attr, ((current_price - ref) / ref) * 100)
            else:
                setattr(state, attr, 0.0)

        ref_10 = self._price_at_seconds_ago(snaps, now, 10)
        state.price_velocity = (current_price - ref_10) / 10.0 if ref_10 else 0.0

        if len(snaps) >= 10:
            returns: List[float] = []
            snap_list = list(snaps)
            for i in range(1, len(snap_list)):
                p0, p1 = snap_list[i - 1][1], snap_list[i][1]
                if p0 > 0:
                    returns.append(math.log(p1 / p0))
            if len(returns) >= 5:
                mean_r = sum(returns) / len(returns)
                var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
                state.volatility_5m = math.sqrt(var_r) * 5612
            else:
                state.volatility_5m = 0.0
        else:
            state.volatility_5m = 0.0

    @staticmethod
    def _price_at_seconds_ago(
        snaps: Deque[tuple], now: float, seconds: int
    ) -> Optional[float]:
        target = now - seconds
        best: Optional[tuple] = None
        for snap in snaps:
            if snap[0] <= target:
                best = snap
            else:
                break
        return best[1] if best else None
