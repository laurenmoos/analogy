from __future__ import annotations

import json
from pathlib import Path

import pytest

from analogy.lean_gym.models import Problem
from analogy.lean_gym.replay import TraceValidationError, normalize_state, replay_trace
from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
from analogy.lean_gym.session import ProofSession
from analogy.lean_gym.traces import JsonlTraceWriter
from analogy.lean_gym.verifier import LeanSubprocessVerifier


IDENTITY = Problem("identity", "Identity", "(P : Prop)", "P → P", "test", "test", "implication", 1)


def record_trace(path: Path) -> None:
    session = ProofSession.start(IDENTITY, LeanSubprocessVerifier(LEAN_PROJECT_ROOT), JsonlTraceWriter(path), "fixed")
    session.submit_tactic("intro h")
    session.submit_tactic("exact missing")
    session.undo()
    session.reset()
    session.submit_tactic("intro h")
    session.submit_tactic("exact h")
    session.close()


def test_real_trace_replays_accepted_rejected_undo_and_reset(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    record_trace(path)

    result = replay_trace(path, {IDENTITY.id: IDENTITY}, LeanSubprocessVerifier(LEAN_PROJECT_ROOT))

    assert result.matched is True
    assert result.checked_events == 4
    assert result.divergent_event_index is None


def test_replay_reports_first_semantic_divergence(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    record_trace(path)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    attempt = next(item for item in records if item["event_type"] == "tactic_attempt" and item["accepted"])
    attempt["state_after"] = "P : Prop\nh : P\n⊢ Q"
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")

    result = replay_trace(path, {IDENTITY.id: IDENTITY}, LeanSubprocessVerifier(LEAN_PROJECT_ROOT))

    assert result.matched is False
    assert result.divergent_event_index == attempt["event_index"]
    assert result.expected is not None
    assert result.actual is not None


def test_state_normalization_removes_only_transport_noise() -> None:
    left = "/private/tmp/lean-gym-abc/Current.lean:3:2\r\nP : Prop  \r\n⊢ P\r\n"
    right = "/private/tmp/lean-gym-def/Current.lean:8:5\nP : Prop\n⊢ P"
    assert normalize_state(left) == normalize_state(right)
    assert normalize_state("⊢ P") != normalize_state("⊢ Q")


def test_replay_rejects_malformed_or_unsupported_trace(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text("not json\n", encoding="utf-8")
    with pytest.raises(TraceValidationError, match="line 1"):
        replay_trace(malformed, {IDENTITY.id: IDENTITY}, LeanSubprocessVerifier(LEAN_PROJECT_ROOT))

    unsupported = tmp_path / "unsupported.jsonl"
    unsupported.write_text(json.dumps({"schema_version": 2}) + "\n", encoding="utf-8")
    with pytest.raises(TraceValidationError, match="schema_version"):
        replay_trace(unsupported, {IDENTITY.id: IDENTITY}, LeanSubprocessVerifier(LEAN_PROJECT_ROOT))


def test_replay_rejects_non_string_state_instead_of_crashing(tmp_path: Path) -> None:
    path = tmp_path / "bad-state.jsonl"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": "fixed",
                "problem_id": "identity",
                "event_index": 0,
                "event_type": "session_start",
                "timestamp": "2026-10-12T00:00:00Z",
                "state_after": 42,
                "accepted_prefix": [],
                "complete": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(TraceValidationError, match="state_after"):
        replay_trace(path, {IDENTITY.id: IDENTITY}, LeanSubprocessVerifier(LEAN_PROJECT_ROOT))
