"""Immutable post-hoc audits for completed analogy experiment runs."""

from __future__ import annotations

import json
import hashlib
import math
import os
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .lean_gym.corpus import CorpusValidationError, load_problems
from .progress_judge import AuditIteration, LLMProcessJudge


class AuditRunError(ValueError):
    pass


_ANALOGY_PHASES = frozenset({"train", "validation", "analogy_loop"})


@dataclass(frozen=True, slots=True)
class AuditSummary:
    source_run: str
    output_directory: str
    eligible: int
    attempted: int
    judged: int
    failures: int
    coverage: float
    stage_counts: tuple[tuple[str, int], ...]
    mean_progress_score: float | None
    mean_analogy_quality: float | None
    mean_confidence: float | None
    model_calls: int
    run_complete: bool
    source_summary: dict[str, Any]


def audit_analogy_run(
    source_run: Path,
    output_dir: Path,
    generator: Any,
    corpus: Path,
    *,
    seed: int,
    max_calls: int,
    max_repair_calls: int = 1,
) -> AuditSummary:
    source = Path(source_run).resolve()
    output = Path(output_dir).resolve()
    for value, name in ((max_calls, "max_calls"),):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if (
        isinstance(max_repair_calls, bool)
        or not isinstance(max_repair_calls, int)
        or max_repair_calls < 0
    ):
        raise ValueError("max_repair_calls must be a non-negative integer")
    if output == source or source in output.parents:
        raise AuditRunError("audit output must be outside the source run")

    iterations = load_audit_iterations(source, Path(corpus))
    source_hash = _tree_hash(source)
    corpus_hash = _file_hash(Path(corpus))
    source_summary = _read_json_object(source / "summary.json", "summary.json")
    model_id = str(
        getattr(generator, "model_revision", None)
        or getattr(generator, "model_name", None)
        or type(generator).__name__
    )
    identity = {
        "schema_version": 1,
        "source_run": str(source),
        "source_hash": source_hash,
        "corpus": str(Path(corpus).resolve()),
        "corpus_hash": corpus_hash,
        "model": model_id,
        "seed": seed,
        "max_repair_calls": max_repair_calls,
    }
    config_path = output / "config.json"
    judgments_path = output / "judgments.jsonl"
    if output.exists():
        if not output.is_dir() or not config_path.is_file() or not judgments_path.is_file():
            raise AuditRunError("existing audit output is incomplete or not a directory")
        existing = _read_json_object(config_path, "audit config.json")
        for name, value in identity.items():
            if existing.get(name) != value:
                label = "source" if name in {"source_run", "source_hash"} else name
                raise AuditRunError(f"audit resume {label} does not match")
    else:
        output.mkdir(parents=True, exist_ok=False)
        judgments_path.touch(exist_ok=False)

    active_config = {
        **identity,
        "max_calls": max_calls,
        "run_complete": False,
    }
    _write_json_atomic(config_path, active_config)
    existing_rows = _read_judgment_rows(judgments_path)
    completed_keys = {_row_key(row) for row in existing_rows}
    if len(completed_keys) != len(existing_rows):
        raise AuditRunError("judgments.jsonl contains duplicate iteration keys")

    judge = LLMProcessJudge(
        generator, base_seed=seed, max_repair_calls=max_repair_calls
    )
    target_count = min(len(iterations), max_calls)
    rows = list(existing_rows)
    with judgments_path.open("a", encoding="utf-8", newline="\n") as handle:
        for item in iterations:
            if len(rows) >= target_count:
                break
            key = _iteration_key(item)
            if key in completed_keys:
                continue
            attempt = judge.evaluate(item)
            row = {
                "problem_id": item.problem_id,
                "phase": item.phase,
                "rollout_id": item.rollout_id,
                "iteration_index": item.iteration_index,
                "input": asdict(item),
                "judgment": (
                    None if attempt.judgment is None else asdict(attempt.judgment)
                ),
                "raw_outputs": list(attempt.raw_outputs),
                "seeds": list(attempt.seeds),
                "failure": attempt.failure,
            }
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            rows.append(row)
            completed_keys.add(key)

    if _tree_hash(source) != source_hash:
        raise AuditRunError("source run changed while it was being audited")
    run_complete = len(rows) >= target_count
    summary = _summarize(
        source,
        output,
        len(iterations),
        rows,
        run_complete,
        source_summary,
    )
    _write_json_atomic(output / "summary.json", asdict(summary))
    _write_json_atomic(config_path, {**active_config, "run_complete": run_complete})
    return summary


def load_audit_iterations(
    source_run: Path, corpus: Path
) -> tuple[AuditIteration, ...]:
    source = Path(source_run)
    required = {
        name: source / name
        for name in ("config.json", "summary.json", "analogy_iterations.jsonl")
    }
    for name, path in required.items():
        if not path.is_file():
            raise AuditRunError(f"source run is missing required {name}")
    _read_json_object(required["config.json"], "config.json")
    summary = _read_json_object(required["summary.json"], "summary.json")
    run_id = summary.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise AuditRunError("summary.json run_id must be nonblank text")
    try:
        problems = {item.id: item for item in load_problems(Path(corpus))}
    except (CorpusValidationError, OSError) as error:
        raise AuditRunError(f"invalid problem corpus: {error}") from error

    loaded: list[AuditIteration] = []
    seen: set[tuple[str, str, str, int]] = set()
    try:
        lines = required["analogy_iterations.jsonl"].read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError as error:
        raise AuditRunError(f"could not read analogy_iterations.jsonl: {error}") from error
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise AuditRunError(
                f"analogy_iterations.jsonl line {line_number}: {error.msg}"
            ) from error
        if not isinstance(row, dict):
            raise AuditRunError(
                f"analogy_iterations.jsonl line {line_number}: expected object"
            )
        phase = row.get("phase")
        if phase not in _ANALOGY_PHASES:
            continue
        try:
            normalized = _normalize_iteration(run_id, row, problems)
        except (KeyError, TypeError, ValueError) as error:
            raise AuditRunError(
                f"analogy_iterations.jsonl line {line_number}: {error}"
            ) from error
        key = (
            normalized.problem_id,
            normalized.phase,
            normalized.rollout_id,
            normalized.iteration_index,
        )
        if key in seen:
            raise AuditRunError(
                f"analogy_iterations.jsonl line {line_number}: duplicate key {key!r}"
            )
        seen.add(key)
        loaded.append(normalized)
    return tuple(loaded)


def _normalize_iteration(
    run_id: str, row: dict[str, Any], problems: dict[str, Any]
) -> AuditIteration:
    problem_id = _text(row["problem_id"], "problem_id")
    if problem_id not in problems:
        raise ValueError(f"unknown problem_id {problem_id!r}")
    state_before = _object(row["state_before"], "state_before")
    hop = _object(row["hop"], "hop")
    tactic_plan = _object(row["tactic_plan"], "tactic_plan")
    decision = _object(row["decision"], "decision")
    attempts = row["attempts"]
    if not isinstance(attempts, list):
        raise ValueError("attempts must be an array")
    outcomes: list[str] = []
    diagnostics: list[str] = []
    for attempt_index, attempt_value in enumerate(attempts):
        attempt = _object(attempt_value, f"attempts[{attempt_index}]")
        result = _object(attempt["result"], f"attempts[{attempt_index}].result")
        outcomes.append(_text(result["status"], f"attempts[{attempt_index}].status"))
        raw_diagnostics = result.get("diagnostics", [])
        if not isinstance(raw_diagnostics, list) or not all(
            isinstance(item, str) for item in raw_diagnostics
        ):
            raise ValueError(
                f"attempts[{attempt_index}].diagnostics must be a string array"
            )
        diagnostics.extend(raw_diagnostics)
    tactics = tactic_plan["tactics"]
    if not isinstance(tactics, list) or not all(isinstance(item, str) for item in tactics):
        raise ValueError("tactic_plan.tactics must be a string array")
    actual = row.get("actual_transition")
    if actual is not None:
        actual = _text(actual, "actual_transition")
    comparison = row.get("comparison")
    surprise = None
    if comparison is not None:
        surprise = _object(comparison, "comparison").get("surprise")
    reason = decision.get("reason")
    if hasattr(reason, "value"):
        reason = reason.value
    return AuditIteration(
        run_id=run_id,
        problem_id=problem_id,
        phase=_text(row["phase"], "phase"),
        rollout_id=_text(row["rollout_id"], "rollout_id"),
        iteration_index=row["iteration_index"],
        target=problems[problem_id].target,
        state_before=_text(state_before["expression"], "state_before.expression"),
        analogy_card=json.dumps(hop, sort_keys=True, separators=(",", ":")),
        predicted_transition=_text(
            row["predicted_transition"], "predicted_transition"
        ),
        tactics=tuple(tactics),
        lean_outcomes=tuple(outcomes),
        lean_diagnostics=tuple(diagnostics),
        actual_transition=actual,
        surprise=surprise,
        stop_decision=_text(reason, "decision.reason"),
    )


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuditRunError(f"invalid {label}: {error}") from error
    if not isinstance(value, dict):
        raise AuditRunError(f"invalid {label}: expected object")
    return value


def _summarize(
    source: Path,
    output: Path,
    eligible: int,
    rows: list[dict[str, Any]],
    run_complete: bool,
    source_summary: dict[str, Any],
) -> AuditSummary:
    judgments = [row["judgment"] for row in rows if row.get("judgment") is not None]
    stage_counts = Counter(item["stage"] for item in judgments)
    return AuditSummary(
        source_run=str(source),
        output_directory=str(output),
        eligible=eligible,
        attempted=len(rows),
        judged=len(judgments),
        failures=sum(row.get("failure") is not None for row in rows),
        coverage=(len(rows) / eligible if eligible else 0.0),
        stage_counts=tuple(sorted(stage_counts.items())),
        mean_progress_score=_mean(judgments, "progress_score"),
        mean_analogy_quality=_mean(judgments, "analogy_quality"),
        mean_confidence=_mean(judgments, "confidence"),
        model_calls=sum(len(row.get("raw_outputs", ())) for row in rows),
        run_complete=run_complete,
        source_summary=source_summary,
    )


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    values = [float(row[key]) for row in rows]
    if any(not math.isfinite(value) for value in values):
        raise AuditRunError(f"persisted judgment {key} must be finite")
    return sum(values) / len(values)


def _read_judgment_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise AuditRunError(
                f"judgments.jsonl line {line_number}: {error.msg}"
            ) from error
        if not isinstance(row, dict):
            raise AuditRunError(f"judgments.jsonl line {line_number}: expected object")
        try:
            _row_key(row)
        except (KeyError, TypeError, ValueError) as error:
            raise AuditRunError(
                f"judgments.jsonl line {line_number}: invalid key: {error}"
            ) from error
        rows.append(row)
    return rows


def _iteration_key(item: AuditIteration) -> tuple[str, str, str, int]:
    return item.problem_id, item.phase, item.rollout_id, item.iteration_index


def _row_key(row: dict[str, Any]) -> tuple[str, str, str, int]:
    index = row["iteration_index"]
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("iteration_index must be a non-negative integer")
    return (
        _text(row["problem_id"], "problem_id"),
        _text(row["phase"], "phase"),
        _text(row["rollout_id"], "rollout_id"),
        index,
    )


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = str(path.relative_to(root)).encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise AuditRunError(f"could not hash corpus {path}: {error}") from error
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except OSError as error:
        raise AuditRunError(f"could not write audit artifact {path}: {error}") from error


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank text")
    return value


__all__ = [
    "AuditRunError",
    "AuditSummary",
    "audit_analogy_run",
    "load_audit_iterations",
]
