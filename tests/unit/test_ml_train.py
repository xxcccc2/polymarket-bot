from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.ml.train import WalkForwardTrainer, filter_training_frame


def test_filter_training_frame_applies_inclusive_bounds():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    datetime(2026, 3, 1, tzinfo=timezone.utc),
                    datetime(2026, 3, 15, tzinfo=timezone.utc),
                    datetime(2026, 4, 1, tzinfo=timezone.utc),
                ],
                utc=True,
            ),
            "value": [1, 2, 3],
        }
    )

    filtered = filter_training_frame(
        frame,
        start_date="2026-03-15",
        end_date="2026-04-01",
    )

    assert filtered["value"].tolist() == [2, 3]


def test_filter_training_frame_rejects_inverted_window():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [datetime(2026, 3, 1, tzinfo=timezone.utc)],
                utc=True,
            ),
            "value": [1],
        }
    )

    with pytest.raises(ValueError, match="start_date"):
        filter_training_frame(
            frame,
            start_date="2026-04-01",
            end_date="2026-03-01",
        )


def test_walk_forward_trainer_excludes_datetime_metadata_columns_from_features():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    datetime(2026, 3, 1, tzinfo=timezone.utc),
                    datetime(2026, 3, 15, tzinfo=timezone.utc),
                    datetime(2026, 4, 1, tzinfo=timezone.utc),
                    datetime(2026, 4, 15, tzinfo=timezone.utc),
                ],
                utc=True,
            ),
            "as_of_ts": pd.to_datetime(
                [
                    datetime(2026, 3, 1, tzinfo=timezone.utc),
                    datetime(2026, 3, 15, tzinfo=timezone.utc),
                    datetime(2026, 4, 1, tzinfo=timezone.utc),
                    datetime(2026, 4, 15, tzinfo=timezone.utc),
                ],
                utc=True,
            ),
            "micro_available_ts": pd.to_datetime(
                [
                    datetime(2026, 3, 1, tzinfo=timezone.utc),
                    datetime(2026, 3, 15, tzinfo=timezone.utc),
                    datetime(2026, 4, 1, tzinfo=timezone.utc),
                    datetime(2026, 4, 15, tzinfo=timezone.utc),
                ],
                utc=True,
            ),
            "feature_a": [0.1, 0.2, 0.3, 0.4],
            "target_up": [0.0, 1.0, 0.0, 1.0],
            "target_return": [0.0, 0.1, -0.1, 0.2],
        }
    )
    trainer = WalkForwardTrainer(min_train_months=1, embargo_rows=0)

    result = trainer.run(frame)

    assert "feature_a" in result.feature_columns
    assert "micro_available_ts" not in result.feature_columns
    assert "as_of_ts" not in result.feature_columns
