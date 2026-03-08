"""
ML tooling for the Polymarket directional edge strategy.

This package contains the offline data pipeline, feature builders, model
artifacts, training helpers, and the dedicated research backtest harness used
by the live `ml_directional` strategy.
"""

from .data_loader import MicrostructureDataset, MicrostructureLoader, OhlcvDataset, OhlcvLoader
from .features import (
    DEFAULT_RUNTIME_FEATURE_COLUMNS,
    FeatureBuilder,
    align_feature_row,
    compute_orderbook_imbalance,
)
from .model import LightGBMBinaryClassifier, ModelArtifact

__all__ = [
    "DEFAULT_RUNTIME_FEATURE_COLUMNS",
    "FeatureBuilder",
    "LightGBMBinaryClassifier",
    "ModelArtifact",
    "MicrostructureDataset",
    "MicrostructureLoader",
    "OhlcvDataset",
    "OhlcvLoader",
    "align_feature_row",
    "compute_orderbook_imbalance",
]
