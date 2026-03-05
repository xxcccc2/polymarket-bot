"""Logging utilities for polymarket-bot."""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional, Iterable

_CONFIGURED = False

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


def configure_logging(level: Optional[str] = None) -> None:
    """Configure root logging once."""
    global _CONFIGURED

    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")

    level_value = getattr(logging, level.upper(), logging.INFO)

    if _CONFIGURED:
        logging.getLogger().setLevel(level_value)
        return

    logging.basicConfig(
        level=level_value,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
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
    if _DASHBOARD_MODE:
        _push_to_dashboard(message, color, attrs)
        return

    configure_logging()
    level = _COLOR_LEVEL_MAP.get(color, logging.INFO)
    logger = logging.getLogger("polymarket-bot")
    logger.log(level, message)


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
