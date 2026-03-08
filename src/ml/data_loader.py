"""
Utilities for loading BTC OHLCV datasets used by the ML strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd


_TIMESTAMP_CANDIDATES = (
    "timestamp",
    "time",
    "date",
    "datetime",
    "open_time",
    "Open time",
)
_COLUMN_ALIASES = {
    "open": ("open", "Open"),
    "high": ("high", "High"),
    "low": ("low", "Low"),
    "close": ("close", "Close"),
    "volume": ("volume", "Volume"),
}


@dataclass
class OhlcvDataset:
    """Normalized OHLCV frames keyed by timeframe."""

    asset: str
    frames: Dict[str, pd.DataFrame]

    def get(self, timeframe: str) -> pd.DataFrame:
        """Return a timeframe frame or raise a helpful error."""
        if timeframe not in self.frames:
            available = ", ".join(sorted(self.frames))
            raise KeyError(f"Missing timeframe '{timeframe}'. Available: {available}")
        return self.frames[timeframe]


@dataclass
class MicrostructureDataset:
    """Time-safe microstructure features derived from the collector SQLite DB."""

    symbol: str
    frame: pd.DataFrame


class OhlcvLoader:
    """Load and normalize BTC OHLCV CSVs from the repo data directory."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)

    def load_asset(self, asset: str = "btc", timeframes: Optional[Iterable[str]] = None) -> OhlcvDataset:
        """
        Load normalized OHLCV frames for one asset.

        File names are expected to contain the timeframe, for example:
        `btc_15m_data_2018_to_2026.csv`.
        """
        asset_dir = self.base_dir / asset.lower()
        if not asset_dir.exists():
            raise FileNotFoundError(f"OHLCV asset directory not found: {asset_dir}")

        requested = {tf.lower() for tf in timeframes} if timeframes else None
        frames: Dict[str, pd.DataFrame] = {}
        for path in sorted(asset_dir.glob("*.csv")):
            timeframe = self._infer_timeframe(path.name)
            if not timeframe:
                continue
            if requested and timeframe not in requested:
                continue
            frames[timeframe] = self.load_csv(path)

        if not frames:
            raise FileNotFoundError(f"No OHLCV CSVs found in {asset_dir}")
        return OhlcvDataset(asset=asset.lower(), frames=frames)

    def load_csv(self, path: str | Path) -> pd.DataFrame:
        """Load and normalize a single OHLCV CSV."""
        frame = pd.read_csv(path)
        return normalize_ohlcv_frame(frame)

    @staticmethod
    def _infer_timeframe(filename: str) -> Optional[str]:
        lowered = filename.lower()
        for timeframe in ("1m", "5m", "15m", "1h", "4h", "1d"):
            if timeframe in lowered:
                return timeframe
        return None


def normalize_ohlcv_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize a raw OHLCV CSV into a canonical schema.

    Output columns:
    - `timestamp` (`datetime64[ns, UTC]`)
    - `open`, `high`, `low`, `close`, `volume`
    """
    normalized = frame.copy()

    ts_col = _pick_column(normalized.columns, _TIMESTAMP_CANDIDATES)
    if ts_col is None:
        raise ValueError("Could not find a timestamp column in OHLCV dataset")

    normalized = normalized.rename(columns={ts_col: "timestamp"})
    normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], utc=True)

    rename_map = {}
    for target, aliases in _COLUMN_ALIASES.items():
        source = _pick_column(normalized.columns, aliases)
        if source is None:
            raise ValueError(f"Could not find OHLCV column for '{target}'")
        rename_map[source] = target
    normalized = normalized.rename(columns=rename_map)

    keep = ["timestamp", "open", "high", "low", "close", "volume"]
    normalized = normalized[keep].copy()
    for column in keep[1:]:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    normalized = normalized.dropna().sort_values("timestamp").reset_index(drop=True)
    normalized = normalized.drop_duplicates(subset=["timestamp"], keep="last")
    return normalized


def _pick_column(columns: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    lowered = {str(col).lower(): col for col in columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


class MicrostructureLoader:
    """
    Load collector SQLite data and aggregate it into training-ready features.

    The output frame uses:
    - `timestamp`: bucket end time
    - `available_ts`: same as timestamp (all features are backward-looking)
    - microstructure feature columns prefixed with `micro_`
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def load_symbol(
        self,
        symbol: str = "btcusdt",
        *,
        bucket: str = "1min",
    ) -> MicrostructureDataset:
        if not self.db_path.exists():
            raise FileNotFoundError(f"Microstructure DB not found: {self.db_path}")

        with sqlite3.connect(self.db_path) as conn:
            depth = pd.read_sql_query(
                "SELECT event_time, bids_json, asks_json FROM depth_snapshots WHERE symbol = ? ORDER BY event_time ASC",
                conn,
                params=[symbol.lower()],
            )
            trades = pd.read_sql_query(
                "SELECT event_time, price, quantity, is_buyer_maker FROM agg_trades WHERE symbol = ? ORDER BY event_time ASC",
                conn,
                params=[symbol.lower()],
            )
            liquidations = pd.read_sql_query(
                "SELECT event_time, side, quantity FROM liquidations WHERE symbol = ? ORDER BY event_time ASC",
                conn,
                params=[symbol.lower()],
            )
            funding_oi = pd.read_sql_query(
                "SELECT sampled_at, funding_rate, open_interest FROM funding_open_interest WHERE symbol = ? ORDER BY sampled_at ASC",
                conn,
                params=[symbol.lower()],
            )

        frame = self._aggregate(
            depth=depth,
            trades=trades,
            liquidations=liquidations,
            funding_oi=funding_oi,
            bucket=bucket,
        )
        return MicrostructureDataset(symbol=symbol.lower(), frame=frame)

    def _aggregate(
        self,
        *,
        depth: pd.DataFrame,
        trades: pd.DataFrame,
        liquidations: pd.DataFrame,
        funding_oi: pd.DataFrame,
        bucket: str,
    ) -> pd.DataFrame:
        base_index = None
        frames: list[pd.DataFrame] = []

        if not depth.empty:
            depth = depth.copy()
            depth["timestamp"] = pd.to_datetime(depth["event_time"], unit="s", utc=True)
            depth = depth.set_index("timestamp")
            depth_features = depth.apply(self._depth_row_to_features, axis=1, result_type="expand")
            depth_features = depth_features.resample(bucket).last().ffill()
            frames.append(depth_features)
            base_index = depth_features.index

        if not trades.empty:
            trades = trades.copy()
            trades["timestamp"] = pd.to_datetime(trades["event_time"], unit="s", utc=True)
            trades = trades.set_index("timestamp")
            trades["signed_qty"] = np.where(
                trades["is_buyer_maker"].astype(int) == 1,
                -trades["quantity"].astype(float),
                trades["quantity"].astype(float),
            )
            trade_features = pd.DataFrame(index=trades.index)
            trade_features["micro_trade_qty"] = trades["quantity"].astype(float)
            trade_features["micro_trade_signed_qty"] = trades["signed_qty"].astype(float)
            trade_features["micro_trade_notional"] = trades["quantity"].astype(float) * trades["price"].astype(float)
            trade_features = trade_features.resample(bucket).sum().fillna(0.0)
            trade_features["micro_cvd_1m"] = trade_features["micro_trade_signed_qty"]
            trade_features["micro_cvd_5m"] = trade_features["micro_trade_signed_qty"].rolling(5, min_periods=1).sum()
            trade_features["micro_cvd_15m"] = trade_features["micro_trade_signed_qty"].rolling(15, min_periods=1).sum()
            frames.append(trade_features)
            base_index = trade_features.index if base_index is None else base_index.union(trade_features.index)

        if not liquidations.empty:
            liquidations = liquidations.copy()
            liquidations["timestamp"] = pd.to_datetime(liquidations["event_time"], unit="s", utc=True)
            liquidations = liquidations.set_index("timestamp")
            liquidations["buy_qty"] = np.where(
                liquidations["side"].astype(str).str.upper() == "BUY",
                liquidations["quantity"].astype(float),
                0.0,
            )
            liquidations["sell_qty"] = np.where(
                liquidations["side"].astype(str).str.upper() == "SELL",
                liquidations["quantity"].astype(float),
                0.0,
            )
            liq_features = liquidations[["buy_qty", "sell_qty"]].resample(bucket).sum().fillna(0.0)
            liq_features = liq_features.rename(
                columns={
                    "buy_qty": "micro_liq_buy_1m",
                    "sell_qty": "micro_liq_sell_1m",
                }
            )
            liq_features["micro_liq_delta_1m"] = liq_features["micro_liq_buy_1m"] - liq_features["micro_liq_sell_1m"]
            liq_features["micro_liq_delta_5m"] = liq_features["micro_liq_delta_1m"].rolling(5, min_periods=1).sum()
            frames.append(liq_features)
            base_index = liq_features.index if base_index is None else base_index.union(liq_features.index)

        if not funding_oi.empty:
            funding_oi = funding_oi.copy()
            funding_oi["timestamp"] = pd.to_datetime(funding_oi["sampled_at"], unit="s", utc=True)
            funding_oi = funding_oi.set_index("timestamp")
            funding_oi = funding_oi.rename(
                columns={
                    "funding_rate": "micro_funding_rate",
                    "open_interest": "micro_open_interest",
                }
            )
            funding_features = funding_oi[["micro_funding_rate", "micro_open_interest"]].resample(bucket).last().ffill()
            funding_features["micro_open_interest_delta"] = funding_features["micro_open_interest"].diff().fillna(0.0)
            frames.append(funding_features)
            base_index = funding_features.index if base_index is None else base_index.union(funding_features.index)

        if base_index is None:
            return pd.DataFrame(columns=["timestamp", "available_ts"])

        merged = pd.DataFrame(index=base_index.sort_values())
        for frame in frames:
            merged = merged.join(frame, how="left")

        merged = merged.sort_index().ffill().fillna(0.0).reset_index().rename(columns={"index": "timestamp"})
        merged["available_ts"] = merged["timestamp"]
        return merged

    @staticmethod
    def _depth_row_to_features(row: pd.Series) -> Dict[str, float]:
        bids = json.loads(row["bids_json"]) if row.get("bids_json") else []
        asks = json.loads(row["asks_json"]) if row.get("asks_json") else []
        features = {}
        for levels in (1, 5, 10):
            bid_vol = _sum_levels(bids[:levels])
            ask_vol = _sum_levels(asks[:levels])
            denom = bid_vol + ask_vol
            imbalance = (bid_vol - ask_vol) / denom if denom > 0 else 0.0
            features[f"micro_obi_l{levels}"] = float(imbalance)
            features[f"micro_bid_depth_l{levels}"] = float(bid_vol)
            features[f"micro_ask_depth_l{levels}"] = float(ask_vol)
        return features


def _sum_levels(levels) -> float:
    total = 0.0
    for level in levels:
        if isinstance(level, (list, tuple)) and len(level) >= 2:
            size = level[1]
        elif isinstance(level, dict):
            size = level.get("size") or level.get("quantity") or level.get("qty") or 0
        else:
            size = 0
        try:
            total += float(size)
        except (TypeError, ValueError):
            continue
    return total
