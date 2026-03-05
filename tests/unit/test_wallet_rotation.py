"""Unit tests for wallet rotation: scoring, replacement logic, inactivity detection."""

from __future__ import annotations

import time
from unittest.mock import patch

from src.wallet_rotation.scoring import score_wallet
from src.wallet_rotation.rotation_manager import (
    RotationManager,
    RotationResult,
    get_last_trade_ts,
    is_wallet_inactive,
)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def test_score_wallet_no_analysis_returns_zero():
    score, reasons = score_wallet({}, None)
    assert score == 0.0
    assert "No trade data" in reasons


def test_score_wallet_too_few_trades_filtered():
    candidate = {"address": "0xabc", "rank_daily": 1, "rank_monthly": 2}
    analysis = {
        "address": "0xabc",
        "n_trades": 10,
        "crypto_pct": 90,
        "shortterm_pct": 60,
        "trades_per_day": 2,
        "days_since_last_trade": 1,
    }
    score, reasons = score_wallet(candidate, analysis, min_trades=20)
    assert score == 0.0
    assert "Too few trades" in reasons[0]


def test_score_wallet_low_crypto_filtered():
    candidate = {"address": "0xabc", "rank_daily": 1, "rank_monthly": 2}
    analysis = {
        "address": "0xabc",
        "n_trades": 50,
        "crypto_pct": 50,
        "shortterm_pct": 60,
        "trades_per_day": 2,
        "days_since_last_trade": 1,
    }
    score, reasons = score_wallet(candidate, analysis, min_crypto_pct=70)
    assert score == 0.0
    assert "Low crypto" in reasons[0]


def test_score_wallet_inactive_filtered():
    candidate = {"address": "0xabc", "rank_daily": 1, "rank_monthly": 2}
    analysis = {
        "address": "0xabc",
        "n_trades": 50,
        "crypto_pct": 90,
        "shortterm_pct": 60,
        "trades_per_day": 2,
        "days_since_last_trade": 10,
    }
    score, reasons = score_wallet(candidate, analysis, max_days_since_last_trade=7)
    assert score == 0.0
    assert "Inactive" in reasons[0]


def test_score_wallet_low_trades_per_day_filtered():
    candidate = {"address": "0xabc", "rank_daily": 1, "rank_monthly": 2}
    analysis = {
        "address": "0xabc",
        "n_trades": 50,
        "crypto_pct": 90,
        "shortterm_pct": 60,
        "trades_per_day": 0.2,
        "days_since_last_trade": 1,
    }
    score, reasons = score_wallet(candidate, analysis, min_trades_per_day=0.5)
    assert score == 0.0
    assert "Low activity" in reasons[0]


def test_score_wallet_passes_and_returns_positive_score():
    candidate = {"address": "0xabc", "rank_daily": 1, "rank_monthly": 3}
    analysis = {
        "address": "0xabc",
        "n_trades": 50,
        "crypto_pct": 90,
        "shortterm_pct": 60,
        "trades_per_day": 2,
        "days_since_last_trade": 1,
    }
    score, reasons = score_wallet(candidate, analysis)
    assert score > 0
    assert score <= 1.0
    assert "daily#1" in reasons
    assert "month#3" in reasons


def test_score_wallet_mm_like_filtered():
    candidate = {"address": "0xmm", "rank_daily": 5, "rank_monthly": 10}
    analysis = {
        "n_trades": 100,
        "crypto_pct": 80,
        "shortterm_pct": 50,
        "trades_per_day": 20,
        "days_since_last_trade": 0.5,
        "mm_like": True,
        "volume_farmer_like": False,
    }
    score, reasons = score_wallet(candidate, analysis)
    assert score == 0.0
    assert "MM/Spread-like" in reasons[0]


def test_score_wallet_volume_farmer_filtered():
    candidate = {"address": "0xvf", "rank_daily": 3, "rank_monthly": 8}
    analysis = {
        "n_trades": 100,
        "crypto_pct": 80,
        "shortterm_pct": 50,
        "trades_per_day": 30,
        "days_since_last_trade": 0.5,
        "mm_like": False,
        "volume_farmer_like": True,
    }
    score, reasons = score_wallet(candidate, analysis)
    assert score == 0.0
    assert "Volume-farmer" in reasons[0]


# ---------------------------------------------------------------------------
# Inactivity detection
# ---------------------------------------------------------------------------
def test_is_wallet_inactive_no_trades():
    with patch("src.wallet_rotation.rotation_manager.get_trades_by_user") as m:
        m.return_value = []
        assert is_wallet_inactive("0xabc", 24) is True


def test_is_wallet_inactive_recent_trade():
    now = time.time()
    recent_ts = now - 3600  # 1 hour ago
    with patch("src.wallet_rotation.rotation_manager.get_trades_by_user") as m:
        m.return_value = [{"timestamp": recent_ts}]
        assert is_wallet_inactive("0xabc", 48) is False


def test_is_wallet_inactive_stale_trade():
    now = time.time()
    stale_ts = now - (72 * 3600)  # 72 hours ago
    with patch("src.wallet_rotation.rotation_manager.get_trades_by_user") as m:
        m.return_value = [{"timestamp": stale_ts}]
        assert is_wallet_inactive("0xabc", 48) is True


def test_get_last_trade_ts_handles_millisecond_timestamps():
    now = time.time()
    ts_ms = int(now * 1000)
    with patch("src.wallet_rotation.rotation_manager.get_trades_by_user") as m:
        m.return_value = [{"timestamp": ts_ms}]
        last = get_last_trade_ts("0xabc")
        assert last is not None
        assert abs(last - now) < 2  # within 2 seconds


# ---------------------------------------------------------------------------
# Rotation manager
# ---------------------------------------------------------------------------
def test_rotation_manager_respects_refresh_interval():
    mgr = RotationManager(
        refresh_interval_seconds=60,
        inactivity_threshold_hours=48,
    )
    tracked = ["0xdead", "0xbeef"]
    with patch.object(mgr, "_last_rotation_ts", time.time() - 30):  # 30s ago
        new_tracked, result = mgr.run_rotation(tracked)
        assert new_tracked == tracked
        assert not result.replaced
        assert not result.added


def test_rotation_manager_no_inactive_returns_unchanged():
    mgr = RotationManager(
        refresh_interval_seconds=0,
        inactivity_threshold_hours=9999,  # no one inactive
    )
    tracked = ["0xdead", "0xbeef"]
    with patch("src.wallet_rotation.rotation_manager.is_wallet_inactive") as m:
        m.return_value = False
        new_tracked, result = mgr.run_rotation(tracked)
        assert new_tracked == tracked
        assert not result.replaced
        assert not result.added


def test_rotation_manager_replaces_inactive_when_candidates_available():
    mgr = RotationManager(
        refresh_interval_seconds=0,
        inactivity_threshold_hours=1,
        max_replacements_per_cycle=2,
        min_tracked_wallets=1,
        candidate_pool_size=10,
        min_trades=5,
        min_crypto_pct=50,
        min_shortterm_pct=30,
        max_days_since_last_trade=14,
        min_trades_per_day=0.1,
        removed_cooldown_hours=24,
    )
    tracked = ["0xdead", "0xbeef"]

    def fake_inactive(addr, _hours):
        return addr == "0xdead"

    with patch("src.wallet_rotation.rotation_manager.is_wallet_inactive", side_effect=fake_inactive):
        with patch(
            "src.wallet_rotation.rotation_manager.fetch_candidate_wallets"
        ) as fetch_mock:
            fetch_mock.return_value = [
                {
                    "address": "0xnew1",
                    "rank_daily": 1,
                    "rank_monthly": 2,
                    "pnl_daily": 100,
                    "pnl_monthly": 500,
                },
            ]
            with patch(
                "src.wallet_rotation.rotation_manager.compute_wallet_metrics"
            ) as metrics_mock:
                metrics_mock.return_value = {
                    "address": "0xnew1",
                    "n_trades": 50,
                    "crypto_pct": 80,
                    "shortterm_pct": 60,
                    "trades_per_day": 2,
                    "days_since_last_trade": 1,
                }
                new_tracked, result = mgr.run_rotation(tracked)

    assert "0xdead" in result.replaced
    assert "0xnew1" in result.added
    assert "0xbeef" in new_tracked
    assert "0xnew1" in new_tracked
    assert "0xdead" not in new_tracked


def test_rotation_manager_never_drops_below_min_tracked():
    mgr = RotationManager(
        refresh_interval_seconds=0,
        inactivity_threshold_hours=1,
        max_replacements_per_cycle=2,
        min_tracked_wallets=2,
        candidate_pool_size=10,
    )
    tracked = ["0xdead", "0xbeef"]

    with patch("src.wallet_rotation.rotation_manager.is_wallet_inactive") as m:
        m.return_value = True  # all inactive
        with patch(
            "src.wallet_rotation.rotation_manager.fetch_candidate_wallets"
        ) as fetch_mock:
            fetch_mock.return_value = []  # no qualified candidates
            new_tracked, result = mgr.run_rotation(tracked)

    # Would drop to 0, so we skip
    assert result.reason == "skipped_below_min"
    assert new_tracked == tracked
    assert not result.replaced
    assert not result.added


def test_rotation_manager_excludes_recently_removed_from_candidates():
    mgr = RotationManager(
        refresh_interval_seconds=0,
        inactivity_threshold_hours=1,
        max_replacements_per_cycle=1,
        min_tracked_wallets=1,
        removed_cooldown_hours=24,
    )
    mgr._removed_at["0xrecently_removed"] = time.time() - 100  # removed 100s ago
    tracked = ["0xdead"]

    with patch("src.wallet_rotation.rotation_manager.is_wallet_inactive") as m:
        m.return_value = True
        with patch(
            "src.wallet_rotation.rotation_manager.fetch_candidate_wallets"
        ) as fetch_mock:
            fetch_mock.return_value = [
                {"address": "0xrecently_removed", "rank_daily": 1, "rank_monthly": 1},
                {"address": "0xnew1", "rank_daily": 2, "rank_monthly": 2},
            ]
            with patch(
                "src.wallet_rotation.rotation_manager.compute_wallet_metrics"
            ) as metrics_mock:
                def metrics_side_effect(addr, **kwargs):
                    if addr == "0xrecently_removed":
                        return None  # excluded
                    return {
                        "address": addr,
                        "n_trades": 50,
                        "crypto_pct": 80,
                        "shortterm_pct": 60,
                        "trades_per_day": 2,
                        "days_since_last_trade": 1,
                    }
                metrics_mock.side_effect = metrics_side_effect
                new_tracked, result = mgr.run_rotation(tracked)

    # 0xrecently_removed should be excluded (in removed_at)
    assert "0xrecently_removed" not in result.added
    assert "0xnew1" in result.added
