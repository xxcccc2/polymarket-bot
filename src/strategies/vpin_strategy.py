"""
VPIN (Volume-Synchronized Probability of Informed Trading) Strategy

Detects when informed traders are active by measuring buy/sell volume
imbalance across time buckets.

Core thesis: On Polymarket, sudden spikes in directional volume signal
that someone with information is positioning. Follow that flow.

How it works:
    1. Collect recent trades from Polymarket REST API
    2. Bucket them into fixed time windows
    3. Classify trades as buyer- or seller-initiated (tick rule)
    4. Compute VPIN = |buy_vol - sell_vol| / total_vol per bucket
    5. When VPIN exceeds threshold → informed trading detected
    6. Trade in the direction of the informed flow

References:
    - Easley, López de Prado & O'Hara (2012): VPIN metric
    - Polymarket on-chain: 25% of volume is wash trading → filter it
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional
from datetime import datetime

from .base_strategy import BaseStrategy, Signal, SignalType, MarketData
from ..logging_utils import cprint
from ..config import (
    ORDER_SIZE_USD,
    MAX_POSITION_USD,
    TRADING_FEE_RATE,
)


# ----- VPIN Configuration Defaults -----

# Number of time buckets to keep per market
VPIN_BUCKET_COUNT = 20

# Seconds per bucket
VPIN_BUCKET_SECONDS = 15

# VPIN threshold to trigger a signal (0.0 - 1.0)
# Higher = more selective. 0.60 means 80% of volume is one-directional.
VPIN_THRESHOLD = 0.60

# Minimum total volume in a bucket window to be meaningful (USD)
VPIN_MIN_VOLUME_USD = 50.0

# Minimum trades in window to avoid noise
VPIN_MIN_TRADES = 5

# Cooldown per market after a signal (seconds)
VPIN_COOLDOWN_SECONDS = 120

# Confidence floor
VPIN_MIN_CONFIDENCE = 0.55

# Maximum price to buy (avoid buying at 95¢+)
VPIN_MAX_BUY_PRICE = 0.90

# Minimum price to buy (avoid dust)
VPIN_MIN_BUY_PRICE = 0.05


class TradeBucket:
    """Accumulates buy/sell volume over a time window."""

    __slots__ = ("start_time", "buy_volume", "sell_volume", "trade_count",
                 "last_price", "first_price")

    def __init__(self, start_time: float) -> None:
        self.start_time = start_time
        self.buy_volume = 0.0
        self.sell_volume = 0.0
        self.trade_count = 0
        self.last_price = 0.0
        self.first_price = 0.0

    @property
    def total_volume(self) -> float:
        return self.buy_volume + self.sell_volume

    @property
    def vpin(self) -> float:
        """Volume-synchronized probability of informed trading."""
        total = self.total_volume
        if total <= 0:
            return 0.0
        return abs(self.buy_volume - self.sell_volume) / total

    @property
    def direction(self) -> str:
        """Which side has more volume: BUY or SELL."""
        if self.buy_volume > self.sell_volume:
            return "BUY"
        elif self.sell_volume > self.buy_volume:
            return "SELL"
        return "NEUTRAL"

    @property
    def net_flow(self) -> float:
        """Signed flow: positive = buy pressure, negative = sell pressure."""
        return self.buy_volume - self.sell_volume


class VPINStrategy(BaseStrategy):
    """
    Volume-Synchronized Probability of Informed Trading.

    Detects informed flow on Polymarket markets and trades in the
    direction of the smart money.

    Config options:
        - vpin_threshold: VPIN level to trigger (default: 0.60)
        - vpin_bucket_seconds: Time per bucket (default: 15)
        - vpin_bucket_count: Buckets to keep (default: 20)
        - vpin_min_volume: Min USD volume per window (default: 50)
        - vpin_cooldown: Seconds between signals per market (default: 120)
        - order_size_usd: Trade size (default: from config)
    """

    name = "vpin"
    description = "VPIN: detect and follow informed trader flow"
    version = "1.0.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)

        # Parameters
        self.threshold = float(self.config.get("vpin_threshold", VPIN_THRESHOLD))
        self.bucket_seconds = int(self.config.get("vpin_bucket_seconds", VPIN_BUCKET_SECONDS))
        self.bucket_count = int(self.config.get("vpin_bucket_count", VPIN_BUCKET_COUNT))
        self.min_volume = float(self.config.get("vpin_min_volume", VPIN_MIN_VOLUME_USD))
        self.min_trades = int(self.config.get("vpin_min_trades", VPIN_MIN_TRADES))
        self.cooldown = float(self.config.get("vpin_cooldown", VPIN_COOLDOWN_SECONDS))
        self.order_size = float(self.config.get("order_size_usd", ORDER_SIZE_USD))
        self.max_position = float(self.config.get("max_position_usd", MAX_POSITION_USD))
        self.min_confidence = float(self.config.get("vpin_min_confidence", VPIN_MIN_CONFIDENCE))

        # State: token_id -> deque of TradeBuckets
        self._buckets: Dict[str, deque] = defaultdict(lambda: deque(maxlen=self.bucket_count))

        # Cooldown tracking: token_id -> last signal timestamp
        self._last_signal_time: Dict[str, float] = {}

        # Track processed trade IDs to avoid double-counting
        self._processed_trade_ids: set = set()

        cprint(f"   VPIN threshold={self.threshold}, bucket={self.bucket_seconds}s, "
               f"min_vol=${self.min_volume}", "white")

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        """Analyze recent trades to compute VPIN and generate signals."""
        signals: List[Signal] = []
        now = time.time()

        for md in market_data:
            token_id = md.token_id
            if not token_id:
                continue

            # Get recent trades for this token from market data
            recent_trades = getattr(md, "recent_trades", None) or []

            if recent_trades:
                self._ingest_trades(token_id, recent_trades, now)

            # Compute rolling VPIN across recent buckets
            buckets = self._buckets.get(token_id)
            if not buckets or len(buckets) < 2:
                continue

            # Aggregate across recent buckets
            total_buy = sum(b.buy_volume for b in buckets)
            total_sell = sum(b.sell_volume for b in buckets)
            total_trades = sum(b.trade_count for b in buckets)
            total_vol = total_buy + total_sell

            if total_vol < self.min_volume or total_trades < self.min_trades:
                continue

            rolling_vpin = abs(total_buy - total_sell) / total_vol

            if rolling_vpin < self.threshold:
                continue

            # Determine direction
            if total_buy > total_sell:
                direction = "BUY"
            else:
                direction = "SELL"

            # Cooldown check
            last = self._last_signal_time.get(token_id, 0)
            if now - last < self.cooldown:
                continue

            # Price checks
            price = md.best_bid if md.best_bid else (md.last_price or 0)
            if direction == "BUY":
                if price > VPIN_MAX_BUY_PRICE or price < VPIN_MIN_BUY_PRICE:
                    continue

            # Confidence scales with VPIN strength
            confidence = min(0.95, 0.5 + (rolling_vpin - self.threshold) * 2)
            if confidence < self.min_confidence:
                continue

            # Generate signal
            signal = Signal(
                strategy=self.name,
                signal_type=SignalType.BUY if direction == "BUY" else SignalType.SELL,
                token_id=token_id,
                market_slug=md.market_slug or "",
                price=price,
                size=self.order_size,
                confidence=confidence,
                metadata={
                    "vpin": round(rolling_vpin, 4),
                    "direction": direction,
                    "buy_vol": round(total_buy, 2),
                    "sell_vol": round(total_sell, 2),
                    "trade_count": total_trades,
                    "window_seconds": self.bucket_seconds * len(buckets),
                },
            )
            signals.append(signal)
            self._last_signal_time[token_id] = now

            cprint(
                f"   🐋 VPIN signal: {direction} on {md.market_slug[:40]} | "
                f"VPIN={rolling_vpin:.2f} conf={confidence:.2f} "
                f"buy=${total_buy:.0f} sell=${total_sell:.0f}",
                "magenta",
            )

        return signals

    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        """Execute VPIN signals."""
        results: List[Dict] = []

        for sig in signals:
            if not self._validate_signal(sig):
                continue

            result = order_manager.place_limit_order(
                token_id=sig.token_id,
                side=sig.signal_type.value.upper(),
                price=sig.price,
                size=sig.size,
                market_slug=sig.market_slug,
                metadata={
                    "strategy": self.name,
                    "vpin": sig.metadata.get("vpin"),
                    "direction": sig.metadata.get("direction"),
                    "confidence": sig.confidence,
                    "entry_price": sig.price,
                },
            )
            results.append(result)

            if result.get("success"):
                self.trades_executed += 1
                cprint(
                    f"   🐋 VPIN order: {sig.signal_type.value.upper()} "
                    f"${sig.size:.2f} @ ${sig.price:.3f}",
                    "magenta",
                )

        return results

    # ------------------------------------------------------------------
    # Trade ingestion
    # ------------------------------------------------------------------

    def _ingest_trades(self, token_id: str, trades: List[Dict], now: float) -> None:
        """Classify trades and add to time buckets."""
        buckets = self._buckets[token_id]

        # Get or create current bucket
        if not buckets or (now - buckets[-1].start_time) >= self.bucket_seconds:
            buckets.append(TradeBucket(now))
        current = buckets[-1]

        prev_price = current.last_price or None

        for trade in trades:
            trade_id = trade.get("id") or trade.get("trade_id")
            if trade_id and trade_id in self._processed_trade_ids:
                continue
            if trade_id:
                self._processed_trade_ids.add(trade_id)
                # Keep set manageable
                if len(self._processed_trade_ids) > 2000:
                    self._processed_trade_ids = set(list(self._processed_trade_ids)[-1000:])

            price = float(trade.get("price", 0))
            size = float(trade.get("size", 0))
            usd_vol = price * size

            if usd_vol <= 0:
                continue

            if current.first_price == 0:
                current.first_price = price

            # Tick rule classification: if price >= prev → buyer-initiated
            side = trade.get("side", "").upper()
            if side in ("BUY", "SELL"):
                is_buy = side == "BUY"
            elif prev_price is not None:
                is_buy = price >= prev_price
            else:
                is_buy = True  # default if no prior data

            if is_buy:
                current.buy_volume += usd_vol
            else:
                current.sell_volume += usd_vol

            current.trade_count += 1
            current.last_price = price
            prev_price = price

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_signal(self, signal: Signal) -> bool:
        """Basic signal validation."""
        if signal.price <= 0 or signal.price >= 1:
            return False
        if signal.size <= 0:
            return False
        if signal.confidence < self.min_confidence:
            return False
        return True

    def get_state(self) -> Dict:
        """Return strategy state for monitoring."""
        state = super().get_state()
        state["active_markets"] = len(self._buckets)
        state["cooldowns"] = len(self._last_signal_time)
        return state
