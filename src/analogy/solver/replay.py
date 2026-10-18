"""Verified, deterministic replay of observable stuck proof states."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Protocol

from ..lean_gym.models import Problem, VerificationStatus, normalize_proof_state_text
from ..utils.records import SymbolicState


class ReplayMismatchError(ValueError):
    pass


def proof_state_fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_proof_state_text(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StuckStateRecord:
    problem: Problem
    split: str
    accepted_tactics: tuple[str, ...]
    proof_state: str
    fingerprint: str
    candidate_sources: tuple[str, ...]
    ordinary_attempts: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.split not in {"train", "validation", "test"}:
            raise ValueError("split must be train, validation, or test")
        if not self.proof_state.strip():
            raise ValueError("proof_state must be nonblank")
        if self.fingerprint != proof_state_fingerprint(self.proof_state):
            raise ValueError("fingerprint must match normalized proof state")
        for values, name in (
            (self.accepted_tactics, "accepted_tactics"),
            (self.candidate_sources, "candidate_sources"),
            (self.ordinary_attempts, "ordinary_attempts"),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ValueError(f"{name} must contain nonblank strings")

    @property
    def key(self) -> tuple[str, str]:
        return self.problem.id, self.fingerprint


class StuckStateSink(Protocol):
    def add(self, record: StuckStateRecord) -> None: ...


class JsonlStuckStateStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def add(self, record: StuckStateRecord) -> None:
        if not isinstance(record, StuckStateRecord):
            raise ValueError("record must be a StuckStateRecord")
        if record.key in {item.key for item in self.load()}:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")

    def load(self) -> tuple[StuckStateRecord, ...]:
        if not self.path.exists():
            return ()
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            payload = json.loads(line)
            payload["problem"] = Problem(**payload["problem"])
            for name in ("accepted_tactics", "candidate_sources", "ordinary_attempts"):
                payload[name] = tuple(payload[name])
            records.append(StuckStateRecord(**payload))
        return tuple(records)


def reconstruct_stuck_state(record: StuckStateRecord, verifier: object) -> SymbolicState:
    result = verifier.state_after(record.problem, record.accepted_tactics)
    if result.status is not VerificationStatus.OPEN or result.proof_state is None:
        raise ReplayMismatchError("accepted prefix no longer reconstructs an open state")
    normalized = normalize_proof_state_text(result.proof_state.text)
    if normalized != normalize_proof_state_text(record.proof_state):
        raise ReplayMismatchError("reconstructed proof state text does not match")
    if proof_state_fingerprint(normalized) != record.fingerprint:
        raise ReplayMismatchError("reconstructed proof state fingerprint does not match")
    return SymbolicState(
        f"{record.problem.id}:{record.fingerprint[:16]}",
        normalized,
        "lean4",
        record.accepted_tactics,
    )


class CurriculumSampler:
    def __init__(self, records: Iterable[StuckStateRecord], seed: int = 0) -> None:
        self.records = tuple(records)
        self.seed = seed

    def sample(self, count: int, *, split: str = "train") -> tuple[StuckStateRecord, ...]:
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError("count must be a positive integer")
        eligible = sorted(
            (item for item in self.records if item.split == split),
            key=lambda item: (
                item.problem.difficulty,
                item.problem.connective,
                item.problem.id,
                item.fingerprint,
            ),
        )
        if len(eligible) < count:
            raise ValueError("not enough replay records for requested split")
        groups: dict[str, list[StuckStateRecord]] = {}
        for item in eligible:
            groups.setdefault(item.problem.connective, []).append(item)
        names = sorted(groups)
        random.Random(self.seed).shuffle(names)
        selected: list[StuckStateRecord] = []
        while len(selected) < count:
            for name in names:
                if groups[name] and len(selected) < count:
                    selected.append(groups[name].pop(0))
        return tuple(selected)


__all__ = [
    "CurriculumSampler",
    "JsonlStuckStateStore",
    "ReplayMismatchError",
    "StuckStateRecord",
    "StuckStateSink",
    "proof_state_fingerprint",
    "reconstruct_stuck_state",
]
