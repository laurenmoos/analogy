from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum


_TEMP_PATH = re.compile(r"(?:/[^\s:]+/)?lean-gym-[^/\s:]+/[^\s:]+\.lean(?::\d+:\d+)?")
_UNRESOLVED_METAVARIABLE = re.compile(r"\?[A-Za-z_][A-Za-z0-9_.✝]*")
_LEAN_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_LEAN_CHARACTER = re.compile(r"'(?:\\.|[^'\\])'")
_LEAN_QUOTED_IDENTIFIER = re.compile(r"«[^»]*»")


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


def has_unresolved_metavariable(proof_state: str) -> bool:
    """Return whether Lean proof-state text contains an unquoted metavariable."""

    unquoted = _LEAN_STRING.sub("", proof_state)
    unquoted = _LEAN_CHARACTER.sub("", unquoted)
    unquoted = _LEAN_QUOTED_IDENTIFIER.sub("", unquoted)
    return _UNRESOLVED_METAVARIABLE.search(unquoted) is not None


class ProofStructureError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProofStructure:
    """Lean-native structural description of the current proof state."""

    goal_count: int
    target_shape: str
    hypothesis_shapes: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.goal_count, bool)
            or not isinstance(self.goal_count, int)
            or self.goal_count <= 0
        ):
            raise ProofStructureError("goal_count must be a positive integer")
        for value in (self.target_shape, *self.hypothesis_shapes):
            if not isinstance(value, str) or not value.strip():
                raise ProofStructureError("proof shapes must be nonblank JSON text")
            try:
                json.loads(value)
            except json.JSONDecodeError as error:
                raise ProofStructureError("proof shapes must be valid JSON") from error


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
