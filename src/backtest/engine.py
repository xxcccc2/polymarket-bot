"""
Backtest engine for replaying historical data through strategies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import ENABLE_BTC_5MIN, TERMINAL_CONVERGENCE_WINDOW_SECONDS
from ..logging_utils import cprint
from ..strategies.terminal_convergence_strategy import TerminalConvergenceStrategy

from .mappers import _parse_end_ts, snapshots_to_market_data_list
from .replay_feed import ReplayBinanceFeed, _parse_time
from .store import BacktestStore


@dataclass
class BacktestTrade:
    """Simulated trade from backtest."""
    market_id: str
    token_id: str
    outcome: str
    side: str
    price: float
    size: float
    timestamp: float
    winner: Optional[str]
    pnl: float
    resolved: bool


@dataclass
class BacktestResult:
    """Result of a backtest run."""
    trades: List[BacktestTrade] = field(default_factory=list)
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    markets_run: int = 0
    markets_skipped: int = 0


class BacktestEngine:
    """
    Replay historical snapshots through strategies and compute PnL.
    """

    def __init__(
        self,
        store: Optional[BacktestStore] = None,
        convergence_window_s: Optional[int] = None,
        step_interval: int = 1,
    ):
        self.store = store or BacktestStore()
        self.convergence_window_s = (
            convergence_window_s or TERMINAL_CONVERGENCE_WINDOW_SECONDS
        )
        self.step_interval = max(1, step_interval)

    def run(
        self,
        strategy_name: str = "terminal_convergence",
        market_type: Optional[str] = None,
        coin: str = "btc",
        limit: Optional[int] = None,
    ) -> BacktestResult:
        """
        Run backtest for the given strategy.

        Args:
            strategy_name: Strategy to run (currently terminal_convergence)
            market_type: Filter markets (5m, 15m, 1hr, 4hr, 24hr)
            coin: btc or eth
            limit: Max markets to run (for quick tests)

        Returns:
            BacktestResult with trades and PnL
        """
        if strategy_name != "terminal_convergence":
            raise ValueError(f"Unsupported strategy: {strategy_name}")

        if not ENABLE_BTC_5MIN:
            cprint("  ENABLE_BTC_5MIN is False, enabling for backtest", "yellow")

        markets = self.store.load_markets(
            market_type=market_type,
            coin=coin,
            limit=limit,
        )

        result = BacktestResult()

        for m in markets:
            winner = m.get("winner")
            if not winner:
                result.markets_skipped += 1
                continue

            market_id = str(m.get("market_id", ""))
            snapshots = self.store.load_snapshots(market_id)
            if not snapshots:
                result.markets_skipped += 1
                continue

            end_ts = _parse_end_ts(m)
            if not end_ts:
                result.markets_skipped += 1
                continue

            # Indices of snapshots in convergence window
            window_indices = [
                j for j, s in enumerate(snapshots)
                if 0 <= end_ts - _parse_time(s.get("time", 0)) <= self.convergence_window_s
            ]
            if not window_indices:
                result.markets_skipped += 1
                continue

            result.markets_run += 1

            replay_feed = ReplayBinanceFeed(snapshots)
            strategy_config: Dict[str, Any] = {"binance_feed": replay_feed}
            if market_type in ("5m", "15m"):
                strategy_config["1h_only"] = False
            strategy = TerminalConvergenceStrategy(config=strategy_config)

            filled_token_ids: set = set()
            position: Dict[str, Dict[str, Any]] = {}  # token_id -> {size, price}

            for step in range(0, len(window_indices), self.step_interval):
                j = window_indices[step]
                snap = snapshots[j]
                replay_feed.set_current_idx(j)

                market_data_list = snapshots_to_market_data_list(snap, m)
                if not market_data_list:
                    continue

                signals = strategy.analyze(market_data_list)

                for sig in signals:
                    if sig.signal_type.value != "buy":
                        continue
                    if sig.token_id in filled_token_ids:
                        continue
                    filled_token_ids.add(sig.token_id)

                    outcome = (
                        "Up"
                        if str(sig.token_id) == str(m.get("clob_token_up", ""))
                        else "Down"
                    )
                    size = sig.size
                    price = sig.price

                    position[sig.token_id] = {
                        "size": size,
                        "price": price,
                        "outcome": outcome,
                        "timestamp": _parse_time(snap.get("time")),
                    }

            # Resolve positions
            for token_id, pos in position.items():
                outcome = pos["outcome"]
                size = pos["size"]
                price = pos["price"]
                ts = pos["timestamp"]

                payout = 1.0 if outcome == winner else 0.0
                pnl = size * (payout - price)

                result.trades.append(
                    BacktestTrade(
                        market_id=market_id,
                        token_id=token_id,
                        outcome=outcome,
                        side="BUY",
                        price=price,
                        size=size,
                        timestamp=ts,
                        winner=winner,
                        pnl=pnl,
                        resolved=True,
                    )
                )
                result.total_pnl += pnl
                if pnl > 0:
                    result.win_count += 1
                else:
                    result.loss_count += 1

        return result
