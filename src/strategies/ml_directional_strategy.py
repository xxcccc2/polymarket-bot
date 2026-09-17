"""
ML-informed directional strategy for Polymarket BTC Up/Down markets.
"""

from __future__ import annotations

import math
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from .base_strategy import BaseStrategy, MarketData, Signal, SignalType
from ..config import (
    KELLY_FRACTION_MODE,
    ML_DIRECTIONAL_BRIER_WINDOW,
    ML_DIRECTIONAL_ENABLED,
    ML_DIRECTIONAL_ENABLED_HORIZONS,
    ML_DIRECTIONAL_FEED_STALE_SECONDS,
    ML_DIRECTIONAL_GTD_MAX_HORIZON_SECONDS,
    ML_DIRECTIONAL_HARD_LOSS_STREAK,
    ML_DIRECTIONAL_HARD_PAUSE_SECONDS,
    ML_DIRECTIONAL_MAKER_OFFSET,
    ML_DIRECTIONAL_MAX_ROLLING_BRIER,
    ML_DIRECTIONAL_MAX_SIGNALS_PER_CYCLE,
    ML_DIRECTIONAL_MIN_EDGE,
    ML_DIRECTIONAL_KELLY_ENABLED,
    ML_DIRECTIONAL_MIN_PROBABILITY,
    ML_DIRECTIONAL_MIN_ROLLING_ACCURACY,
    ML_DIRECTIONAL_MODEL_PATH,
    ML_DIRECTIONAL_MODEL_PATH_15M,
    ML_DIRECTIONAL_MODEL_PATH_1H,
    ML_DIRECTIONAL_ONLY_CRYPTO,
    ML_DIRECTIONAL_POST_ONLY,
    ML_DIRECTIONAL_POST_ONLY_BUFFER_TICKS,
    ML_DIRECTIONAL_ROLLING_ACCURACY_WINDOW,
    clob_gtd_expiration_unix,
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
        self.kelly_mode = str(self.config.get("kelly_fraction_mode", KELLY_FRACTION_MODE) or KELLY_FRACTION_MODE)
        self.kelly_enabled = bool(self.config.get("kelly_enabled", ML_DIRECTIONAL_KELLY_ENABLED))
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
        self._explicit_min_probability_override = "ML_DIRECTIONAL_MIN_PROBABILITY" in os.environ
        self.min_edge = float(self.config.get("min_edge", ML_DIRECTIONAL_MIN_EDGE))
        self.maker_offset = float(self.config.get("maker_offset", ML_DIRECTIONAL_MAKER_OFFSET))
        self.post_only = bool(self.config.get("post_only", ML_DIRECTIONAL_POST_ONLY))
        self.post_only_buffer_ticks = max(
            1,
            int(self.config.get("post_only_buffer_ticks", ML_DIRECTIONAL_POST_ONLY_BUFFER_TICKS)),
        )
        self.gtd_max_horizon_seconds = int(
            self.config.get("gtd_max_horizon_seconds", ML_DIRECTIONAL_GTD_MAX_HORIZON_SECONDS)
        )
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
        self._recent_cancelled_orders: Dict[str, Dict[str, Any]] = {}
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
        self._horizon_status: Dict[str, str] = {"15m": "idle", "1h": "idle"}
        self._last_decision_snapshot_by_horizon: Dict[str, Dict[str, Any]] = {
            "15m": {},
            "1h": {},
        }
        self._last_sizing_debug: Dict[str, Dict[str, float | str | bool | None]] = {
            "15m": {},
            "1h": {},
        }

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
        if not market_data.has_real_quotes or market_data.data_source_quality != "live_quotes":
            return False
        if not market_data.accepting_orders or market_data.is_resolved:
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
        block_reason = "idle"
        horizon_blocks: Dict[str, str] = {horizon: "idle" for horizon in ("15m", "1h")}
        horizon_signal_counts: Dict[str, int] = {horizon: 0 for horizon in ("15m", "1h")}
        decision_candidates: Dict[str, Dict[str, Any]] = {horizon: {} for horizon in ("15m", "1h")}

        if not self.enabled:
            self._last_scan_status = "disabled"
            self._horizon_status = {horizon: "disabled" for horizon in self._horizon_status}
            return signals
        if not self.binance_feed:
            self._last_scan_status = "no Binance feed"
            self._horizon_status = {horizon: "no Binance feed" for horizon in self._horizon_status}
            return signals

        current_state = self.binance_feed.get_state("btc")
        if not current_state.connected:
            self._halt_reason = "Binance feed disconnected"
            self._last_scan_status = "Binance disconnected"
            self._horizon_status = {horizon: "Binance disconnected" for horizon in self._horizon_status}
            return signals
        if current_state.last_update and (time.time() - current_state.last_update) > self.feed_stale_seconds:
            self._halt_reason = "Binance feed stale"
            self._last_scan_status = "Binance stale"
            self._horizon_status = {horizon: "Binance stale" for horizon in self._horizon_status}
            return signals
        if self._halt_reason == "Binance feed disconnected" or self._halt_reason == "Binance feed stale":
            self._halt_reason = None

        for data in market_data:
            horizon = self._extract_horizon(f"{data.question} {data.market_slug}".lower()) or "15m"
            if not self.should_trade_market(data):
                if not data.has_real_quotes:
                    block_reason = "missing real quotes"
                elif not data.accepting_orders:
                    block_reason = "market not accepting orders"
                elif data.is_resolved:
                    block_reason = "market resolved"
                horizon_blocks[horizon] = block_reason
                continue
            if data.condition_id in self.active_condition_ids or data.condition_id in seen_condition_ids:
                block_reason = "condition already active"
                horizon_blocks[horizon] = block_reason
                continue

            inference = self._infer_market(data, current_state)
            if inference is None:
                block_reason = self.model_error or "inference unavailable"
                horizon_blocks[horizon] = block_reason
                continue
            outcome_probability = float(inference["probability"])
            model_threshold = float(inference["threshold_probability"])
            model_version = str(inference["model_version"])
            buy_price = self._maker_limit_buy_price(data)
            if buy_price is None:
                block_reason = "no post-only price (spread/tick)"
                horizon_blocks[horizon] = block_reason
                continue

            threshold = max(self.min_edge, self._compute_threshold(buy_price))
            mid_edge = outcome_probability - data.mid_price
            edge = outcome_probability - buy_price
            decision_snapshot: Dict[str, Any] = {
                "market_slug": data.market_slug,
                "outcome": data.outcome,
                "probability": round(outcome_probability, 6),
                "threshold_probability": round(model_threshold, 6),
                "market_mid_price": round(float(data.mid_price), 6),
                "buy_price": round(float(buy_price), 6),
                "entry_edge": round(edge, 6),
                "mid_edge": round(mid_edge, 6),
                "required_edge": round(threshold, 6),
                "model_version": model_version,
                "last_inference_ts": self._last_inference_ts,
                "decision": "BLOCKED",
                "block_reason": "",
            }
            if outcome_probability < model_threshold:
                block_reason = f"prob {outcome_probability*100:.1f}c < {model_threshold*100:.1f}c"
                horizon_blocks[horizon] = block_reason
                decision_snapshot["block_reason"] = block_reason
                self._update_decision_candidate(decision_candidates, horizon, decision_snapshot)
                continue
            if edge <= threshold:
                block_reason = f"edge {edge*100:.1f}c < {threshold*100:.1f}c"
                horizon_blocks[horizon] = block_reason
                decision_snapshot["block_reason"] = block_reason
                self._update_decision_candidate(decision_candidates, horizon, decision_snapshot)
                continue

            bet_size_usd = self._size_bet(outcome_probability, buy_price, data.token_id, horizon=horizon)
            if bet_size_usd <= 0:
                block_reason = self._zero_size_block_reason(horizon)
                horizon_blocks[horizon] = block_reason
                decision_snapshot["block_reason"] = block_reason
                self._update_decision_candidate(decision_candidates, horizon, decision_snapshot)
                continue
            cancel_block_reason = self._recent_cancel_block_reason(
                condition_id=data.condition_id,
                buy_price=buy_price,
                edge=edge,
                predicted_prob=outcome_probability,
                tick_size=float(data.tick_size or 0.001),
            )
            if cancel_block_reason:
                block_reason = cancel_block_reason
                horizon_blocks[horizon] = block_reason
                decision_snapshot["block_reason"] = block_reason
                self._update_decision_candidate(decision_candidates, horizon, decision_snapshot)
                continue
            shares = round(max(0.0, bet_size_usd / buy_price), 2)
            if shares <= 0:
                block_reason = "shares <= 0"
                horizon_blocks[horizon] = block_reason
                decision_snapshot["block_reason"] = block_reason
                self._update_decision_candidate(decision_candidates, horizon, decision_snapshot)
                continue

            decision_snapshot["decision"] = "BUY"
            self._update_decision_candidate(decision_candidates, horizon, decision_snapshot)

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
                    f"mkt={data.mid_price:.3f} px={buy_price:.3f} "
                    f"mid_edge={mid_edge*100:.1f}c entry_edge={edge*100:.1f}c"
                ),
                metadata={
                    "strategy": self.name,
                    "predicted_prob": round(outcome_probability, 6),
                    "edge": round(edge, 6),
                    "entry_edge": round(edge, 6),
                    "mid_edge": round(mid_edge, 6),
                    "market_mid_price": round(float(data.mid_price), 6),
                    "buy_price": round(float(buy_price), 6),
                    "threshold": round(threshold, 6),
                    "horizon": horizon,
                    "model_version": model_version,
                    "condition_id": data.condition_id,
                    "end_date_ts": data.end_date_ts,
                },
            )
            signals.append(signal)
            seen_condition_ids.add(data.condition_id)
            horizon_signal_counts[horizon] += 1

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
        if signals:
            self._last_scan_status = f"{len(signals)} live signals"
        elif self._halt_reason:
            self._last_scan_status = self._halt_reason
        else:
            self._last_scan_status = block_reason
        for horizon in self._horizon_status:
            if horizon_signal_counts.get(horizon, 0) > 0:
                self._horizon_status[horizon] = f"{horizon_signal_counts[horizon]} live signals"
            else:
                self._horizon_status[horizon] = horizon_blocks.get(horizon) or "idle"
            self._last_decision_snapshot_by_horizon[horizon] = decision_candidates.get(horizon, {})
        return signals

    def observe(self, market_data: List[MarketData]) -> List[Dict[str, Any]]:
        """Return one non-trading forecast per current Up outcome."""
        self._load_model_if_needed()
        if not self.enabled or not self.binance_feed:
            return []
        state = self.binance_feed.get_state("btc")
        if not state.connected or (state.last_update and time.time() - state.last_update > self.feed_stale_seconds):
            return []

        observations: List[Dict[str, Any]] = []
        for data in market_data:
            horizon = self._extract_horizon(f"{data.question} {data.market_slug}".lower()) or "15m"
            if horizon not in self.enabled_horizons or data.outcome.lower() != "up":
                continue
            if not data.has_real_quotes or not data.accepting_orders or data.is_resolved:
                continue
            inference = self._infer_market(data, state)
            if inference is None:
                continue
            buy_price = self._maker_limit_buy_price(data)
            if buy_price is None:
                continue
            probability = float(inference["probability"])
            observations.append({
                "market_slug": data.market_slug,
                "token_id": data.token_id,
                "condition_id": data.condition_id,
                "outcome": data.outcome,
                "observed_at": time.time(),
                "end_date_ts": data.end_date_ts,
                "probability": probability,
                "market_mid_price": float(data.mid_price),
                "buy_price": buy_price,
                "entry_edge": probability - buy_price,
                "required_edge": max(self.min_edge, self._compute_threshold(buy_price)),
                "model_version": str(inference["model_version"]),
            })
        return observations

    def _update_decision_candidate(
        self,
        candidates: Dict[str, Dict[str, Any]],
        horizon: str,
        snapshot: Dict[str, Any],
    ) -> None:
        existing = candidates.get(horizon) or {}
        if not existing:
            candidates[horizon] = snapshot
            return
        existing_edge = float(existing.get("entry_edge", -999.0) or -999.0)
        snapshot_edge = float(snapshot.get("entry_edge", -999.0) or -999.0)
        existing_buy = existing.get("decision") == "BUY"
        snapshot_buy = snapshot.get("decision") == "BUY"
        if snapshot_buy and not existing_buy:
            candidates[horizon] = snapshot
            return
        if snapshot_buy == existing_buy and snapshot_edge > existing_edge:
            candidates[horizon] = snapshot

    def execute(self, signals: List[Signal], order_manager) -> List[Dict]:
        results = []
        for signal in signals:
            if signal.signal_type != SignalType.BUY:
                continue
            try:
                gtd_exp = clob_gtd_expiration_unix(
                    signal.metadata.get("end_date_ts"),
                    max_horizon_sec=float(self.gtd_max_horizon_seconds),
                )
                order_metadata = {"strategy": self.name, **signal.metadata}
                result = order_manager.place_limit_order(
                    token_id=signal.token_id,
                    side="BUY",
                    price=signal.price,
                    size=signal.size,
                    order_type="GTD" if gtd_exp is not None else "GTC",
                    market_slug=signal.market_slug,
                    expiration=gtd_exp,
                    post_only=self.post_only,
                    metadata=order_metadata,
                )
                if self._is_post_only_cross_error(result):
                    retry_signal = self._repriced_signal_for_post_only_retry(signal, order_manager)
                    if retry_signal is not None:
                        retry_metadata = {
                            "strategy": self.name,
                            **retry_signal.metadata,
                            "post_only_retry": True,
                            "original_price": signal.price,
                        }
                        retry_result = order_manager.place_limit_order(
                            token_id=retry_signal.token_id,
                            side="BUY",
                            price=retry_signal.price,
                            size=retry_signal.size,
                            order_type="GTD" if gtd_exp is not None else "GTC",
                            market_slug=retry_signal.market_slug,
                            expiration=gtd_exp,
                            post_only=self.post_only,
                            metadata=retry_metadata,
                        )
                        if retry_result.get("success"):
                            horizon = str(retry_signal.metadata.get("horizon") or "")
                            if horizon:
                                self._horizon_status[horizon] = (
                                    f"repriced {signal.price:.3f}->{retry_signal.price:.3f}"
                                )
                            result = retry_result
                        else:
                            result = retry_result
                if result.get("success"):
                    order = result.get("order")
                    condition_id = signal.metadata.get("condition_id")
                    horizon = str(signal.metadata.get("horizon") or "")
                    if condition_id:
                        self.active_condition_ids.add(condition_id)
                    if order is not None:
                        placed_price = float(getattr(order, "price", signal.price) or signal.price)
                        order_meta = getattr(order, "metadata", {}) or {}
                        self._order_context[order.order_id] = {
                            "token_id": signal.token_id,
                            "condition_id": signal.metadata.get("condition_id"),
                            "predicted_prob": signal.metadata.get("predicted_prob"),
                            "edge": signal.metadata.get("edge"),
                            "price": placed_price,
                            "horizon": horizon,
                            "end_date_ts": signal.metadata.get("end_date_ts"),
                            "side": signal.side,
                            "market_slug": signal.market_slug,
                            "submitted_expiration": order_meta.get("submitted_expiration"),
                            "submitted_order_type": order_meta.get("submitted_order_type"),
                            "post_only_retry": bool(order_meta.get("post_only_retry")),
                            "original_price": order_meta.get("original_price"),
                            "repriced_from": order_meta.get("repriced_from"),
                        }
                    if horizon in self._horizon_trade_counts:
                        self._horizon_trade_counts[horizon] += 1
                    placed_order_type = getattr(order, "order_type", None) if order is not None else None
                    placed_expiration = None
                    if order is not None:
                        placed_expiration = (getattr(order, "metadata", {}) or {}).get("submitted_expiration")
                        placed_order_type = placed_order_type or (getattr(order, "metadata", {}) or {}).get("submitted_order_type")
                    retry_flag = bool((getattr(order, "metadata", {}) or {}).get("post_only_retry")) if order is not None else False
                    reprice_note = ""
                    if order is not None:
                        order_meta = getattr(order, "metadata", {}) or {}
                        repriced_from = order_meta.get("repriced_from") or order_meta.get("original_price")
                        if repriced_from is not None:
                            try:
                                reprice_note = f" | repriced {float(repriced_from):.3f}->{placed_price:.3f}"
                            except (TypeError, ValueError):
                                reprice_note = ""
                    cprint(
                        f"ML directional order placed: {signal.market_slug} | {result.get('order_id')}"
                        f" | type={placed_order_type or 'unknown'} exp={placed_expiration or 'none'}"
                        f" | retry={'Y' if retry_flag else 'N'}{reprice_note}",
                        "green",
                    )
                else:
                    cprint(f"  ❌ ML directional order failed: {result.get('error')}", "red")
                results.append(result)
            except Exception as exc:
                cprint(f"  ❌ ML directional execute error: {exc}", "red")
                results.append({"success": False, "error": str(exc)})
        return results

    def _is_post_only_cross_error(self, result: Dict[str, Any]) -> bool:
        error_text = str(result.get("error") or "").lower()
        return "post-only" in error_text and "cross" in error_text

    def _repriced_signal_for_post_only_retry(self, signal: Signal, order_manager) -> Optional[Signal]:
        client = getattr(order_manager, "client", None)
        if client is None or not hasattr(client, "get_orderbook"):
            return None
        book = client.get_orderbook(signal.token_id)
        if not isinstance(book, dict):
            return None
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        best_bid = float(bids[0].get("price", 0) or 0) if bids else 0.0
        best_ask = float(asks[0].get("price", 0) or 0) if asks else 0.0
        if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
            return None
        tick_size = signal.metadata.get("tick_size")
        tick = max(float(tick_size or 0.001), 1e-4)
        market_data = MarketData(
            token_id=signal.token_id,
            condition_id=str(signal.metadata.get("condition_id") or ""),
            market_slug=signal.market_slug,
            question=str(signal.metadata.get("question") or signal.market_slug),
            outcome=signal.side,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=(best_bid + best_ask) / 2,
            spread=best_ask - best_bid,
            volume_24h=0.0,
            liquidity=0.0,
            last_price=(best_bid + best_ask) / 2,
            orderbook=book,
            end_date_ts=signal.metadata.get("end_date_ts"),
            has_real_quotes=True,
            accepting_orders=True,
            tick_size=tick,
        )
        refreshed_price = self._maker_limit_buy_price(market_data)
        if refreshed_price is None or refreshed_price >= best_ask:
            return None
        original_notional = float(signal.price or 0.0) * float(signal.size or 0.0)
        if original_notional <= 0 or refreshed_price <= 0:
            return None
        refreshed_size = round(max(0.0, original_notional / refreshed_price), 4)
        if refreshed_size <= 0:
            return None
        return Signal(
            signal_type=signal.signal_type,
            token_id=signal.token_id,
            market_slug=signal.market_slug,
            side=signal.side,
            price=refreshed_price,
            size=refreshed_size,
            confidence=signal.confidence,
            reason=signal.reason,
            metadata={
                **signal.metadata,
                "question": market_data.question,
                "tick_size": tick,
                "repriced_from": signal.price,
                "repriced_best_bid": best_bid,
                "repriced_best_ask": best_ask,
            },
        )

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
            self._recent_cancelled_orders[str(context["condition_id"])] = {
                **context,
                "reason": reason,
                "cancelled_at": time.time(),
            }
            self.active_condition_ids.discard(context["condition_id"])

    def _recent_cancel_block_reason(
        self,
        *,
        condition_id: Optional[str],
        buy_price: float,
        edge: float,
        predicted_prob: float,
        tick_size: float,
    ) -> Optional[str]:
        if not condition_id:
            return None
        key = str(condition_id)
        recent = self._recent_cancelled_orders.get(key)
        if not recent:
            return None

        cancel_age = time.time() - float(recent.get("cancelled_at", 0.0) or 0.0)
        if cancel_age > max(180.0, float(self.signal_cooldown_s) * 6.0):
            self._recent_cancelled_orders.pop(key, None)
            return None

        tick = max(float(tick_size or 0.001), 1e-4)
        cancelled_price = float(recent.get("price", 0.0) or 0.0)
        cancelled_edge = float(recent.get("edge", 0.0) or 0.0)
        cancelled_prob = float(recent.get("predicted_prob", 0.0) or 0.0)

        price_improved = cancelled_price > 0 and buy_price <= (cancelled_price - tick + 1e-9)
        same_or_better_price = cancelled_price <= 0 or buy_price <= (cancelled_price + 1e-9)
        edge_improved = edge >= cancelled_edge + max(0.005, tick)
        prob_improved = predicted_prob >= cancelled_prob + 0.01

        if price_improved or (same_or_better_price and (edge_improved or prob_improved)):
            self._recent_cancelled_orders.pop(key, None)
            return None

        return (
            f"waiting after cancel @{cancelled_price:.3f} "
            f"(now {buy_price:.3f}, edge {edge*100:.1f}c, {cancel_age:.0f}s)"
        )

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
                "resolved_accuracy_samples": len(self._resolved_accuracy),
                "resolved_brier_samples": len(self._resolved_brier),
                "positions": self.positions,
                "active_conditions": len(self.active_condition_ids),
                "horizon_stats": {
                    horizon: {
                        "signals": self._horizon_signal_counts.get(horizon, 0),
                        "trades": self._horizon_trade_counts.get(horizon, 0),
                        "last_signal_ts": self._horizon_last_signal_ts.get(horizon, 0.0),
                        "status": self._model_errors_by_horizon.get(horizon) or self._horizon_status.get(horizon) or "—",
                        "model_version": self._model_versions_by_horizon.get(horizon, "unloaded"),
                        "last_inference_ts": self._last_inference_ts,
                        "rolling_accuracy": rolling_accuracy,
                        "rolling_brier": rolling_brier,
                        "pending_resolutions": len(self._pending_resolutions),
                        "resolved_accuracy_samples": len(self._resolved_accuracy),
                        "resolved_brier_samples": len(self._resolved_brier),
                        "halt_reason": self._halt_reason,
                        "sizing_debug": self._last_sizing_debug.get(horizon, {}),
                        "decision_snapshot": self._last_decision_snapshot_by_horizon.get(horizon, {}),
                    }
                    for horizon in sorted(self.enabled_horizons or {"15m", "1h"})
                },
                "status": self._last_scan_status or "—",
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

    def _maker_limit_buy_price(self, data: MarketData) -> Optional[float]:
        """Limit price for BUY. Post-only must sit strictly below best ask (tick-aware)."""
        if not self.post_only:
            raw = min(data.best_bid + self.maker_offset, data.best_ask - 1e-4)
            return round(max(0.01, min(0.99, raw)), 4)

        tick = float(data.tick_size or 0.001)
        tick = max(tick, 1e-4)
        if data.best_ask <= 0:
            return None
        # Use an N-tick safety buffer below best ask so that a small downward
        # move between signal generation and actual post_order submission does
        # not cause Polymarket's CLOB to reject the order with
        # "invalid post-only order: order crosses book". Configurable via
        # ML_DIRECTIONAL_POST_ONLY_BUFFER_TICKS (default 2).
        buffer_ticks = float(self.post_only_buffer_ticks)
        ceiling = data.best_ask - (buffer_ticks * tick)
        if ceiling < 0.01:
            return None
        bid_lift = data.best_bid + self.maker_offset
        raw = min(bid_lift, ceiling)
        if raw >= data.best_ask - 1e-12:
            return None
        steps = math.floor(raw / tick + 1e-12)
        price = max(0.01, steps * tick)
        max_steps = math.floor(ceiling / tick + 1e-12)
        max_price = max(0.01, max_steps * tick)
        price = min(price, max_price)
        if price < 0.01 or price > ceiling + 1e-9:
            return None
        if price >= data.best_ask - 1e-9:
            return None
        return float(round(price, 10))

    def _compute_threshold(self, market_price: float) -> float:
        adverse_selection = 0.01
        spread_cost = 0.005
        missed_fill_penalty = 0.005
        calibration_buffer = 0.005
        return adverse_selection + spread_cost + missed_fill_penalty + calibration_buffer

    def _size_bet(
        self,
        estimated_prob: float,
        market_price: float,
        token_id: str,
        horizon: Optional[str] = None,
    ) -> float:
        bankroll = self._effective_bankroll()
        pos_usd = self._current_position_usd(token_id)
        risk_limit_usd = self._effective_risk_limit_usd()
        sizing_horizon = horizon or self._position_horizon_for_token(token_id)
        if not self.kelly_enabled:
            remaining_limit = max(0.0, (risk_limit_usd if risk_limit_usd is not None else self.order_size_usd) - pos_usd)
            bet_size_usd = round(max(0.0, min(float(self.order_size_usd), remaining_limit)), 2)
            if sizing_horizon:
                self._last_sizing_debug[sizing_horizon] = {
                    "bankroll": round(bankroll, 2),
                    "mode": "fixed",
                    "market_price": round(market_price, 4),
                    "estimated_prob": round(estimated_prob, 4),
                    "edge": round(float(estimated_prob - market_price), 4),
                    "bet_size_usd": bet_size_usd,
                    "raw_fraction": None,
                    "adjusted_fraction": None,
                    "inventory_q": round(float(pos_usd / market_price if market_price > 0 else 0.0), 4),
                    "inventory_scale": None,
                    "risk_limit_usd": round(float(risk_limit_usd or 0.0), 2) if risk_limit_usd is not None else None,
                    "native_sized": False,
                    "kelly_enabled": False,
                }
            return bet_size_usd
        inventory_q = pos_usd / market_price if market_price > 0 else 0.0
        result = kelly_size(
            estimated_prob=estimated_prob,
            market_price=market_price,
            bankroll=bankroll,
            mode=self.kelly_mode,
            max_bet_usd=self.order_size_usd,
            inventory_q=inventory_q,
            risk_limit_usd=risk_limit_usd,
        )
        if sizing_horizon:
            self._last_sizing_debug[sizing_horizon] = {
                "bankroll": round(bankroll, 2),
                "mode": self.kelly_mode,
                "market_price": round(market_price, 4),
                "estimated_prob": round(estimated_prob, 4),
                "edge": round(float(result.edge or 0.0), 4),
                "bet_size_usd": round(float(result.bet_size_usd or 0.0), 2),
                "raw_fraction": round(float(result.raw_fraction or 0.0), 4),
                "adjusted_fraction": round(float(result.adjusted_fraction or 0.0), 4),
                "inventory_q": round(float(result.inventory_q or 0.0), 4),
                "inventory_scale": round(float(result.inventory_scale or 0.0), 4),
                "risk_limit_usd": round(float(risk_limit_usd or 0.0), 2) if risk_limit_usd is not None else None,
                "native_sized": bool(result.native_sized),
                "kelly_enabled": True,
            }
        return result.bet_size_usd or 0.0

    def _zero_size_block_reason(self, horizon: str) -> str:
        sizing_debug = self._last_sizing_debug.get(horizon, {}) or {}
        bankroll = sizing_debug.get("bankroll")
        adjusted_fraction = sizing_debug.get("adjusted_fraction")
        edge = sizing_debug.get("edge")
        if bankroll not in (None, "") and adjusted_fraction not in (None, ""):
            candidate_bet = float(bankroll) * float(adjusted_fraction)
            if candidate_bet < 1.0:
                edge_txt = f", edge {float(edge) * 100:.1f}c" if edge not in (None, "") else ""
                return f"kelly ${candidate_bet:.2f} < $1.00 min{edge_txt}"
        return "size <= 0"

    def _position_horizon_for_token(self, token_id: str) -> Optional[str]:
        for pending in self._pending_resolutions.values():
            if pending.get("token_id") == token_id:
                horizon = str(pending.get("horizon") or "")
                if horizon:
                    return horizon
        for context in self._order_context.values():
            if context.get("token_id") == token_id:
                horizon = str(context.get("horizon") or "")
                if horizon:
                    return horizon
        return None

    def _effective_bankroll(self) -> float:
        configured = self.config.get("bankroll")
        if configured not in (None, ""):
            try:
                value = float(configured)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
        if self.risk_manager is not None:
            try:
                live_balance = float(getattr(self.risk_manager, "current_balance", 0.0) or 0.0)
                if live_balance > 0:
                    return live_balance
            except (TypeError, ValueError):
                pass
        return float(PAPER_BALANCE_USD if PAPER_TRADING else 1000.0)

    def _effective_risk_limit_usd(self) -> Optional[float]:
        if self.risk_manager is None:
            return None
        try:
            if getattr(self.risk_manager, "adaptive_enabled", False):
                balance = float(getattr(self.risk_manager, "current_balance", 0.0) or 0.0)
                throttle = float(self.risk_manager.get_throttle_factor())
                max_position_pct = float(getattr(self.risk_manager, "adaptive_max_position_pct", 0.0) or 0.0)
                if balance > 0 and max_position_pct > 0:
                    return max(balance * max_position_pct * throttle, 0.0)
            max_pos_fn = getattr(self.risk_manager, "_get_max_position", None)
            if callable(max_pos_fn):
                limit = float(max_pos_fn() or 0.0)
                if limit > 0:
                    return limit
        except (TypeError, ValueError):
            return None
        return None

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
                    artifact_threshold = float(model.artifact.threshold_probability)
                    self._model_thresholds_by_horizon[current_horizon] = (
                        self.min_probability
                        if self._explicit_min_probability_override
                        else artifact_threshold
                    )
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
            actual = self._resolved_actual(current)
            if actual is None:
                continue
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

    @staticmethod
    def _resolved_actual(market_data: MarketData) -> Optional[float]:
        if not market_data.is_resolved or not market_data.resolution_outcome:
            return None
        return 1.0 if str(market_data.outcome).upper() == str(market_data.resolution_outcome).upper() else 0.0

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
