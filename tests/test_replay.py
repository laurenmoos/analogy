from dataclasses import replace
from pathlib import Path

import pytest

from analogy.lean_gym.models import Problem, ProofState, VerificationResult, VerificationStatus
from analogy.replay import (
    CurriculumSampler,
    JsonlStuckStateStore,
    ReplayMismatchError,
    StuckStateRecord,
    proof_state_fingerprint,
    reconstruct_stuck_state,
)


def record(identifier="p", connective="and", difficulty=1, split="train"):
    text = "h : P\n⊢ P"
    return StuckStateRecord(
        Problem(identifier, identifier, "(P : Prop) (h : P)", "P", "x", "fixture", connective, difficulty),
        split,
        ("intro",),
        text,
        proof_state_fingerprint(text),
        ("exact h",),
        ("bad",),
    )


def test_store_deduplicates_by_problem_and_fingerprint(tmp_path: Path):
    store = JsonlStuckStateStore(tmp_path / "states.jsonl")
    item = record()
    store.add(item); store.add(item)
    assert store.load() == (item,)


def test_reconstruction_rejects_normalized_state_mismatch():
    item = record()
    class Verifier:
        def state_after(self, problem, tactics):
            return VerificationResult(VerificationStatus.OPEN, True, ProofState.from_text("⊢ forged"), (), 0, 1)
    with pytest.raises(ReplayMismatchError):
        reconstruct_stuck_state(item, Verifier())


def test_curriculum_is_deterministic_stratified_and_split_safe():
    values = (record("a", "and", 2), record("b", "or", 1), record("c", "and", 1), record("v", split="validation"))
    first = CurriculumSampler(values, seed=7).sample(3)
    assert first == CurriculumSampler(values, seed=7).sample(3)
    assert {item.problem.connective for item in first[:2]} == {"and", "or"}
    assert all(item.split == "train" for item in first)
    with pytest.raises(ValueError, match="not enough"):
        CurriculumSampler(values).sample(4)
