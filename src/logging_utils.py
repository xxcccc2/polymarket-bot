"""Logging utilities for polymarket-bot."""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional, Iterable

_CONFIGURED = False

_COLOR_LEVEL_MAP = {
    "red": logging.ERROR,
    "yellow": logging.WARNING,
    "green": logging.INFO,
    "cyan": logging.INFO,
    "white": logging.INFO,
    "blue": logging.INFO,
    "magenta": logging.INFO,
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
    _CONFIGURED = True


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
    """termcolor-compatible print wrapper backed by logging."""
    configure_logging()
    level = _COLOR_LEVEL_MAP.get(color, logging.INFO)
    logger = logging.getLogger("polymarket-bot")
    logger.log(level, message)
