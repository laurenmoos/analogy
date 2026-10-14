from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from analogy.lean_gym.models import Diagnostic, Problem, ProofState, VerificationResult, VerificationStatus
from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
from analogy.lean_gym.session import ProofSession, SessionClosedError, SessionCommandError
from analogy.lean_gym.traces import JsonlTraceWriter
from analogy.lean_gym.verifier import LeanSubprocessVerifier


IDENTITY = Problem("identity", "Identity", "(P : Prop)", "P → P", "test", "test", "implication", 1)


def test_rejected_tactic_does_not_mutate_real_session(tmp_path: Path) -> None:
    session = ProofSession.start(IDENTITY, LeanSubprocessVerifier(LEAN_PROJECT_ROOT), JsonlTraceWriter(tmp_path / "trace.jsonl"), "fixed")
    session.submit_tactic("intro h")
    before = session.snapshot()

    rejected = session.submit_tactic("exact missing")

    assert rejected.status is VerificationStatus.REJECTED
    assert session.snapshot() == before
    attempts = [event for event in session.events if event.event_type == "tactic_attempt"]
    assert [event.payload["accepted"] for event in attempts] == [True, False]
    session.close()


def test_complete_undo_reset_save_and_close_lifecycle(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    session = ProofSession.start(IDENTITY, LeanSubprocessVerifier(LEAN_PROJECT_ROOT), JsonlTraceWriter(trace_path), "fixed")
    session.submit_tactic("intro h")
    completed = session.submit_tactic("exact h")
    assert completed.status is VerificationStatus.COMPLETE
    assert session.snapshot().complete is True
    with pytest.raises(SessionCommandError, match="complete"):
        session.submit_tactic("exact h")

    partial_path = tmp_path / "proofs/identity.lean"
    session.save(partial_path)
    assert "exact h" in partial_path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        session.save(partial_path)

    undone = session.undo()
    assert undone.complete is False
    assert undone.accepted_tactics == ("intro h",)
    reset = session.reset()
    assert reset.accepted_tactics == ()
    with pytest.raises(SessionCommandError):
        session.undo()

    session.close()
    session.close()
    with pytest.raises(SessionClosedError):
        session.submit_tactic("intro h")
    event_types = [event.event_type for event in session.events]
    assert event_types.count("session_end") == 1
    assert [event.event_index for event in session.events] == list(range(len(session.events)))

    environment = {**os.environ, "ELAN_TOOLCHAIN": "leanprover/lean4:v4.29.1"}
    compiled = subprocess.run(["lake", "env", "lean", str(partial_path)], cwd=LEAN_PROJECT_ROOT, env=environment, capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr


def test_start_failure_does_not_write_trace(tmp_path: Path) -> None:
    impossible = Problem("bad", "Bad", "", "UnknownName", "test", "test", "bad", 1)
    path = tmp_path / "bad.jsonl"
    with pytest.raises(Exception, match="initialize"):
        ProofSession.start(impossible, LeanSubprocessVerifier(LEAN_PROJECT_ROOT), JsonlTraceWriter(path))
    assert path.read_text(encoding="utf-8") == ""


def test_timeout_attempt_preserves_session_state(tmp_path: Path) -> None:
    class TimeoutVerifier:
        def initial_state(self, problem: Problem) -> VerificationResult:
            state = ProofState.from_text("P : Prop\n⊢ P → P")
            return VerificationResult(VerificationStatus.OPEN, True, state, (), 0, 1)

        def state_after(self, problem: Problem, tactics: tuple[str, ...]) -> VerificationResult:
            return self.initial_state(problem)

        def try_tactic(self, problem: Problem, tactics: tuple[str, ...], candidate: str) -> VerificationResult:
            diagnostic = Diagnostic("error", "timeout", "Lean timed out", None, None)
            return VerificationResult(VerificationStatus.TIMEOUT, False, None, (diagnostic,), None, 10)

        def verify_complete(self, problem: Problem, tactics: tuple[str, ...]) -> VerificationResult:
            raise AssertionError("not used")

    session = ProofSession.start(IDENTITY, TimeoutVerifier(), JsonlTraceWriter(tmp_path / "timeout.jsonl"))
    before = session.snapshot()
    result = session.submit_tactic("intro h")
    assert result.status is VerificationStatus.TIMEOUT
    assert session.snapshot() == before
    assert session.events[-1].payload["accepted"] is False
    session.close()
