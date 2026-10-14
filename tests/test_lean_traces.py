from __future__ import annotations

import json
from pathlib import Path

import pytest

from analogy.lean_gym.traces import JsonlTraceWriter, make_event


def test_writer_appends_and_flushes_one_unicode_json_object_per_event(tmp_path: Path) -> None:
    path = tmp_path / "nested/session.jsonl"
    writer = JsonlTraceWriter(path)
    writer.write(make_event("session", "identity", 0, "tactic_attempt", {"tactic": "exact ⟨p, q⟩"}))

    first = path.read_text(encoding="utf-8")
    assert first.endswith("\n")
    assert len(first.splitlines()) == 1
    assert json.loads(first)["tactic"] == "exact ⟨p, q⟩"
    assert json.loads(first)["timestamp"].endswith("Z")

    writer.write(make_event("session", "identity", 1, "session_end", {"reason": "quit"}))
    writer.close()
    assert [json.loads(line)["event_index"] for line in path.read_text(encoding="utf-8").splitlines()] == [0, 1]


def test_writer_preserves_prior_event_when_later_event_is_invalid(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    writer = JsonlTraceWriter(path)
    writer.write(make_event("session", "identity", 0, "session_start", {}))
    bad = make_event("session", "identity", 1, "save", {"session_id": "collision"})
    with pytest.raises(ValueError, match="collides"):
        writer.write(bad)
    writer.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event_type"] == "session_start"


def test_writer_rejects_writes_after_close(tmp_path: Path) -> None:
    writer = JsonlTraceWriter(tmp_path / "session.jsonl")
    writer.close()
    with pytest.raises(ValueError, match="closed"):
        writer.write(make_event("session", "identity", 0, "session_end", {}))
