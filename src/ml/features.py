"""
Feature engineering for the ML directional strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Iterable, List, Mapping, Optional

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from ..strategies.base_strategy import MarketData


DEFAULT_RUNTIME_FEATURE_COLUMNS = [
    "market_mid_price",
    "market_spread",
    "market_volume_24h",
    "market_liquidity",
    "market_orderbook_imbalance",
    "market_time_to_expiry_s",
    "market_time_to_expiry_m",
    "binance_last_price",
    "binance_vwap_5m",
    "binance_volatility_5m",
    "binance_price_change_pct_10s",
    "binance_price_change_pct_30s",
    "binance_price_change_pct_60s",
    "binance_price_velocity",
    "binance_bid_pressure",
]


def compute_orderbook_imbalance(orderbook: Optional[Mapping]) -> float:
    """
    Compute a normalized order book imbalance in `[-1, 1]`.

    Returns 0 when order book data is unavailable.
    """
    if not orderbook:
        return 0.0

    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []
    bid_vol = _sum_orderbook_levels(bids)
    ask_vol = _sum_orderbook_levels(asks)
    denom = bid_vol + ask_vol
    if denom <= 0:
        return 0.0
    return float((bid_vol - ask_vol) / denom)


def align_feature_row(row: Mapping[str, float], feature_columns: Iterable[str]) -> pd.DataFrame:
    """
    Align a feature row to a model schema, filling missing values with 0.
    """
    payload = {column: float(row.get(column, 0.0) or 0.0) for column in feature_columns}
    return pd.DataFrame([payload], columns=list(feature_columns))


def _normalize_timestamp_column(frame: pd.DataFrame, column: str = "timestamp") -> pd.DataFrame:
    normalized = frame.copy()
    normalized[column] = pd.to_datetime(normalized[column], utc=True).astype("datetime64[ns, UTC]")
    return normalized


@dataclass
class FeatureBuilder:
    """Build both offline OHLCV features and runtime/live feature rows."""

    atr_window: int = 14
    momentum_windows: tuple[int, ...] = (1, 3, 6)

    def build_training_schema_runtime_row(
        self,
        frames: Dict[str, pd.DataFrame],
        *,
        target_timeframe: str = "15m",
        microstructure_frame: Optional[pd.DataFrame] = None,
    ) -> Dict[str, float]:
        runtime_frame = self.build_ohlcv_feature_frame(
            frames,
            target_timeframe=target_timeframe,
            include_target=False,
            microstructure_frame=microstructure_frame,
        )
        if runtime_frame.empty:
            return {}
        latest = runtime_frame.iloc[-1]
        return {
            column: float(latest[column])
            for column in runtime_frame.columns
            if column not in {"timestamp", "as_of_ts", "available_ts", "micro_available_ts"}
            and pd.notna(latest[column])
        }

    def build_ohlcv_feature_frame(
        self,
        frames: Dict[str, pd.DataFrame],
        target_timeframe: str = "15m",
        include_target: bool = True,
        microstructure_frame: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        """
        Build a leakage-aware training frame from normalized OHLCV data.

        The target timeframe acts as the decision clock. Higher timeframes are
        shifted by one bar before `merge_asof` so the row only sees information
        that would have been available at decision time.
        """
        if target_timeframe not in frames:
            available = ", ".join(sorted(frames))
            raise KeyError(f"Target timeframe '{target_timeframe}' not found. Available: {available}")

        base = _normalize_timestamp_column(frames[target_timeframe]).sort_values("timestamp").reset_index(drop=True)
        base = self._add_candle_features(base, prefix=target_timeframe)
        base["as_of_ts"] = base["timestamp"]
        base["available_ts"] = base["timestamp"]
        base["hour_sin"] = np.sin((base["timestamp"].dt.hour / 24.0) * 2 * np.pi)
        base["hour_cos"] = np.cos((base["timestamp"].dt.hour / 24.0) * 2 * np.pi)
        base["weekday_sin"] = np.sin((base["timestamp"].dt.dayofweek / 7.0) * 2 * np.pi)
        base["weekday_cos"] = np.cos((base["timestamp"].dt.dayofweek / 7.0) * 2 * np.pi)

        merged = base
        for timeframe, frame in frames.items():
            if timeframe == target_timeframe:
                continue
            enriched = self._add_candle_features(_normalize_timestamp_column(frame), prefix=timeframe)
            enriched = enriched.sort_values("timestamp").reset_index(drop=True)
            enriched["timestamp"] = enriched["timestamp"].shift(1)
            keep = ["timestamp"] + [col for col in enriched.columns if col.startswith(f"{timeframe}_")]
            enriched = enriched[keep].dropna(subset=["timestamp"])
            merged = pd.merge_asof(
                merged.sort_values("timestamp"),
                enriched.sort_values("timestamp"),
                on="timestamp",
                direction="backward",
            )

        if microstructure_frame is not None and not microstructure_frame.empty:
            micro = _normalize_timestamp_column(microstructure_frame).sort_values("timestamp").reset_index(drop=True)
            if "available_ts" in micro.columns:
                micro = micro.rename(columns={"available_ts": "micro_available_ts"})
            merge_columns = ["timestamp"] + [
                col for col in micro.columns if col not in {"timestamp"}
            ]
            merged = pd.merge_asof(
                merged.sort_values("timestamp"),
                micro[merge_columns].sort_values("timestamp"),
                on="timestamp",
                direction="backward",
            )

        if include_target:
            future_open = base["open"].shift(-1)
            future_close = base["close"].shift(-1)
            merged["target_up"] = (future_close > future_open).astype(float)
            merged["target_return"] = (future_close / future_open) - 1.0

        merged = merged.replace([np.inf, -np.inf], np.nan)
        if include_target:
            merged = merged.dropna(subset=["target_up", "target_return"])
        numeric_columns = merged.select_dtypes(include=[np.number]).columns
        merged[numeric_columns] = merged[numeric_columns].fillna(0.0)
        merged = merged.reset_index(drop=True)
        return merged

    def build_runtime_feature_row(
        self,
        market_data: "MarketData",
        binance_state,
        now_ts: Optional[float] = None,
    ) -> Dict[str, float]:
        """
        Build a single live inference row using runtime-available inputs.
        """
        orderbook_imbalance = compute_orderbook_imbalance(market_data.orderbook)
        timestamp = now_ts
        if timestamp is None:
            if hasattr(market_data.timestamp, "timestamp"):
                timestamp = market_data.timestamp.timestamp()
            else:
                timestamp = float(market_data.timestamp or 0.0)

        time_to_expiry = 0.0
        if market_data.end_date_ts:
            time_to_expiry = max(float(market_data.end_date_ts) - float(timestamp or 0.0), 0.0)

        row = {
            "market_mid_price": float(market_data.mid_price),
            "market_spread": float(market_data.spread),
            "market_volume_24h": float(market_data.volume_24h),
            "market_liquidity": float(market_data.liquidity),
            "market_orderbook_imbalance": float(orderbook_imbalance),
            "market_time_to_expiry_s": float(time_to_expiry),
            "market_time_to_expiry_m": float(time_to_expiry / 60.0),
            "binance_last_price": float(getattr(binance_state, "last_price", 0.0) or 0.0),
            "binance_vwap_5m": float(getattr(binance_state, "vwap_5m", 0.0) or 0.0),
            "binance_volatility_5m": float(getattr(binance_state, "volatility_5m", 0.0) or 0.0),
            "binance_price_change_pct_10s": float(getattr(binance_state, "price_change_pct_10s", 0.0) or 0.0),
            "binance_price_change_pct_30s": float(getattr(binance_state, "price_change_pct_30s", 0.0) or 0.0),
            "binance_price_change_pct_60s": float(getattr(binance_state, "price_change_pct_60s", 0.0) or 0.0),
            "binance_price_velocity": float(getattr(binance_state, "price_velocity", 0.0) or 0.0),
            "binance_bid_pressure": float(getattr(binance_state, "bid_pressure", 0.5) or 0.5),
        }
        row["binance_price_vs_vwap_5m"] = row["binance_last_price"] - row["binance_vwap_5m"]
        row["market_spread_bps"] = (row["market_spread"] / max(row["market_mid_price"], 1e-8)) * 10000.0
        return row

    def _add_candle_features(self, frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
        result = frame.copy()
        result["return_1"] = result["close"].pct_change()
        result["log_return_1"] = np.log(result["close"] / result["close"].shift(1))
        result["body"] = result["close"] - result["open"]
        result["range"] = result["high"] - result["low"]
        result["body_ratio"] = result["body"] / result["open"].replace(0, np.nan)
        result["wick_ratio"] = result["range"] / result["open"].replace(0, np.nan)
        result["close_to_range"] = (result["close"] - result["low"]) / result["range"].replace(0, np.nan)
        result["atr"] = _average_true_range(result, self.atr_window)
        result["atr_pct"] = result["atr"] / result["close"].replace(0, np.nan)
        for window in self.momentum_windows:
            result[f"momentum_{window}"] = result["close"].pct_change(window)
            result[f"volatility_{window}"] = result["return_1"].rolling(window).std()

        rename_map = {
            col: f"{prefix}_{col}"
            for col in result.columns
            if col not in {"timestamp", "open", "high", "low", "close", "volume"}
        }
        return result.rename(columns=rename_map)


def _average_true_range(frame: pd.DataFrame, window: int) -> pd.Series:
    prev_close = frame["close"].shift(1)
    tr_components = pd.concat(
        [
            (frame["high"] - frame["low"]).abs(),
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    true_range = tr_components.max(axis=1)
    return true_range.rolling(window).mean()


def _sum_orderbook_levels(levels: List) -> float:
    total = 0.0
    for level in levels:
        if isinstance(level, Mapping):
            size = level.get("size") or level.get("quantity") or level.get("qty") or 0
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            size = level[1]
        else:
            size = 0
        try:
            total += float(size)
        except (TypeError, ValueError):
            continue
    return total
