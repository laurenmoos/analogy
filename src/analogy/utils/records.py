from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from numbers import Real
from typing import Iterable, Protocol, TypeVar


def _require_text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not allow_empty and not value.strip():
        raise ValueError(f"{field} must not be blank")
    return value


def _text_tuple(values: Iterable[object], field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must be an iterable of strings, not one string")
    try:
        normalized = tuple(values)
    except TypeError as error:
        raise ValueError(f"{field} must be an iterable of strings") from error
    return tuple(_require_text(value, field) for value in normalized)


def _finite_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} must be a finite real number")
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a finite real number") from error
    if not math.isfinite(normalized):
        raise ValueError(f"{field} must be a finite real number")
    return normalized


@dataclass(frozen=True, slots=True)
class Embedding:
    model_id: str
    values: tuple[float, ...]
    model_revision: str | None = None
    layer: int | None = None
    token_index: int | None = None
    text_hash: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.model_id, "model_id")
        try:
            raw_values = tuple(self.values)
        except TypeError as error:
            raise ValueError("values must be a non-empty iterable of finite real numbers") from error
        if not raw_values:
            raise ValueError("values must not be empty")
        normalized = tuple(_finite_float(value, "values") for value in raw_values)
        object.__setattr__(self, "values", normalized)
        if self.model_revision is not None:
            _require_text(self.model_revision, "model_revision")
        for value, name in ((self.layer, "layer"), (self.token_index, "token_index")):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"{name} must be an integer or None")
        if self.token_index is not None and self.token_index < 0:
            raise ValueError("token_index must be non-negative")
        if self.text_hash is not None:
            _require_text(self.text_hash, "text_hash")

    @property
    def dimension(self) -> int:
        return len(self.values)


@dataclass(frozen=True, slots=True)
class SymbolicState:
    id: str
    expression: str
    language: str
    context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.expression, "expression")
        _require_text(self.language, "language")
        object.__setattr__(self, "context", _text_tuple(self.context, "context"))


_T = TypeVar("_T")


def _probability(value: object, field: str) -> float:
    normalized = _finite_float(value, field)
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field} must be in [0.0, 1.0]")
    return normalized


def _typed_tuple(
    values: Iterable[_T], expected: type[_T], field: str
) -> tuple[_T, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must be an iterable of {expected.__name__} values")
    try:
        normalized = tuple(values)
    except TypeError as error:
        raise ValueError(
            f"{field} must be an iterable of {expected.__name__} values"
        ) from error
    if not all(isinstance(value, expected) for value in normalized):
        raise ValueError(f"{field} must contain only {expected.__name__} values")
    return normalized


class CandidateOrigin(str, Enum):
    ORDINARY = "ordinary"
    ANALOGICAL = "analogical"
    DELIBERATIVE = "deliberative"


class TacticStatus(str, Enum):
    ERROR = "error"
    ACCEPTED = "accepted"
    COMPLETE = "complete"


class DiscardReason(str, Enum):
    REPEATED_STATE = "repeated_state"
    NO_PROGRESS = "no_progress"
    SEMANTIC_REFUTED = "semantic_refuted"


class StuckReason(str, Enum):
    ALL_TACTICS_ERRORED = "all_tactics_errored"
    ONLY_REPEATED_STATES = "only_repeated_states"
    NO_PROGRESS = "no_progress"
    CANDIDATES_EXHAUSTED = "candidates_exhausted"


class SearchStatus(str, Enum):
    COMPLETE = "complete"
    EXHAUSTED = "exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    SEMANTIC_FAILURE = "semantic_failure"


class SolveStatus(str, Enum):
    COMPLETE = "complete"
    EXHAUSTED = "exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    MODEL_FAILURE = "model_failure"
    VERIFIER_FAILURE = "verifier_failure"
    SEMANTIC_FAILURE = "semantic_failure"


@dataclass(frozen=True, slots=True)
class TacticCandidate:
    source: str
    score: float
    origin: CandidateOrigin
    model_rank: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.source, "source")
        object.__setattr__(self, "score", _probability(self.score, "score"))
        if not isinstance(self.origin, CandidateOrigin):
            raise ValueError("origin must be a CandidateOrigin")
        if self.model_rank is not None and (
            isinstance(self.model_rank, bool)
            or not isinstance(self.model_rank, int)
            or self.model_rank <= 0
        ):
            raise ValueError("model_rank must be a positive integer or None")


@dataclass(frozen=True, slots=True)
class TacticResult:
    candidate: TacticCandidate
    status: TacticStatus
    state: SymbolicState | None = None
    proof: str | None = None
    diagnostics: tuple[str, ...] = ()
    cache_hit: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, TacticCandidate):
            raise ValueError("candidate must be a TacticCandidate")
        if not isinstance(self.status, TacticStatus):
            raise ValueError("status must be a TacticStatus")
        object.__setattr__(
            self, "diagnostics", _text_tuple(self.diagnostics, "diagnostics")
        )
        if not isinstance(self.cache_hit, bool):
            raise ValueError("cache_hit must be a bool")
        if self.status is TacticStatus.ERROR:
            if self.state is not None or self.proof is not None:
                raise ValueError("error results must not include state or proof")
            return
        if self.status is TacticStatus.ACCEPTED:
            if not isinstance(self.state, SymbolicState):
                raise ValueError("accepted results require a state")
            if self.proof is not None:
                raise ValueError("accepted results must not include proof")
            return
        if self.state is not None:
            raise ValueError("complete results must not include a state")
        _require_text(self.proof, "proof")


@dataclass(frozen=True, slots=True)
class SearchNode:
    state: SymbolicState
    score: float
    parent_state_id: str | None = None
    tactic: str | None = None
    depth: int = 0
    incoming_candidate: TacticCandidate | None = None
    cache_hit: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.state, SymbolicState):
            raise ValueError("state must be a SymbolicState")
        object.__setattr__(self, "score", _probability(self.score, "score"))
        if self.parent_state_id is not None:
            _require_text(self.parent_state_id, "parent_state_id")
        if self.tactic is not None:
            _require_text(self.tactic, "tactic")
        if isinstance(self.depth, bool) or not isinstance(self.depth, int) or self.depth < 0:
            raise ValueError("depth must be a non-negative integer")
        if self.incoming_candidate is not None:
            if not isinstance(self.incoming_candidate, TacticCandidate):
                raise ValueError("incoming_candidate must be a TacticCandidate or None")
            if self.tactic != self.incoming_candidate.source:
                raise ValueError("incoming_candidate must match tactic")
        if not isinstance(self.cache_hit, bool):
            raise ValueError("cache_hit must be a bool")


@dataclass(frozen=True, slots=True)
class WinningTacticStep:
    source: str
    origin: CandidateOrigin
    model_rank: int | None
    cache_hit: bool

    def __post_init__(self) -> None:
        _require_text(self.source, "source")
        if not isinstance(self.origin, CandidateOrigin):
            raise ValueError("origin must be a CandidateOrigin")
        if self.model_rank is not None and (
            isinstance(self.model_rank, bool)
            or not isinstance(self.model_rank, int)
            or self.model_rank <= 0
        ):
            raise ValueError("model_rank must be a positive integer or None")
        if not isinstance(self.cache_hit, bool):
            raise ValueError("cache_hit must be a bool")


@dataclass(frozen=True, slots=True)
class DiscardedSuccessor:
    result: TacticResult
    reason: DiscardReason

    def __post_init__(self) -> None:
        if not isinstance(self.result, TacticResult):
            raise ValueError("result must be a TacticResult")
        if self.result.status is not TacticStatus.ACCEPTED:
            raise ValueError("discarded successors require an accepted result")
        if not isinstance(self.reason, DiscardReason):
            raise ValueError("reason must be a DiscardReason")


@dataclass(frozen=True, slots=True)
class SkippedCandidate:
    candidate: TacticCandidate
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, TacticCandidate):
            raise ValueError("candidate must be a TacticCandidate")
        _require_text(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class ExpansionOutcome:
    node: SearchNode
    attempts: tuple[TacticResult, ...] = ()
    successors: tuple[SearchNode, ...] = ()
    discarded: tuple[DiscardedSuccessor, ...] = ()
    completed_proof: str | None = None
    candidate_pool: tuple[TacticCandidate, ...] = ()
    skipped_candidates: tuple[SkippedCandidate, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.node, SearchNode):
            raise ValueError("node must be a SearchNode")
        object.__setattr__(
            self, "attempts", _typed_tuple(self.attempts, TacticResult, "attempts")
        )
        object.__setattr__(
            self,
            "successors",
            _typed_tuple(self.successors, SearchNode, "successors"),
        )
        object.__setattr__(
            self,
            "discarded",
            _typed_tuple(self.discarded, DiscardedSuccessor, "discarded"),
        )
        if self.completed_proof is not None:
            _require_text(self.completed_proof, "completed_proof")
        object.__setattr__(
            self,
            "candidate_pool",
            _typed_tuple(self.candidate_pool, TacticCandidate, "candidate_pool"),
        )
        object.__setattr__(
            self,
            "skipped_candidates",
            _typed_tuple(self.skipped_candidates, SkippedCandidate, "skipped_candidates"),
        )


@dataclass(frozen=True, slots=True)
class StucknessDecision:
    state_id: str
    is_stuck: bool
    reasons: tuple[StuckReason, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.state_id, "state_id")
        if not isinstance(self.is_stuck, bool):
            raise ValueError("is_stuck must be a bool")
        normalized = _typed_tuple(self.reasons, StuckReason, "reasons")
        object.__setattr__(self, "reasons", normalized)
        if self.is_stuck and not normalized:
            raise ValueError("stuck decisions require reasons")
        if not self.is_stuck and normalized:
            raise ValueError("non-stuck decisions must not include reasons")


class TacticCanonicalizer(Protocol):
    def canonicalize(self, source: str) -> str: ...


class CancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class Message:
    role: str
    content: str

    def __post_init__(self) -> None:
        _require_text(self.role, "role")
        _require_text(self.content, "content")


def _nonnegative_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class ProblemResult:
    problem_id: str
    status: SolveStatus
    proof: str | None
    failure: str | None
    expansion_count: int
    verifier_call_count: int
    invalid_tactic_count: int
    repeated_state_count: int
    no_progress_count: int
    stuck_state_count: int
    analogy_fallback_count: int = 0
    tactic_submission_count: int = 0
    analogy_winning_path_count: int = 0
    winning_tactic_ranks: tuple[int | None, ...] = ()
    deliberation_fallback_count: int = 0
    deliberation_winning_path_count: int = 0

    def __post_init__(self) -> None:
        _require_text(self.problem_id, "problem_id")
        if not isinstance(self.status, SolveStatus):
            raise ValueError("status must be a SolveStatus")
        if self.status is SolveStatus.COMPLETE:
            _require_text(self.proof, "proof")
            if self.failure is not None:
                raise ValueError("complete results must not include failure")
        elif self.proof is not None:
            raise ValueError("non-complete results must not include proof")
        if self.failure is not None:
            _require_text(self.failure, "failure")
        for field in (
            "expansion_count",
            "verifier_call_count",
            "invalid_tactic_count",
            "repeated_state_count",
            "no_progress_count",
            "stuck_state_count",
            "analogy_fallback_count",
            "tactic_submission_count",
            "analogy_winning_path_count",
            "deliberation_fallback_count",
            "deliberation_winning_path_count",
        ):
            _nonnegative_integer(getattr(self, field), field)
        ranks = tuple(self.winning_tactic_ranks)
        if any(
            item is not None
            and (isinstance(item, bool) or not isinstance(item, int) or item <= 0)
            for item in ranks
        ):
            raise ValueError("winning_tactic_ranks must contain positive integers or None")
        object.__setattr__(self, "winning_tactic_ranks", ranks)


@dataclass(frozen=True, slots=True)
class SolveSummary:
    run_id: str
    results: tuple[ProblemResult, ...]
    elapsed_seconds: float

    def __post_init__(self) -> None:
        _require_text(self.run_id, "run_id")
        object.__setattr__(
            self,
            "results",
            _typed_tuple(self.results, ProblemResult, "results"),
        )
        elapsed = _finite_float(self.elapsed_seconds, "elapsed_seconds")
        if elapsed < 0.0:
            raise ValueError("elapsed_seconds must be non-negative")
        object.__setattr__(self, "elapsed_seconds", elapsed)

    @property
    def attempted_count(self) -> int:
        return len(self.results)

    @property
    def solved_count(self) -> int:
        return sum(item.status is SolveStatus.COMPLETE for item in self.results)

    @property
    def model_failure_count(self) -> int:
        return sum(
            item.status is SolveStatus.MODEL_FAILURE for item in self.results
        )

    @property
    def invalid_tactic_count(self) -> int:
        return sum(item.invalid_tactic_count for item in self.results)

    @property
    def repeated_state_count(self) -> int:
        return sum(item.repeated_state_count for item in self.results)

    @property
    def no_progress_count(self) -> int:
        return sum(item.no_progress_count for item in self.results)

    @property
    def stuck_state_count(self) -> int:
        return sum(item.stuck_state_count for item in self.results)

    @property
    def verifier_call_count(self) -> int:
        return sum(item.verifier_call_count for item in self.results)
