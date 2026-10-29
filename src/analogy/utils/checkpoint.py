"""File-backed checkpoint utilities for repeat experiment runs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from ..config import AppConfig
from ..lean_gym.models import Problem


_SCHEMA_VERSION = 1


def _problem_fingerprint(problem: Problem) -> str:
    encoded = json.dumps(
        asdict(problem), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _signature(config: AppConfig, condition: str) -> str:
    prompt_hash = hashlib.sha256(config.prompts.path.read_bytes()).hexdigest()
    calibration_hash = (
        hashlib.sha256(config.analogy.calibration_path.read_bytes()).hexdigest()
        if config.analogy.calibration_path is not None
        else None
    )
    payload = {
        "condition": condition,
        "corpus": str(config.benchmark.corpus),
        "prompts_sha256": prompt_hash,
        "calibration_sha256": calibration_hash,
        "model": asdict(config.model),
        "search": asdict(config.search),
        "analogy": asdict(config.analogy),
        "run_seed": config.run.seed,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_payload(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"schema_version": _SCHEMA_VERSION, "experiments": {}}
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != _SCHEMA_VERSION
        or not isinstance(value.get("experiments"), dict)
    ):
        return {"schema_version": _SCHEMA_VERSION, "experiments": {}}
    return value


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


@dataclass(slots=True)
class Checkpoint:
    path: Path
    signature: str
    problems: tuple[Problem, ...]
    fingerprints: dict[str, str]
    statuses: dict[str, str]


def _reconstruct(
    output_dir: Path,
    signature: str,
    fingerprints: dict[str, str],
) -> dict[str, str]:
    statuses: dict[str, str] = {}
    root = output_dir / "solve"
    paths = (
        sorted(
            root.glob("*/run.json"),
            key=lambda item: (item.stat().st_mtime_ns, str(item)),
        )
        if root.exists()
        else ()
    )
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("problem_index_signature") != signature:
            continue
        artifact_fingerprints = payload.get("problem_index_fingerprints", {})
        if not isinstance(artifact_fingerprints, dict):
            continue
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            summary = payload.get("inference", {})
        results = summary.get("results", []) if isinstance(summary, dict) else []
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            problem_id, status = result.get("problem_id"), result.get("status")
            if (
                isinstance(problem_id, str)
                and isinstance(status, str)
                and artifact_fingerprints.get(problem_id)
                == fingerprints.get(problem_id)
            ):
                statuses[problem_id] = status
    return statuses


def _persist_checkpoint(checkpoint: Checkpoint) -> None:
    payload = _read_payload(checkpoint.path)
    payload["experiments"][checkpoint.signature] = {
        "problem_ids": [problem.id for problem in checkpoint.problems],
        "problem_fingerprints": dict(sorted(checkpoint.fingerprints.items())),
        "statuses": dict(sorted(checkpoint.statuses.items())),
    }
    _atomic_write(checkpoint.path, payload)


def load_checkpoint(
    config: AppConfig,
    problems: Sequence[Problem],
    *,
    condition: str | None = None,
) -> Checkpoint:
    normalized = tuple(problems)
    active_condition = condition or (
        "analogy" if config.analogy.enabled else "ordinary"
    )
    if active_condition not in {"ordinary", "deliberation", "analogy"}:
        raise ValueError(f"unknown solver condition {active_condition!r}")
    signature = _signature(config, active_condition)
    fingerprints = {
        problem.id: _problem_fingerprint(problem) for problem in normalized
    }
    path = config.run.output_dir.parent / "problem-index.json"
    payload = _read_payload(path)
    record = payload["experiments"].get(signature, {})
    statuses = record.get("statuses", {}) if isinstance(record, dict) else {}
    stored_fingerprints = (
        record.get("problem_fingerprints", {})
        if isinstance(record, dict)
        else {}
    )
    statuses = (
        {
            key: value
            for key, value in statuses.items()
            if (
                isinstance(key, str)
                and isinstance(value, str)
                and isinstance(stored_fingerprints, dict)
                and stored_fingerprints.get(key) == fingerprints.get(key)
            )
        }
        if isinstance(statuses, dict)
        else {}
    )
    if not statuses:
        statuses = _reconstruct(config.run.output_dir, signature, fingerprints)
    checkpoint = Checkpoint(path, signature, normalized, fingerprints, statuses)
    _persist_checkpoint(checkpoint)
    return checkpoint


def select_problems(checkpoint: Checkpoint, mode: str) -> tuple[Problem, ...]:
    if mode == "exhaustive":
        return checkpoint.problems
    if mode != "previous_failures":
        raise ValueError(f"unknown problem selection mode {mode!r}")
    return tuple(
        problem
        for problem in checkpoint.problems
        if checkpoint.statuses.get(problem.id) != "complete"
    )


def record_status(checkpoint: Checkpoint, problem_id: str, status: str) -> None:
    known = {problem.id for problem in checkpoint.problems}
    if problem_id not in known:
        raise ValueError(f"unknown problem id {problem_id!r}")
    checkpoint.statuses[problem_id] = status
    _persist_checkpoint(checkpoint)


__all__ = ["Checkpoint", "load_checkpoint", "record_status", "select_problems"]
