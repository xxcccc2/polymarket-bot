"""
Live TUI Dashboard for Polymarket Trading Bot.

Uses ``rich`` to render a persistent, auto-refreshing terminal UI.
Static info (config, strategy list) stays fixed; mutable data
(BTC price, signals, orders, balance) updates in-place every scan cycle.
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Deque, Dict, List, Optional, Any

from rich.console import Console, Group
from rich.columns import Columns
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box


# ---------------------------------------------------------------------------
# Shared log buffer — strategies and bot push messages here
# ---------------------------------------------------------------------------
_LOG_BUFFER: Deque[str] = deque(maxlen=80)
_LOG_LOCK = threading.Lock()

MAX_LOG_LINES = 40  # visible in the Activity panel


def log(msg: str) -> None:
    """Append a timestamped message to the dashboard log buffer."""
    ts = datetime.now().strftime("%H:%M:%S")
    with _LOG_LOCK:
        _LOG_BUFFER.append(f"[dim]{ts}[/dim] {msg}")


def get_log_lines(n: int = MAX_LOG_LINES, filter_keywords: Optional[List[str]] = None) -> List[str]:
    """Return the last *n* log lines (newest last). If filter_keywords is set, only include lines containing any keyword."""
    with _LOG_LOCK:
        lines = list(_LOG_BUFFER)
    if filter_keywords:
        kw_lower = [k.lower() for k in filter_keywords if k]
        lines = [ln for ln in lines if any(kw in ln.lower() for kw in kw_lower)]
    return lines[-n:]


# ---------------------------------------------------------------------------
# Data containers the bot pushes each cycle
# ---------------------------------------------------------------------------
@dataclass
class BinanceSnapshot:
    connected: bool = False
    price: float = 0.0
    chg_10s: float = 0.0
    chg_30s: float = 0.0
    chg_60s: float = 0.0
    volatility: float = 0.0
    pressure: float = 0.5


@dataclass
class StrategyRow:
    name: str = ""
    signals: int = 0
    trades: int = 0
    pnl: float = 0.0
    healthy: bool = True
    last_signal: str = ""  # human-readable time or "—"
    status: str = ""  # e.g. "0 in window", "2 groups" — visible in TUI


@dataclass
class PortfolioSnapshot:
    balance: float = 0.0
    start_balance: float = 0.0
    exposure: float = 0.0
    exposure_pct: float = 0.0
    daily_pnl: float = 0.0
    positions: int = 0
    active_orders: int = 0
    max_orders: int = 10
    filled: int = 0
    cancelled: int = 0
    fill_rate: float = 0.0
    throttle: float = 1.0
    # Staleness & sync
    balance_age_seconds: float = 0.0
    balance_stale_block_buys: bool = False
    balance_stale_max_seconds: float = 240.0
    last_balance_sync_ts: float = 0.0
    last_positions_sync_ts: float = 0.0


@dataclass
class RiskEngineSnapshot:
    native_available: bool = False
    engine_label: str = "PYTHON"
    net_delta: float = 0.0
    net_gamma: float = 0.0
    active_markets: int = 0
    sigma_avg: float = 0.0
    shock_tests_passed: int = 0
    shock_tests_total: int = 0
    kelly_scale: float = 1.0


@dataclass
class DashboardState:
    """Everything the dashboard needs to render one frame."""
    paper: bool = True
    uptime_seconds: float = 0.0
    n_markets: int = 0
    scan_number: int = 0
    binance: BinanceSnapshot = field(default_factory=BinanceSnapshot)
    strategies: List[StrategyRow] = field(default_factory=list)
    portfolio: PortfolioSnapshot = field(default_factory=PortfolioSnapshot)
    risk_engine: RiskEngineSnapshot = field(default_factory=RiskEngineSnapshot)


# ---------------------------------------------------------------------------
# Dashboard renderer
# ---------------------------------------------------------------------------
class Dashboard:
    """Persistent TUI powered by rich.live.Live."""

    def __init__(self) -> None:
        self.console = Console()
        self._live: Optional[Live] = None
        self._state = DashboardState()
        self._started_at = time.time()

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Enter the Live context — takes over the terminal."""
        self._started_at = time.time()
        self._live = Live(
            self._render(self._state),
            console=self.console,
            refresh_per_second=2,
            screen=True,         # full-screen alternate buffer
            transient=False,
        )
        self._live.start()

    def stop(self) -> None:
        """Exit the Live context — restores the normal terminal."""
        if self._live:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None

    def update(self, state: DashboardState) -> None:
        """Push new state and re-render."""
        state.uptime_seconds = time.time() - self._started_at
        self._state = state
        if self._live:
            try:
                self._live.update(self._render(state))
            except Exception:
                pass

    # -- rendering ----------------------------------------------------------

    def _render(self, s: DashboardState) -> Group:
        """Build the full dashboard as a Group of renderables."""
        return Group(
            self._render_header(s),
            Columns(
                [self._render_btc(s.binance), self._render_portfolio(s.portfolio)],
                equal=True,
                expand=True,
            ),
            self._render_risk_engine(s.risk_engine),
            self._render_strategies(s.strategies),
            self._render_logs(),
        )

    # -- panels -------------------------------------------------------------

    @staticmethod
    def _render_header(s: DashboardState) -> Panel:
        uptime = str(timedelta(seconds=int(s.uptime_seconds)))
        mode = "[bold green]PAPER[/]" if s.paper else "[bold red]LIVE[/]"
        scan_txt = f"scan #{s.scan_number}" if s.scan_number else "starting…"

        text = Text.from_markup(
            f" 🤖 [bold]Polymarket Bot[/]   {mode}"
            f"   ⏱  {uptime}"
            f"   📊 {s.n_markets} markets"
            f"   {scan_txt}"
        )
        return Panel(text, style="bright_cyan", box=box.HEAVY_EDGE)

    @staticmethod
    def _render_btc(b: BinanceSnapshot) -> Panel:
        if not b.connected or b.price <= 0:
            body = Text("⏳ Connecting…", style="yellow")
        else:
            def _clr(v: float) -> str:
                if v > 0:
                    return f"[green]{v:+.4f}%[/]"
                elif v < 0:
                    return f"[red]{v:+.4f}%[/]"
                return f"[dim]{v:+.4f}%[/]"

            body = Text.from_markup(
                f"[bold]${b.price:,.0f}[/]\n"
                f"10s {_clr(b.chg_10s)}  30s {_clr(b.chg_30s)}  60s {_clr(b.chg_60s)}\n"
                f"vol [cyan]{b.volatility:.2f}σ[/]  press [cyan]{b.pressure:.2f}[/]"
            )
        return Panel(body, title="₿ BTC/USDT", border_style="yellow", box=box.ROUNDED)

    @staticmethod
    def _render_portfolio(p: PortfolioSnapshot) -> Panel:
        session_pnl = p.balance - p.start_balance
        session_style = "green" if session_pnl >= 0 else "red"

        lines = [
            f"Balance  [bold]${p.balance:,.2f}[/] ([{session_style}]{session_pnl:+.2f}[/])",
            f"Orders   {p.active_orders}  |  Fill {p.filled} ({p.fill_rate:.0f}%)",
        ]

        if p.throttle < 1.0:
            lines.append(f"⚠️  [yellow]Throttle: {p.throttle*100:.0f}%[/]")

        # Balance staleness warning
        if p.balance_stale_block_buys and p.last_balance_sync_ts > 0:
            age = p.balance_age_seconds
            if age > p.balance_stale_max_seconds:
                lines.append(f"⚠️  [red]Balance stale {int(age)}s[/]")

        body = Text.from_markup("\n".join(lines))
        return Panel(body, title="💰 Portfolio", border_style="green", box=box.ROUNDED)

    @staticmethod
    def _render_risk_engine(r: RiskEngineSnapshot) -> Panel:
        engine_style = "green" if r.native_available else "yellow"
        delta_style = "red" if abs(r.net_delta) > 0.3 else "green"
        gamma_style = "red" if abs(r.net_gamma) > 0.2 else "green"

        shock_txt = f"{r.shock_tests_passed}/{r.shock_tests_total} passed" if r.shock_tests_total else "—"
        sigma_txt = f"{r.sigma_avg:.2f}" if r.sigma_avg > 0 else "—"

        lines = [
            f"Engine: [{engine_style}]{r.engine_label}[/]",
            f"Portfolio Delta: [{delta_style}]{r.net_delta:+.4f}[/]  "
            f"Gamma: [{gamma_style}]{r.net_gamma:+.4f}[/]",
            f"Markets: {r.active_markets}  Avg σ: {sigma_txt}",
            f"Shock Test: {shock_txt}  Kelly Scale: {r.kelly_scale:.2f}",
        ]
        body = Text.from_markup("\n".join(lines))
        return Panel(body, title="⚙ Risk Engine", border_style="magenta", box=box.ROUNDED)

    @staticmethod
    def _render_strategies(rows: List[StrategyRow]) -> Panel:
        table = Table(
            box=box.SIMPLE_HEAVY,
            expand=True,
            show_edge=False,
            padding=(0, 1),
        )
        table.add_column("Strategy", style="bold", ratio=3)
        table.add_column("Sig", justify="right", ratio=1)
        table.add_column("Trades", justify="right", ratio=1)
        table.add_column("Status", justify="left", ratio=2)

        for r in rows:
            table.add_row(
                r.name,
                str(r.signals),
                str(r.trades),
                r.status or "—",
            )

        return Panel(table, title="📊 Strategies", border_style="cyan", box=box.ROUNDED)

    @staticmethod
    def _render_logs() -> Panel:
        from .config import DASHBOARD_LOG_FILTER
        filter_kw = DASHBOARD_LOG_FILTER if DASHBOARD_LOG_FILTER else None
        lines = get_log_lines(MAX_LOG_LINES, filter_keywords=filter_kw)
        if not lines:
            filter_hint = f" (filter: {', '.join(DASHBOARD_LOG_FILTER)})" if DASHBOARD_LOG_FILTER else ""
            body = Text(f"  Waiting for activity…{filter_hint}", style="dim")
        else:
            body = Text.from_markup("\n".join(lines))
        return Panel(body, title="📝 Activity Log", border_style="bright_black", box=box.ROUNDED)
