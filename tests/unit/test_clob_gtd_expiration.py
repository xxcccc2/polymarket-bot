"""Polymarket GTD expiration helper (API min lead vs resolution cap)."""

import pytest

from src.config import CLOB_GTD_MIN_LEAD_SECONDS, clob_gtd_expiration_unix


def test_clob_gtd_expiration_at_least_min_lead(monkeypatch):
    now = 1_776_319_000.0
    monkeypatch.setattr("src.config.time.time", lambda: now)
    end = now + 3600.0
    exp = clob_gtd_expiration_unix(end)
    assert exp is not None
    assert exp >= int(now + CLOB_GTD_MIN_LEAD_SECONDS)


def test_clob_gtd_expiration_none_when_resolution_too_close(monkeypatch):
    now = 1_776_319_000.0
    monkeypatch.setattr("src.config.time.time", lambda: now)
    # cap = end - 15 = now + 15; min lead ~ now + 90 → conflict
    end = now + 30.0
    assert clob_gtd_expiration_unix(end, before_resolution_sec=15.0) is None


def test_clob_gtd_expiration_fits_before_resolution(monkeypatch):
    now = 1_776_319_000.0
    monkeypatch.setattr("src.config.time.time", lambda: now)
    end = now + 400.0
    exp = clob_gtd_expiration_unix(end, max_horizon_sec=120.0, before_resolution_sec=15.0)
    assert exp is not None
    assert exp <= int(end - 15.0)
