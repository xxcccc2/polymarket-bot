"""
Evaluation helpers for the ML directional training pipeline and backtests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence

import numpy as np


@dataclass
class FoldMetrics:
    """Summary of one training or backtest evaluation fold."""

    fold_name: str
    sample_count: int
    accuracy: float
    brier_score: float
    trade_rate: float
    trade_precision: float
    trade_recall: float
    net_ev_per_trade: float
    profit_factor: float
    max_drawdown: float
    feature_importances: Dict[str, float] = field(default_factory=dict)


def compute_fold_metrics(
    y_true: Sequence[float],
    y_prob: Sequence[float],
    *,
    threshold_probability: float = 0.53,
    fee_rate: float = 0.0,
    fill_rate: float = 1.0,
    fold_name: str = "fold",
    feature_importances: Dict[str, float] | None = None,
) -> FoldMetrics:
    """Compute strategy-facing metrics from binary labels and probabilities."""
    truth = np.asarray(y_true, dtype=float)
    prob = np.clip(np.asarray(y_prob, dtype=float), 0.0, 1.0)
    pred = (prob >= 0.5).astype(float)

    accuracy = float((pred == truth).mean()) if len(truth) else 0.0
    brier = float(np.mean((prob - truth) ** 2)) if len(truth) else 0.0

    trade_mask = (prob >= threshold_probability) | (prob <= (1.0 - threshold_probability))
    trade_rate = float(trade_mask.mean()) if len(truth) else 0.0

    trade_truth = truth[trade_mask]
    trade_prob = prob[trade_mask]
    trade_pred = (trade_prob >= 0.5).astype(float)
    trade_wins = (trade_pred == trade_truth).astype(float)

    positives = (trade_pred == 1).sum()
    true_positives = float(((trade_pred == 1) & (trade_truth == 1)).sum())
    actual_positives = float((trade_truth == 1).sum())
    trade_precision = float(true_positives / positives) if positives else 0.0
    trade_recall = float(true_positives / actual_positives) if actual_positives else 0.0

    per_trade_pnl = []
    for idx, fired in enumerate(trade_mask):
        if not fired:
            continue
        direction = 1.0 if prob[idx] >= 0.5 else 0.0
        won = truth[idx] == direction
        gross = fill_rate * (1.0 - fee_rate) if won else -fill_rate
        per_trade_pnl.append(gross - fee_rate)

    net_ev_per_trade = float(np.mean(per_trade_pnl)) if per_trade_pnl else 0.0
    gross_profit = sum(value for value in per_trade_pnl if value > 0)
    gross_loss = abs(sum(value for value in per_trade_pnl if value < 0))
    profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else float(gross_profit > 0)
    max_drawdown = compute_max_drawdown(per_trade_pnl)

    return FoldMetrics(
        fold_name=fold_name,
        sample_count=len(truth),
        accuracy=accuracy,
        brier_score=brier,
        trade_rate=trade_rate,
        trade_precision=trade_precision,
        trade_recall=trade_recall,
        net_ev_per_trade=net_ev_per_trade,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        feature_importances=feature_importances or {},
    )


def compute_max_drawdown(pnl_series: Sequence[float]) -> float:
    """Compute max drawdown from a list of incremental PnL values."""
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnl_series:
        equity += float(pnl)
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return abs(max_drawdown)


def summarize_feature_stability(fold_importances: Iterable[Dict[str, float]], top_n: int = 5) -> Dict[str, float]:
    """
    Summarize how often each feature shows up in the top-N importances.
    """
    counts: Dict[str, int] = {}
    total = 0
    for fold in fold_importances:
        ordered = sorted(fold.items(), key=lambda item: item[1], reverse=True)[:top_n]
        for feature, _ in ordered:
            counts[feature] = counts.get(feature, 0) + 1
        total += 1

    if total == 0:
        return {}
    return {feature: count / total for feature, count in counts.items()}


def summarize_folds(folds: List[FoldMetrics]) -> Dict[str, float]:
    """Aggregate a list of fold metrics."""
    if not folds:
        return {}

    def avg(values: Iterable[float]) -> float:
        values = list(values)
        return float(sum(values) / len(values)) if values else 0.0

    return {
        "fold_count": float(len(folds)),
        "avg_accuracy": avg(fold.accuracy for fold in folds),
        "avg_brier_score": avg(fold.brier_score for fold in folds),
        "avg_trade_rate": avg(fold.trade_rate for fold in folds),
        "avg_trade_precision": avg(fold.trade_precision for fold in folds),
        "avg_trade_recall": avg(fold.trade_recall for fold in folds),
        "avg_net_ev_per_trade": avg(fold.net_ev_per_trade for fold in folds),
        "avg_profit_factor": avg(fold.profit_factor for fold in folds),
        "worst_accuracy": min(fold.accuracy for fold in folds),
        "worst_brier_score": max(fold.brier_score for fold in folds),
        "max_drawdown": max(fold.max_drawdown for fold in folds),
    }
