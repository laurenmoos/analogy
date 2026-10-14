from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Problem, ReferenceProof


class CorpusValidationError(ValueError):
    pass


def _expect_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CorpusValidationError(f"{path}: expected object")
    return value


def _expect_exact_keys(value: dict[str, Any], expected: set[str], path: str) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing keys {sorted(missing)}")
        if unknown:
            details.append(f"unknown keys {sorted(unknown)}")
        raise CorpusValidationError(f"{path}: {', '.join(details)}")


def _expect_string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise CorpusValidationError(f"{path}: expected {'string' if allow_empty else 'nonempty string'}")
    return value


def _expect_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CorpusValidationError(f"{path}: expected integer")
    return value


def _expect_string_list(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise CorpusValidationError(f"{path}: expected nonempty string array")
    return tuple(_expect_string(item, f"{path}[{index}]") for index, item in enumerate(value))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return _expect_object(json.loads(path.read_text(encoding="utf-8")), "$root")
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusValidationError(f"{path}: {exc}") from exc


def load_problems(path: Path) -> tuple[Problem, ...]:
    root = _read_json(path)
    _expect_exact_keys(root, {"schema_version", "source", "problems"}, "$root")
    if _expect_int(root["schema_version"], "schema_version") != 1:
        raise CorpusValidationError("schema_version: expected 1")
    source = _expect_object(root["source"], "source")
    _expect_exact_keys(source, {"repository", "license"}, "source")
    _expect_string(source["repository"], "source.repository")
    _expect_string(source["license"], "source.license")
    records = root["problems"]
    if not isinstance(records, list):
        raise CorpusValidationError("problems: expected array")
    expected = {"id", "title", "binders", "target", "source_path", "source_level", "connective", "difficulty"}
    result: list[Problem] = []
    seen: set[str] = set()
    for index, raw in enumerate(records):
        path_prefix = f"problems[{index}]"
        item = _expect_object(raw, path_prefix)
        _expect_exact_keys(item, expected, path_prefix)
        problem_id = _expect_string(item["id"], f"{path_prefix}.id")
        if problem_id in seen:
            raise CorpusValidationError(f"{path_prefix}.id: duplicate problem id {problem_id!r}")
        seen.add(problem_id)
        difficulty = _expect_int(item["difficulty"], f"{path_prefix}.difficulty")
        if difficulty <= 0:
            raise CorpusValidationError(f"{path_prefix}.difficulty: expected positive integer")
        result.append(
            Problem(
                id=problem_id,
                title=_expect_string(item["title"], f"{path_prefix}.title"),
                binders=_expect_string(item["binders"], f"{path_prefix}.binders", allow_empty=True),
                target=_expect_string(item["target"], f"{path_prefix}.target"),
                source_path=_expect_string(item["source_path"], f"{path_prefix}.source_path"),
                source_level=_expect_string(item["source_level"], f"{path_prefix}.source_level"),
                connective=_expect_string(item["connective"], f"{path_prefix}.connective"),
                difficulty=difficulty,
            )
        )
    return tuple(sorted(result, key=lambda problem: (problem.difficulty, problem.id)))


def load_reference_proofs(path: Path) -> dict[str, ReferenceProof]:
    root = _read_json(path)
    _expect_exact_keys(root, {"schema_version", "proofs"}, "$root")
    if _expect_int(root["schema_version"], "schema_version") != 1:
        raise CorpusValidationError("schema_version: expected 1")
    records = root["proofs"]
    if not isinstance(records, list):
        raise CorpusValidationError("proofs: expected array")
    result: dict[str, ReferenceProof] = {}
    for index, raw in enumerate(records):
        path_prefix = f"proofs[{index}]"
        item = _expect_object(raw, path_prefix)
        _expect_exact_keys(item, {"problem_id", "tactics"}, path_prefix)
        problem_id = _expect_string(item["problem_id"], f"{path_prefix}.problem_id")
        if problem_id in result:
            raise CorpusValidationError(f"{path_prefix}.problem_id: duplicate proof id {problem_id!r}")
        result[problem_id] = ReferenceProof(problem_id, _expect_string_list(item["tactics"], f"{path_prefix}.tactics"))
    return result


def validate_reference_coverage(problems: tuple[Problem, ...], proofs: dict[str, ReferenceProof]) -> None:
    problem_ids = {problem.id for problem in problems}
    proof_ids = set(proofs)
    missing = sorted(problem_ids - proof_ids)
    extra = sorted(proof_ids - problem_ids)
    if missing or extra:
        raise CorpusValidationError(f"reference coverage mismatch: missing={missing}, extra={extra}")
