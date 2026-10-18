from analogy.lean_gym.models import (
    Diagnostic,
    Problem,
    ProofState,
    VerificationResult,
    VerificationStatus,
)
from analogy.records import CandidateOrigin, TacticCandidate, TacticStatus
from analogy.solver.lean_adapter import LeanGymAdapter


PROBLEM = Problem("demo", "Demo", "(P : Prop)", "P", "Demo.lean", "Demo/1", "atom", 1)


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


def test_failed_tactic_feedback_is_available_to_later_states():
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
    assert adapter.failed_tactics_for(accepted.state.id) == (
        "ancestor state — split: cannot split this goal",
    )


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


def test_failure_feedback_is_bounded_and_evicts_old_entries():
    class Verifier:
        def initial_state(self, _problem):
            return result(VerificationStatus.OPEN, state="P : Prop\n⊢ P")

        def try_tactic(self, _problem, _prefix, _tactic):
            return result(VerificationStatus.REJECTED, message="x" * 500)

    adapter = LeanGymAdapter(PROBLEM, Verifier())
    initial = adapter.initial_state()
    for index in range(10):
        adapter.run_tactic(initial, candidate(f"long_tactic_{index}_" + "y" * 500))

    feedback = adapter.failed_tactics_for(initial.id)

    assert len(feedback) == 8
    assert "long_tactic_0" not in "\n".join(feedback)
    assert "long_tactic_9" in feedback[-1]
    assert sum(map(len, feedback)) <= 1024
