"""
Combinatorial Arbitrage Strategy

Finds logical pricing inconsistencies across related Polymarket markets.

Core thesis: If "BTC > $100k" is priced at 40¢ but "BTC > $90k" is only 35¢,
that's a logical violation — the first IMPLIES the second. This implementation
trades the underpriced side of the violation with maker-style buy orders.

$40M+ was extracted from Polymarket via combinatorial arb (Milionis et al. 2024).

How it works:
    1. Parse market questions to extract thresholds and directions
    2. Group markets by underlying asset/event
    3. Check monotonicity constraints (e.g., P(X > 100) <= P(X > 90))
    4. When violations are found → buy the underpriced side

Types of logical constraints:
    - Threshold monotonicity: P(BTC > 100k) <= P(BTC > 90k) <= P(BTC > 80k)
    - Complement bounds: P(YES) + P(NO) ≈ 1.00 (already handled by arb strategy)
    - Subset constraints: P(A and B) <= min(P(A), P(B))
    - Time monotonicity: P(event by Dec) >= P(event by Nov)

References:
    - Milionis et al. (2024): Automated Market Making and Combinatorial Arb
    - Polymarket structure: conditional token framework enables these
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from .base_strategy import BaseStrategy, Signal, SignalType, MarketData
from ..logging_utils import cprint
from ..config import (
    ORDER_SIZE_USD,
    MAX_POSITION_USD,
    TRADING_FEE_RATE,
    COMBO_EDGE_SIZE_FACTOR,
    COMBO_EDGE_SIZE_CAP,
    COMBO_MIN_EDGE_CENTS,
    COMBO_COOLDOWN,
)


# ----- Configuration Defaults (fallback values only) -----

# Minimum confidence
COMBO_MIN_CONFIDENCE = 0.60

# Maximum/minimum buy price bounds
COMBO_MAX_BUY_PRICE = 0.92
COMBO_MIN_BUY_PRICE = 0.03

# ----- Threshold Extraction Patterns -----

# Matches patterns like "above $100,000", "over 100k", "> $90,000", "reach $95k"
THRESHOLD_PATTERNS = [
    # "$100,000" or "$100k"
    r"(?:above|over|exceed|reach|>\s*)\$?([\d,]+\.?\d*)\s*k?\b",
    # "100,000 dollars"
    r"([\d,]+\.?\d*)\s*(?:dollars|usd)",
    # Explicit number with context
    r"(?:price|level|mark|target)\s+(?:of\s+)?\$?([\d,]+\.?\d*)\s*k?\b",
    # "BTC $100k"
    r"(?:btc|bitcoin|eth|ethereum)\s+\$?([\d,]+\.?\d*)\s*k?\b",
]

# Direction detection
UP_KEYWORDS = ["above", "over", "exceed", "reach", "higher", "rise", "surpass", ">"]
DOWN_KEYWORDS = ["below", "under", "fall", "drop", "lower", "decline", "<"]

# Date patterns for time monotonicity
DATE_PATTERNS = [
    r"(?:by|before|end of)\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s*(\d{4})?",
    r"(?:by|before)\s+(q[1-4])\s*(\d{4})?",
    r"(?:by|before)\s+(\d{1,2})/(\d{1,2})/(\d{2,4})",
]

MONTH_ORDER = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "q1": 3, "q2": 6, "q3": 9, "q4": 12,
}


class MarketGroup:
    """A group of related markets with the same underlying."""

    __slots__ = ("asset", "direction", "family_key", "markets")

    def __init__(self, asset: str, direction: str, family_key: str) -> None:
        self.asset = asset
        self.direction = direction  # "above" or "below"
        self.family_key = family_key
        # List of (threshold_value, MarketData) sorted by threshold
        self.markets: List[Tuple[float, MarketData]] = []

    def add(self, threshold: float, md: MarketData) -> None:
        self.markets.append((threshold, md))
        self.markets.sort(key=lambda x: x[0])

    def __len__(self) -> int:
        return len(self.markets)


class CombinatorialArbStrategy(BaseStrategy):
    """
    Combinatorial arbitrage across logically related markets.

    Finds threshold monotonicity violations and trades both sides.

    Config options:
        - combo_min_edge_cents: Min mispricing in cents (default: 3)
        - combo_cooldown: Seconds between signals per pair (default: 300)
        - order_size_usd: Trade size (default: from config)
    """

    name = "combinatorial_arb"
    description = "Exploit one-sided logical monotonicity violations across related markets"
    version = "1.0.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)

        self.min_edge_cents = int(self.config.get("combo_min_edge_cents", COMBO_MIN_EDGE_CENTS))
        self.cooldown = float(self.config.get("combo_cooldown", COMBO_COOLDOWN))
        self.edge_size_factor = float(
            self.config.get("combo_edge_size_factor", COMBO_EDGE_SIZE_FACTOR)
        )
        self.edge_size_cap = float(self.config.get("combo_edge_size_cap", COMBO_EDGE_SIZE_CAP))
        self.min_confidence = float(self.config.get("combo_min_confidence", COMBO_MIN_CONFIDENCE))
        self.order_size = float(self.config.get("order_size_usd", ORDER_SIZE_USD))
        # Cooldown: "token_a|token_b" -> last signal time
        self._pair_cooldowns: Dict[str, float] = {}

        # Cache parsed market info
        self._parsed_cache: Dict[str, Optional[Tuple[str, str, float]]] = {}

        cprint(f"   🧩 Combinatorial: min_edge={self.min_edge_cents}¢, "
               f"cooldown={self.cooldown}s", "white")

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        """Find logical pricing violations across related markets."""
        signals: List[Signal] = []
        now = time.time()

        # Step 1: Parse and group markets by underlying asset + event/expiry family
        groups: Dict[str, MarketGroup] = {}
        parsed_markets = 0
        valid_families = 0
        violations_found = 0
        block_reason = "no valid families"

        for md in market_data:
            if not self._eligible_market(md):
                if not md.has_real_quotes:
                    block_reason = "missing real quotes"
                elif not md.accepting_orders:
                    block_reason = "market not accepting orders"
                elif md.is_resolved:
                    block_reason = "market resolved"
                elif str(md.outcome).upper() != "YES":
                    block_reason = "non-YES outcome skipped"
                continue
            parsed = self._parse_market(md)
            if not parsed:
                block_reason = "unsupported question type"
                continue

            asset, direction, threshold = parsed
            parsed_markets += 1
            family_key = self._market_family_key(md, asset, direction)
            key = f"{asset}|{direction}|{family_key}"

            if key not in groups:
                groups[key] = MarketGroup(asset, direction, family_key)
            groups[key].add(threshold, md)

        # Step 2: Check monotonicity within each group
        for key, group in groups.items():
            if len(group) < 2:
                continue
            valid_families += 1

            violations = self._find_violations(group)
            violations_found += len(violations)

            for violation in violations:
                v_signals = self._create_signals(violation, group, now)
                signals.extend(v_signals)
                self.signals_generated += len(v_signals)

        # Status for TUI (always visible)
        if signals:
            top_edge = max((signal.metadata.get("edge_cents", 0) for signal in signals), default=0)
            self._last_scan_status = (
                f"{len(signals)} live | {valid_families} fam | {violations_found} vio | top {top_edge}c"
            )
        else:
            self._last_scan_status = (
                f"{parsed_markets} parsed | {valid_families} fam | {violations_found} vio | {block_reason}"
            )

        # Periodic diagnostic (throttled to avoid log spam)
        if not hasattr(self, "_last_combo_diag") or now - self._last_combo_diag > 300:
            self._last_combo_diag = now
            cprint(
                f"   🧩 Combo arb: {parsed_markets} parsed, {len(groups)} groups, "
                f"{valid_families} valid families (violations: {violations_found})",
                "cyan",
            )

        return signals

    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        """Execute combinatorial arb signals."""
        results: List[Dict] = []

        for sig in signals:
            if sig.price <= 0 or sig.price >= 1 or sig.size <= 0:
                continue

            result = order_manager.place_limit_order(
                token_id=sig.token_id,
                side=sig.signal_type.value.upper(),
                price=sig.price,
                size=sig.size,
                market_slug=sig.market_slug,
                metadata={
                    "strategy": self.name,
                    "violation_type": sig.metadata.get("violation_type"),
                    "edge_cents": sig.metadata.get("edge_cents"),
                    "pair_market": sig.metadata.get("pair_market"),
                    "confidence": sig.confidence,
                    "entry_price": sig.price,
                },
            )
            results.append(result)

            if result.get("success"):
                self.trades_executed += 1
                cprint(
                    f"   🧩 Combo arb: {sig.signal_type.value.upper()} "
                    f"${sig.size:.2f} @ ${sig.price:.3f} | "
                    f"edge={sig.metadata.get('edge_cents', 0)}¢",
                    "magenta",
                )

        return results

    # ------------------------------------------------------------------
    # Market parsing
    # ------------------------------------------------------------------

    def _parse_market(self, md: MarketData) -> Optional[Tuple[str, str, float]]:
        """
        Extract (asset, direction, threshold) from a market question.

        Returns None if the market can't be parsed into a threshold-based question.
        """
        cache_key = md.token_id or md.market_slug or ""
        if cache_key in self._parsed_cache:
            return self._parsed_cache[cache_key]

        question = (md.question or md.market_slug or "").lower()

        # Detect asset
        asset = None
        for asset_name, keywords in {
            "btc": ["bitcoin", "btc"],
            "eth": ["ethereum", "eth"],
            "sol": ["solana", "sol"],
            "xrp": ["xrp", "ripple"],
            "doge": ["doge", "dogecoin"],
        }.items():
            if any(kw in question for kw in keywords):
                asset = asset_name
                break

        if not asset:
            self._parsed_cache[cache_key] = None
            return None

        # Detect direction
        direction = None
        for kw in UP_KEYWORDS:
            if kw in question:
                direction = "above"
                break
        if not direction:
            for kw in DOWN_KEYWORDS:
                if kw in question:
                    direction = "below"
                    break

        if not direction:
            self._parsed_cache[cache_key] = None
            return None

        # Extract threshold value
        threshold = self._extract_threshold(question)
        if threshold is None:
            self._parsed_cache[cache_key] = None
            return None

        result = (asset, direction, threshold)
        self._parsed_cache[cache_key] = result
        return result

    def _extract_threshold(self, question: str) -> Optional[float]:
        """Extract numeric threshold from question text."""
        fallback = re.search(r"\$([\d,]+(?:\.\d+)?)", question, re.IGNORECASE)
        if fallback:
            try:
                return float(fallback.group(1).replace(",", ""))
            except ValueError:
                pass
        for pattern in THRESHOLD_PATTERNS:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                try:
                    value_str = match.group(1).replace(",", "")
                    value = float(value_str)

                    # Handle "k" suffix (e.g., "100k" -> 100000)
                    remaining = question[match.end():]
                    if remaining.startswith("k") or "k" in match.group(0).lower():
                        if value < 10000:  # Only multiply if not already large
                            value *= 1000

                    return value
                except (ValueError, IndexError):
                    continue
        return None

    # ------------------------------------------------------------------
    # Violation detection
    # ------------------------------------------------------------------

    def _find_violations(self, group: MarketGroup) -> List[Dict]:
        """
        Check monotonicity constraint within a group.

        For "above" direction: P(X > 100k) <= P(X > 90k) <= P(X > 80k)
        A violation is when a higher threshold has a higher price than a lower one.

        Checks ALL pairs (not just adjacent) to find the maximum edge in a chain
        (Kroer et al. 2016: profit ∝ distance to coherence).
        """
        violations = []
        markets = group.markets  # Already sorted by threshold ascending

        for i in range(len(markets)):
            for j in range(i + 1, len(markets)):
                # i = lower threshold, j = higher threshold (for "above")
                lower_threshold, lower_md = markets[i]
                higher_threshold, higher_md = markets[j]

                lower_price = lower_md.best_bid if lower_md.best_bid else (lower_md.last_price or 0)
                higher_price = higher_md.best_bid if higher_md.best_bid else (higher_md.last_price or 0)

                if lower_price <= 0 or higher_price <= 0:
                    continue

                if group.direction == "above":
                    # P(X > higher) should be <= P(X > lower)
                    # Violation: P(X > higher) > P(X > lower)
                    if higher_price > lower_price:
                        edge = higher_price - lower_price
                        edge_cents = int(edge * 100)

                        if edge_cents >= self.min_edge_cents:
                            violations.append({
                                "type": "monotonicity",
                                "overpriced": (higher_threshold, higher_md, higher_price),
                                "underpriced": (lower_threshold, lower_md, lower_price),
                                "edge": edge,
                                "edge_cents": edge_cents,
                                "direction": group.direction,
                            })

                elif group.direction == "below":
                    # P(X < lower) should be <= P(X < higher)
                    # Violation: P(X < lower) > P(X < higher)
                    if lower_price > higher_price:
                        edge = lower_price - higher_price
                        edge_cents = int(edge * 100)

                        if edge_cents >= self.min_edge_cents:
                            violations.append({
                                "type": "monotonicity",
                                "overpriced": (lower_threshold, lower_md, lower_price),
                                "underpriced": (higher_threshold, higher_md, higher_price),
                                "edge": edge,
                                "edge_cents": edge_cents,
                                "direction": group.direction,
                            })

        # Keep only the violation with max edge per underpriced token (we buy underpriced)
        seen_under: Dict[str, Dict] = {}
        for v in violations:
            under_token = v["underpriced"][1].token_id or ""
            if under_token not in seen_under or v["edge_cents"] > seen_under[under_token]["edge_cents"]:
                seen_under[under_token] = v
        return list(seen_under.values())

    def _create_signals(self, violation: Dict, group: MarketGroup, now: float) -> List[Signal]:
        """Create buy/sell signals from a monotonicity violation."""
        signals = []

        over_thresh, over_md, over_price = violation["overpriced"]
        under_thresh, under_md, under_price = violation["underpriced"]

        # Cooldown check
        pair_key = f"{over_md.token_id}|{under_md.token_id}"
        last = self._pair_cooldowns.get(pair_key, 0)
        if now - last < self.cooldown:
            return signals

        edge_cents = violation["edge_cents"]
        confidence = min(0.90, 0.55 + (edge_cents / 100) * 2)

        if confidence < self.min_confidence:
            return signals

        cprint(
            f"   🧩 VIOLATION: {group.asset.upper()} {group.direction} "
            f"${over_thresh:,.0f}={over_price:.3f} > "
            f"${under_thresh:,.0f}={under_price:.3f} "
            f"edge={edge_cents}¢",
            "magenta",
            attrs=["bold"],
        )

        # Signal 1: Buy the underpriced market (should be more expensive)
        if COMBO_MIN_BUY_PRICE < under_price < COMBO_MAX_BUY_PRICE:
            # Scale size by edge (Kroer et al. 2016: profit ∝ distance to coherence)
            size_mult = 1.0 + (edge_cents / 100.0) * self.edge_size_factor
            size_mult = min(size_mult, self.edge_size_cap)
            effective_size = self.order_size * size_mult
            size_shares = effective_size / under_price if under_price > 0 else 0
            if size_shares >= 0.1:
                signals.append(Signal(
                    signal_type=SignalType.BUY,
                    token_id=under_md.token_id or "",
                    market_slug=under_md.market_slug or "",
                    side=under_md.outcome or "YES",
                    price=under_price,
                    size=round(size_shares, 2),
                    confidence=confidence,
                    reason=(
                        f"Combo monotonicity: {group.asset} {group.direction} ${under_thresh:,.0f} "
                        f"underpriced vs ${over_thresh:,.0f}"
                    ),
                    metadata={
                        "strategy": self.name,
                        "violation_type": "monotonicity_buy_underpriced",
                        "edge_cents": edge_cents,
                        "threshold": under_thresh,
                        "pair_market": over_md.market_slug or "",
                        "asset": group.asset,
                        "family_key": group.family_key,
                    },
                ))

        self._pair_cooldowns[pair_key] = now
        return signals

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_state(self) -> Dict:
        """Return strategy state."""
        state = super().get_state()
        state["parsed_markets"] = len(self._parsed_cache)
        state["valid_parsed"] = sum(1 for v in self._parsed_cache.values() if v is not None)
        state["active_cooldowns"] = len(self._pair_cooldowns)
        state["status"] = getattr(self, "_last_scan_status", "—")
        return state

    @staticmethod
    def _eligible_market(md: MarketData) -> bool:
        return (
            md.has_real_quotes
            and md.accepting_orders
            and not md.is_resolved
            and str(md.outcome).upper() == "YES"
        )

    @staticmethod
    def _market_family_key(md: MarketData, asset: str, direction: str) -> str:
        if md.event_slug:
            return f"{md.event_slug}|{direction}"
        if md.event_title:
            return f"{md.event_title.lower()}|{direction}"
        if md.end_date_ts:
            bucket = int(float(md.end_date_ts) // 3600)
            return f"{asset}|{bucket}|{direction}"
        return f"{asset}|{md.market_slug}|{direction}"
