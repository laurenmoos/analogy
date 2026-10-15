"""Domain-neutral records and parsing for stuck-state rescue rollouts."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol

from .records import (
    CancellationToken,
    CandidateOrigin,
    DiscardedSuccessor,
    Embedding,
    Message,
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
    INVALID_OUTPUT = "invalid_output"
    BUDGET_EXHAUSTED = "budget_exhausted"


class GenerationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    RUNTIME_ERROR = "runtime_error"


@dataclass(frozen=True, slots=True)
class GenerationAttempt:
    seed: int
    raw_text: str
    status: GenerationStatus
    repair: bool = False
    error_kind: str | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")
        _require_text(self.raw_text, "raw_text", allow_empty=True)
        if not isinstance(self.status, GenerationStatus):
            raise ValueError("status must be a GenerationStatus")
        if not isinstance(self.repair, bool):
            raise ValueError("repair must be a bool")
        if self.status is GenerationStatus.VALID:
            if self.error_kind is not None or self.diagnostic is not None:
                raise ValueError("valid generation attempts must not include errors")
        else:
            _require_text(self.error_kind, "error_kind")
            _require_text(self.diagnostic, "diagnostic")


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
    radius: float | None = None
    raw_direction_norm: float | None = None

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
        radius = delta_norm if self.radius is None else _finite_float(self.radius, "radius")
        raw_direction_norm = (
            delta_norm
            if self.raw_direction_norm is None
            else _finite_float(self.raw_direction_norm, "raw_direction_norm")
        )
        if radius < 0.0 or raw_direction_norm < 0.0:
            raise ValueError("action radius diagnostics must be non-negative")
        object.__setattr__(self, "radius", radius)
        object.__setattr__(self, "raw_direction_norm", raw_direction_norm)


@dataclass(frozen=True, slots=True)
class RescueAttempt:
    proposal: RescueProposal
    result: TacticResult
    discarded: DiscardedSuccessor | None = None
    prediction_distance: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, RescueProposal):
            raise ValueError("proposal must be a RescueProposal")
        if not isinstance(self.result, TacticResult):
            raise ValueError("result must be a TacticResult")
        if self.result.candidate != self.proposal.candidate:
            raise ValueError("result candidate must match proposal candidate")
        if self.prediction_distance is not None:
            distance = _finite_float(
                self.prediction_distance, "prediction_distance"
            )
            if distance < 0.0 or self.result.status is not TacticStatus.ACCEPTED:
                raise ValueError(
                    "prediction distance requires a non-negative accepted result"
                )
            object.__setattr__(self, "prediction_distance", distance)
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
    generations: tuple[GenerationAttempt, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.rollout_id, "rollout_id")
        _require_text(self.state_id, "state_id")
        if not isinstance(self.action, PolicyActionRecord):
            raise ValueError("action must be a PolicyActionRecord")
        proposals = _typed_tuple(self.proposals, RescueProposal, "proposals")
        attempts = _typed_tuple(self.attempts, RescueAttempt, "attempts")
        object.__setattr__(self, "proposals", proposals)
        object.__setattr__(self, "attempts", attempts)
        object.__setattr__(
            self,
            "generations",
            _typed_tuple(self.generations, GenerationAttempt, "generations"),
        )
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
    text = _extract_json_object(text)
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


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if (
        len(lines) < 3
        or lines[0].strip().casefold() != "```json"
        or lines[-1].strip() != "```"
    ):
        raise RescueOutputError(
            "fenced rescue output must contain exactly one json block"
        )
    inner = "\n".join(lines[1:-1]).strip()
    if "```" in inner:
        raise RescueOutputError(
            "fenced rescue output must contain exactly one json block"
        )
    return inner


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


def render_repair_prompt(raw_text: str, diagnostic: str) -> str:
    if not isinstance(raw_text, str):
        raise ValueError("raw_text must be text")
    _require_text(diagnostic, "diagnostic")
    return (
        "The prior response did not satisfy the required JSON schema.\n\n"
        f"Parser diagnostic:\n{diagnostic}\n\n"
        f"Prior response:\n{raw_text}\n\n"
        'Return only one JSON object with exactly "predicted_state" and '
        '"tactics". Do not include prose or Markdown fences.'
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


def derive_rescue_seed(
    base_seed: int, state_id: str, rollout_index: int, operation: str
) -> int:
    if isinstance(base_seed, bool) or not isinstance(base_seed, int):
        raise ValueError("base_seed must be an integer")
    _require_text(state_id, "state_id")
    if (
        isinstance(rollout_index, bool)
        or not isinstance(rollout_index, int)
        or rollout_index < 0
    ):
        raise ValueError("rollout_index must be a non-negative integer")
    _require_text(operation, "operation")
    payload = f"analogy-rescue-seed-v1\x1f{base_seed}\x1f{state_id}\x1f{rollout_index}\x1f{operation}"
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (2**63)


class RolloutCollector:
    """Keep persistent rollout records and short-lived live policy actions."""

    def __init__(self) -> None:
        self._rollouts: list[RescueRollout] = []
        self._live: list[tuple[RescueRollout, Any]] = []

    @property
    def rollouts(self) -> tuple[RescueRollout, ...]:
        return tuple(self._rollouts)

    @property
    def live_actions(self) -> tuple[Any, ...]:
        return tuple(action for _, action in self._live)

    def add(self, rollout: RescueRollout, action: Any) -> None:
        if not isinstance(rollout, RescueRollout):
            raise ValueError("rollout must be a RescueRollout")
        self._rollouts.append(rollout)
        self._live.append((rollout, action))

    def drain(self) -> tuple[tuple[RescueRollout, Any], ...]:
        drained = tuple(self._live)
        self._live.clear()
        return drained


class DirectRescueRunner:
    """Run bounded one-action rescue rollouts for a single stuck proof state."""

    def __init__(
        self,
        *,
        encoder: Any,
        policy: Any,
        generator: Any,
        rollouts_per_state: int,
        tactics_per_rollout: int,
        max_rescue_verifier_calls: int,
        base_seed: int,
        hidden_layer: int,
        max_model_calls: int | None = None,
        max_repair_calls: int = 0,
        collector: RolloutCollector | None = None,
    ) -> None:
        for value, name in (
            (rollouts_per_state, "rollouts_per_state"),
            (tactics_per_rollout, "tactics_per_rollout"),
            (max_rescue_verifier_calls, "max_rescue_verifier_calls"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(base_seed, bool) or not isinstance(base_seed, int):
            raise ValueError("base_seed must be an integer")
        if isinstance(hidden_layer, bool) or not isinstance(hidden_layer, int):
            raise ValueError("hidden_layer must be an integer")
        if max_model_calls is None:
            max_model_calls = rollouts_per_state * (1 + max_repair_calls)
        if (
            isinstance(max_model_calls, bool)
            or not isinstance(max_model_calls, int)
            or max_model_calls <= 0
        ):
            raise ValueError("max_model_calls must be a positive integer")
        if (
            isinstance(max_repair_calls, bool)
            or not isinstance(max_repair_calls, int)
            or max_repair_calls < 0
        ):
            raise ValueError("max_repair_calls must be a non-negative integer")
        self._encoder = encoder
        self._policy = policy
        self._generator = generator
        self._rollouts_per_state = rollouts_per_state
        self._tactics_per_rollout = tactics_per_rollout
        self._max_rescue_verifier_calls = max_rescue_verifier_calls
        self._base_seed = base_seed
        self._hidden_layer = hidden_layer
        self._max_model_calls = max_model_calls
        self._max_repair_calls = max_repair_calls
        self.collector = collector or RolloutCollector()

    def search(
        self,
        state: SymbolicState,
        goal: SymbolicState,
        tactic_pool: tuple[TacticCandidate, ...],
        ordinary_attempts: tuple[TacticResult, ...],
        evaluator: Callable[[TacticCandidate], TacticEvaluation],
        cancellation: CancellationToken,
    ) -> GenericRescueOutcome:
        del tactic_pool
        if cancellation.is_cancelled():
            return GenericRescueOutcome(cancelled=True)
        context = ProofRescueContext(
            state=state,
            goal=goal,
            accepted_tactics=state.context,
            failed_tactics=tuple(item.candidate.source for item in ordinary_attempts),
        )
        representation = self._encoder.encode(context)
        prompt = render_direct_rescue_prompt(context)
        messages = (
            Message(
                "system",
                "Predict the immediate next proof state and propose bounded Lean 4 rescue tactics.",
            ),
            Message("user", prompt),
        )
        attempted = {_canonical_tactic(item.candidate.source) for item in ordinary_attempts}
        rollouts: list[RescueRollout] = []
        verifier_calls = 0
        model_calls = 0

        for index in range(self._rollouts_per_state):
            if cancellation.is_cancelled():
                return GenericRescueOutcome(
                    rollouts=tuple(rollouts), cancelled=True
                )
            if verifier_calls >= self._max_rescue_verifier_calls:
                break
            if model_calls >= self._max_model_calls:
                break
            action_seed = derive_rescue_seed(
                self._base_seed, state.id, index, "action"
            )
            generation_seed = derive_rescue_seed(
                self._base_seed, state.id, index, "generation"
            )
            action = self._sample_action(representation, action_seed)
            action_record = PolicyActionRecord(
                seed=action_seed,
                delta_norm=float(
                    action.delta.detach().float().norm().cpu().item()
                ),
                log_probability=float(
                    action.log_probability.detach().float().cpu().item()
                ),
                entropy=float(action.entropy.detach().float().cpu().item()),
                radius=float(action.radius.detach().float().cpu().item()),
                raw_direction_norm=float(
                    action.raw_direction_norm.detach().float().cpu().item()
                ),
            )
            rollout_id = f"{state.id}:direct:{index}"
            generations: list[GenerationAttempt] = []
            parsed: tuple[RescueProposal, ...] | None = None
            active_messages = messages
            for generation_index in range(1 + self._max_repair_calls):
                if model_calls >= self._max_model_calls:
                    break
                seed = generation_seed
                if generation_index:
                    seed = derive_rescue_seed(
                        self._base_seed,
                        state.id,
                        index,
                        f"generation-repair-{generation_index}",
                    )
                try:
                    generated = self._generator.generate_with_delta(
                        active_messages,
                        delta=action.delta.detach(),
                        layer=self._hidden_layer,
                        seed=seed,
                    )
                    model_calls += 1
                    parsed = parse_rescue_proposals(
                        generated, self._tactics_per_rollout
                    )
                    generations.append(
                        GenerationAttempt(
                            seed,
                            generated,
                            GenerationStatus.VALID,
                            repair=bool(generation_index),
                        )
                    )
                    break
                except RescueOutputError as error:
                    raw = generated if isinstance(generated, str) else repr(generated)
                    generations.append(
                        GenerationAttempt(
                            seed,
                            raw,
                            GenerationStatus.INVALID,
                            repair=bool(generation_index),
                            error_kind="schema",
                            diagnostic=str(error),
                        )
                    )
                    active_messages = (
                        messages[0],
                        Message("user", render_repair_prompt(raw, str(error))),
                    )
                except Exception as error:
                    model_calls += 1
                    generations.append(
                        GenerationAttempt(
                            seed,
                            "",
                            GenerationStatus.RUNTIME_ERROR,
                            repair=bool(generation_index),
                            error_kind=type(error).__name__,
                            diagnostic=str(error) or type(error).__name__,
                        )
                    )
                    break
            if parsed is None:
                termination = (
                    RescueTermination.GENERATION_ERROR
                    if generations
                    and generations[-1].status is GenerationStatus.RUNTIME_ERROR
                    else RescueTermination.INVALID_OUTPUT
                )
                rollout = RescueRollout(
                    rollout_id,
                    state.id,
                    action_record,
                    (),
                    (),
                    termination,
                    generations=tuple(generations),
                )
                self._record(rollouts, rollout, action)
                continue

            fresh_sources: list[tuple[str, str]] = []
            for proposal in parsed:
                key = _canonical_tactic(proposal.candidate.source)
                if key in attempted:
                    continue
                attempted.add(key)
                fresh_sources.append((proposal.candidate.source, proposal.predicted_state))
            proposals = tuple(
                RescueProposal(
                    TacticCandidate(
                        source,
                        1.0 / len(fresh_sources),
                        CandidateOrigin.ANALOGICAL,
                    ),
                    predicted,
                )
                for source, predicted in fresh_sources
            ) if fresh_sources else ()
            attempts: list[RescueAttempt] = []
            termination = RescueTermination.EXHAUSTED

            for proposal in proposals:
                if cancellation.is_cancelled():
                    rollout = RescueRollout(
                        rollout_id,
                        state.id,
                        action_record,
                        proposals,
                        tuple(attempts),
                        RescueTermination.CANCELLED,
                        generations=tuple(generations),
                    )
                    self._record(rollouts, rollout, action)
                    return GenericRescueOutcome(
                        rollouts=tuple(rollouts), cancelled=True
                    )
                if verifier_calls >= self._max_rescue_verifier_calls:
                    termination = RescueTermination.BUDGET_EXHAUSTED
                    break
                evaluation = evaluator(proposal.candidate)
                verifier_calls += 1
                prediction_distance = None
                if (
                    evaluation.result.status is TacticStatus.ACCEPTED
                    and evaluation.result.state is not None
                    and hasattr(self._encoder, "prediction_distance")
                ):
                    prediction_distance = self._encoder.prediction_distance(
                        proposal.predicted_state,
                        evaluation.result.state.expression,
                    )
                rescue_attempt = RescueAttempt(
                    proposal,
                    evaluation.result,
                    evaluation.discarded,
                    prediction_distance,
                )
                attempts.append(rescue_attempt)
                if evaluation.completed_proof is not None:
                    rollout = RescueRollout(
                        rollout_id,
                        state.id,
                        action_record,
                        proposals,
                        tuple(attempts),
                        RescueTermination.COMPLETE,
                        generations=tuple(generations),
                    )
                    self._record(rollouts, rollout, action)
                    return GenericRescueOutcome(
                        completed_proof=evaluation.completed_proof,
                        rollouts=tuple(rollouts),
                    )
                if evaluation.successor is not None:
                    rollout = RescueRollout(
                        rollout_id,
                        state.id,
                        action_record,
                        proposals,
                        tuple(attempts),
                        RescueTermination.PROGRESS,
                        generations=tuple(generations),
                    )
                    self._record(rollouts, rollout, action)
                    return GenericRescueOutcome(
                        successor=evaluation.successor,
                        rollouts=tuple(rollouts),
                    )

            if verifier_calls >= self._max_rescue_verifier_calls and len(attempts) < len(proposals):
                termination = RescueTermination.BUDGET_EXHAUSTED
            elif attempts and all(
                item.result.status is TacticStatus.ERROR for item in attempts
            ):
                termination = RescueTermination.REJECTED
            rollout = RescueRollout(
                rollout_id,
                state.id,
                action_record,
                proposals,
                tuple(attempts),
                termination,
                generations=tuple(generations),
            )
            self._record(rollouts, rollout, action)
            if termination is RescueTermination.BUDGET_EXHAUSTED:
                break

        return GenericRescueOutcome(rollouts=tuple(rollouts))

    def _sample_action(self, representation: ProofRepresentation, seed: int) -> Any:
        try:
            import torch
        except ImportError as error:
            raise RuntimeError("Torch is required for direct rescue") from error
        parameter = next(self._policy.parameters())
        generator = torch.Generator(device=parameter.device).manual_seed(seed)
        return self._policy.sample(representation, generator)

    def _record(
        self, rollouts: list[RescueRollout], rollout: RescueRollout, action: Any
    ) -> None:
        rollouts.append(rollout)
        self.collector.add(rollout, action)


def _canonical_tactic(source: str) -> str:
    return " ".join(source.split())
