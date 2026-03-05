"""
Replay Binance-like feed for backtesting.

Builds BinanceState from historical snapshot btc_price data.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import List, Optional, Tuple

from ..feeds.binance_ws import BinanceState


def _parse_time(t: str) -> float:
    """Parse ISO8601 or timestamp to Unix seconds."""
    if isinstance(t, (int, float)):
        if t > 1e12:
            return t / 1000
        return float(t)
    if isinstance(t, str):
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            pass
    return 0.0


class ReplayBinanceFeed:
    """
    Binance-like feed built from historical snapshots.

    Implements get_state(asset) returning BinanceState computed from
    snapshot btc_price series. Used for backtesting TerminalConvergence
    and similar strategies.
    """

    def __init__(self, snapshots: List[dict]):
        """
        Args:
            snapshots: List of snapshot dicts with 'time' and 'btc_price'.
                       Sorted by time ascending.
        """
        self._series: List[Tuple[float, float]] = []
        for s in snapshots:
            ts = _parse_time(s.get("time", 0))
            price = float(s.get("btc_price") or 0)
            if ts > 0 and price > 0:
                self._series.append((ts, price))
        self._series.sort(key=lambda x: x[0])
        self._current_idx = 0

    def seek(self, timestamp: float) -> None:
        """
        Seek to the snapshot at or just before the given timestamp.

        Args:
            timestamp: Unix seconds
        """
        idx = 0
        for i, (ts, _) in enumerate(self._series):
            if ts <= timestamp:
                idx = i
            else:
                break
        self._current_idx = idx

    def set_current_idx(self, idx: int) -> None:
        """Set current position by index (for stepping through snapshots)."""
        self._current_idx = max(0, min(idx, len(self._series) - 1))

    def __len__(self) -> int:
        return len(self._series)

    def get_state(self, asset: str = "btc") -> BinanceState:
        """
        Get BinanceState at current replay position.

        Computes price_change_pct_10s/30s/60s and volatility_5m from
        prior snapshots. bid_pressure defaults to 0.5 (no trade flow).
        """
        if not self._series or self._current_idx < 0:
            return BinanceState(connected=False)

        now_ts, now_price = self._series[self._current_idx]
        prices = [(ts, p) for ts, p in self._series if ts <= now_ts]

        # Price changes
        def price_at(seconds_ago: float) -> Optional[float]:
            target = now_ts - seconds_ago
            for i in range(len(prices) - 1, -1, -1):
                if prices[i][0] <= target:
                    return prices[i][1]
            return None

        p10 = price_at(10)
        p30 = price_at(30)
        p60 = price_at(60)

        def pct_change(p_old: Optional[float], p_now: float) -> float:
            if p_old is None or p_old <= 0:
                return 0.0
            return (p_now - p_old) / p_old * 100

        change_10s = pct_change(p10, now_price)
        change_30s = pct_change(p30, now_price)
        change_60s = pct_change(p60, now_price)

        # Volatility: std of returns over last 5 min, annualized
        window_5m = [(ts, p) for ts, p in prices if ts >= now_ts - 300]
        if len(window_5m) >= 2:
            returns = []
            for i in range(1, len(window_5m)):
                p_prev = window_5m[i - 1][1]
                p_curr = window_5m[i][1]
                if p_prev > 0:
                    returns.append((p_curr - p_prev) / p_prev)
            if returns:
                mean_r = sum(returns) / len(returns)
                var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
                vol_5m = math.sqrt(var) if var > 0 else 0.0
                # Annualize: ~105120 periods of 5 min per year
                vol_annual = vol_5m * math.sqrt(105120) * 100
            else:
                vol_annual = 0.0
        else:
            vol_annual = 0.0

        # VWAP over 5m (simplified: avg of prices in window)
        if window_5m:
            vwap = sum(p for _, p in window_5m) / len(window_5m)
            vol_sum = sum(p for _, p in window_5m)
        else:
            vwap = now_price
            vol_sum = 0.0

        return BinanceState(
            last_price=now_price,
            vwap_5m=vwap,
            volume_5m=vol_sum,
            volatility_5m=vol_annual,
            price_change_pct_10s=change_10s,
            price_change_pct_30s=change_30s,
            price_change_pct_60s=change_60s,
            price_velocity=change_10s / 10.0 if p10 else 0.0,
            bid_pressure=0.5,
            last_update=now_ts,
            connected=True,
            trade_count_5m=len(window_5m),
        )

    @property
    def connected(self) -> bool:
        return bool(self._series) and self._current_idx >= 0

    @property
    def current_timestamp(self) -> float:
        if self._series and 0 <= self._current_idx < len(self._series):
            return self._series[self._current_idx][0]
        return 0.0
