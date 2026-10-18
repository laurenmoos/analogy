from pathlib import Path

from analogy.lean_gym.models import (
    Diagnostic,
    Problem,
    VerificationStatus,
)
from analogy.lean_gym.verifier import LeanRun, LeanSubprocessVerifier, RenderedSource


ROOT = Path(__file__).parents[1]


def test_completed_probe_does_not_start_a_second_lean_process():
    verifier = LeanSubprocessVerifier(ROOT)
    verifier.verify_complete = lambda *_args: (_ for _ in ()).throw(
        AssertionError("completed probe already proves the tactic")
    )
    run = LeanRun(
        0,
        (Diagnostic("info", "trace", "", 1, 1),),
        5,
    )

    result = verifier._classify_probe(
        object(),
        ("exact h",),
        RenderedSource("", 1, 2),
        run,
    )

    assert result.status is VerificationStatus.COMPLETE
    assert result.accepted is True


def test_initial_state_accepts_multiline_theorem_target():
    verifier = LeanSubprocessVerifier(ROOT)
    problem = Problem(
        id="multiline_target",
        title="multiline target",
        binders="(P Q : Prop)",
        target="P →\n  Q",
        source_path="test",
        source_level="test",
        connective="implication",
        difficulty=1,
    )

    result = verifier.initial_state(problem)

    assert result.status is VerificationStatus.OPEN
    assert result.accepted is True
    assert result.proof_state is not None
    assert result.proof_state.normalized == "P Q : Prop\n⊢ P → Q"
