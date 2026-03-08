"""
ML-informed directional strategy for Polymarket BTC Up/Down markets.
"""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from .base_strategy import BaseStrategy, MarketData, Signal, SignalType
from ..config import (
    ML_DIRECTIONAL_BRIER_WINDOW,
    ML_DIRECTIONAL_ENABLED,
    ML_DIRECTIONAL_ENABLED_HORIZONS,
    ML_DIRECTIONAL_FEED_STALE_SECONDS,
    ML_DIRECTIONAL_HARD_LOSS_STREAK,
    ML_DIRECTIONAL_HARD_PAUSE_SECONDS,
    ML_DIRECTIONAL_MAKER_OFFSET,
    ML_DIRECTIONAL_MAX_ROLLING_BRIER,
    ML_DIRECTIONAL_MAX_SIGNALS_PER_CYCLE,
    ML_DIRECTIONAL_MIN_EDGE,
    ML_DIRECTIONAL_MIN_PROBABILITY,
    ML_DIRECTIONAL_MIN_ROLLING_ACCURACY,
    ML_DIRECTIONAL_MODEL_PATH,
    ML_DIRECTIONAL_ONLY_CRYPTO,
    ML_DIRECTIONAL_ROLLING_ACCURACY_WINDOW,
    ML_DIRECTIONAL_SIGNAL_COOLDOWN_SECONDS,
    ML_DIRECTIONAL_SOFT_LOSS_STREAK,
    ML_DIRECTIONAL_SOFT_PAUSE_SECONDS,
    ORDER_SIZE_USD,
    PAPER_BALANCE_USD,
    PAPER_TRADING,
)
from ..logging_utils import cprint
from ..ml.features import DEFAULT_RUNTIME_FEATURE_COLUMNS, FeatureBuilder, align_feature_row
from ..ml.model import LightGBMBinaryClassifier
from ..sizing.kelly import kelly_size


class MLDirectionalStrategy(BaseStrategy):
    """Use a trained classifier to trade 15m and 1h BTC Up/Down markets."""

    name = "ml_directional"
    description = "Model-driven directional edge for 15m/1h crypto Up/Down markets"
    version = "1.0.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.binance_feed = self.config.get("binance_feed")
        self.risk_manager = self.config.get("risk_manager")
        self.order_size_usd = self.config.get("order_size_usd", ORDER_SIZE_USD)
        self.model_path = Path(self.config.get("model_path", ML_DIRECTIONAL_MODEL_PATH))
        self.enabled = self.config.get("enabled", ML_DIRECTIONAL_ENABLED)
        self.enabled_horizons = {
            value.lower() for value in self.config.get("enabled_horizons", ML_DIRECTIONAL_ENABLED_HORIZONS)
        }
        self.min_probability = float(self.config.get("min_probability", ML_DIRECTIONAL_MIN_PROBABILITY))
        self.min_edge = float(self.config.get("min_edge", ML_DIRECTIONAL_MIN_EDGE))
        self.maker_offset = float(self.config.get("maker_offset", ML_DIRECTIONAL_MAKER_OFFSET))
        self.signal_cooldown_s = int(self.config.get("signal_cooldown_s", ML_DIRECTIONAL_SIGNAL_COOLDOWN_SECONDS))
        self.max_signals_per_cycle = int(self.config.get("max_signals_per_cycle", ML_DIRECTIONAL_MAX_SIGNALS_PER_CYCLE))
        self.feed_stale_seconds = int(self.config.get("feed_stale_seconds", ML_DIRECTIONAL_FEED_STALE_SECONDS))
        self.only_crypto = bool(self.config.get("only_crypto", ML_DIRECTIONAL_ONLY_CRYPTO))
        self.rolling_accuracy_window = int(
            self.config.get("rolling_accuracy_window", ML_DIRECTIONAL_ROLLING_ACCURACY_WINDOW)
        )
        self.min_rolling_accuracy = float(
            self.config.get("min_rolling_accuracy", ML_DIRECTIONAL_MIN_ROLLING_ACCURACY)
        )
        self.brier_window = int(self.config.get("brier_window", ML_DIRECTIONAL_BRIER_WINDOW))
        self.max_rolling_brier = float(
            self.config.get("max_rolling_brier", ML_DIRECTIONAL_MAX_ROLLING_BRIER)
        )
        self.soft_loss_streak = int(self.config.get("soft_loss_streak", ML_DIRECTIONAL_SOFT_LOSS_STREAK))
        self.soft_pause_seconds = int(self.config.get("soft_pause_seconds", ML_DIRECTIONAL_SOFT_PAUSE_SECONDS))
        self.hard_loss_streak = int(self.config.get("hard_loss_streak", ML_DIRECTIONAL_HARD_LOSS_STREAK))
        self.hard_pause_seconds = int(self.config.get("hard_pause_seconds", ML_DIRECTIONAL_HARD_PAUSE_SECONDS))

        self.feature_builder = FeatureBuilder()
        self.model: Optional[LightGBMBinaryClassifier] = None
        self.model_version = "unloaded"
        self.feature_columns = list(DEFAULT_RUNTIME_FEATURE_COLUMNS)
        self.model_threshold = self.min_probability
        self.model_error: Optional[str] = None
        self._model_mtime: float = 0.0

        self.last_signal_time: Dict[str, float] = {}
        self.positions: Dict[str, float] = {}
        self.active_condition_ids: set[str] = set()
        self._order_context: Dict[str, Dict[str, Any]] = {}
        self._pending_resolutions: Dict[str, Dict[str, Any]] = {}
        self._resolved_accuracy: Deque[float] = deque(maxlen=self.rolling_accuracy_window)
        self._resolved_brier: Deque[float] = deque(maxlen=self.brier_window)
        self._fill_outcomes: Deque[bool] = deque(maxlen=50)
        self._consecutive_losses = 0
        self._paused_until = 0.0
        self._halt_reason: Optional[str] = None
        self._last_inference_ts = 0.0

        self._load_model_if_needed(force=True)

    def should_trade_market(self, market_data: MarketData) -> bool:
        if not self.enabled:
            return False
        if self._halt_reason:
            return False
        if time.time() < self._paused_until:
            return False

        text = f"{market_data.question} {market_data.market_slug}".lower()
        if self.only_crypto and not any(token in text for token in ("bitcoin", "btc")):
            return False
        if "up or down" not in text:
            return False

        horizon = self._extract_horizon(text)
        if not horizon or horizon not in self.enabled_horizons:
            return False

        last_t = self.last_signal_time.get(market_data.token_id, 0.0)
        if time.time() - last_t < self.signal_cooldown_s:
            return False
        return True

    def analyze(self, market_data: List[MarketData]) -> List[Signal]:
        self._load_model_if_needed()
        self._update_resolution_tracking(market_data)
        signals: list[Signal] = []
        seen_condition_ids: set[str] = set()

        if not self.enabled or self.model is None or self.model_error:
            return signals
        if not self.binance_feed:
            return signals

        current_state = self.binance_feed.get_state("btc")
        if not current_state.connected:
            self._halt_reason = "Binance feed disconnected"
            return signals
        if current_state.last_update and (time.time() - current_state.last_update) > self.feed_stale_seconds:
            self._halt_reason = "Binance feed stale"
            return signals
        if self._halt_reason == "Binance feed disconnected" or self._halt_reason == "Binance feed stale":
            self._halt_reason = None

        for data in market_data:
            if not self.should_trade_market(data):
                continue
            if data.condition_id in self.active_condition_ids or data.condition_id in seen_condition_ids:
                continue

            outcome_probability = self._probability_for_market(data, current_state)
            if outcome_probability is None:
                continue

            threshold = max(self.min_edge, self._compute_threshold(data.mid_price))
            edge = outcome_probability - data.mid_price
            if outcome_probability < self.model_threshold or edge <= threshold:
                continue

            bet_size_usd = self._size_bet(outcome_probability, data.mid_price, data.token_id)
            if bet_size_usd <= 0:
                continue

            buy_price = min(data.best_bid + self.maker_offset, data.best_ask - 0.001)
            buy_price = round(max(0.01, min(0.99, buy_price)), 3)
            shares = round(max(0.0, bet_size_usd / buy_price), 2)
            if shares <= 0:
                continue

            horizon = self._extract_horizon(f"{data.question} {data.market_slug}".lower())
            signal = Signal(
                signal_type=SignalType.BUY,
                token_id=data.token_id,
                market_slug=data.market_slug,
                side=data.outcome,
                price=buy_price,
                size=shares,
                confidence=round(min(max(outcome_probability, 0.5), 0.99), 3),
                reason=(
                    f"ML directional {horizon}: prob={outcome_probability:.3f} "
                    f"mkt={data.mid_price:.3f} edge={edge*100:.1f}c"
                ),
                metadata={
                    "strategy": self.name,
                    "predicted_prob": round(outcome_probability, 6),
                    "edge": round(edge, 6),
                    "threshold": round(threshold, 6),
                    "horizon": horizon,
                    "model_version": self.model_version,
                    "condition_id": data.condition_id,
                    "end_date_ts": data.end_date_ts,
                },
            )
            signals.append(signal)
            seen_condition_ids.add(data.condition_id)

        if len(signals) > self.max_signals_per_cycle:
            signals.sort(key=lambda item: item.metadata.get("edge", 0.0), reverse=True)
            signals = signals[: self.max_signals_per_cycle]

        for signal in signals:
            self.signals_generated += 1
            self.last_signal_time[signal.token_id] = time.time()
        return signals

    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        results = []
        for signal in signals:
            if signal.signal_type != SignalType.BUY:
                continue
            try:
                result = order_manager.place_limit_order(
                    token_id=signal.token_id,
                    side="BUY",
                    price=signal.price,
                    size=signal.size,
                    order_type="GTC",
                    market_slug=signal.market_slug,
                    metadata={"strategy": self.name, **signal.metadata},
                )
                if result.get("success"):
                    order = result.get("order")
                    condition_id = signal.metadata.get("condition_id")
                    if condition_id:
                        self.active_condition_ids.add(condition_id)
                    if order is not None:
                        self._order_context[order.order_id] = {
                            "token_id": signal.token_id,
                            "condition_id": signal.metadata.get("condition_id"),
                            "predicted_prob": signal.metadata.get("predicted_prob"),
                            "end_date_ts": signal.metadata.get("end_date_ts"),
                            "side": signal.side,
                            "market_slug": signal.market_slug,
                        }
                    cprint(f"  ✅ ML directional order placed: {result.get('order_id')}", "green")
                else:
                    cprint(f"  ❌ ML directional order failed: {result.get('error')}", "red")
                results.append(result)
            except Exception as exc:
                cprint(f"  ❌ ML directional execute error: {exc}", "red")
                results.append({"success": False, "error": str(exc)})
        return results

    def on_order_filled(self, order_id: str, fill_data: Dict):
        super().on_order_filled(order_id, fill_data)
        token_id = fill_data.get("token_id", "")
        size_usd = float(fill_data.get("size", 0) or 0) * float(fill_data.get("price", 0) or 0)
        if fill_data.get("side") == "BUY":
            self.positions[token_id] = self.positions.get(token_id, 0.0) + size_usd
            context = self._order_context.get(order_id)
            if context:
                self._pending_resolutions[token_id] = {
                    **context,
                    "filled_at": time.time(),
                    "entry_price": float(fill_data.get("price", 0) or 0),
                }
        elif fill_data.get("side") == "SELL":
            self.positions[token_id] = max(0.0, self.positions.get(token_id, 0.0) - size_usd)

    def on_order_cancelled(self, order_id: str, reason: str):
        context = self._order_context.pop(order_id, None)
        if context and context.get("condition_id"):
            self.active_condition_ids.discard(context["condition_id"])

    def get_state(self) -> Dict[str, Any]:
        state = super().get_state()
        rolling_accuracy = self._rolling_accuracy()
        rolling_brier = self._rolling_brier()
        state.update(
            {
                "model_version": self.model_version,
                "model_path": str(self.model_path),
                "model_error": self.model_error,
                "last_inference_ts": self._last_inference_ts,
                "rolling_accuracy": rolling_accuracy,
                "rolling_brier": rolling_brier,
                "paused_until": self._paused_until,
                "halt_reason": self._halt_reason,
                "pending_resolutions": len(self._pending_resolutions),
                "positions": self.positions,
                "active_conditions": len(self.active_condition_ids),
            }
        )
        return state

    def _probability_for_market(self, market_data: MarketData, binance_state) -> Optional[float]:
        feature_row = self.feature_builder.build_runtime_feature_row(
            market_data=market_data,
            binance_state=binance_state,
            now_ts=time.time(),
        )
        frame = align_feature_row(feature_row, self.feature_columns)
        probability_up = float(self.model.predict_positive_proba(frame).iloc[0])
        self._last_inference_ts = time.time()

        outcome = (market_data.outcome or "").lower()
        if outcome == "up":
            return probability_up
        if outcome == "down":
            return 1.0 - probability_up
        return None

    def _compute_threshold(self, market_price: float) -> float:
        adverse_selection = 0.01
        spread_cost = 0.005
        missed_fill_penalty = 0.005
        calibration_buffer = 0.01
        return adverse_selection + spread_cost + missed_fill_penalty + calibration_buffer

    def _size_bet(self, estimated_prob: float, market_price: float, token_id: str) -> float:
        bankroll = float(self.config.get("bankroll", PAPER_BALANCE_USD if PAPER_TRADING else 1000.0))
        pos_usd = self._current_position_usd(token_id)
        inventory_q = pos_usd / market_price if market_price > 0 else 0.0
        result = kelly_size(
            estimated_prob=estimated_prob,
            market_price=market_price,
            bankroll=bankroll,
            mode="quarter",
            max_bet_usd=self.order_size_usd,
            inventory_q=inventory_q,
        )
        return result.bet_size_usd or 0.0

    def _current_position_usd(self, token_id: str) -> float:
        if self.risk_manager and hasattr(self.risk_manager, "positions"):
            position = self.risk_manager.positions.get(token_id)
            if position is not None:
                current_price = (
                    getattr(position, "current_price", 0.0)
                    or getattr(position, "avg_price", 0.0)
                    or 0.0
                )
                size = getattr(position, "size", 0.0) or 0.0
                return float(size) * float(current_price)
        return self.positions.get(token_id, 0.0)

    def _load_model_if_needed(self, force: bool = False) -> None:
        if not self.model_path.exists():
            self.model = None
            self.model_error = f"Model artifact not found: {self.model_path}"
            return

        mtime = self.model_path.stat().st_mtime
        if not force and self.model is not None and mtime == self._model_mtime:
            return

        try:
            self.model = LightGBMBinaryClassifier.load(self.model_path)
            self._model_mtime = mtime
            self.model_error = None
            if self.model.artifact:
                self.model_version = self.model.artifact.version
                self.feature_columns = list(self.model.artifact.feature_columns)
                self.model_threshold = float(self.model.artifact.threshold_probability)
            else:
                self.model_version = "legacy"
                self.feature_columns = list(DEFAULT_RUNTIME_FEATURE_COLUMNS)
                self.model_threshold = self.min_probability
        except Exception as exc:
            self.model = None
            self.model_error = str(exc)
            cprint(f"⚠️  Failed to load ML artifact: {exc}", "yellow")

    def _update_resolution_tracking(self, market_data: List[MarketData]) -> None:
        if not self._pending_resolutions:
            return

        now_ts = time.time()
        by_token = {item.token_id: item for item in market_data}
        resolved_tokens: list[str] = []

        for token_id, pending in list(self._pending_resolutions.items()):
            current = by_token.get(token_id)
            end_date_ts = pending.get("end_date_ts") or (current.end_date_ts if current else None)
            if current is None or end_date_ts is None:
                continue
            if now_ts < float(end_date_ts):
                continue

            actual = 1.0 if current.mid_price >= 0.5 else 0.0
            predicted_prob = float(pending.get("predicted_prob", 0.5) or 0.5)
            predicted_label = 1.0 if predicted_prob >= 0.5 else 0.0
            won = predicted_label == actual

            self._resolved_accuracy.append(1.0 if won else 0.0)
            self._resolved_brier.append((predicted_prob - actual) ** 2)
            self._fill_outcomes.append(won)
            if won:
                self._consecutive_losses = 0
            else:
                self._consecutive_losses += 1
            self._update_safety_state()
            resolved_tokens.append(token_id)

        for token_id in resolved_tokens:
            pending = self._pending_resolutions.pop(token_id, None)
            if pending and pending.get("condition_id"):
                self.active_condition_ids.discard(pending["condition_id"])

    def _update_safety_state(self) -> None:
        rolling_accuracy = self._rolling_accuracy()
        rolling_brier = self._rolling_brier()

        if len(self._resolved_accuracy) >= self.rolling_accuracy_window and rolling_accuracy < self.min_rolling_accuracy:
            self._halt_reason = f"Rolling accuracy {rolling_accuracy:.2%} below threshold"
        if len(self._resolved_brier) >= self.brier_window and rolling_brier > self.max_rolling_brier:
            self._halt_reason = f"Rolling Brier {rolling_brier:.3f} above threshold"

        now = time.time()
        if self._consecutive_losses >= self.hard_loss_streak:
            self._paused_until = max(self._paused_until, now + self.hard_pause_seconds)
        elif self._consecutive_losses >= self.soft_loss_streak:
            self._paused_until = max(self._paused_until, now + self.soft_pause_seconds)

    def _rolling_accuracy(self) -> float:
        if not self._resolved_accuracy:
            return 0.0
        return sum(self._resolved_accuracy) / len(self._resolved_accuracy)

    def _rolling_brier(self) -> float:
        if not self._resolved_brier:
            return 0.0
        return sum(self._resolved_brier) / len(self._resolved_brier)

    @staticmethod
    def _extract_horizon(text: str) -> Optional[str]:
        lowered = text.lower()
        if "15 min" in lowered or "15m" in lowered or "15-min" in lowered:
            return "15m"
        if "1 hour" in lowered or "1h" in lowered or "hourly" in lowered:
            return "1h"
        return None
