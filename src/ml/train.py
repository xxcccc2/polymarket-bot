"""
Walk-forward training pipeline for the ML directional strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from .evaluate import FoldMetrics, compute_fold_metrics, summarize_feature_stability, summarize_folds
from .model import LightGBMBinaryClassifier, ModelArtifact


@dataclass
class WalkForwardFold:
    """One expanding-window train/test split."""

    name: str
    train_index: list[int]
    test_index: list[int]


@dataclass
class TrainingRunResult:
    """Result bundle for an end-to-end training run."""

    feature_columns: list[str]
    folds: List[FoldMetrics]
    summary: Dict[str, float]
    feature_stability: Dict[str, float]
    artifact: Optional[ModelArtifact] = None
    artifact_path: Optional[str] = None


class WalkForwardTrainer:
    """
    Train a binary directional classifier using expanding monthly folds.
    """

    def __init__(
        self,
        *,
        min_train_months: int = 6,
        embargo_rows: int = 1,
        threshold_probability: float = 0.53,
    ) -> None:
        self.min_train_months = min_train_months
        self.embargo_rows = embargo_rows
        self.threshold_probability = threshold_probability

    def build_monthly_folds(self, dataset: pd.DataFrame, timestamp_column: str = "timestamp") -> List[WalkForwardFold]:
        """Create expanding-window folds grouped by calendar month."""
        frame = dataset.copy()
        frame[timestamp_column] = pd.to_datetime(frame[timestamp_column], utc=True)
        frame["__month"] = frame[timestamp_column].dt.to_period("M").astype(str)
        months = list(dict.fromkeys(frame["__month"].tolist()))

        folds: list[WalkForwardFold] = []
        for idx in range(self.min_train_months, len(months)):
            train_months = months[:idx]
            test_month = months[idx]
            train_index = frame.index[frame["__month"].isin(train_months)].tolist()
            test_index = frame.index[frame["__month"] == test_month].tolist()
            if not train_index or not test_index:
                continue

            purged_train = [row_idx for row_idx in train_index if row_idx < (test_index[0] - self.embargo_rows)]
            if not purged_train:
                continue
            folds.append(
                WalkForwardFold(
                    name=f"{train_months[0]}->{test_month}",
                    train_index=purged_train,
                    test_index=test_index,
                )
            )
        return folds

    def run(
        self,
        dataset: pd.DataFrame,
        *,
        feature_columns: Optional[Iterable[str]] = None,
        target_column: str = "target_up",
        timestamp_column: str = "timestamp",
        artifact_path: Optional[str | Path] = None,
        model_params: Optional[Dict] = None,
    ) -> TrainingRunResult:
        """Train through all folds and optionally export a final artifact."""
        if feature_columns is None:
            feature_columns = [
                column
                for column in dataset.columns
                if column not in {target_column, timestamp_column, "as_of_ts", "available_ts", "target_return"}
            ]
        feature_columns = list(feature_columns)

        folds = self.build_monthly_folds(dataset, timestamp_column=timestamp_column)
        fold_metrics: list[FoldMetrics] = []
        fold_importances: list[Dict[str, float]] = []

        for fold in folds:
            train_df = dataset.loc[fold.train_index]
            test_df = dataset.loc[fold.test_index]
            if train_df.empty or test_df.empty:
                continue

            model = LightGBMBinaryClassifier(params=model_params)
            model.artifact = ModelArtifact(version="training-run", feature_columns=feature_columns)
            model.fit(train_df[feature_columns], train_df[target_column])

            probabilities = model.predict_positive_proba(test_df[feature_columns])
            importances = model.feature_importances()
            fold_importances.append(importances)
            fold_metrics.append(
                compute_fold_metrics(
                    test_df[target_column],
                    probabilities,
                    threshold_probability=self.threshold_probability,
                    fold_name=fold.name,
                    feature_importances=importances,
                )
            )

        summary = summarize_folds(fold_metrics)
        feature_stability = summarize_feature_stability(fold_importances)
        result = TrainingRunResult(
            feature_columns=feature_columns,
            folds=fold_metrics,
            summary=summary,
            feature_stability=feature_stability,
        )

        if artifact_path:
            artifact = self.fit_final_model(
                dataset=dataset,
                feature_columns=feature_columns,
                target_column=target_column,
                artifact_path=artifact_path,
                model_params=model_params,
                summary=summary,
                feature_stability=feature_stability,
            )
            result.artifact = artifact
            result.artifact_path = str(artifact_path)

        return result

    def fit_final_model(
        self,
        *,
        dataset: pd.DataFrame,
        feature_columns: list[str],
        target_column: str,
        artifact_path: str | Path,
        model_params: Optional[Dict] = None,
        summary: Optional[Dict[str, float]] = None,
        feature_stability: Optional[Dict[str, float]] = None,
    ) -> ModelArtifact:
        """Fit the final model on the full dataset and persist the artifact."""
        model = LightGBMBinaryClassifier(params=model_params)
        artifact = ModelArtifact(
            version=pd.Timestamp.utcnow().strftime("ml_directional_%Y%m%d_%H%M%S"),
            feature_columns=feature_columns,
            threshold_probability=self.threshold_probability,
            metadata={
                "summary": summary or {},
                "feature_stability": feature_stability or {},
            },
        )
        model.artifact = artifact
        model.fit(dataset[feature_columns], dataset[target_column])
        model.save(artifact_path, artifact)
        return artifact
