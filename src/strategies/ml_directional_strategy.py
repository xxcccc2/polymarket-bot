"""
ML-informed directional strategy for Polymarket BTC Up/Down markets.
"""

from __future__ import annotations

import re
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
    ML_DIRECTIONAL_MODEL_PATH_15M,
    ML_DIRECTIONAL_MODEL_PATH_1H,
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

    _TRAINING_TIMEFRAME_PREFIXES = ("1m", "5m", "15m", "1h", "4h", "1d")

    name = "ml_directional"
    description = "Model-driven directional edge for 15m/1h crypto Up/Down markets"
    version = "1.0.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.binance_feed = self.config.get("binance_feed")
        self.risk_manager = self.config.get("risk_manager")
        self.order_size_usd = self.config.get("order_size_usd", ORDER_SIZE_USD)
        self.default_model_path = Path(self.config.get("model_path", ML_DIRECTIONAL_MODEL_PATH))
        self.model_paths: Dict[str, Path] = {
            "15m": Path(self.config.get("model_path_15m", self.default_model_path)),
            "1h": Path(self.config.get("model_path_1h", self.default_model_path)),
        }
        if "model_path_15m" not in self.config and ML_DIRECTIONAL_MODEL_PATH_15M != ML_DIRECTIONAL_MODEL_PATH:
            self.model_paths["15m"] = ML_DIRECTIONAL_MODEL_PATH_15M
        if "model_path_1h" not in self.config and ML_DIRECTIONAL_MODEL_PATH_1H != ML_DIRECTIONAL_MODEL_PATH:
            self.model_paths["1h"] = ML_DIRECTIONAL_MODEL_PATH_1H
        self.model_path = self.default_model_path
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
        self._models_by_horizon: Dict[str, Optional[LightGBMBinaryClassifier]] = {}
        self._model_versions_by_horizon: Dict[str, str] = {}
        self._feature_columns_by_horizon: Dict[str, List[str]] = {}
        self._model_thresholds_by_horizon: Dict[str, float] = {}
        self._model_mtimes_by_horizon: Dict[str, float] = {}
        self._model_errors_by_horizon: Dict[str, Optional[str]] = {}

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
        self._horizon_signal_counts: Dict[str, int] = {"15m": 0, "1h": 0}
        self._horizon_trade_counts: Dict[str, int] = {"15m": 0, "1h": 0}
        self._horizon_last_signal_ts: Dict[str, float] = {"15m": 0.0, "1h": 0.0}

        self._load_model_if_needed(force=True)
        if self.enabled_horizons:
            self._model_bundle_for_horizon(sorted(self.enabled_horizons)[0])

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

        if not self.enabled:
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

            inference = self._infer_market(data, current_state)
            if inference is None:
                continue
            outcome_probability = float(inference["probability"])
            model_threshold = float(inference["threshold_probability"])
            model_version = str(inference["model_version"])

            threshold = max(self.min_edge, self._compute_threshold(data.mid_price))
            edge = outcome_probability - data.mid_price
            if outcome_probability < model_threshold or edge <= threshold:
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
                    "model_version": model_version,
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
            horizon = str(signal.metadata.get("horizon") or "")
            self.signals_generated += 1
            self.last_signal_time[signal.token_id] = time.time()
            if horizon in self._horizon_signal_counts:
                self._horizon_signal_counts[horizon] += 1
                self._horizon_last_signal_ts[horizon] = self.last_signal_time[signal.token_id]
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
                    horizon = str(signal.metadata.get("horizon") or "")
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
                    if horizon in self._horizon_trade_counts:
                        self._horizon_trade_counts[horizon] += 1
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
                "horizon_stats": {
                    horizon: {
                        "signals": self._horizon_signal_counts.get(horizon, 0),
                        "trades": self._horizon_trade_counts.get(horizon, 0),
                        "last_signal_ts": self._horizon_last_signal_ts.get(horizon, 0.0),
                        "status": self._model_errors_by_horizon.get(horizon) or "—",
                    }
                    for horizon in sorted(self.enabled_horizons or {"15m", "1h"})
                },
            }
        )
        return state

    def _infer_market(self, market_data: MarketData, binance_state) -> Optional[Dict[str, Any]]:
        horizon = self._extract_horizon(f"{market_data.question} {market_data.market_slug}".lower()) or "15m"
        model, feature_columns, threshold_probability, model_version = self._model_bundle_for_horizon(horizon)
        if model is None:
            return None
        feature_row = self._build_inference_feature_row(
            market_data,
            binance_state,
            feature_columns=feature_columns,
        )
        if not feature_row:
            return None
        frame = align_feature_row(feature_row, feature_columns)
        probability_up = float(model.predict_positive_proba(frame).iloc[0])
        self._last_inference_ts = time.time()

        outcome = (market_data.outcome or "").lower()
        if outcome == "up":
            probability = probability_up
        elif outcome == "down":
            probability = 1.0 - probability_up
        else:
            return None
        return {
            "probability": probability,
            "threshold_probability": threshold_probability,
            "model_version": model_version,
        }

    def _build_inference_feature_row(
        self,
        market_data: MarketData,
        binance_state,
        *,
        feature_columns: Optional[List[str]] = None,
    ) -> Optional[Dict[str, float]]:
        selected_feature_columns = feature_columns or self.feature_columns
        runtime_row = self.feature_builder.build_runtime_feature_row(
            market_data=market_data,
            binance_state=binance_state,
            now_ts=time.time(),
        )
        if not self._uses_training_schema_features(selected_feature_columns):
            return runtime_row
        if any(column.startswith("micro_") for column in selected_feature_columns):
            self.model_error = "Loaded artifact requires live microstructure parity for micro_* columns"
            return None
        if not self.binance_feed or not hasattr(self.binance_feed, "get_recent_ohlcv"):
            self.model_error = "Binance feed does not support recent OHLC retrieval for training-schema inference"
            return None

        horizon = self._extract_horizon(f"{market_data.question} {market_data.market_slug}".lower())
        target_timeframe = horizon or "15m"
        required_timeframes = self._required_training_timeframes(target_timeframe, selected_feature_columns)
        frames = {}
        for timeframe in required_timeframes:
            frame = self.binance_feed.get_recent_ohlcv("btc", timeframe=timeframe, limit=128)
            if timeframe == target_timeframe and frame.empty:
                self.model_error = f"No OHLC history available for target timeframe {target_timeframe}"
                return None
            if not frame.empty:
                frames[timeframe] = frame

        training_row = self.feature_builder.build_training_schema_runtime_row(
            frames,
            target_timeframe=target_timeframe,
        )
        if not training_row:
            self.model_error = "Unable to construct training-schema feature row from live OHLC history"
            return None
        self.model_error = None
        return {**runtime_row, **training_row}

    def _uses_training_schema_features(self, feature_columns: Optional[List[str]] = None) -> bool:
        columns = feature_columns or self.feature_columns
        for column in columns:
            if column in {"hour_sin", "hour_cos", "weekday_sin", "weekday_cos"}:
                return True
            if any(column.startswith(f"{prefix}_") for prefix in self._TRAINING_TIMEFRAME_PREFIXES):
                return True
        return False

    def _required_training_timeframes(
        self,
        target_timeframe: str,
        feature_columns: Optional[List[str]] = None,
    ) -> List[str]:
        columns = feature_columns or self.feature_columns
        ordered = []
        for timeframe in self._TRAINING_TIMEFRAME_PREFIXES:
            if timeframe == target_timeframe or any(
                column.startswith(f"{timeframe}_") for column in columns
            ):
                ordered.append(timeframe)
        if target_timeframe not in ordered:
            ordered.insert(0, target_timeframe)
        return ordered

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

    def _model_bundle_for_horizon(
        self,
        horizon: str,
    ) -> tuple[Optional[LightGBMBinaryClassifier], List[str], float, str]:
        self._load_model_if_needed(horizon=horizon)
        model = self._models_by_horizon.get(horizon)
        self.model = model
        self.model_path = self._model_path_for_horizon(horizon)
        self.feature_columns = list(
            self._feature_columns_by_horizon.get(horizon, list(DEFAULT_RUNTIME_FEATURE_COLUMNS))
        )
        self.model_threshold = float(self._model_thresholds_by_horizon.get(horizon, self.min_probability))
        self.model_version = self._model_versions_by_horizon.get(horizon, "unloaded")
        self.model_error = self._model_errors_by_horizon.get(horizon)
        return self.model, self.feature_columns, self.model_threshold, self.model_version

    def _model_path_for_horizon(self, horizon: Optional[str]) -> Path:
        if horizon and horizon in self.model_paths:
            return self.model_paths[horizon]
        return self.default_model_path

    def _load_model_if_needed(self, *, horizon: Optional[str] = None, force: bool = False) -> None:
        horizons = [horizon] if horizon else sorted(self.enabled_horizons or {"15m", "1h"})
        for current_horizon in horizons:
            model_path = self._model_path_for_horizon(current_horizon)
            if not model_path.exists():
                self._models_by_horizon[current_horizon] = None
                self._model_errors_by_horizon[current_horizon] = f"Model artifact not found: {model_path}"
                continue

            mtime = model_path.stat().st_mtime
            if (
                not force
                and current_horizon in self._models_by_horizon
                and self._models_by_horizon.get(current_horizon) is not None
                and mtime == self._model_mtimes_by_horizon.get(current_horizon)
            ):
                continue

            try:
                model = LightGBMBinaryClassifier.load(model_path)
                self._models_by_horizon[current_horizon] = model
                self._model_mtimes_by_horizon[current_horizon] = mtime
                self._model_errors_by_horizon[current_horizon] = None
                if model.artifact:
                    self._model_versions_by_horizon[current_horizon] = model.artifact.version
                    self._feature_columns_by_horizon[current_horizon] = list(model.artifact.feature_columns)
                    self._model_thresholds_by_horizon[current_horizon] = float(model.artifact.threshold_probability)
                else:
                    self._model_versions_by_horizon[current_horizon] = "legacy"
                    self._feature_columns_by_horizon[current_horizon] = list(DEFAULT_RUNTIME_FEATURE_COLUMNS)
                    self._model_thresholds_by_horizon[current_horizon] = self.min_probability
            except Exception as exc:
                self._models_by_horizon[current_horizon] = None
                self._model_errors_by_horizon[current_horizon] = str(exc)
                cprint(f"⚠️  Failed to load ML artifact for {current_horizon}: {exc}", "yellow")

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
        if (
            ("updown" in lowered or "up or down" in lowered)
            and not any(token in lowered for token in ("5m", "15m", "4h", "5 min", "15 min", "4 hour", "4-hour"))
            and (
                re.search(
                    r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*-\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
                    lowered,
                )
                or re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\s*et\b", lowered)
                or re.search(r"-\d{1,2}(?:am|pm)-et\b", lowered)
            )
        ):
            return "1h"
        return None
