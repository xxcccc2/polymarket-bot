#!/usr/bin/env python3
"""
Run backtest on downloaded PolyBackTest data.

Usage:
  python -m scripts.run_backtest --strategy terminal_convergence --market-type 5m
  python -m scripts.run_backtest --strategy terminal_convergence --limit 10

Requires data from download_polybacktest.py first.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import BacktestEngine, BacktestResult
from src.backtest.store import BacktestStore
from src.logging_utils import cprint


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run backtest on downloaded PolyBackTest data"
    )
    parser.add_argument(
        "--strategy",
        default="terminal_convergence",
        help="Strategy to run (default: terminal_convergence)",
    )
    parser.add_argument(
        "--market-type",
        default=None,
        choices=["5m", "15m", "1hr", "4hr", "24hr"],
        help="Filter by market type",
    )
    parser.add_argument(
        "--coin",
        default="btc",
        choices=["btc", "eth"],
        help="Coin (default: btc)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max markets to run (for quick tests)",
    )
    parser.add_argument(
        "--step-interval",
        type=int,
        default=5,
        help="Step every N snapshots (simulate scan interval, default: 5)",
    )
    args = parser.parse_args()

    store = BacktestStore()
    market_count = store.get_market_count(args.market_type)
    if market_count == 0:
        cprint(
            f"ERROR: No markets found for type={args.market_type or 'all'}. Run download_polybacktest first.",
            "red",
        )
        return 1

    cprint("\nBacktest Run", "cyan", attrs=["bold"])
    cprint(f"  Strategy: {args.strategy}", "white")
    cprint(f"  Market type: {args.market_type or 'all'}", "white")
    cprint(f"  Coin: {args.coin}", "white")
    cprint(f"  Markets in store: {market_count}", "white")

    engine = BacktestEngine(step_interval=args.step_interval)

    try:
        result = engine.run(
            strategy_name=args.strategy,
            market_type=args.market_type,
            coin=args.coin,
            limit=args.limit,
        )
    except Exception as e:
        cprint(f"\nERROR: {e}", "red")
        return 1

    _print_result(result)
    return 0


def _print_result(result: BacktestResult) -> None:
    cprint("\n" + "=" * 50, "cyan")
    cprint("Backtest Results", "green", attrs=["bold"])
    cprint(f"  Markets run: {result.markets_run}", "white")
    cprint(f"  Markets skipped: {result.markets_skipped}", "white")
    cprint(f"  Trades: {len(result.trades)}", "white")
    cprint(f"  Wins: {result.win_count}", "green")
    cprint(f"  Losses: {result.loss_count}", "red")
    cprint(f"  Total PnL: ${result.total_pnl:.2f}", "green" if result.total_pnl >= 0 else "red")
    if result.win_count + result.loss_count > 0:
        wr = result.win_count / (result.win_count + result.loss_count) * 100
        cprint(f"  Win rate: {wr:.1f}%", "white")
    cprint("=" * 50 + "\n", "cyan")


if __name__ == "__main__":
    sys.exit(main())
