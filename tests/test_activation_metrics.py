"""Hand-calculated pre-Lean metrics and leakage-resistant selection."""

from dataclasses import asdict, replace
import math

import pytest

from analogy.experiments.activation_metrics import (
    DiagonalCalibration, NeutralVector, RawComponents, ScoredCandidateMeasurement,
    clip_patch_delta, concatenate_layers, fit_leave_one_problem_out,
    measure_candidate, score_candidate, select_candidate, trimmed_mean,
    whitened_squared_distance,
)
from analogy.experiments.activation_records import (
    ActivationTrialRecord, AnchorRecord, CandidateMeasurement,
)
from analogy.inference.model_provider import (
    CompletionActivationTrace, CompletionScore, LayerActivation,
)
from analogy.records import Message, SymbolicState


def trace(values, *, text="exact h", likelihood=-10.0):
    return CompletionActivationTrace(
        CompletionScore(text, likelihood, likelihood / 2, 2),
        tuple(LayerActivation(layer, tuple(vector)) for layer, vector in values),
        2, 8,
    )


def calibration(**kwargs):
    values = dict(
        held_out_problem_id="target", mean=(0.0, 0.0), variance=(4.0, 1.0),
        component_center=(1.0, 2.0, 3.0), component_scale=(2.0, 4.0, 5.0),
    )
    values.update(kwargs)
    return DiagonalCalibration(**values)


class PatchProvider:
    """Numerical boundary double; the result changes if clipping/layer is wrong."""

    def score_completion_with_delta(self, messages, completion, *, delta, layer):
        assert messages == (Message("user", "neutral"),)
        assert completion == "exact h"
        assert layer == 3
        effect = sum(delta)
        return CompletionScore(completion, -10.0 + effect, (-10.0 + effect) / 2, 2)


def measured(**kwargs):
    values = dict(
        candidate_id="analogy-b",
        neutral_traces=(trace(((3, (0.0, 0.0)),)),),
        analogy_traces=(trace(((3, (6.0, 8.0)),)),),
        provider=PatchProvider(), neutral_messages=(Message("user", "neutral"),),
        calibration=calibration(), intervention_layer=3, max_delta_norm=5.0,
        weights=(0.4, 0.4, 0.2),
    )
    values.update(kwargs)
    return measure_candidate(**values)


def test_cross_fit_excludes_every_target_row_and_floors_population_variance():
    neutral = (
        NeutralVector("a", (1000.0, 1000.0)),
        NeutralVector("a", (2000.0, 2000.0)),
        NeutralVector("b", (1.0, 2.0)),
        NeutralVector("c", (3.0, 2.0)),
    )
    components = (
        RawComponents("a", 1000.0, 1000.0, 1000.0),
        RawComponents("a", 2000.0, 2000.0, 2000.0),
        RawComponents("b", 1.0, 2.0, None),
        RawComponents("c", 5.0, 6.0, 7.0),
    )
    result = fit_leave_one_problem_out(
        neutral, components, problem_ids=("a", "b", "c"), variance_floor=0.25,
    )
    assert tuple(result) == ("a", "b", "c")
    assert result["a"].mean == (2.0, 2.0)
    assert result["a"].variance == (1.0, 0.25)
    assert result["a"].component_center == (3.0, 4.0, 7.0)
    assert result["a"].component_scale == (2.0, 2.0, 0.5)
    changed = tuple(replace(row, values=(-9999.0, 99.0)) if row.problem_id == "a" else row for row in neutral)
    changed_components = tuple(replace(row, reasoning_displacement=-1234.0) if row.problem_id == "a" else row for row in components)
    assert fit_leave_one_problem_out(changed, changed_components, problem_ids=("a", "b", "c"), variance_floor=0.25)["a"] == result["a"]


def test_two_pass_variance_retains_small_variation_on_large_baseline():
    result = fit_leave_one_problem_out(
        (NeutralVector("a", (0.0,)), NeutralVector("b", (1e12 + 1,)), NeutralVector("b", (1e12 + 3,))),
        (), problem_ids=("a", "b"), variance_floor=0.01,
    )
    assert result["a"].mean == (1e12 + 2,)
    assert result["a"].variance == (1.0,)
    assert result["a"].component_center == (0.0, 0.0, 0.0)
    assert result["a"].component_scale == (1.0, 1.0, 1.0)


@pytest.mark.parametrize("floor", [0.0, -1.0, math.inf, math.nan, True])
def test_calibration_requires_positive_finite_variance_floor(floor):
    with pytest.raises(ValueError, match="variance_floor"):
        fit_leave_one_problem_out((), (), problem_ids=("a", "b"), variance_floor=floor)


def test_calibration_rejects_unknown_problems_and_empty_other_problem_sample():
    with pytest.raises(ValueError, match="problem"):
        fit_leave_one_problem_out((NeutralVector("unexpected", (1.0,)),), (), problem_ids=("a", "b"), variance_floor=1.0)
    with pytest.raises(ValueError, match="neutral"):
        fit_leave_one_problem_out((NeutralVector("a", (1.0,)),), (), problem_ids=("a", "b"), variance_floor=1.0)


def test_layer_concatenation_and_distance_use_neutral_difference_not_mean():
    neutral = trace(((1, (1.0, 2.0)), (3, (3.0, 4.0))))
    analogy = trace(((1, (3.0, 4.0)), (3, (5.0, 6.0))))
    assert concatenate_layers(neutral) == (1.0, 2.0, 3.0, 4.0)
    fitted = calibration(mean=(999.0,) * 4, variance=(1.0, 4.0, 1.0, 4.0))
    assert whitened_squared_distance(neutral, analogy, fitted) == 10.0


def test_distance_rejects_different_tactics_layers_dimensions_and_overflow():
    neutral = trace(((3, (1.0, 2.0)),))
    for other in (
        trace(((3, (1.0, 2.0)),), text="other"),
        trace(((4, (1.0, 2.0)),)),
        trace(((3, (1.0,)),)),
        trace(((3, (1e308, 2.0)),)),
    ):
        with pytest.raises(ValueError):
            whitened_squared_distance(neutral, other, calibration())


@pytest.mark.parametrize("values, expected", [
    ((3.0, 4.0), (3.0, 4.0)),
    ((6.0, 8.0), (3.0, 4.0)),
    ((0.0, 0.0), (0.0, 0.0)),
    ((1e300, 1e300), (5 / math.sqrt(2), 5 / math.sqrt(2))),
])
def test_patch_clip_preserves_direction_and_bounds_norm(values, expected):
    patch = clip_patch_delta(values, max_delta_norm=5.0)
    assert patch == pytest.approx(expected)
    assert math.hypot(*patch) <= 5.0 + 1e-14


def test_measurement_uses_clipped_patch_minus_neutral_total_likelihood():
    result = measured()
    assert isinstance(result, CandidateMeasurement)
    assert isinstance(result, ScoredCandidateMeasurement)
    assert result.reasoning_displacement == 73.0  # 6^2/4 + 8^2/1
    assert result.causal_effect == 7.0  # patch (3,4): -3 - (-10)
    assert result.previous_state_value is None
    assert result.tactic_components == ((73.0, 7.0, None),)
    assert result.patch_deltas == ((3.0, 4.0),)
    assert result.standardized_components == (36.0, 1.25, None)
    assert result.effective_weights == (0.5, 0.5, 0.0)
    assert result.weighted_components == (18.0, 0.625, None)
    assert result.selection_score == 18.625
    serialized = asdict(result)
    assert serialized["tactic_components"] == ((73.0, 7.0, None),)
    assert serialized["selection_score"] == 18.625


def test_previous_state_effect_uses_total_likelihood_and_real_predecessor():
    with_previous = (CompletionScore("exact h", -4.0, -2.0, 2),)
    without_previous = (CompletionScore("exact h", -9.0, -4.5, 2),)
    result = measured(with_previous_scores=with_previous, without_previous_scores=without_previous)
    assert result.previous_state_value == 5.0
    assert result.standardized_components == (36.0, 1.25, 0.4)
    assert result.effective_weights == (0.4, 0.4, 0.2)
    assert result.selection_score == pytest.approx(14.98)
    assert measured().previous_state_value is None


@pytest.mark.parametrize("values, expected", [
    ((1.0, 2.0, 30.0), 11.0),
    ((1000.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, -1000.0), 4.5),
    ((1.0,), 1.0),
])
def test_trimmed_mean_is_deterministic_ten_percent_with_small_sample_mean(values, expected):
    assert trimmed_mean(values) == expected
    assert trimmed_mean(tuple(reversed(values))) == expected


def test_measurement_aggregates_tactic_rows_not_largest_displacement():
    neutral = tuple(trace(((3, (0.0, 0.0)),)) for _ in range(10))
    analogy = tuple(trace(((3, (value, 0.0)),)) for value in (0., 2., 2., 2., 2., 2., 2., 2., 2., 100.))
    result = measured(neutral_traces=neutral, analogy_traces=analogy)
    assert result.reasoning_displacement == 1.0
    assert result.causal_effect == 2.0
    assert len(result.tactic_components) == 10


def test_missing_components_are_omitted_and_weights_renormalized():
    result = score_candidate(CandidateMeasurement("a", 5.0, None, 13.0), calibration=calibration(), weights=(0.4, 0.4, 0.2))
    assert result.standardized_components == (2.0, None, 2.0)
    assert result.effective_weights == pytest.approx((2 / 3, 0.0, 1 / 3))
    assert result.selection_score == pytest.approx(2.0)


def test_selection_recomputes_only_with_supplied_held_out_calibration():
    # Raw ranking favors a; target standardization favors b. Previously scored
    # or donor-problem scores cannot be trusted at this boundary.
    a = CandidateMeasurement("a", 10.0, 0.0, None)
    b = CandidateMeasurement("b", 0.0, 5.0, None)
    donor = calibration(component_scale=(1.0, 100.0, 1.0))
    target = calibration(component_scale=(100.0, 1.0, 1.0))
    previously_scored = (score_candidate(a, calibration=donor), score_candidate(b, calibration=donor))
    assert select_candidate(previously_scored, calibration=target).candidate_id == "b"


def test_selection_breaks_ties_by_id_and_never_falls_back():
    a = CandidateMeasurement("a", 3.0, 6.0, None)
    b = CandidateMeasurement("b", 3.0, 6.0, None)
    assert select_candidate((b, a), calibration=calibration()).candidate_id == "a"
    assert select_candidate((a, b), calibration=calibration()).candidate_id == "a"
    assert select_candidate((), calibration=calibration()) is None
    assert select_candidate((CandidateMeasurement("prompted", None, None, None),), calibration=calibration()) is None


def test_nonfinite_or_failed_candidates_are_unscorable():
    # CandidateMeasurement validates construction; emulate a corrupted external
    # record to ensure selection validates again rather than accepting NaN.
    corrupted = CandidateMeasurement("bad", 1.0, 2.0, None)
    object.__setattr__(corrupted, "causal_effect", math.nan)
    assert select_candidate((corrupted,), calibration=calibration()) is None

    class BrokenPatch(PatchProvider):
        def score_completion_with_delta(self, *args, **kwargs):
            raise RuntimeError("patch failed")

    failed = measured(provider=BrokenPatch())
    assert failed.selection_score is None
    assert failed.reasoning_displacement is None
    assert any("patch failed" in diagnostic for diagnostic in failed.diagnostics)
    assert select_candidate((failed,), calibration=calibration()) is None


def test_measurement_rejects_partial_predecessor_or_mismatched_scores():
    score = CompletionScore("wrong tactic", -1.0, -1.0, 1)
    partial = measured(with_previous_scores=(score,))
    assert partial.selection_score is None
    mismatch = measured(with_previous_scores=(score,), without_previous_scores=(score,))
    assert mismatch.selection_score is None
    assert measured(neutral_traces=(), analogy_traces=()).selection_score is None


def test_selection_score_overflow_is_unscorable():
    candidate = CandidateMeasurement("overflow", 1e308, 2.0, None)
    result = score_candidate(candidate, calibration=calibration(component_scale=(1e-300, 1.0, 1.0)))
    assert result.selection_score is None
    assert result.reasoning_displacement == 1e308
    assert select_candidate((candidate,), calibration=calibration(component_scale=(1e-300, 1.0, 1.0))) is None


def test_unscorable_weight_subset_preserves_raw_values_and_diagnostics():
    raw = CandidateMeasurement("no-previous", 5.0, 6.0, None, ("real predecessor unavailable",))
    result = score_candidate(raw, calibration=calibration(), weights=(0.0, 0.0, 1.0))
    assert result.selection_score is None
    assert result.reasoning_displacement == 5.0
    assert result.causal_effect == 6.0
    assert result.diagnostics[0] == "real predecessor unavailable"


def test_scored_subtype_serializes_inside_trial_and_records_winning_margin():
    selected = select_candidate((CandidateMeasurement("a", 5.0, None, None), CandidateMeasurement("b", 3.0, None, None)), calibration=calibration())
    assert selected.winning_margin == 1.0
    state = SymbolicState("s", "P", "lean")
    anchor = AnchorRecord("target", 0, "theorem", state, None, (), (), (), "state", "anchor", False)
    trial = ActivationTrialRecord("target", 0, anchor, (), (selected,), ())
    assert asdict(trial)["measurements"][0]["winning_margin"] == 1.0


def test_overflowing_margin_does_not_invalidate_finite_selection_scores():
    target = calibration(component_center=(0.0, 0.0, 0.0), component_scale=(1.0, 1.0, 1.0))
    selected = select_candidate((CandidateMeasurement("high", 1e308, None, None), CandidateMeasurement("low", -1e308, None, None)), calibration=target)
    assert selected.candidate_id == "high"
    assert selected.selection_score == 1e308
    assert selected.winning_margin is None
    assert any("margin" in message for message in selected.diagnostics)


def test_signed_configured_weights_are_renormalized_and_zero_sum_is_unscorable():
    raw = CandidateMeasurement("signed", 5.0, 6.0, None)
    result = score_candidate(raw, calibration=calibration(), weights=(-1.0, 2.0, 0.0))
    assert result.effective_weights == (-1.0, 2.0, 0.0)
    assert result.weighted_components == (-2.0, 2.0, None)
    assert result.selection_score == 0.0
    assert score_candidate(raw, calibration=calibration(), weights=(-1.0, 1.0, 0.0)).selection_score is None
