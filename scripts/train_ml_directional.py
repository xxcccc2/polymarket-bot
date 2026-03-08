#!/usr/bin/env python3
"""
Train the ML directional model and export a live artifact.

Usage:
  python -m scripts.train_ml_directional --target-timeframe 15m
  python -m scripts.train_ml_directional --include-microstructure --microstructure-db data/ml/collectors/binance_microstructure.sqlite
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ML_ARTIFACTS_DIR, ML_BINANCE_COLLECTOR_DB, ML_OHLC_DIR
from src.logging_utils import cprint
from src.ml.data_loader import MicrostructureLoader, OhlcvLoader
from src.ml.features import FeatureBuilder
from src.ml.train import WalkForwardTrainer


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the ML directional artifact")
    parser.add_argument("--asset", default="btc", help="Asset to train, default: btc")
    parser.add_argument("--target-timeframe", default="15m", choices=["15m", "1h"], help="Prediction target")
    parser.add_argument("--timeframes", default="15m,1h,4h", help="Comma-separated OHLCV frames to load")
    parser.add_argument("--artifact-path", default="", help="Output artifact path")
    parser.add_argument("--include-microstructure", action="store_true", help="Merge collector features into training")
    parser.add_argument("--microstructure-db", default=str(ML_BINANCE_COLLECTOR_DB), help="Collector DB path")
    parser.add_argument("--threshold-probability", type=float, default=0.53, help="Trade threshold used in validation")
    parser.add_argument("--min-train-months", type=int, default=6, help="Minimum months before first test fold")
    parser.add_argument("--embargo-rows", type=int, default=2, help="Rows to purge before each test fold")
    args = parser.parse_args()

    timeframes = [value.strip() for value in args.timeframes.split(",") if value.strip()]
    loader = OhlcvLoader(ML_OHLC_DIR)
    dataset = loader.load_asset(args.asset, timeframes=timeframes)
    feature_builder = FeatureBuilder()

    microstructure_frame = None
    if args.include_microstructure:
        micro_loader = MicrostructureLoader(args.microstructure_db)
        micro_dataset = micro_loader.load_symbol("btcusdt")
        microstructure_frame = micro_dataset.frame
        cprint(f"Merged microstructure rows: {len(microstructure_frame)}", "cyan")

    training_frame = feature_builder.build_ohlcv_feature_frame(
        dataset.frames,
        target_timeframe=args.target_timeframe,
        microstructure_frame=microstructure_frame,
    )
    artifact_path = Path(args.artifact_path) if args.artifact_path else ML_ARTIFACTS_DIR / "ml_directional_latest.pkl"

    trainer = WalkForwardTrainer(
        min_train_months=args.min_train_months,
        embargo_rows=args.embargo_rows,
        threshold_probability=args.threshold_probability,
    )
    result = trainer.run(
        training_frame,
        artifact_path=artifact_path,
    )

    cprint("\nTraining summary", "cyan", attrs=["bold"])
    cprint(json.dumps(result.summary, indent=2), "white")
    cprint("\nFeature stability", "cyan", attrs=["bold"])
    cprint(json.dumps(result.feature_stability, indent=2), "white")
    cprint(f"\nArtifact written to {artifact_path}", "green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
