#!/usr/bin/env python3
"""Import a deterministic, verifier-compatible subset of PropL test_ood."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import NamedTuple


_TACTIC = re.compile(r"^state_\d+_tactic_(\d+):\n([^\n]+)", re.MULTILINE)
SOURCE_REVISION = "62a13de410999c20f60ec2f3be93457dcaac0d62"
SOURCE_REPOSITORY = "https://huggingface.co/datasets/KomeijiForce/PropL"


class SourceRecord(NamedTuple):
    data_id: str
    target: str
    tactics: tuple[str, ...]
    attempt_indices: tuple[int, ...]


class Candidate(NamedTuple):
    data_id: str
    target: str
    tactics: tuple[str, ...]
    failed_attempts: int
    flat_trace: bool


class ValidatedCandidate(NamedTuple):
    candidate: Candidate
    one_step_simp_status: str


def parse_records(path: Path) -> dict[str, SourceRecord]:
    records: dict[str, SourceRecord] = {}
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            data_id = row["data_id"]
            if data_id in records:
                raise ValueError(f"duplicate PropL data_id: {data_id}")
            try:
                target = row["input"].split("⊢ ", 1)[1].strip()
            except IndexError as error:
                raise ValueError(f"missing target marker for PropL data_id {data_id}") from error
            matches = _TACTIC.findall(row["output"])
            if not matches:
                raise ValueError(f"missing tactic trace for PropL data_id {data_id}")
            records[data_id] = SourceRecord(
                data_id=data_id,
                target=target,
                tactics=tuple(tactic for _, tactic in matches),
                attempt_indices=tuple(int(index) for index, _ in matches),
            )
    return records


def pair_records(normal_path: Path, backtrack_path: Path) -> dict[str, Candidate]:
    normal = parse_records(normal_path)
    backtrack = parse_records(backtrack_path)
    if normal.keys() != backtrack.keys():
        raise ValueError("normal and backtrack PropL files contain different data_ids")
    result: dict[str, Candidate] = {}
    for data_id, record in normal.items():
        paired = backtrack[data_id]
        if record.target != paired.target:
            raise ValueError(f"target mismatch for PropL data_id {data_id}")
        flat_trace = not any(
            tactic.startswith("case ") or tactic.endswith(":= by")
            for tactic in record.tactics
        )
        result[data_id] = Candidate(
            data_id=data_id,
            target=record.target,
            tactics=record.tactics,
            failed_attempts=sum(index > 0 for index in paired.attempt_indices),
            flat_trace=flat_trace,
        )
    return result


def is_static_candidate(candidate: Candidate) -> bool:
    return (
        candidate.flat_trace
        and len(candidate.tactics) >= 15
        and all(connective in candidate.target for connective in ("∧", "∨", "→"))
    )


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    return sorted(
        candidates,
        key=lambda item: (
            -item.failed_attempts,
            -len(item.tactics),
            -len(item.target),
            item.data_id,
        ),
    )


def select_verified(
    normal_path: Path,
    backtrack_path: Path,
    project_root: Path,
    limit: int = 30,
) -> list[ValidatedCandidate]:
    from analogy.lean_gym.models import Problem, VerificationStatus
    from analogy.lean_gym.verifier import LeanSubprocessVerifier

    verifier = LeanSubprocessVerifier(project_root, timeout_seconds=20)
    candidates = rank_candidates(
        [
            candidate
            for candidate in pair_records(normal_path, backtrack_path).values()
            if is_static_candidate(candidate)
        ]
    )
    selected: list[ValidatedCandidate] = []
    for candidate in candidates:
        problem = Problem(
            id=f"propl_ood_{candidate.data_id}",
            title="candidate",
            binders="(p1 p2 p3 p4 p5 : Prop)",
            target=candidate.target,
            source_path="normal/test_ood.csv",
            source_level=f"test_ood/{candidate.data_id}",
            connective="mixed",
            difficulty=len(candidate.tactics) + candidate.failed_attempts,
        )
        proof_result = verifier.verify_complete(problem, candidate.tactics)
        if proof_result.status is not VerificationStatus.COMPLETE:
            continue
        simp_result = verifier.try_tactic(problem, (), "simp")
        if simp_result.status is VerificationStatus.COMPLETE:
            continue
        selected.append(ValidatedCandidate(candidate, simp_result.status.value))
        if len(selected) == limit:
            return selected
    raise ValueError(f"only {len(selected)} verifier-compatible PropL records found; need {limit}")


def build_documents(
    selected: list[ValidatedCandidate],
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    problems: list[dict[str, object]] = []
    proofs: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    for rank, validated in enumerate(selected, start=1):
        candidate = validated.candidate
        problem_id = f"propl_ood_{candidate.data_id}"
        problems.append(
            {
                "id": problem_id,
                "title": f"PropL OOD {rank:02d}",
                "binders": "(p1 p2 p3 p4 p5 : Prop)",
                "target": candidate.target,
                "source_path": "normal/test_ood.csv",
                "source_level": f"test_ood/{candidate.data_id}",
                "connective": "mixed",
                "difficulty": len(candidate.tactics) + candidate.failed_attempts,
            }
        )
        proofs.append({"problem_id": problem_id, "tactics": list(candidate.tactics)})
        records.append(
            {
                "problem_id": problem_id,
                "source_data_id": candidate.data_id,
                "selection_rank": rank,
                "reference_steps": len(candidate.tactics),
                "failed_attempts": candidate.failed_attempts,
                "lean_status": "complete",
                "one_step_simp_status": validated.one_step_simp_status,
            }
        )
    corpus = {
        "schema_version": 1,
        "source": {"repository": SOURCE_REPOSITORY, "license": "MIT"},
        "problems": problems,
    }
    reference_proofs = {"schema_version": 1, "proofs": proofs}
    metadata = {
        "schema_version": 1,
        "source": {
            "dataset": "KomeijiForce/PropL",
            "repository": SOURCE_REPOSITORY,
            "generator_repository": "https://github.com/ucsd-atp/PropL",
            "paper": "https://aclanthology.org/2024.acl-long.45/",
            "revision": SOURCE_REVISION,
            "split": "test_ood",
            "normal_path": "normal/test_ood.csv",
            "backtrack_path": "backtrack/test_ood.csv",
            "license": "MIT",
        },
        "selection": {
            "required_connectives": ["∧", "∨", "→"],
            "minimum_reference_steps": 15,
            "flat_trace_only": True,
            "exclude_one_step_simp": True,
            "ordering": [
                "failed_attempts descending",
                "reference_steps descending",
                "target_length descending",
                "source_data_id ascending",
            ],
        },
        "records": records,
    }
    return corpus, reference_proofs, metadata


def write_documents(output_dir: Path, documents: tuple[dict[str, object], ...]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, document in zip(
        ("problems.json", "reference_proofs.json", "benchmark_metadata.json"),
        documents,
        strict=True,
    ):
        (output_dir / filename).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("normal_csv", type=Path)
    parser.add_argument("backtrack_csv", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    selected = select_verified(
        args.normal_csv,
        args.backtrack_csv,
        args.project_root,
    )
    write_documents(args.output_dir, build_documents(selected))
    print(f"wrote {len(selected)} PropL OOD problems to {args.output_dir}")


if __name__ == "__main__":
    main()
