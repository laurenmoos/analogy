from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .models import Problem, ReplayResult, VerificationStatus, normalize_proof_state_text
from .verifier import Verifier


class TraceValidationError(ValueError):
    pass


def normalize_state(value: str | None) -> str | None:
    return None if value is None else normalize_proof_state_text(value)


def _read_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise TraceValidationError(f"{path}: {exc}") from exc
    for number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TraceValidationError(f"line {number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise TraceValidationError(f"line {number}: expected object")
        if record.get("schema_version") != 1:
            raise TraceValidationError(f"line {number}: unsupported schema_version {record.get('schema_version')!r}")
        required = {"session_id", "problem_id", "event_index", "event_type", "timestamp"}
        missing = required - record.keys()
        if missing:
            raise TraceValidationError(f"line {number}: missing keys {sorted(missing)}")
        if not isinstance(record["session_id"], str) or not isinstance(record["problem_id"], str):
            raise TraceValidationError(f"line {number}: session_id and problem_id must be strings")
        if isinstance(record["event_index"], bool) or not isinstance(record["event_index"], int):
            raise TraceValidationError(f"line {number}: event_index must be an integer")
        if not isinstance(record["event_type"], str) or not isinstance(record["timestamp"], str):
            raise TraceValidationError(f"line {number}: event_type and timestamp must be strings")
        for state_key in ("state_before", "state_after"):
            if state_key in record and record[state_key] is not None and not isinstance(record[state_key], str):
                raise TraceValidationError(f"line {number}: {state_key} must be a string or null")
        if "accepted_prefix" in record:
            prefix = record["accepted_prefix"]
            if not isinstance(prefix, list) or any(not isinstance(tactic, str) for tactic in prefix):
                raise TraceValidationError(f"line {number}: accepted_prefix must be a string array")
        records.append(record)
    if not records:
        raise TraceValidationError("trace is empty")
    return records


def _divergence(index: int, checked: int, expected: Mapping[str, Any], actual: Mapping[str, Any]) -> ReplayResult:
    return ReplayResult(False, checked, index, dict(expected), dict(actual))


def replay_trace(path: Path, problems: Mapping[str, Problem], verifier: Verifier) -> ReplayResult:
    records = _read_records(path)
    first = records[0]
    if first["event_type"] != "session_start":
        raise TraceValidationError("first event must be session_start")
    session_id = first["session_id"]
    problem_id = first["problem_id"]
    if problem_id not in problems:
        raise TraceValidationError(f"unknown problem id {problem_id!r}")
    for expected_index, record in enumerate(records):
        if record["event_index"] != expected_index:
            raise TraceValidationError(f"event index {record['event_index']} is not expected index {expected_index}")
        if record["session_id"] != session_id or record["problem_id"] != problem_id:
            raise TraceValidationError(f"event {expected_index}: mixed session or problem ids")

    problem = problems[problem_id]
    initial = verifier.initial_state(problem)
    if initial.status is not VerificationStatus.OPEN or initial.proof_state is None:
        raise TraceValidationError("problem no longer has a valid initial state")
    current_state: str | None = initial.proof_state.text
    expected_start = first.get("state_after")
    if normalize_state(expected_start) != normalize_state(current_state):
        return _divergence(0, 0, {"state_after": expected_start}, {"state_after": current_state})

    prefix: tuple[str, ...] = ()
    checked = 0
    allowed = {"session_start", "tactic_attempt", "undo", "reset", "save", "completion", "session_end"}
    for record in records[1:]:
        index = record["event_index"]
        event_type = record["event_type"]
        if event_type not in allowed:
            raise TraceValidationError(f"event {index}: unknown event type {event_type!r}")
        if event_type == "tactic_attempt":
            tactic = record.get("tactic")
            if not isinstance(tactic, str) or not isinstance(record.get("accepted"), bool) or not isinstance(record.get("status"), str):
                raise TraceValidationError(f"event {index}: invalid tactic attempt payload")
            result = verifier.try_tactic(problem, prefix, tactic)
            actual_prefix = prefix
            actual_state = current_state
            if result.accepted:
                actual_prefix = prefix + (tactic,)
                actual_state = result.proof_state.text if result.proof_state else None
            expected = {
                "accepted": record["accepted"],
                "status": record["status"],
                "state_after": normalize_state(record.get("state_after")),
                "accepted_prefix": record.get("accepted_prefix"),
            }
            actual = {
                "accepted": result.accepted,
                "status": result.status.value,
                "state_after": normalize_state(actual_state),
                "accepted_prefix": list(actual_prefix),
            }
            if expected != actual:
                return _divergence(index, checked, expected, actual)
            prefix, current_state = actual_prefix, actual_state
            checked += 1
        elif event_type == "undo":
            if not prefix:
                raise TraceValidationError(f"event {index}: undo with empty prefix")
            prefix = prefix[:-1]
            state = verifier.state_after(problem, prefix)
            if state.status is not VerificationStatus.OPEN or state.proof_state is None:
                raise TraceValidationError(f"event {index}: unable to reconstruct undo")
            current_state = state.proof_state.text
            if normalize_state(record.get("state_after")) != normalize_state(current_state):
                return _divergence(index, checked, {"state_after": record.get("state_after")}, {"state_after": current_state})
        elif event_type == "reset":
            prefix = ()
            state = verifier.initial_state(problem)
            if state.status is not VerificationStatus.OPEN or state.proof_state is None:
                raise TraceValidationError(f"event {index}: unable to reconstruct reset")
            current_state = state.proof_state.text
            if normalize_state(record.get("state_after")) != normalize_state(current_state):
                return _divergence(index, checked, {"state_after": record.get("state_after")}, {"state_after": current_state})
    return ReplayResult(True, checked)
