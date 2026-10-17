from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


_TEMP_PATH = re.compile(r"(?:/[^\s:]+/)?lean-gym-[^/\s:]+/[^\s:]+\.lean(?::\d+:\d+)?")


class VerificationStatus(str, Enum):
    OPEN = "open"
    COMPLETE = "complete"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"


def normalize_proof_state_text(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized = "\n".join(line.rstrip() for line in lines).strip("\n")
    return _TEMP_PATH.sub("<temp>", normalized)


@dataclass(frozen=True, slots=True)
class Problem:
    id: str
    title: str
    binders: str
    target: str
    source_path: str
    source_level: str
    connective: str
    difficulty: int

    def theorem_header(self, name: str = "current_problem") -> str:
        binder_segment = f" {self.binders}" if self.binders else ""
        return f"theorem {name}{binder_segment} : {self.target} := by"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: str
    kind: str | None
    message: str
    line: int | None
    column: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "kind": self.kind,
            "message": self.message,
            "line": self.line,
            "column": self.column,
        }


@dataclass(frozen=True, slots=True)
class ProofState:
    text: str
    normalized: str

    @classmethod
    def from_text(cls, value: str) -> ProofState:
        return cls(text=value, normalized=normalize_proof_state_text(value))


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: VerificationStatus
    accepted: bool
    proof_state: ProofState | None
    diagnostics: tuple[Diagnostic, ...]
    exit_code: int | None
    duration_ms: int


@dataclass(frozen=True, slots=True)
class ReferenceProof:
    problem_id: str
    tactics: tuple[str, ...]
