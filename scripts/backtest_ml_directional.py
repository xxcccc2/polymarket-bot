#!/usr/bin/env python3
"""
Run the dedicated ML directional replay backtest.

Usage:
  python -m scripts.backtest_ml_directional --market-type 15m
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ML_DIRECTIONAL_MODEL_PATH
from src.logging_utils import cprint
from src.ml.backtest import MLBacktestEngine


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the ML directional replay backtest")
    parser.add_argument("--model-path", default=str(ML_DIRECTIONAL_MODEL_PATH), help="Model artifact path")
    parser.add_argument("--market-type", default=None, choices=["5m", "15m", "1hr", "4hr", "24hr"], help="Filter market type")
    parser.add_argument("--coin", default="btc", choices=["btc", "eth"], help="Coin symbol")
    parser.add_argument("--limit", type=int, default=None, help="Limit markets for quick tests")
    args = parser.parse_args()

    engine = MLBacktestEngine()
    result = engine.run(
        model_path=args.model_path,
        market_type=args.market_type,
        coin=args.coin,
        limit=args.limit,
    )

    cprint("\nML replay backtest", "cyan", attrs=["bold"])
    cprint(
        json.dumps(
            {
                "trade_count": result.trade_count,
                "total_pnl": result.total_pnl,
                "win_rate": result.win_rate,
                "avg_edge": result.avg_edge,
                "avg_fill_fraction": result.avg_fill_fraction,
                "net_ev_per_trade": result.net_ev_per_trade,
                "profit_factor": result.profit_factor,
                "max_drawdown": result.max_drawdown,
            },
            indent=2,
        ),
        "white",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
