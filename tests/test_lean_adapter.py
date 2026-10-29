from pathlib import Path

from analogy.lean_gym.models import (
    Diagnostic,
    Problem,
    ProofState,
    VerificationResult,
    VerificationStatus,
)
from analogy.lean_gym.verifier import LeanSubprocessVerifier
from analogy.utils.records import CandidateOrigin, TacticCandidate, TacticStatus
from analogy.solver.proof.adapter import LeanGymAdapter


PROBLEM = Problem("demo", "Demo", "(P : Prop)", "P", "Demo.lean", "Demo/1", "atom", 1)
LEAN_PROJECT = Path(__file__).parents[1] / "src/analogy/lean_gym/lean_project"


def result(status, *, state=None, message=""):
    diagnostics = (
        (Diagnostic("error", "tactic", message, None, None),) if message else ()
    )
    return VerificationResult(
        status,
        status in {VerificationStatus.OPEN, VerificationStatus.COMPLETE},
        None if state is None else ProofState.from_text(state),
        diagnostics,
        0,
        1,
    )


def candidate(source):
    return TacticCandidate(source, 1.0, CandidateOrigin.ORDINARY)


def test_failed_tactic_feedback_is_scoped_to_the_exact_state():
    class Verifier:
        def initial_state(self, _problem):
            return result(VerificationStatus.OPEN, state="P : Prop\n⊢ P")

        def try_tactic(self, _problem, _prefix, tactic):
            if tactic == "split":
                return result(VerificationStatus.REJECTED, message="cannot split this goal")
            return result(VerificationStatus.OPEN, state="P : Prop\nh : P\n⊢ P")

    adapter = LeanGymAdapter(PROBLEM, Verifier())
    initial = adapter.initial_state()
    adapter.run_tactic(initial, candidate("split"))
    accepted = adapter.run_tactic(initial, candidate("intro h"))

    assert accepted.state is not None
    assert adapter.failed_tactics_for(initial.id) == (
        "current state — split: cannot split this goal",
    )
    assert adapter.failed_tactics_for(accepted.state.id) == ()


def test_open_state_with_unresolved_metavariables_is_rejected():
    class Verifier:
        def initial_state(self, _problem):
            return result(VerificationStatus.OPEN, state="P : Prop\n⊢ P")

        def try_tactic(self, _problem, _prefix, _tactic):
            return result(
                VerificationStatus.OPEN,
                state="?mp.a : Prop\n⊢ ?mp.b",
            )

    adapter = LeanGymAdapter(PROBLEM, Verifier())
    initial = adapter.initial_state()
    attempted = adapter.run_tactic(initial, candidate("apply Iff.elim"))

    assert attempted.status is TacticStatus.ERROR
    assert attempted.state is None
    assert "unresolved metavariables" in attempted.diagnostics[-1]
    assert adapter.failed_tactics_for(initial.id) == (
        "current state — apply Iff.elim: unresolved metavariables",
    )


def test_metavariable_like_text_inside_a_string_is_not_rejected():
    class Verifier:
        def initial_state(self, _problem):
            return result(VerificationStatus.OPEN, state="P : Prop\n⊢ P")

        def try_tactic(self, _problem, _prefix, _tactic):
            return result(
                VerificationStatus.OPEN,
                state='s : String\nh : s = "?pending"\n⊢ True',
            )

    adapter = LeanGymAdapter(PROBLEM, Verifier())
    attempted = adapter.run_tactic(adapter.initial_state(), candidate("intro"))

    assert attempted.status is TacticStatus.ACCEPTED


def test_failure_feedback_keeps_full_lean_diagnostic_and_evicts_old_entries():
    class Verifier:
        def initial_state(self, _problem):
            return result(VerificationStatus.OPEN, state="P : Prop\n⊢ P")

        def try_tactic(self, _problem, _prefix, _tactic):
            return result(
                VerificationStatus.REJECTED,
                message="application type mismatch\nhas type P\nbut is expected to have type Q",
            )

    adapter = LeanGymAdapter(PROBLEM, Verifier())
    initial = adapter.initial_state()
    for index in range(10):
        adapter.run_tactic(initial, candidate(f"long_tactic_{index}"))

    feedback = adapter.failed_tactics_for(initial.id)

    assert len(feedback) == 8
    assert "long_tactic_0" not in "\n".join(feedback)
    assert "long_tactic_9" in feedback[-1]
    assert "has type P" in feedback[-1]
    assert "but is expected to have type Q" in feedback[-1]


def test_normalized_state_and_tactic_cache_avoids_duplicate_lean_execution():
    calls = []

    class Verifier:
        def initial_state(self, _problem):
            return result(VerificationStatus.OPEN, state="case h.h\nP : Prop\n⊢ P")

        def try_tactic(self, _problem, prefix, tactic):
            calls.append((prefix, tactic))
            return result(VerificationStatus.REJECTED, message="full diagnostic")

    adapter = LeanGymAdapter(PROBLEM, Verifier())
    initial = adapter.initial_state()
    equivalent = type(initial)(
        initial.id,
        "case h.h.h\nP : Prop\n⊢ P",
        initial.language,
        ("intro branch",),
    )

    adapter.run_tactic(initial, candidate("exact   h"))
    adapter.run_tactic(equivalent, candidate("exact h"))

    assert calls == [((), "exact   h")]


def test_probe_completion_requires_final_verification():
    calls = []

    class Verifier:
        def initial_state(self, _problem):
            return result(VerificationStatus.OPEN, state="P : Prop\n⊢ P")

        def try_tactic(self, _problem, _prefix, tactic):
            calls.append(("probe", tactic))
            return result(VerificationStatus.COMPLETE)

        def verify_complete(self, _problem, tactics):
            calls.append(("final", tactics))
            return result(
                VerificationStatus.REJECTED,
                message="declaration uses sorry",
            )

    adapter = LeanGymAdapter(PROBLEM, Verifier())
    attempted = adapter.run_tactic(
        adapter.initial_state(), candidate("exact (sorry)")
    )

    assert attempted.status is TacticStatus.ERROR
    assert attempted.proof is None
    assert attempted.diagnostics == ("declaration uses sorry",)
    assert calls == [
        ("probe", "exact (sorry)"),
        ("final", ("exact (sorry)",)),
    ]


def test_verifier_rejects_parenthesized_sorry():
    verifier = LeanSubprocessVerifier(LEAN_PROJECT)

    result = verifier.try_tactic(PROBLEM, (), "exact (sorry)")

    assert result.status is VerificationStatus.REJECTED
    assert result.accepted is False


def test_verifier_rejects_admitted_intermediate_fact():
    verifier = LeanSubprocessVerifier(LEAN_PROJECT)
    problem = Problem(
        "pair",
        "Pair",
        "(P Q : Prop)",
        "P → Q → P ∧ Q",
        "Demo.lean",
        "Demo/2",
        "and",
        2,
    )

    result = verifier.try_tactic(
        problem,
        ("intro hp", "intro hq"),
        "have admitted : P := by sorry",
    )

    assert result.status is VerificationStatus.REJECTED
    assert result.accepted is False
