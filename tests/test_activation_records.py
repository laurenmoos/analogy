from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from analogy.config import ConfigError
from analogy.experiments.activation_records import (
    ActivationCondition,
    ActivationEvalConfig,
    AnchorRecord,
    AnalogyCandidateRecord,
    CandidateMeasurement,
    ConditionOutcome,
    ActivationTrialRecord,
    load_activation_eval_config,
)
from analogy.records import SymbolicState


ROOT = Path(__file__).parents[1]
PROFILE = ROOT / "config.activation-analogy.yaml"
PROBLEM_IDS = (
    "imp_intro_05", "iff_intro_03", "iff_intro_05", "not_intro_05",
    "or_intro_05", "or_intro_06",
)


def read_profile() -> dict:
    return yaml.safe_load(PROFILE.read_text(encoding="utf-8"))


def write_profile(tmp_path: Path, payload: dict) -> Path:
    payload["base_config"] = str(ROOT / "config.yaml")
    path = tmp_path / "activation.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_profile_resolves_base_config_and_has_frozen_defaults():
    config = load_activation_eval_config(PROFILE)
    assert config.base_config_path == (ROOT / "config.yaml").resolve()
    assert config.base_config.model.name == "mlx-community/Qwen3-8B-4bit"
    assert config.problem_ids == PROBLEM_IDS
    assert config.seed_start == 0
    assert config.seed_count == 16
    assert config.analogy_candidates == 4
    assert config.tactics_per_slate == 4
    assert config.tactic_union_limit == 8
    assert config.activation_layer_count == 3
    assert config.variance_floor == 1e-6
    assert config.persist_full_activations is False
    assert config.score_weights == (1.0, 1.0, 1.0)


@pytest.mark.parametrize("mutate, diagnostic", [
    (lambda p: p.update(extra=True), "unknown"),
    (lambda p: p.update(problem_ids=["imp_intro_05"] * 6), "duplicates"),
    (lambda p: p.update(seed_count=0), "positive"),
    (lambda p: p.update(tactics_per_slate=0), "positive"),
    (lambda p: p.update(tactic_union_limit=3), "at least"),
    (lambda p: p.update(variance_floor=0), "positive"),
    (lambda p: p.update(score_weights=[1.0, float("nan"), 1.0]), "finite"),
])
def test_profile_rejects_invalid_values(tmp_path, mutate, diagnostic):
    payload = read_profile()
    mutate(payload)
    with pytest.raises(ConfigError, match=diagnostic):
        load_activation_eval_config(write_profile(tmp_path, payload))


def test_nested_records_are_frozen_and_asdict_serializable():
    state = SymbolicState("s1", "P → Q", "lean")
    anchor = AnchorRecord(
        problem_id="imp_intro_05", seed=0, theorem="example", current_state=state,
        predecessor_state=None, accepted_prefix=("intro h",), failed_tactics=("exact h",),
        diagnostics=("type mismatch",), state_hash="abc", anchor_hash="def",
        no_stuck_anchor=False,
    )
    candidate = AnalogyCandidateRecord(
        candidate_id="a1", source_domain="physical/mechanical", analogy="valve",
        role_mapping=(("valve", "implication"),), limitation="only a rough map",
        tactics=("intro h",),
    )
    measurement = CandidateMeasurement(
        candidate_id="a1", reasoning_displacement=0.0, causal_effect=None,
        previous_state_value=None, diagnostics=("no predecessor",),
    )
    outcome = ConditionOutcome(
        condition=ActivationCondition.NEUTRAL, generated_tactics=("intro h",),
        verifier_results=("accepted",), successor_state_hashes=("xyz",),
        completed=False, diagnostics=(),
    )
    trial = ActivationTrialRecord(
        problem_id="imp_intro_05", seed=0, anchor=anchor, candidates=(candidate,),
        measurements=(measurement,), outcomes=(outcome,),
    )
    serialized = asdict(trial)
    assert serialized["anchor"]["current_state"]["expression"] == "P → Q"
    assert serialized["measurements"][0]["reasoning_displacement"] == 0.0
    assert serialized["measurements"][0]["causal_effect"] is None
    assert serialized["outcomes"][0]["condition"] == "neutral"
    with pytest.raises((AttributeError, TypeError)):
        anchor.seed = 1


def test_candidate_measurement_rejects_non_finite_metric():
    with pytest.raises(ValueError, match="finite"):
        CandidateMeasurement("a", float("inf"), None, None)
