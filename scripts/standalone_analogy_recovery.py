#!/usr/bin/env python3
"""Standalone poetry/physics retrieval experiment for Lean tactic recovery.

This file intentionally does not import the ``analogy`` package.  It owns
its data, model, retrieval, verification, and reporting boundaries so the
experiment can be inspected and run independently of the numbered plans.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any


class ExperimentError(RuntimeError):
    """Raised when experiment inputs or runtime behavior are invalid."""


def _nonblank(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        qualifier = "string" if allow_empty else "nonblank string"
        raise ExperimentError(f"{field} must be a {qualifier}")
    return value


@dataclass(frozen=True, slots=True)
class Problem:
    id: str
    binders: str
    target: str
    title: str = ""
    difficulty: int = 1

    def __post_init__(self) -> None:
        _nonblank(self.id, "problem.id")
        _nonblank(self.binders, "problem.binders", allow_empty=True)
        _nonblank(self.target, "problem.target")
        if isinstance(self.difficulty, bool) or not isinstance(self.difficulty, int) or self.difficulty <= 0:
            raise ExperimentError("problem.difficulty must be a positive integer")

    def theorem_header(self, name: str = "current_problem") -> str:
        binder_segment = f" {self.binders}" if self.binders else ""
        return f"theorem {name}{binder_segment} : {self.target} := by"


@dataclass(frozen=True, slots=True)
class ReferenceProof:
    problem_id: str
    tactics: tuple[str, ...]

    def __post_init__(self) -> None:
        _nonblank(self.problem_id, "reference_proof.problem_id")
        normalized = tuple(self.tactics)
        if not normalized or any(not isinstance(item, str) or not item.strip() for item in normalized):
            raise ExperimentError("reference_proof.tactics must be nonempty strings")
        object.__setattr__(self, "tactics", normalized)


@dataclass(frozen=True, slots=True)
class Transition:
    id: str
    domain: str
    before: str
    after: str
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        _nonblank(self.id, "transition.id")
        if self.domain not in {"poetry", "physics"}:
            raise ExperimentError("transition.domain must be poetry or physics")
        _nonblank(self.before, "transition.before")
        _nonblank(self.after, "transition.after")
        if not isinstance(self.provenance, Mapping):
            raise ExperimentError("transition.provenance must be a mapping")
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


class Condition(str, Enum):
    NONE = "none"
    RANDOM = "random"
    ANALOGY = "analogy"


def masked_problem_payload(problem: Problem) -> dict[str, str]:
    """Return the only benchmark fields allowed to enter inference prompts."""

    if not isinstance(problem, Problem):
        raise ExperimentError("problem must be a Problem")
    return {
        "problem_id": problem.id,
        "binders": problem.binders,
        "target": problem.target,
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExperimentError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise ExperimentError(f"{path} must contain a JSON object")
    return value


def load_benchmark(
    problem_path: Path,
    proof_path: Path,
) -> tuple[tuple[Problem, ...], dict[str, ReferenceProof]]:
    """Load the raw benchmark while keeping reference proofs separate."""

    problem_root = _read_json_object(Path(problem_path))
    proof_root = _read_json_object(Path(proof_path))
    if problem_root.get("schema_version") != 1 or proof_root.get("schema_version") != 1:
        raise ExperimentError("benchmark schema_version must be 1")
    raw_problems = problem_root.get("problems")
    raw_proofs = proof_root.get("proofs")
    if not isinstance(raw_problems, list) or not isinstance(raw_proofs, list):
        raise ExperimentError("benchmark problems and proofs must be arrays")

    problems: list[Problem] = []
    seen_problem_ids: set[str] = set()
    for index, raw in enumerate(raw_problems):
        if not isinstance(raw, dict):
            raise ExperimentError(f"problems[{index}] must be an object")
        try:
            problem = Problem(
                id=raw["id"],
                binders=raw["binders"],
                target=raw["target"],
                title=raw.get("title", ""),
                difficulty=raw["difficulty"],
            )
        except KeyError as error:
            raise ExperimentError(f"problems[{index}] is missing {error.args[0]}") from error
        if problem.id in seen_problem_ids:
            raise ExperimentError(f"duplicate problem id {problem.id!r}")
        seen_problem_ids.add(problem.id)
        problems.append(problem)

    proofs: dict[str, ReferenceProof] = {}
    for index, raw in enumerate(raw_proofs):
        if not isinstance(raw, dict):
            raise ExperimentError(f"proofs[{index}] must be an object")
        try:
            proof = ReferenceProof(raw["problem_id"], tuple(raw["tactics"]))
        except (KeyError, TypeError) as error:
            raise ExperimentError(f"proofs[{index}] is malformed") from error
        if proof.problem_id in proofs:
            raise ExperimentError(f"duplicate proof id {proof.problem_id!r}")
        proofs[proof.problem_id] = proof

    proof_ids = set(proofs)
    if seen_problem_ids != proof_ids:
        missing = sorted(seen_problem_ids - proof_ids)
        extra = sorted(proof_ids - seen_problem_ids)
        raise ExperimentError(f"reference coverage mismatch: missing={missing}, extra={extra}")
    return tuple(sorted(problems, key=lambda item: (item.difficulty, item.id))), proofs


def _transition_id(domain: str, source_id: str, before: str, after: str) -> str:
    content = json.dumps(
        [domain, source_id, before, after],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]


def iter_poetry_transitions(path: Path) -> Iterator[Transition]:
    """Yield adjacent nonblank lines without crossing Gutenberg works."""

    previous_gid: str | None = None
    previous_line: str | None = None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    previous_gid = None
                    previous_line = None
                    continue
                if not isinstance(raw, dict):
                    previous_gid = None
                    previous_line = None
                    continue
                gid = raw.get("gid")
                text = raw.get("s")
                if not isinstance(gid, str) or not gid.strip() or not isinstance(text, str) or not text.strip():
                    previous_gid = None
                    previous_line = None
                    continue
                text = " ".join(text.split())
                if gid == previous_gid and previous_line is not None and previous_line != text:
                    source_id = f"gutenberg:{gid}:{line_number - 1}-{line_number}"
                    yield Transition(
                        _transition_id("poetry", source_id, previous_line, text),
                        "poetry",
                        previous_line,
                        text,
                        {"gid": gid, "line_end": line_number, "source_id": source_id},
                    )
                previous_gid = gid
                previous_line = text
    except OSError as error:
        raise ExperimentError(f"cannot read poetry corpus {path}: {error}") from error


def iter_physics_transitions(path: Path) -> Iterator[Transition]:
    """Yield adjacent sentence transitions from every valid TRIP story."""

    try:
        handle = Path(path).open(encoding="utf-8")
    except OSError as error:
        raise ExperimentError(f"cannot read physics corpus {path}: {error}") from error
    with handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or not isinstance(record.get("stories"), list):
                continue
            example_id = record.get("example_id")
            for story_position, story in enumerate(record["stories"]):
                if not isinstance(story, dict) or not isinstance(story.get("sentences"), list):
                    continue
                sentences = [
                    " ".join(item.split())
                    for item in story["sentences"]
                    if isinstance(item, str) and item.strip()
                ]
                story_id = story.get("story_id", story_position)
                for sentence_index, (before, after) in enumerate(zip(sentences, sentences[1:])):
                    if before == after:
                        continue
                    source_id = f"trip:{example_id}:{story_id}:{sentence_index}-{sentence_index + 1}"
                    provenance = {
                        "source_id": source_id,
                        "line": line_number,
                        "example_id": example_id,
                        "story_id": story_id,
                        "plausible": story.get("plausible"),
                        "breakpoint": story.get("breakpoint", record.get("breakpoint")),
                        "confl_sents": story.get("confl_sents", record.get("confl_sents", [])),
                        "confl_pairs": story.get("confl_pairs", record.get("confl_pairs", [])),
                    }
                    yield Transition(
                        _transition_id("physics", source_id, before, after),
                        "physics",
                        before,
                        after,
                        provenance,
                    )


def _reservoir_sample(
    values: Iterable[Transition],
    count: int,
    rng: random.Random,
) -> tuple[Transition, ...]:
    reservoir: list[Transition] = []
    seen_ids: set[str] = set()
    eligible_count = 0
    for item in values:
        if not isinstance(item, Transition):
            raise ExperimentError("transition streams must contain Transition values")
        if item.id in seen_ids:
            continue
        seen_ids.add(item.id)
        eligible_count += 1
        if len(reservoir) < count:
            reservoir.append(item)
            continue
        replacement = rng.randrange(eligible_count)
        if replacement < count:
            reservoir[replacement] = item
    return tuple(reservoir)


def sample_transitions(
    poetry: Iterable[Transition],
    physics: Iterable[Transition],
    per_domain: int,
    seed: int,
) -> tuple[Transition, ...]:
    """Reservoir-sample a deterministic balanced index without full materialization."""

    if isinstance(per_domain, bool) or not isinstance(per_domain, int) or per_domain <= 0:
        raise ExperimentError("per_domain must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ExperimentError("seed must be an integer")
    poetry_sample = _reservoir_sample(poetry, per_domain, random.Random(f"{seed}:poetry"))
    physics_sample = _reservoir_sample(physics, per_domain, random.Random(f"{seed}:physics"))
    return poetry_sample + physics_sample

