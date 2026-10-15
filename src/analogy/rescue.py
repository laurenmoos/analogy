"""Domain-neutral records and parsing for stuck-state rescue rollouts."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from .records import (
    CancellationToken,
    CandidateOrigin,
    DiscardedSuccessor,
    Embedding,
    SearchNode,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
    _finite_float,
    _require_text,
    _text_tuple,
    _typed_tuple,
)


@dataclass(frozen=True, slots=True)
class TacticEvaluation:
    result: TacticResult
    completed_proof: str | None = None
    successor: SearchNode | None = None
    discarded: DiscardedSuccessor | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.result, TacticResult):
            raise ValueError("result must be a TacticResult")
        if self.successor is not None and not isinstance(self.successor, SearchNode):
            raise ValueError("successor must be a SearchNode or None")
        if self.discarded is not None and not isinstance(
            self.discarded, DiscardedSuccessor
        ):
            raise ValueError("discarded must be a DiscardedSuccessor or None")
        classifications = sum(
            value is not None
            for value in (self.completed_proof, self.successor, self.discarded)
        )
        if self.result.status is TacticStatus.ERROR:
            if classifications:
                raise ValueError("error evaluations must not include an outcome")
            return
        if classifications != 1:
            raise ValueError("accepted and complete evaluations require one outcome")
        if self.result.status is TacticStatus.COMPLETE:
            _require_text(self.completed_proof, "completed_proof")
            if self.successor is not None or self.discarded is not None:
                raise ValueError("complete evaluations require only completed_proof")
        elif self.completed_proof is not None:
            raise ValueError("accepted evaluations must not include completed_proof")
        elif self.successor is not None and self.successor.state != self.result.state:
            raise ValueError("successor state must match the accepted result")
        elif self.discarded is not None and self.discarded.result != self.result:
            raise ValueError("discarded result must match the accepted result")


class RescueTermination(str, Enum):
    COMPLETE = "complete"
    PROGRESS = "progress"
    REJECTED = "rejected"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"
    GENERATION_ERROR = "generation_error"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True, slots=True)
class ProofRescueContext:
    state: SymbolicState
    goal: SymbolicState
    accepted_tactics: tuple[str, ...]
    failed_tactics: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.state, SymbolicState):
            raise ValueError("state must be a SymbolicState")
        if not isinstance(self.goal, SymbolicState):
            raise ValueError("goal must be a SymbolicState")
        object.__setattr__(
            self, "accepted_tactics", _text_tuple(self.accepted_tactics, "accepted_tactics")
        )
        object.__setattr__(
            self, "failed_tactics", _text_tuple(self.failed_tactics, "failed_tactics")
        )


@dataclass(frozen=True, slots=True)
class ProofRepresentation:
    context: ProofRescueContext
    language_prompt_hash: str
    structure_prompt_hash: str
    language_embedding: Embedding
    structure_embedding: Embedding

    def __post_init__(self) -> None:
        if not isinstance(self.context, ProofRescueContext):
            raise ValueError("context must be a ProofRescueContext")
        _require_text(self.language_prompt_hash, "language_prompt_hash")
        _require_text(self.structure_prompt_hash, "structure_prompt_hash")
        if self.language_prompt_hash == self.structure_prompt_hash:
            raise ValueError("proof-view prompt hashes must be distinct")
        if not isinstance(self.language_embedding, Embedding):
            raise ValueError("language_embedding must be an Embedding")
        if not isinstance(self.structure_embedding, Embedding):
            raise ValueError("structure_embedding must be an Embedding")
        if self.language_embedding.dimension != self.structure_embedding.dimension:
            raise ValueError("proof-view embedding dimensions must match")

    @property
    def dimension(self) -> int:
        return self.language_embedding.dimension


@dataclass(frozen=True, slots=True)
class RescueProposal:
    candidate: TacticCandidate
    predicted_state: str

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, TacticCandidate):
            raise ValueError("candidate must be a TacticCandidate")
        if self.candidate.origin is not CandidateOrigin.ANALOGICAL:
            raise ValueError("rescue candidates must have analogical origin")
        _require_text(self.predicted_state, "predicted_state")


@dataclass(frozen=True, slots=True)
class PolicyActionRecord:
    seed: int
    delta_norm: float
    log_probability: float
    entropy: float

    def __post_init__(self) -> None:
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")
        delta_norm = _finite_float(self.delta_norm, "delta_norm")
        if delta_norm < 0.0:
            raise ValueError("delta_norm must be non-negative")
        object.__setattr__(self, "delta_norm", delta_norm)
        object.__setattr__(
            self, "log_probability", _finite_float(self.log_probability, "log_probability")
        )
        object.__setattr__(self, "entropy", _finite_float(self.entropy, "entropy"))


@dataclass(frozen=True, slots=True)
class RescueAttempt:
    proposal: RescueProposal
    result: TacticResult
    discarded: DiscardedSuccessor | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, RescueProposal):
            raise ValueError("proposal must be a RescueProposal")
        if not isinstance(self.result, TacticResult):
            raise ValueError("result must be a TacticResult")
        if self.result.candidate != self.proposal.candidate:
            raise ValueError("result candidate must match proposal candidate")
        if self.discarded is not None:
            if not isinstance(self.discarded, DiscardedSuccessor):
                raise ValueError("discarded must be a DiscardedSuccessor or None")
            if self.discarded.result != self.result:
                raise ValueError("discarded must reference the attempt result")


@dataclass(frozen=True, slots=True)
class RescueRollout:
    rollout_id: str
    state_id: str
    action: PolicyActionRecord
    proposals: tuple[RescueProposal, ...]
    attempts: tuple[RescueAttempt, ...]
    termination: RescueTermination
    reward: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.rollout_id, "rollout_id")
        _require_text(self.state_id, "state_id")
        if not isinstance(self.action, PolicyActionRecord):
            raise ValueError("action must be a PolicyActionRecord")
        proposals = _typed_tuple(self.proposals, RescueProposal, "proposals")
        attempts = _typed_tuple(self.attempts, RescueAttempt, "attempts")
        object.__setattr__(self, "proposals", proposals)
        object.__setattr__(self, "attempts", attempts)
        if not isinstance(self.termination, RescueTermination):
            raise ValueError("termination must be a RescueTermination")
        if any(item.proposal not in proposals for item in attempts):
            raise ValueError("attempt proposals must belong to the rollout")
        if self.termination is RescueTermination.COMPLETE and not any(
            item.result.status is TacticStatus.COMPLETE for item in attempts
        ):
            raise ValueError("complete rollout requires a completing attempt")
        if self.termination is RescueTermination.PROGRESS and not any(
            item.result.status is TacticStatus.ACCEPTED and item.discarded is None
            for item in attempts
        ):
            raise ValueError("progress rollout requires a frontier successor")
        if self.termination is RescueTermination.REJECTED and (
            not attempts
            or any(item.result.status is not TacticStatus.ERROR for item in attempts)
        ):
            raise ValueError("rejected rollout requires only rejected attempts")
        if self.reward is not None:
            object.__setattr__(self, "reward", _finite_float(self.reward, "reward"))


@dataclass(frozen=True, slots=True)
class GenericRescueOutcome:
    completed_proof: str | None = None
    successor: SearchNode | None = None
    rollouts: tuple[RescueRollout, ...] = ()
    cancelled: bool = False

    def __post_init__(self) -> None:
        if self.completed_proof is not None:
            _require_text(self.completed_proof, "completed_proof")
        if self.successor is not None and not isinstance(self.successor, SearchNode):
            raise ValueError("successor must be a SearchNode or None")
        object.__setattr__(
            self, "rollouts", _typed_tuple(self.rollouts, RescueRollout, "rollouts")
        )
        if not isinstance(self.cancelled, bool):
            raise ValueError("cancelled must be a bool")
        terminal_count = sum(
            value is not None for value in (self.completed_proof, self.successor)
        )
        if terminal_count > 1:
            raise ValueError("rescue outcome must have at most one terminal result")
        if self.cancelled and terminal_count:
            raise ValueError("cancelled rescue outcome must not have a terminal result")


class GenericRescueRunner(Protocol):
    def search(
        self,
        state: SymbolicState,
        goal: SymbolicState,
        tactic_pool: tuple[TacticCandidate, ...],
        ordinary_attempts: tuple[TacticResult, ...],
        evaluator: Callable[[TacticCandidate], TacticEvaluation],
        cancellation: CancellationToken,
    ) -> GenericRescueOutcome: ...


class RescueOutputError(ValueError):
    """Raised when generated rescue JSON violates the proposal contract."""


def parse_rescue_proposals(text: str, limit: int) -> tuple[RescueProposal, ...]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    if not isinstance(text, str):
        raise RescueOutputError("rescue output must be text")
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise RescueOutputError("rescue output must be one JSON object") from error
    if not isinstance(payload, dict) or set(payload) != {"predicted_state", "tactics"}:
        raise RescueOutputError(
            'rescue output must contain exactly "predicted_state" and "tactics"'
        )
    predicted_state = payload["predicted_state"]
    if not isinstance(predicted_state, str) or not predicted_state.strip():
        raise RescueOutputError("predicted_state must be nonblank text")
    tactics = payload["tactics"]
    if not isinstance(tactics, list) or not tactics:
        raise RescueOutputError("tactics must be a non-empty list")
    canonical: list[str] = []
    for tactic in tactics:
        if not isinstance(tactic, str) or not tactic.strip():
            raise RescueOutputError("tactics must contain nonblank strings")
        if "\n" in tactic or "\r" in tactic:
            raise RescueOutputError("tactics must be single-line, not multiline")
        if "\x00" in tactic:
            raise RescueOutputError("tactics must not contain NUL")
        normalized = " ".join(tactic.split())
        if normalized in canonical:
            raise RescueOutputError("tactics must be unique after canonicalization")
        canonical.append(normalized)
    selected = canonical[:limit]
    score = 1.0 / len(selected)
    return tuple(
        RescueProposal(
            TacticCandidate(source, score, CandidateOrigin.ANALOGICAL),
            predicted_state.strip(),
        )
        for source in selected
    )


def render_direct_rescue_prompt(context: ProofRescueContext) -> str:
    if not isinstance(context, ProofRescueContext):
        raise ValueError("context must be a ProofRescueContext")
    accepted = "\n".join(context.accepted_tactics) or "(none)"
    failed = "\n".join(context.failed_tactics) or "(none)"
    return (
        f"Overall target:\n{context.goal.expression}\n\n"
        f"Current goals:\n{context.state.expression}\n\n"
        f"Accepted prefix:\n{accepted}\n\n"
        f"Failed tactics:\n{failed}\n\n"
        'Return one JSON object with exactly the keys "predicted_state" and '
        '"tactics". predicted_state must describe the expected next Lean goals; '
        "tactics must be a non-empty list of single-line Lean tactics."
    )


def render_proof_embeddings(context: ProofRescueContext) -> tuple[str, str]:
    if not isinstance(context, ProofRescueContext):
        raise ValueError("context must be a ProofRescueContext")
    accepted = "\n".join(context.accepted_tactics) or "(none)"
    failed = "\n".join(context.failed_tactics) or "(none)"
    language = (
        "Explain the proof's current reasoning need without solving it.\n\n"
        f"Overall target:\n{context.goal.expression}\n\n"
        f"Current goals:\n{context.state.expression}\n\n"
        f"Accepted prefix:\n{accepted}\n\n"
        f"Failed tactics:\n{failed}"
    )
    structure = (
        "Describe the Current Lean goals structurally: propositions, hypotheses, "
        "constructors, dependencies, and unresolved relationships. Abstract from "
        "identifier spelling. Do not propose tactics.\n\n"
        f"Overall target:\n{context.goal.expression}\n\n"
        f"Current Lean goals:\n{context.state.expression}"
    )
    return language, structure


def proof_prompt_hash(text: str) -> str:
    _require_text(text, "text")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
