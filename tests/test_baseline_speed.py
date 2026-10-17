from pathlib import Path

from analogy.lean_gym.models import (
    Diagnostic,
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
