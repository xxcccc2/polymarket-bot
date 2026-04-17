"""Logging utilities for polymarket-bot."""

from __future__ import annotations

from datetime import datetime
import logging
import os
from pathlib import Path
import sys
from typing import Optional, Iterable

_CONFIGURED = False
_SESSION_LOG_PATH: Optional[Path] = None

# When the TUI dashboard is active, cprint skips stdout logging
# and only pushes to the dashboard log buffer.
_DASHBOARD_MODE = False

_COLOR_LEVEL_MAP = {
    "red": logging.ERROR,
    "yellow": logging.WARNING,
    "green": logging.INFO,
    "cyan": logging.INFO,
    "white": logging.INFO,
    "blue": logging.INFO,
    "magenta": logging.INFO,
    "dark_grey": logging.DEBUG,
}

# Rich markup mapping for colours used in existing cprint calls
_RICH_COLOR = {
    "red": "red",
    "yellow": "yellow",
    "green": "green",
    "cyan": "cyan",
    "white": "white",
    "blue": "blue",
    "magenta": "magenta",
    "dark_grey": "dim",
}


class _DashboardAwareStdoutFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _DASHBOARD_MODE


def _resolve_session_log_path() -> Optional[Path]:
    enabled = os.getenv("SESSION_LOG_ENABLED", "true").lower() == "true"
    if not enabled:
        return None

    project_root = Path(__file__).resolve().parent.parent
    override_path = os.getenv("SESSION_LOG_PATH", "").strip()
    if override_path:
        path = Path(override_path).expanduser()
        if not path.is_absolute():
            path = project_root / path
    else:
        log_dir_raw = os.getenv("SESSION_LOG_DIR", "").strip()
        log_dir = Path(log_dir_raw).expanduser() if log_dir_raw else (project_root / "logs")
        if not log_dir.is_absolute():
            log_dir = project_root / log_dir
        wallet_id = os.getenv("BOT_WALLET_ID", "").strip().lower()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"session_{wallet_id}_{ts}.log" if wallet_id else f"session_{ts}.log"
        path = log_dir / filename

    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_session_log_path() -> Optional[str]:
    global _SESSION_LOG_PATH
    if _SESSION_LOG_PATH is None:
        _SESSION_LOG_PATH = _resolve_session_log_path()
    return str(_SESSION_LOG_PATH) if _SESSION_LOG_PATH is not None else None


def configure_logging(level: Optional[str] = None) -> None:
    """Configure root logging once."""
    global _CONFIGURED, _SESSION_LOG_PATH

    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")

    level_value = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()

    if _CONFIGURED:
        root_logger.setLevel(level_value)
        return

    root_logger.setLevel(level_value)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(_DashboardAwareStdoutFilter())
    root_logger.addHandler(stdout_handler)

    _SESSION_LOG_PATH = _resolve_session_log_path()
    if _SESSION_LOG_PATH is not None:
        file_handler = logging.FileHandler(_SESSION_LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Suppress noisy HTTP request logs from httpx (py-clob-client)
    for _logger in ("httpx", "httpcore"):
        logging.getLogger(_logger).setLevel(logging.WARNING)
    _CONFIGURED = True


def set_dashboard_mode(active: bool) -> None:
    """Switch between TUI dashboard mode and plain-text logging."""
    global _DASHBOARD_MODE
    _DASHBOARD_MODE = active


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a configured logger."""
    configure_logging()
    return logging.getLogger(name or "polymarket-bot")


def cprint(
    message: str,
    color: Optional[str] = None,
    on_color: Optional[str] = None,
    attrs: Optional[Iterable[str]] = None,
    **kwargs,
) -> None:
    """termcolor-compatible print wrapper backed by logging.

    When dashboard mode is ON, messages are routed to the TUI log
    buffer instead of stdout.
    """
    configure_logging()
    level = _COLOR_LEVEL_MAP.get(color, logging.INFO)
    logger = logging.getLogger("polymarket-bot")
    logger.log(level, message)
    if _DASHBOARD_MODE:
        _push_to_dashboard(message, color, attrs)


def _push_to_dashboard(
    message: str,
    color: Optional[str] = None,
    attrs: Optional[Iterable[str]] = None,
) -> None:
    """Forward a message to the dashboard log panel."""
    from . import dashboard as _dash  # lazy to avoid circular imports

    # Strip leading whitespace/newlines that look fine in scrolling
    # logs but ugly in a fixed-height panel
    clean = message.strip()
    if not clean:
        return

    # Apply rich markup colour
    rich_clr = _RICH_COLOR.get(color or "", "")
    is_bold = attrs and "bold" in attrs
    if rich_clr and is_bold:
        clean = f"[bold {rich_clr}]{clean}[/]"
    elif rich_clr:
        clean = f"[{rich_clr}]{clean}[/]"
    elif is_bold:
        clean = f"[bold]{clean}[/]"

    _dash.log(clean)
