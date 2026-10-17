"""Pure-Python cross-fitted metrics; inputs contain no post-Lean outcomes.

First fit neutral covariance, collect all pre-Lean candidate components, then
fit component scales and freeze selections before executing any Lean calls.
Population diagonal variances use a positive floor. Tactic rows use a sorted
10% trimmed mean (ordinary mean for fewer than ten observations).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Protocol, Sequence

from ..inference.model_provider import CompletionActivationTrace, CompletionScore
from ..records import Message
from .activation_records import CandidateMeasurement


Components = tuple[float | None, float | None, float | None]
Weights = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class NeutralVector:
    problem_id: str
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class RawComponents:
    problem_id: str
    reasoning_displacement: float | None
    causal_effect: float | None
    previous_state_value: float | None


@dataclass(frozen=True, slots=True)
class DiagonalCalibration:
    held_out_problem_id: str
    mean: tuple[float, ...]
    variance: tuple[float, ...]
    component_center: tuple[float, float, float]
    component_scale: tuple[float, float, float]

    def __post_init__(self) -> None:
        if not self.held_out_problem_id:
            raise ValueError("held-out problem ID must be nonempty")
        if not self.mean or len(self.mean) != len(self.variance):
            raise ValueError("calibration mean and variance dimensions must agree")
        if len(self.component_center) != 3 or len(self.component_scale) != 3:
            raise ValueError("calibration requires three component statistics")
        for value in (*self.mean, *self.component_center):
            _finite(value)
        for value in (*self.variance, *self.component_scale):
            _positive(value, "calibration variance/scale")


@dataclass(frozen=True, slots=True)
class ScoredCandidateMeasurement(CandidateMeasurement):
    """Base record plus serializable evidence for the exact selection score."""

    tactic_components: tuple[Components, ...] = ()
    patch_deltas: tuple[tuple[float, ...], ...] = ()
    patch_delta_summaries: tuple[dict, ...] = ()
    standardized_components: Components = (None, None, None)
    effective_weights: Weights = (0.0, 0.0, 0.0)
    weighted_components: Components = (None, None, None)
    selection_score: float | None = None
    calibration_problem_id: str | None = None
    winning_margin: float | None = None


class PatchScorer(Protocol):
    def score_completion_with_delta(
        self, messages: tuple[Message, ...], completion: str,
        *, delta: tuple[float, ...], layer: int,
    ) -> CompletionScore: ...


def _finite(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("metric values must be finite numbers")
    return float(value)


def _positive(value: float, name: str) -> float:
    if _finite(value) <= 0.0:
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


def _moments(values: Sequence[float], floor: float) -> tuple[float, float]:
    """Stable two-pass population moments, without subtracting squared means."""
    numbers = tuple(_finite(value) for value in values)
    origin = numbers[0]
    count = len(numbers)
    mean = _finite(origin + math.fsum((value - origin) / count for value in numbers))
    variance = _finite(math.fsum((value - mean) ** 2 / count for value in numbers))
    return mean, max(variance, floor)


def _components(row: CandidateMeasurement | RawComponents) -> Components:
    return row.reasoning_displacement, row.causal_effect, row.previous_state_value


def fit_leave_one_problem_out(
    neutral_vectors: Sequence[NeutralVector], prelean_components: Sequence[RawComponents],
    *, problem_ids: tuple[str, ...], variance_floor: float,
) -> dict[str, DiagonalCalibration]:
    """Fit each target using *all* rows of the other configured problems only.

    An empty component column is unavailable in the calibration sample: identity
    centering/scaling keeps it reportable without fabricating observations.
    An empty neutral calibration sample cannot support whitening and raises.
    This function accepts only pre-Lean vectors/components, never trial outcomes.
    """
    try:
        floor = _positive(variance_floor, "variance_floor")
    except ValueError as error:
        raise ValueError("variance_floor must be finite and positive") from error
    if len(problem_ids) < 2 or len(set(problem_ids)) != len(problem_ids) or any(not item for item in problem_ids):
        raise ValueError("problem_ids must contain at least two distinct problem IDs")
    neutral = tuple(neutral_vectors)
    raw = tuple(prelean_components)
    if any(row.problem_id not in problem_ids for row in (*neutral, *raw)):
        raise ValueError("calibration row belongs to an unknown problem")
    if not neutral or not neutral[0].values:
        raise ValueError("neutral calibration requires nonempty vectors")
    dimension = len(neutral[0].values)
    if any(len(row.values) != dimension for row in neutral):
        raise ValueError("neutral vector dimensions must agree")
    for row in neutral:
        for value in row.values:
            _finite(value)
    for row in raw:
        for value in _components(row):
            if value is not None:
                _finite(value)
    result = {}
    for target in problem_ids:
        training = tuple(row.values for row in neutral if row.problem_id != target)
        if not training:
            raise ValueError(f"no neutral observations outside held-out problem {target}")
        moments = tuple(_moments(column, floor) for column in zip(*training))
        columns = tuple(
            tuple(_components(row)[index] for row in raw if row.problem_id != target and _components(row)[index] is not None)
            for index in range(3)
        )
        component_moments = tuple(_moments(column, floor) if column else (0.0, 1.0) for column in columns)
        result[target] = DiagonalCalibration(
            target, tuple(item[0] for item in moments), tuple(item[1] for item in moments),
            tuple(item[0] for item in component_moments),
            tuple(math.sqrt(item[1]) for item in component_moments),
        )
    return result


def concatenate_layers(trace: CompletionActivationTrace) -> tuple[float, ...]:
    """Concatenate recorded layer vectors in their recorded order."""
    return tuple(_finite(value) for activation in trace.activations for value in activation.values)


def _aligned(neutral: CompletionActivationTrace, analogy: CompletionActivationTrace) -> None:
    if neutral.score.text != analogy.score.text or neutral.completion_token_count != analogy.completion_token_count:
        raise ValueError("activation traces must teacher-force the identical tactic")
    if tuple((item.layer, len(item.values)) for item in neutral.activations) != tuple((item.layer, len(item.values)) for item in analogy.activations):
        raise ValueError("activation traces must have matching layers and dimensions")


def whitened_squared_distance(
    neutral: CompletionActivationTrace, analogy: CompletionActivationTrace,
    calibration: DiagonalCalibration,
) -> float:
    """Return sum((analogy - neutral)^2 / floored diagonal variance).

    Calibration mean cancels between the two whitened traces. No extra floor is
    added here: the fitted variance already contains the configured floor.
    """
    _aligned(neutral, analogy)
    baseline, contextual = concatenate_layers(neutral), concatenate_layers(analogy)
    if len(baseline) != len(calibration.variance):
        raise ValueError("trace and calibration dimensions must agree")
    try:
        return _finite(math.fsum(((right - left) / math.sqrt(variance)) ** 2 for left, right, variance in zip(baseline, contextual, calibration.variance)))
    except (OverflowError, ZeroDivisionError) as error:
        raise ValueError("whitened displacement is not finite") from error


def clip_patch_delta(values: Sequence[float], *, max_delta_norm: float) -> tuple[float, ...]:
    """Bound the intervention delta with an overflow-safe Euclidean norm."""
    maximum = _positive(max_delta_norm, "max_delta_norm")
    delta = tuple(_finite(value) for value in values)
    if not delta:
        raise ValueError("patch delta must be nonempty")
    largest = max(abs(value) for value in delta)
    if largest == 0.0:
        return delta
    # Divide first so a finite-coordinate vector with an overflowing total norm
    # still has a well-defined clipped direction.
    scaled_norm = math.hypot(*(value / largest for value in delta))
    if largest <= maximum / scaled_norm:
        return delta
    radius = maximum / scaled_norm
    return tuple((value / largest) * radius for value in delta)


def trimmed_mean(values: Sequence[float]) -> float:
    """Discard floor(n/10) values at either tail, then take a stable mean."""
    ordered = sorted(_finite(value) for value in values)
    if not ordered:
        raise ValueError("trimmed mean requires observations")
    trim = len(ordered) // 10
    retained = ordered[trim:len(ordered) - trim]
    return _finite(math.fsum(value / len(retained) for value in retained))


def _failed(candidate_id: str, diagnostic: str) -> ScoredCandidateMeasurement:
    return ScoredCandidateMeasurement(candidate_id, None, None, None, (diagnostic,))


def _unscorable(
    measurement: CandidateMeasurement, diagnostic: str, calibration: DiagonalCalibration,
) -> ScoredCandidateMeasurement:
    """Keep finite raw evidence even when the requested score is unavailable."""
    raw = _components(measurement)
    try:
        for value in raw:
            if value is not None:
                _finite(value)
    except ValueError:
        raw = (None, None, None)
    return ScoredCandidateMeasurement(
        measurement.candidate_id, *raw, (*measurement.diagnostics, diagnostic),
        tactic_components=getattr(measurement, "tactic_components", ()),
        patch_deltas=getattr(measurement, "patch_deltas", ()),
        calibration_problem_id=calibration.held_out_problem_id,
    )


def score_candidate(
    measurement: CandidateMeasurement, *, calibration: DiagonalCalibration,
    weights: Weights = (1.0, 1.0, 1.0),
) -> ScoredCandidateMeasurement:
    """Standardize raw aggregates using exactly the supplied target calibration.

    Missing components receive no weight. Any nonfinite observed component or
    score invalidates the candidate, rather than dropping the offending value.
    """
    if len(weights) != 3:
        raise ValueError("weights must contain three finite values")
    weights = tuple(_finite(value) for value in weights)
    try:
        raw = _components(measurement)
        normalized = tuple(None if value is None else _finite((_finite(value) - center) / scale) for value, center, scale in zip(raw, calibration.component_center, calibration.component_scale))
        available = tuple(weight if value is not None else 0.0 for value, weight in zip(raw, weights))
        # Dividing weights by their maximum prevents finite weights overflowing
        # their sum, without changing their normalized contribution.
        largest = max(abs(weight) for weight in available)
        if largest == 0.0:
            return _unscorable(measurement, "candidate has no weighted available components", calibration)
        total = math.fsum(weight / largest for weight in available)
        if total == 0.0:
            return _unscorable(measurement, "available component weights have zero sum", calibration)
        effective = tuple((weight / largest) / total for weight in available)
        weighted = tuple(None if value is None else _finite(value * weight) for value, weight in zip(normalized, effective))
        selection_score = _finite(math.fsum(value for value in weighted if value is not None))
        return ScoredCandidateMeasurement(
            measurement.candidate_id, *raw, measurement.diagnostics,
            tactic_components=getattr(measurement, "tactic_components", ()),
            patch_deltas=getattr(measurement, "patch_deltas", ()),
            standardized_components=normalized, effective_weights=effective,
            weighted_components=weighted, selection_score=selection_score,
            calibration_problem_id=calibration.held_out_problem_id,
        )
    except (ValueError, OverflowError, ZeroDivisionError) as error:
        return _unscorable(measurement, f"unscorable components: {error}", calibration)


def _validate_score(score: CompletionScore, neutral: CompletionActivationTrace) -> float:
    if score.text != neutral.score.text or score.token_count != neutral.completion_token_count:
        raise ValueError("likelihood score must refer to the identical tactic tokens")
    _finite(score.mean_log_likelihood)
    return _finite(score.total_log_likelihood)


def measure_candidate(
    candidate_id: str, neutral_traces: Sequence[CompletionActivationTrace],
    analogy_traces: Sequence[CompletionActivationTrace], *, provider: PatchScorer,
    neutral_messages: tuple[Message, ...], calibration: DiagonalCalibration,
    intervention_layer: int, max_delta_norm: float,
    weights: Weights = (1.0, 1.0, 1.0),
    with_previous_scores: Sequence[CompletionScore] | None = None,
    without_previous_scores: Sequence[CompletionScore] | None = None,
) -> ScoredCandidateMeasurement:
    """Measure a canonical tactic slate and apply bounded single-layer patches.

    ``max_delta_norm`` must be supplied from ``app_config.rl.max_delta_norm``.
    The two previous-state score sequences must be supplied together only when
    a real accepted predecessor exists; absent pairs yield unavailable E_prev.
    Capture is separate so neutral traces can be shared across analogies. A
    capture/patch/likelihood failure invalidates the entire candidate.
    """
    try:
        _positive(max_delta_norm, "max_delta_norm")
        neutral, analogy = tuple(neutral_traces), tuple(analogy_traces)
        if not neutral or len(neutral) != len(analogy):
            raise ValueError("candidate requires matched nonempty tactic trace slates")
        if (with_previous_scores is None) != (without_previous_scores is None):
            raise ValueError("previous-state likelihoods require both matched contexts")
        with_previous = None if with_previous_scores is None else tuple(with_previous_scores)
        without_previous = None if without_previous_scores is None else tuple(without_previous_scores)
        if with_previous is not None and (len(with_previous) != len(neutral) or len(without_previous) != len(neutral)):
            raise ValueError("previous-state score slate length must match tactics")
        rows, patches = [], []
        for index, (baseline, contextual) in enumerate(zip(neutral, analogy)):
            displacement = whitened_squared_distance(baseline, contextual, calibration)
            neutral_layer = next((item.values for item in baseline.activations if item.layer == intervention_layer), None)
            analogy_layer = next((item.values for item in contextual.activations if item.layer == intervention_layer), None)
            if neutral_layer is None or analogy_layer is None:
                raise ValueError("intervention layer is absent from captured traces")
            patch = clip_patch_delta(tuple(right - left for left, right in zip(neutral_layer, analogy_layer)), max_delta_norm=max_delta_norm)
            patched = provider.score_completion_with_delta(neutral_messages, baseline.score.text, delta=patch, layer=intervention_layer)
            causal = _finite(_validate_score(patched, baseline) - _finite(baseline.score.total_log_likelihood))
            previous = None
            if with_previous is not None:
                previous = _finite(_validate_score(with_previous[index], baseline) - _validate_score(without_previous[index], baseline))
            rows.append((displacement, causal, previous))
            patches.append(patch)
        aggregate = tuple(trimmed_mean(tuple(row[index] for row in rows if row[index] is not None)) if any(row[index] is not None for row in rows) else None for index in range(3))
        measurement = ScoredCandidateMeasurement(candidate_id, *aggregate, tactic_components=tuple(rows), patch_deltas=tuple(patches))
    except Exception as error:
        # Provider failures are experimental data. They must not silently select
        # a different condition, partially score this candidate, or abort peers.
        return _failed(candidate_id, f"candidate measurement failed: {type(error).__name__}: {error}")
    return score_candidate(measurement, calibration=calibration, weights=weights)


def select_candidate(
    measurements: Sequence[CandidateMeasurement], *, calibration: DiagonalCalibration,
    weights: Weights = (1.0, 1.0, 1.0),
) -> ScoredCandidateMeasurement | None:
    """Select from raw pre-Lean aggregates; stable analogy ID breaks score ties.

    Always recompute standardization, even for already-scored input. An empty or
    all-unscorable slate returns None, with no prompted-analogy fallback.
    """
    scored = tuple(score_candidate(item, calibration=calibration, weights=weights) for item in measurements)
    ordered = sorted((item for item in scored if item.selection_score is not None), key=lambda item: (-item.selection_score, item.candidate_id))
    if not ordered:
        return None
    winner = ordered[0]
    margin = None if len(ordered) == 1 else winner.selection_score - ordered[1].selection_score
    if margin is not None and not math.isfinite(margin):
        return replace(winner, diagnostics=(*winner.diagnostics, "winning margin exceeds finite range"))
    return replace(winner, winning_margin=margin)


__all__ = [
    "NeutralVector", "RawComponents", "DiagonalCalibration", "ScoredCandidateMeasurement",
    "fit_leave_one_problem_out", "concatenate_layers", "whitened_squared_distance",
    "clip_patch_delta", "trimmed_mean", "measure_candidate", "score_candidate", "select_candidate",
]
