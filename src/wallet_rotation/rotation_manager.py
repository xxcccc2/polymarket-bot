"""
Rotation manager: inactivity detection and replacement decisions.

Evaluates tracked wallets for inactivity, discovers/scores candidates,
and produces replacement decisions for hot-wiring into wallet_copy.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..data_client import get_trades_by_user

from .candidate_discovery import fetch_candidate_wallets
from .metrics import compute_wallet_metrics
from .scoring import score_wallet


@dataclass
class RotationResult:
    """Result of a rotation cycle."""

    replaced: List[str] = field(default_factory=list)  # wallets removed
    added: List[str] = field(default_factory=list)  # wallets added
    reason: str = ""
    score_breakdown: Dict[str, float] = field(default_factory=dict)


def _parse_trade_ts(trade: dict) -> Optional[float]:
    """Parse trade timestamp to Unix seconds."""
    ts = trade.get("timestamp")
    if ts is None:
        return None
    try:
        ts_num = float(ts)
        if ts_num > 1e12:
            ts_num /= 1000
        return ts_num
    except (TypeError, ValueError):
        return None


def get_last_trade_ts(addr: str, limit: int = 50) -> Optional[float]:
    """
    Get timestamp of most recent trade for a wallet.

    Returns:
        Unix seconds of latest trade, or None if no trades.
    """
    try:
        trades = get_trades_by_user(addr, limit=limit, offset=0, taker_only=False)
    except Exception:
        return None
    if not trades:
        return None
    last = None
    for t in trades:
        ts = _parse_trade_ts(t)
        if ts is not None and (last is None or ts > last):
            last = ts
    return last


def is_wallet_inactive(
    addr: str,
    inactivity_threshold_hours: float,
) -> bool:
    """
    Check if wallet has no trades within threshold.

    Args:
        addr: Proxy wallet address
        inactivity_threshold_hours: Hours since last trade to consider inactive

    Returns:
        True if inactive (no recent trade within threshold).
    """
    last_ts = get_last_trade_ts(addr)
    if last_ts is None:
        return True
    age_hours = (time.time() - last_ts) / 3600
    return age_hours >= inactivity_threshold_hours


class RotationManager:
    """
    Manages wallet rotation: inactivity detection, candidate discovery,
    scoring, and replacement selection with churn prevention.
    """

    def __init__(
        self,
        *,
        inactivity_threshold_hours: float = 48,
        refresh_interval_seconds: float = 600,
        max_replacements_per_cycle: int = 2,
        min_tracked_wallets: int = 1,
        candidate_pool_size: int = 30,
        blocked_wallets: Optional[List[str]] = None,
        min_trades: int = 20,
        min_crypto_pct: float = 70,
        min_shortterm_pct: float = 40,
        max_days_since_last_trade: float = 7,
        min_trades_per_day: float = 0.5,
        leaderboard_category: str = "CRYPTO",
        removed_cooldown_hours: float = 24,
        initial_removed_at: Optional[Dict[str, float]] = None,
    ):
        self.inactivity_threshold_hours = inactivity_threshold_hours
        self.refresh_interval_seconds = refresh_interval_seconds
        self.max_replacements_per_cycle = max_replacements_per_cycle
        self.min_tracked_wallets = min_tracked_wallets
        self.candidate_pool_size = candidate_pool_size
        self.blocked_wallets = {a.lower() for a in (blocked_wallets or [])}
        self.min_trades = min_trades
        self.min_crypto_pct = min_crypto_pct
        self.min_shortterm_pct = min_shortterm_pct
        self.max_days_since_last_trade = max_days_since_last_trade
        self.min_trades_per_day = min_trades_per_day
        self.leaderboard_category = leaderboard_category
        self.removed_cooldown_hours = removed_cooldown_hours

        self._last_rotation_ts = 0.0
        self._removed_at: Dict[str, float] = dict(initial_removed_at or {})

    def get_removed_at(self) -> Dict[str, float]:
        """Return current removed-at cooldown map for persistence."""
        return dict(self._removed_at)

    def _prune_cooldown(self) -> None:
        """Remove expired entries from removed_at."""
        cutoff = time.time() - (self.removed_cooldown_hours * 3600)
        to_remove = [k for k, v in self._removed_at.items() if v < cutoff]
        for k in to_remove:
            del self._removed_at[k]

    def run_rotation(
        self,
        tracked_wallets: List[str],
        on_log=None,
    ) -> tuple[List[str], RotationResult]:
        """
        Run one rotation cycle: detect inactive, find replacements, return new list.

        Args:
            tracked_wallets: Current tracked wallet addresses
            on_log: Optional callback(msg: str) for logging

        Returns:
            (new_tracked_wallets, result)
        """
        result = RotationResult()

        now = time.time()
        if now - self._last_rotation_ts < self.refresh_interval_seconds:
            return list(tracked_wallets), result

        self._prune_cooldown()

        # 1. Identify inactive wallets
        inactive = []
        for addr in tracked_wallets:
            if is_wallet_inactive(addr, self.inactivity_threshold_hours):
                inactive.append(addr)

        if not inactive:
            self._last_rotation_ts = now
            return list(tracked_wallets), result

        # Guard: never replace all
        if len(inactive) >= len(tracked_wallets):
            inactive = inactive[: max(1, len(tracked_wallets) - self.min_tracked_wallets)]
        # Cap replacements per cycle
        to_replace = inactive[: self.max_replacements_per_cycle]

        if on_log:
            on_log(f"[ROTATION] {len(to_replace)} inactive: {[a[:10]+'...' for a in to_replace]}")

        # 2. Fetch candidates
        candidates = fetch_candidate_wallets(
            category=self.leaderboard_category,
            top_n=self.candidate_pool_size,
        )

        # Exclude current tracked, recently removed, and blocked (hedge/MM)
        current_set = {a.lower() for a in tracked_wallets}
        for addr in to_replace:
            self._removed_at[addr.lower()] = now
        excluded = current_set | set(self._removed_at.keys()) | self.blocked_wallets

        # 3. Score candidates
        scored: List[tuple[float, Dict, List[str]]] = []
        for c in candidates:
            addr = (c.get("address") or "").strip()
            if not addr or addr.lower() in excluded:
                continue
            analysis = compute_wallet_metrics(addr, max_trades=200, sleep_between_pages=0.2)
            score, reasons = score_wallet(
                c,
                analysis,
                min_trades=self.min_trades,
                min_crypto_pct=self.min_crypto_pct,
                min_shortterm_pct=self.min_shortterm_pct,
                max_days_since_last_trade=self.max_days_since_last_trade,
                min_trades_per_day=self.min_trades_per_day,
            )
            if score > 0:
                scored.append((score, {"address": addr, "candidate": c, "analysis": analysis}, reasons))
            time.sleep(0.2)  # Rate limit

        scored.sort(key=lambda x: x[0], reverse=True)

        # 4. Pick replacements
        new_wallets: List[str] = []
        for _ in range(len(to_replace)):
            if not scored:
                break
            score, data, reasons = scored.pop(0)
            addr = data["address"]
            new_wallets.append(addr)
            result.added.append(addr)
            result.score_breakdown[addr] = score
            if on_log:
                on_log(f"[ROTATION] +{addr[:10]}...{addr[-6:]} score={score:.2f} {reasons}")

        for addr in to_replace:
            result.replaced.append(addr)
            if on_log:
                on_log(f"[ROTATION] -{addr[:10]}...{addr[-6:]} (inactive)")

        result.reason = "inactive"

        # 5. Build new tracked list: keep active, remove replaced, add new
        kept = [a for a in tracked_wallets if a not in to_replace]
        new_tracked = kept + new_wallets

        # Guard: never drop below min_tracked_wallets
        if len(new_tracked) < self.min_tracked_wallets:
            if on_log:
                on_log(
                    f"[ROTATION] Skipping: would drop to {len(new_tracked)} wallets "
                    f"(min={self.min_tracked_wallets})"
                )
            result.replaced.clear()
            result.added.clear()
            result.reason = "skipped_below_min"
            self._last_rotation_ts = now
            return list(tracked_wallets), result

        self._last_rotation_ts = now
        return new_tracked, result
