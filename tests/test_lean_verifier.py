from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from analogy.lean_gym.models import Diagnostic, Problem, VerificationStatus
from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
from analogy.lean_gym.verifier import LeanRun, LeanSubprocessVerifier


def problem(binders: str = "(P : Prop)", target: str = "P → P") -> Problem:
    return Problem("identity", "Identity", binders, target, "test", "test", "implication", 1)


@pytest.fixture
def verifier() -> LeanSubprocessVerifier:
    return LeanSubprocessVerifier(LEAN_PROJECT_ROOT)


def test_real_lean_tracks_open_rejected_and_complete_steps(verifier: LeanSubprocessVerifier) -> None:
    initial = verifier.initial_state(problem())
    assert initial.status is VerificationStatus.OPEN
    assert initial.proof_state is not None
    assert "⊢ P → P" in initial.proof_state.text

    opened = verifier.try_tactic(problem(), (), "intro h")
    assert opened.status is VerificationStatus.OPEN
    assert opened.accepted is True
    assert opened.proof_state is not None
    assert "h : P" in opened.proof_state.text
    assert "⊢ P" in opened.proof_state.text

    rejected = verifier.try_tactic(problem(), ("intro h",), "exact missing")
    assert rejected.status is VerificationStatus.REJECTED
    assert rejected.accepted is False
    assert any("Unknown identifier" in diagnostic.message for diagnostic in rejected.diagnostics)

    complete = verifier.try_tactic(problem(), ("intro h",), "exact h")
    assert complete.status is VerificationStatus.COMPLETE
    assert complete.accepted is True
    assert complete.proof_state is None


def test_real_lean_preserves_multiple_goals(verifier: LeanSubprocessVerifier) -> None:
    two = problem("(P Q : Prop)", "P → Q → P ∧ Q")
    prefix = ("intro p", "intro q")
    split = verifier.try_tactic(two, prefix, "constructor")
    assert split.status is VerificationStatus.OPEN
    assert split.proof_state is not None
    assert split.proof_state.text.count("⊢") == 2

    first = verifier.try_tactic(two, prefix + ("constructor",), "exact p")
    assert first.status is VerificationStatus.OPEN
    assert first.proof_state is not None
    assert first.proof_state.text.count("⊢") == 1

    final = verifier.try_tactic(two, prefix + ("constructor", "exact p"), "exact q")
    assert final.status is VerificationStatus.COMPLETE


@pytest.mark.parametrize("candidate", ["", "   ", "intro h\nend", "intro h\rend", "intro\x00h", "x" * 4097])
def test_invalid_tactic_is_rejected_without_starting_lean(monkeypatch: pytest.MonkeyPatch, candidate: str) -> None:
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess must not start")

    monkeypatch.setattr(subprocess, "run", forbidden)
    result = LeanSubprocessVerifier(LEAN_PROJECT_ROOT).try_tactic(problem(), (), candidate)
    assert result.status is VerificationStatus.REJECTED
    assert result.diagnostics[0].kind == "input_validation"
    assert called is False


def test_verify_complete_rejects_unsolved_proof(verifier: LeanSubprocessVerifier) -> None:
    result = verifier.verify_complete(problem(), ("intro h",))
    assert result.status is VerificationStatus.REJECTED
    assert result.accepted is False


def test_empty_controlled_trace_is_not_complete_when_clean_pass_fails() -> None:
    class ScriptedVerifier(LeanSubprocessVerifier):
        def __init__(self) -> None:
            super().__init__(LEAN_PROJECT_ROOT)
            self.calls = 0

        def _run_lean(self, source: str) -> LeanRun:
            self.calls += 1
            if self.calls == 1:
                return LeanRun(0, (Diagnostic("information", "trace", "", 3, 2),), 1)
            return LeanRun(1, (Diagnostic("error", "type", "clean proof failed", 2, 2),), 1)

    result = ScriptedVerifier().try_tactic(problem(), (), "exact fun h => h")
    assert result.status is VerificationStatus.REJECTED
    assert result.accepted is False


def test_empty_trace_at_uncontrolled_line_is_rejected_without_clean_pass() -> None:
    class SpoofedVerifier(LeanSubprocessVerifier):
        def __init__(self) -> None:
            super().__init__(LEAN_PROJECT_ROOT)
            self.calls = 0

        def _run_lean(self, source: str) -> LeanRun:
            self.calls += 1
            return LeanRun(0, (Diagnostic("information", "trace", "", 99, 2),), 1)

    verifier = SpoofedVerifier()
    result = verifier.try_tactic(problem(), (), "exact fun h => h")
    assert result.status is VerificationStatus.REJECTED
    assert verifier.calls == 1
