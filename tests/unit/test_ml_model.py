"""Unit tests for ML model artifacts."""

from __future__ import annotations

import pandas as pd

from src.ml.model import LightGBMBinaryClassifier, ModelArtifact


def test_model_save_and_load_round_trip(tmp_path):
    x = pd.DataFrame(
        {
            "feature_a": [0.0, 1.0, 0.0, 1.0],
            "feature_b": [0.0, 0.0, 1.0, 1.0],
        }
    )
    y = pd.Series([0, 1, 0, 1], name="target")

    model = LightGBMBinaryClassifier()
    model.fit(x, y)
    artifact = ModelArtifact(
        version="test-model",
        feature_columns=["feature_a", "feature_b"],
        threshold_probability=0.57,
        metadata={"source": "unit-test"},
    )
    path = tmp_path / "artifact.pkl"
    model.save(path, artifact)

    loaded = LightGBMBinaryClassifier.load(path)
    probs = loaded.predict_positive_proba(x)

    assert loaded.artifact is not None
    assert loaded.artifact.version == "test-model"
    assert loaded.artifact.threshold_probability == 0.57
    assert len(probs) == len(x)
