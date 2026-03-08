"""
Model wrapper and artifact persistence for the ML directional strategy.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import numpy as np
import pandas as pd


DEFAULT_LGBM_PARAMS = {
    "objective": "binary",
    "n_estimators": 500,
    "max_depth": 6,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.7,
    "min_child_samples": 50,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "class_weight": "balanced",
    "random_state": 42,
}


@dataclass
class ModelArtifact:
    """Serialized model bundle consumed by the live strategy."""

    version: str
    feature_columns: list[str]
    threshold_probability: float = 0.53
    metadata: Dict[str, Any] = field(default_factory=dict)
    trained_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class LightGBMBinaryClassifier:
    """Small wrapper around LightGBM with a safe fallback estimator."""

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        self.params = {**DEFAULT_LGBM_PARAMS, **(params or {})}
        self.model: Any = None
        self.backend = "uninitialized"
        self.artifact: Optional[ModelArtifact] = None

    def fit(self, x: pd.DataFrame, y: pd.Series) -> "LightGBMBinaryClassifier":
        """Fit the classifier on a binary target."""
        estimator, backend = _build_estimator(self.params)
        estimator.fit(x, y)
        self.model = estimator
        self.backend = backend
        return self

    def predict_proba(self, x: pd.DataFrame) -> pd.DataFrame:
        """Return a 2-column probability array compatible with sklearn style."""
        if self.model is None:
            raise RuntimeError("Model has not been fitted or loaded")

        raw = self.model.predict_proba(x)
        return raw

    def predict_positive_proba(self, x: pd.DataFrame) -> pd.Series:
        """Return the positive-class probability as a Series."""
        raw = self.predict_proba(x)
        if isinstance(raw, pd.DataFrame):
            return raw.iloc[:, -1]
        raw_array = np.asarray(raw)
        return pd.Series(raw_array[:, -1], index=x.index if hasattr(x, "index") else None)

    def feature_importances(self) -> Dict[str, float]:
        """Return feature importances when the backend exposes them."""
        if self.model is None:
            return {}

        raw = getattr(self.model, "feature_importances_", None)
        if raw is None:
            return {}

        if self.artifact:
            columns = self.artifact.feature_columns
        else:
            columns = []
        return {
            column: float(raw[idx])
            for idx, column in enumerate(columns)
            if idx < len(raw)
        }

    def save(self, path: str | Path, artifact: ModelArtifact) -> None:
        """Persist the fitted model and artifact metadata."""
        if self.model is None:
            raise RuntimeError("Cannot save an unfitted model")

        payload = {
            "model": self.model,
            "artifact": artifact,
            "backend": self.backend,
            "params": self.params,
        }
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as handle:
            pickle.dump(payload, handle)
        self.artifact = artifact

    @classmethod
    def load(cls, path: str | Path) -> "LightGBMBinaryClassifier":
        """Load a persisted classifier bundle."""
        with Path(path).open("rb") as handle:
            payload = pickle.load(handle)

        instance = cls(params=payload.get("params"))
        instance.model = payload["model"]
        instance.backend = payload.get("backend", "unknown")
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            artifact = ModelArtifact(**artifact)
        instance.artifact = artifact
        return instance


class LogisticFallbackClassifier:
    """
    Lightweight binary classifier used when LightGBM and sklearn are missing.

    This is intentionally simple. It estimates a linear score from feature means
    and maps it through a logistic transform so unit tests and artifact loading
    continue to work in minimal environments.
    """

    def __init__(self) -> None:
        self.columns: list[str] = []
        self.mean_vector: Optional[pd.Series] = None
        self.scale_vector: Optional[pd.Series] = None
        self.coefficients: Optional[pd.Series] = None

    def fit(self, x: pd.DataFrame, y: pd.Series) -> "LogisticFallbackClassifier":
        self.columns = list(x.columns)
        self.mean_vector = x.mean()
        centered = x - self.mean_vector
        self.scale_vector = centered.std().replace(0, 1.0).fillna(1.0)
        z = centered / self.scale_vector
        target_centered = y - float(y.mean())
        weights = z.mul(target_centered, axis=0).mean()
        self.coefficients = weights.fillna(0.0)
        return self

    def predict_proba(self, x: pd.DataFrame):
        if self.coefficients is None or self.mean_vector is None or self.scale_vector is None:
            raise RuntimeError("Fallback model has not been fitted")

        z = (x[self.columns] - self.mean_vector) / self.scale_vector
        score = z.mul(self.coefficients, axis=1).sum(axis=1)
        positive = 1.0 / (1.0 + (-score).apply(float).map(lambda value: pow(2.718281828, -value)))
        negative = 1.0 - positive
        return pd.DataFrame({0: negative, 1: positive})


def _build_estimator(params: Dict[str, Any]) -> tuple[Any, str]:
    try:
        import lightgbm as lgb

        return lgb.LGBMClassifier(**params), "lightgbm"
    except Exception:
        pass

    try:
        from sklearn.ensemble import HistGradientBoostingClassifier

        estimator = HistGradientBoostingClassifier(
            learning_rate=float(params.get("learning_rate", 0.05)),
            max_depth=int(params.get("max_depth", 6)),
            max_iter=int(params.get("n_estimators", 200)),
            random_state=int(params.get("random_state", 42)),
        )
        return estimator, "sklearn_hist_gradient_boosting"
    except Exception:
        return LogisticFallbackClassifier(), "logistic_fallback"
