#!/usr/bin/env python3
"""
Download PolyBackTest data for backtesting.

Fetches markets and snapshots within free-plan limits and stores locally.

Usage:
  python -m scripts.download_polybacktest
  python -m scripts.download_polybacktest --coin btc --types 5m,15m
  python -m scripts.download_polybacktest --include-orderbook

Requires POLYBACKTEST_API_KEY in .env.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.downloader import DataDownloader
from src.backtest.polybacktest_client import PolyBackTestError
from src.config import POLYBACKTEST_API_KEY
from src.logging_utils import cprint


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download PolyBackTest markets and snapshots for backtesting"
    )
    parser.add_argument(
        "--coin",
        default="btc",
        choices=["btc", "eth"],
        help="Coin to fetch (default: btc)",
    )
    parser.add_argument(
        "--types",
        default="5m,15m,1hr,4hr,24hr",
        help="Comma-separated market types (default: all)",
    )
    parser.add_argument(
        "--include-orderbook",
        action="store_true",
        help="Include full orderbook in snapshots (larger payloads)",
    )
    args = parser.parse_args()

    if not POLYBACKTEST_API_KEY:
        cprint("ERROR: POLYBACKTEST_API_KEY not set in .env", "red")
        return 1

    types = [t.strip() for t in args.types.split(",") if t.strip()]
    if not types:
        cprint("ERROR: No market types specified", "red")
        return 1

    cprint("\nPolyBackTest Data Download", "cyan", attrs=["bold"])
    cprint(f"  Coin: {args.coin}", "white")
    cprint(f"  Types: {types}", "white")
    cprint(f"  Orderbook: {args.include_orderbook}", "white")

    try:
        downloader = DataDownloader()
        summary = downloader.download(
            coin=args.coin,
            market_types=types,
            include_orderbook=args.include_orderbook,
        )
    except PolyBackTestError as e:
        cprint(f"\nERROR: {e}", "red")
        return 1

    cprint("\n" + "=" * 50, "cyan")
    cprint("Download Complete", "green", attrs=["bold"])
    cprint(f"  Markets: {summary.get('markets_downloaded', 0)}", "white")
    cprint(f"  Snapshots: {summary.get('snapshots_downloaded', 0)}", "white")
    if summary.get("warnings"):
        for w in summary["warnings"]:
            cprint(f"  Warning: {w}", "yellow")
    if summary.get("errors"):
        for e in summary["errors"]:
            cprint(f"  Error: {e}", "red")
    cprint("=" * 50 + "\n", "cyan")

    return 0


if __name__ == "__main__":
    sys.exit(main())
