"""
Backtesting module for Polymarket strategies.

Uses PolyBackTest API for historical Up/Down market data and snapshots.
"""

from .polybacktest_client import PolyBackTestClient
from .store import BacktestStore
from .downloader import DataDownloader
from .replay_feed import ReplayBinanceFeed
from .mappers import snapshot_to_market_data
from .engine import BacktestEngine

__all__ = [
    "PolyBackTestClient",
    "BacktestStore",
    "DataDownloader",
    "ReplayBinanceFeed",
    "snapshot_to_market_data",
    "BacktestEngine",
]
