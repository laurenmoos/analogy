#!/usr/bin/env python3
"""Import a deterministic, verifier-compatible subset of PropL test_ood."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import NamedTuple


_TACTIC = re.compile(r"^state_\d+_tactic_(\d+):\n([^\n]+)", re.MULTILINE)


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
