#!/usr/bin/env python3
"""
Collect Binance microstructure events into the ML collector SQLite database.

Usage:
  python -m scripts.collect_binance_microstructure --symbol btcusdt
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ML_BINANCE_COLLECTOR_DB, ML_COLLECTOR_DEPTH_LEVELS, ML_COLLECTOR_REST_POLL_SECONDS
from src.logging_utils import cprint
from src.ml.collectors.binance_depth import BinanceMicrostructureCollector


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Binance microstructure data for ML training")
    parser.add_argument("--symbol", default="btcusdt", help="Binance symbol, default: btcusdt")
    parser.add_argument("--db-path", default=str(ML_BINANCE_COLLECTOR_DB), help="SQLite output path")
    parser.add_argument("--depth-levels", type=int, default=ML_COLLECTOR_DEPTH_LEVELS, help="Depth stream levels")
    parser.add_argument(
        "--rest-poll-seconds",
        type=int,
        default=ML_COLLECTOR_REST_POLL_SECONDS,
        help="Funding/OI polling interval",
    )
    args = parser.parse_args()

    collector = BinanceMicrostructureCollector(
        symbol=args.symbol,
        db_path=args.db_path,
        depth_levels=args.depth_levels,
        rest_poll_seconds=args.rest_poll_seconds,
    )

    stop_requested = False

    def _request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    collector.start()
    cprint(f"Writing collector data to {args.db_path}", "cyan")
    cprint("Press Ctrl+C to stop", "white")
    try:
        while not stop_requested:
            time.sleep(1)
    finally:
        collector.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
