"""Measured runtime records and transparent experiment projections."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeMeasurement:
    load: float
    generation: float
    embedding: float
    scoring: float
    lean: float

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("runtime measurements must be finite and non-negative")
            if not math.isfinite(float(value)) or value < 0:
                raise ValueError("runtime measurements must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class OperationCounts:
    generations: int
    embeddings: int
    scorings: int
    lean_calls: int

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("operation counts must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class RuntimeProjection:
    cold_load_seconds: float
    expected_seconds: float
    hard_upper_seconds: float


def project_runtime(
    measurement: RuntimeMeasurement,
    counts: OperationCounts,
    *,
    hard_timeout_seconds: float,
) -> RuntimeProjection:
    if (
        isinstance(hard_timeout_seconds, bool)
        or not isinstance(hard_timeout_seconds, (int, float))
        or not math.isfinite(float(hard_timeout_seconds))
        or hard_timeout_seconds < 0
    ):
        raise ValueError("hard timeout must be finite and non-negative")
    expected = (
        measurement.generation * counts.generations
        + measurement.embedding * counts.embeddings
        + measurement.scoring * counts.scorings
        + measurement.lean * counts.lean_calls
    )
    return RuntimeProjection(
        float(measurement.load), float(expected), float(hard_timeout_seconds)
    )


def runtime_report_payload(
    *,
    model_identifier: str,
    machine: str,
    measurement: RuntimeMeasurement,
    counts: OperationCounts,
    projection: RuntimeProjection,
) -> dict[str, Any]:
    if not model_identifier.strip() or not machine.strip():
        raise ValueError("model identifier and machine must be nonblank")
    return {
        "model_identifier": model_identifier,
        "machine": machine,
        "measurement": asdict(measurement),
        "counts": asdict(counts),
        "projection": asdict(projection),
    }


__all__ = [
    "OperationCounts",
    "RuntimeMeasurement",
    "RuntimeProjection",
    "project_runtime",
    "runtime_report_payload",
]
