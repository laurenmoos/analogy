import json
import math

import pytest

from analogy.runtime_probe import (
    OperationCounts,
    RuntimeMeasurement,
    project_runtime,
    runtime_report_payload,
)


def test_projection_uses_measured_calls_and_reports_hard_bound() -> None:
    measured = RuntimeMeasurement(
        load=4, generation=6, embedding=1, scoring=2, lean=1
    )
    counts = OperationCounts(
        generations=10, embeddings=20, scorings=2, lean_calls=12
    )

    result = project_runtime(
        measured, counts, hard_timeout_seconds=300
    )

    assert result.expected_seconds == 96
    assert result.hard_upper_seconds == 300
    assert result.cold_load_seconds == 4


@pytest.mark.parametrize("value", (-1, float("nan"), float("inf")))
def test_runtime_measurement_rejects_negative_or_nonfinite_values(value) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        RuntimeMeasurement(value, 1, 1, 1, 1)


@pytest.mark.parametrize("value", (-1, 1.5, True))
def test_operation_counts_reject_invalid_values(value) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        OperationCounts(value, 1, 1, 1)


def test_runtime_report_payload_is_json_serializable_and_complete() -> None:
    measured = RuntimeMeasurement(4, 6, 1, 2, 1)
    counts = OperationCounts(10, 20, 2, 12)
    projection = project_runtime(
        measured, counts, hard_timeout_seconds=300
    )

    payload = runtime_report_payload(
        model_identifier="mlx-community/Qwen3-8B-4bit",
        machine="Apple M3 / 24 GB",
        measurement=measured,
        counts=counts,
        projection=projection,
    )
    encoded = json.dumps(payload)

    assert "mlx-community/Qwen3-8B-4bit" in encoded
    assert payload["machine"] == "Apple M3 / 24 GB"
    assert payload["measurement"]["generation"] == 6
    assert payload["counts"]["lean_calls"] == 12
    assert payload["projection"]["expected_seconds"] == 96
    assert math.isfinite(payload["projection"]["hard_upper_seconds"])
