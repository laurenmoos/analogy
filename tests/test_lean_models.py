from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from analogy.lean_gym.models import Problem, ProofState, VerificationStatus


def test_problem_is_immutable_and_renders_bound_and_binderless_headers() -> None:
    bound = Problem("id", "Title", "(P : Prop)", "P → P", "source", "L01", "implication", 1)
    binderless = Problem("false", "Not false", "", "¬False", "source", "L01", "negation", 1)

    assert bound.theorem_header() == "theorem current_problem (P : Prop) : P → P := by"
    assert binderless.theorem_header("answer") == "theorem answer : ¬False := by"
    with pytest.raises(FrozenInstanceError):
        bound.title = "Changed"  # type: ignore[misc]


def test_proof_state_normalizes_only_unstable_formatting() -> None:
    state = ProofState.from_text("\r\nP : Prop  \r\n⊢ P\r\n\r\n")
    assert state.text.startswith("\r\n")
    assert state.normalized == "P : Prop\n⊢ P"
    assert VerificationStatus.COMPLETE.value == "complete"
