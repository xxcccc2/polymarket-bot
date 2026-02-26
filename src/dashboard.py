"""
Live TUI Dashboard for Polymarket Trading Bot.

Uses ``rich`` to render a persistent, auto-refreshing terminal UI.
Static info (config, strategy list) stays fixed; mutable data
(BTC price, signals, orders, P&L) updates in-place every scan cycle.
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

MAX_LOG_LINES = 20  # visible in the Activity panel


def log(msg: str) -> None:
    """Append a timestamped message to the dashboard log buffer."""
    ts = datetime.now().strftime("%H:%M:%S")
    with _LOG_LOCK:
        _LOG_BUFFER.append(f"[dim]{ts}[/dim] {msg}")


def get_log_lines(n: int = MAX_LOG_LINES) -> List[str]:
    """Return the last *n* log lines (newest last)."""
    with _LOG_LOCK:
        return list(_LOG_BUFFER)[-n:]


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
        pnl_style = "green" if p.daily_pnl >= 0 else "red"
        session_pnl = p.balance - p.start_balance
        session_style = "green" if session_pnl >= 0 else "red"

        throttle_txt = ""
        if p.throttle < 1.0:
            throttle_txt = f"\n⚠️  [yellow]Throttle: {p.throttle*100:.0f}%[/]"

        body = Text.from_markup(
            f"Balance  [bold]${p.balance:,.2f}[/]"
            f" ([{session_style}]{session_pnl:+.2f}[/])\n"
            f"Exposure ${p.exposure:,.2f} ({p.exposure_pct:.1f}%)\n"
            f"Day P&L  [{pnl_style}]${p.daily_pnl:+.2f}[/]"
            f"  Orders {p.active_orders}/{p.max_orders}"
            f"  Fills {p.filled}"
            f"{throttle_txt}"
        )
        return Panel(body, title="💰 Portfolio", border_style="green", box=box.ROUNDED)

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
        table.add_column("P&L", justify="right", ratio=1)
        table.add_column("", justify="center", width=3)
        table.add_column("Last", justify="right", ratio=1)

        for r in rows:
            pnl_str = f"${r.pnl:+.2f}"
            pnl_style = "green" if r.pnl >= 0 else "red"
            health = "✅" if r.healthy else "⏸️"
            table.add_row(
                r.name,
                str(r.signals),
                str(r.trades),
                f"[{pnl_style}]{pnl_str}[/]",
                health,
                r.last_signal or "—",
            )

        return Panel(table, title="📊 Strategies", border_style="cyan", box=box.ROUNDED)

    @staticmethod
    def _render_logs() -> Panel:
        lines = get_log_lines(MAX_LOG_LINES)
        if not lines:
            body = Text("  Waiting for activity…", style="dim")
        else:
            body = Text.from_markup("\n".join(lines))
        return Panel(body, title="📝 Activity Log", border_style="bright_black", box=box.ROUNDED)
