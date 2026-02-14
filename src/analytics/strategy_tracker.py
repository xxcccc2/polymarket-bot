"""
Strategy Performance Tracker

Tracks per-strategy metrics in real time:
- Win rate, total P&L, average P&L per trade
- Sharpe ratio (rolling)
- Max drawdown
- Consecutive win/loss streaks
- Auto-health scoring → strategies below threshold get disabled

Persists trade history to SQLite so metrics survive restarts.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Dict, List, Optional

from ..logging_utils import cprint


@dataclass
class TradeRecord:
    """Single trade for analytics."""
    strategy: str
    token_id: str
    market_slug: str
    side: str           # BUY / SELL
    price: float
    size: float
    pnl: float          # realized P&L (0 if entry, actual on exit)
    fees: float
    timestamp: float     # unix
    is_exit: bool = False


@dataclass
class StrategyMetrics:
    """Computed performance metrics for a single strategy."""
    name: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    net_pnl: float = 0.0
    avg_pnl_per_trade: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    current_streak: int = 0       # positive = wins, negative = losses
    longest_win_streak: int = 0
    longest_loss_streak: int = 0
    is_healthy: bool = True
    health_score: float = 1.0     # 0.0 = dead, 1.0 = perfect
    disabled_reason: Optional[str] = None
    last_trade_time: Optional[float] = None


class StrategyTracker:
    """
    Real-time performance tracker for all strategies.

    Usage:
        tracker = StrategyTracker()
        tracker.record_trade("cross_asset", token_id, ...)
        metrics = tracker.get_metrics("cross_asset")
        health = tracker.get_health_report()
    """

    def __init__(
        self,
        max_history: int = 500,
        min_trades_for_eval: int = 10,
        min_win_rate: float = 0.30,
        min_sharpe: float = -1.0,
        max_consecutive_losses: int = 5,
        max_drawdown_pct: float = 0.15,
    ) -> None:
        self.max_history = max_history
        self.min_trades_for_eval = min_trades_for_eval
        self.min_win_rate = min_win_rate
        self.min_sharpe = min_sharpe
        self.max_consecutive_losses = max_consecutive_losses
        self.max_drawdown_pct = max_drawdown_pct

        # Per-strategy trade history
        self._trades: Dict[str, Deque[TradeRecord]] = {}

        # Per-strategy running P&L for drawdown calc
        self._equity_curves: Dict[str, List[float]] = {}
        self._peak_equity: Dict[str, float] = {}

        # Cached metrics
        self._metrics_cache: Dict[str, StrategyMetrics] = {}
        self._cache_dirty: Dict[str, bool] = {}

        # Auto-disabled strategies
        self.disabled_strategies: Dict[str, str] = {}  # name → reason

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_trade(
        self,
        strategy: str,
        token_id: str,
        market_slug: str,
        side: str,
        price: float,
        size: float,
        pnl: float = 0.0,
        fees: float = 0.0,
        is_exit: bool = False,
    ) -> None:
        """Record a completed trade for a strategy."""
        record = TradeRecord(
            strategy=strategy,
            token_id=token_id,
            market_slug=market_slug,
            side=side,
            price=price,
            size=size,
            pnl=pnl,
            fees=fees,
            timestamp=time.time(),
            is_exit=is_exit,
        )

        if strategy not in self._trades:
            self._trades[strategy] = deque(maxlen=self.max_history)
            self._equity_curves[strategy] = [0.0]
            self._peak_equity[strategy] = 0.0

        self._trades[strategy].append(record)

        # Update equity curve (only count exits — that's where P&L is realized)
        if is_exit:
            net = pnl - fees
            curve = self._equity_curves[strategy]
            new_equity = curve[-1] + net
            curve.append(new_equity)
            self._peak_equity[strategy] = max(
                self._peak_equity[strategy], new_equity
            )

        self._cache_dirty[strategy] = True

        # Check health after recording
        self._check_health(strategy)

    def get_metrics(self, strategy: str) -> StrategyMetrics:
        """Get computed metrics for a strategy."""
        if strategy not in self._trades:
            return StrategyMetrics(name=strategy)

        if self._cache_dirty.get(strategy, True):
            self._recompute(strategy)
            self._cache_dirty[strategy] = False

        return self._metrics_cache.get(strategy, StrategyMetrics(name=strategy))

    def get_all_metrics(self) -> Dict[str, StrategyMetrics]:
        """Get metrics for all tracked strategies."""
        return {name: self.get_metrics(name) for name in self._trades}

    def get_health_report(self) -> Dict[str, Dict]:
        """Get health report for all strategies."""
        report = {}
        for name in self._trades:
            m = self.get_metrics(name)
            report[name] = {
                "healthy": m.is_healthy,
                "health_score": m.health_score,
                "win_rate": m.win_rate,
                "sharpe": m.sharpe_ratio,
                "net_pnl": m.net_pnl,
                "trades": m.total_trades,
                "streak": m.current_streak,
                "max_dd": m.max_drawdown,
                "disabled_reason": m.disabled_reason,
            }
        return report

    def is_strategy_healthy(self, strategy: str) -> tuple:
        """Check if a strategy is healthy enough to trade.

        Returns:
            (is_healthy: bool, reason: str)
        """
        if strategy in self.disabled_strategies:
            return False, self.disabled_strategies[strategy]

        m = self.get_metrics(strategy)

        if not m.is_healthy:
            return False, m.disabled_reason or "Unhealthy"

        return True, "OK"

    def enable_strategy(self, strategy: str) -> None:
        """Re-enable a previously disabled strategy."""
        if strategy in self.disabled_strategies:
            del self.disabled_strategies[strategy]
            cprint(f"  ✅ Strategy re-enabled: {strategy}", "green")

    def print_scorecard(self) -> None:
        """Print a formatted scorecard of all strategies."""
        all_metrics = self.get_all_metrics()

        if not all_metrics:
            cprint("  No strategy data yet", "white")
            return

        cprint("\n" + "=" * 70, "cyan")
        cprint("📊 Strategy Scorecard", "cyan", attrs=["bold"])
        cprint("=" * 70, "cyan")
        cprint(
            f"  {'Strategy':<22} {'Trades':>6} {'Win%':>6} {'Net P&L':>9} "
            f"{'Sharpe':>7} {'Streak':>7} {'Health':>7}",
            "white",
        )
        cprint("-" * 70, "white")

        for name, m in sorted(all_metrics.items()):
            pnl_color = "green" if m.net_pnl >= 0 else "red"
            health_icon = "✅" if m.is_healthy else "❌"
            streak_str = f"{m.current_streak:+d}" if m.current_streak != 0 else "0"

            cprint(
                f"  {name:<22} {m.total_trades:>6} {m.win_rate * 100:>5.1f}% "
                f"${m.net_pnl:>+8.2f} {m.sharpe_ratio:>+7.2f} "
                f"{streak_str:>7} {health_icon:>5}",
                pnl_color,
            )

            if m.disabled_reason:
                cprint(f"    ⚠️  {m.disabled_reason}", "yellow")

        cprint("=" * 70 + "\n", "cyan")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _recompute(self, strategy: str) -> None:
        """Recompute all metrics for a strategy."""
        trades = self._trades.get(strategy, deque())
        exits = [t for t in trades if t.is_exit]

        m = StrategyMetrics(name=strategy)
        m.total_trades = len(exits)

        if not exits:
            self._metrics_cache[strategy] = m
            return

        # Win/loss
        for t in exits:
            net = t.pnl - t.fees
            m.total_pnl += t.pnl
            m.total_fees += t.fees
            if net > 0:
                m.wins += 1
            elif net < 0:
                m.losses += 1

        m.net_pnl = m.total_pnl - m.total_fees
        m.win_rate = m.wins / m.total_trades if m.total_trades > 0 else 0.0
        m.avg_pnl_per_trade = m.net_pnl / m.total_trades if m.total_trades > 0 else 0.0
        m.last_trade_time = exits[-1].timestamp

        # Streaks
        streak = 0
        max_win = 0
        max_loss = 0
        for t in exits:
            net = t.pnl - t.fees
            if net > 0:
                streak = max(1, streak + 1)
                max_win = max(max_win, streak)
            elif net < 0:
                streak = min(-1, streak - 1)
                max_loss = min(max_loss, streak)
            else:
                streak = 0

        m.current_streak = streak
        m.longest_win_streak = max_win
        m.longest_loss_streak = abs(max_loss)

        # Sharpe ratio (from per-trade returns)
        returns = [(t.pnl - t.fees) for t in exits]
        if len(returns) >= 5:
            mean_r = sum(returns) / len(returns)
            var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
            std_r = math.sqrt(var_r) if var_r > 0 else 0.001
            # Annualize: assume ~50 trades/day, 365 days
            m.sharpe_ratio = (mean_r / std_r) * math.sqrt(50 * 365) if std_r > 0 else 0.0
        else:
            m.sharpe_ratio = 0.0

        # Max drawdown from equity curve
        curve = self._equity_curves.get(strategy, [0.0])
        peak = 0.0
        max_dd = 0.0
        for eq in curve:
            peak = max(peak, eq)
            dd = peak - eq
            max_dd = max(max_dd, dd)

        m.max_drawdown = max_dd
        m.max_drawdown_pct = max_dd / peak if peak > 0 else 0.0

        # Health scoring
        m.health_score = self._compute_health_score(m)
        m.is_healthy = m.health_score >= 0.3
        if not m.is_healthy:
            m.disabled_reason = self._diagnose_unhealthy(m)

        self._metrics_cache[strategy] = m

    def _compute_health_score(self, m: StrategyMetrics) -> float:
        """Compute a 0-1 health score from metrics."""
        if m.total_trades < self.min_trades_for_eval:
            return 1.0  # Not enough data — assume healthy

        score = 1.0

        # Win rate penalty (below 30% is bad)
        if m.win_rate < self.min_win_rate:
            score -= 0.3

        # Sharpe penalty
        if m.sharpe_ratio < 0:
            score -= min(abs(m.sharpe_ratio) * 0.1, 0.3)

        # Losing streak penalty
        if m.current_streak <= -self.max_consecutive_losses:
            score -= 0.3

        # Net P&L penalty
        if m.net_pnl < 0:
            score -= 0.1

        # Drawdown penalty
        if m.max_drawdown_pct > self.max_drawdown_pct:
            score -= 0.2

        return max(0.0, min(1.0, score))

    def _diagnose_unhealthy(self, m: StrategyMetrics) -> str:
        """Diagnose why a strategy is unhealthy."""
        reasons = []

        if m.total_trades >= self.min_trades_for_eval:
            if m.win_rate < self.min_win_rate:
                reasons.append(f"win rate {m.win_rate*100:.0f}% < {self.min_win_rate*100:.0f}%")
            if m.sharpe_ratio < self.min_sharpe:
                reasons.append(f"Sharpe {m.sharpe_ratio:.2f} < {self.min_sharpe:.1f}")
            if m.current_streak <= -self.max_consecutive_losses:
                reasons.append(f"{abs(m.current_streak)} consecutive losses")
            if m.max_drawdown_pct > self.max_drawdown_pct:
                reasons.append(f"drawdown {m.max_drawdown_pct*100:.1f}% > {self.max_drawdown_pct*100:.0f}%")

        return "; ".join(reasons) if reasons else "Low health score"

    def _check_health(self, strategy: str) -> None:
        """After recording, check if strategy should be auto-disabled."""
        m = self.get_metrics(strategy)

        if m.total_trades < self.min_trades_for_eval:
            return  # Not enough data

        if not m.is_healthy and strategy not in self.disabled_strategies:
            self.disabled_strategies[strategy] = m.disabled_reason or "Unhealthy"
            cprint(
                f"  🚨 Auto-disabled strategy '{strategy}': {m.disabled_reason}",
                "red",
            )
