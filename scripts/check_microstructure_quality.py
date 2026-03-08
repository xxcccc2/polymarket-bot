#!/usr/bin/env python3
"""
Inspect the Binance microstructure collector database for coverage and freshness.

Usage:
  python -m scripts.check_microstructure_quality --db-path data/ml/collectors/binance_microstructure.sqlite
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ML_BINANCE_COLLECTOR_DB
from src.logging_utils import cprint


TABLES = {
    "depth_snapshots": "event_time",
    "agg_trades": "event_time",
    "liquidations": "event_time",
    "funding_open_interest": "sampled_at",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check ML microstructure collector database quality")
    parser.add_argument("--db-path", default=str(ML_BINANCE_COLLECTOR_DB), help="SQLite collector DB")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        cprint(f"Collector DB not found: {db_path}", "red")
        return 1

    with sqlite3.connect(db_path) as conn:
        for table, ts_column in TABLES.items():
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            latest = conn.execute(f"SELECT MAX({ts_column}) FROM {table}").fetchone()[0]
            latest_str = "n/a"
            if latest:
                latest_str = datetime.fromtimestamp(float(latest), tz=timezone.utc).isoformat()
            cprint(f"{table:<22} rows={count:<10} latest={latest_str}", "white")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
