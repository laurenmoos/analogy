"""Typed records and bounded analogy correction for stuck proof states."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Protocol

from ..model.parser import (
    candidate_rejection_reason,
    normalize_lean4_tactic,
    tactic_is_incomplete,
)
from ..model.prompts import (
    focused_lean_goal,
    lean_tactic_card,
    prompt_catalog,
    render_lean_goal_for_model,
)
from ..utils.records import (
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
from .analogy_state import (
    AnalogyScratchpad,
    AnalogyState,
    AnalogyStateError,
    AnalogyUpdateAction,
    extract_analogy_block,
    remove_analogy_blocks,
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
    DUPLICATE = "duplicate"
    PREDICTED_NO_CHANGE = "predicted_no_change"


class GenerationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    RUNTIME_ERROR = "runtime_error"
    DUPLICATE = "duplicate"


class PredictionLabel(str, Enum):
    PREDICTED_NO_CHANGE = "predicted_no_change"
    PREDICTION_MATCHED = "prediction_matched"
    PREDICTION_MISMATCHED = "prediction_mismatched"


@dataclass(frozen=True, slots=True)
class GenerationAttempt:
    seed: int
    raw_text: str
    status: GenerationStatus
    repair: bool = False
    error_kind: str | None = None
    diagnostic: str | None = None
    prediction_label: PredictionLabel | None = None

    def __post_init__(self) -> None:
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")
        _require_text(self.raw_text, "raw_text", allow_empty=True)
        if not isinstance(self.status, GenerationStatus):
            raise ValueError("status must be a GenerationStatus")
        if self.prediction_label is not None and not isinstance(
            self.prediction_label, PredictionLabel
        ):
            raise ValueError("prediction_label must be a PredictionLabel or None")
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
        if not isinstance(self.state, SymbolicState) or not isinstance(self.goal, SymbolicState):
            raise ValueError("state and goal must be SymbolicState values")
        object.__setattr__(self, "accepted_tactics", _text_tuple(self.accepted_tactics, "accepted_tactics"))
        object.__setattr__(self, "failed_tactics", _text_tuple(self.failed_tactics, "failed_tactics"))


def zero_embedding_like(embedding: Embedding, *, rendering: str = "(none)") -> Embedding:
    return replace(
        embedding,
        values=(0.0,) * embedding.dimension,
        text_hash=hashlib.sha256(rendering.encode("utf-8")).hexdigest(),
    )


@dataclass(frozen=True, slots=True)
class ProofRepresentation:
    context: ProofRescueContext
    language_prompt_hash: str
    structure_prompt_hash: str
    language_embedding: Embedding
    structure_embedding: Embedding
    transition_embedding: Embedding | None = None
    analogy_embedding: Embedding | None = None
    scalar_features: tuple[float, ...] = (0.0, 1.0, 1.0, 0.0)

    def __post_init__(self) -> None:
        if not isinstance(self.context, ProofRescueContext):
            raise ValueError("context must be a ProofRescueContext")
        _require_text(self.language_prompt_hash, "language_prompt_hash")
        _require_text(self.structure_prompt_hash, "structure_prompt_hash")
        if self.language_embedding.dimension != self.structure_embedding.dimension:
            raise ValueError("proof-view embedding dimensions must match")
        for name in ("transition_embedding", "analogy_embedding"):
            value = getattr(self, name)
            if value is None:
                value = zero_embedding_like(self.language_embedding)
                object.__setattr__(self, name, value)
            if value.dimension != self.language_embedding.dimension:
                raise ValueError("proof-view embedding dimensions must match")
        object.__setattr__(self, "scalar_features", tuple(_finite_float(value, "scalar_features") for value in self.scalar_features))

    @property
    def dimension(self) -> int:
        return self.language_embedding.dimension


@dataclass(frozen=True, slots=True)
class RescueProposal:
    candidate: TacticCandidate
    predicted_state: str

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, TacticCandidate) or self.candidate.origin is not CandidateOrigin.ANALOGICAL:
            raise ValueError("rescue candidate must have analogical origin")
        _require_text(self.predicted_state, "predicted_state")


@dataclass(frozen=True, slots=True)
class PolicyActionRecord:
    seed: int
    delta_norm: float
    log_probability: float
    entropy: float
    radius: float | None = None
    raw_direction_norm: float | None = None


@dataclass(frozen=True, slots=True)
class RescueAttempt:
    proposal: RescueProposal
    result: TacticResult
    discarded: DiscardedSuccessor | None = None
    prediction_distance: float | None = None
    stage: str = "analogy_fallback"

    def __post_init__(self) -> None:
        if self.result.candidate != self.proposal.candidate:
            raise ValueError("result candidate must match proposal candidate")
        if self.discarded is not None and self.discarded.result != self.result:
            raise ValueError("discarded must reference the attempt result")


@dataclass(frozen=True, slots=True)
class RescueRollout:
    rollout_id: str
    state_id: str
    action: PolicyActionRecord | None
    proposals: tuple[RescueProposal, ...]
    attempts: tuple[RescueAttempt, ...]
    termination: RescueTermination
    reward: float | None = None
    generations: tuple[GenerationAttempt, ...] = ()
    analogy_stream: Any | None = None
    analogy_trace: Any | None = None
    semantic_event_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.rollout_id, "rollout_id")
        _require_text(self.state_id, "state_id")
        object.__setattr__(self, "proposals", _typed_tuple(self.proposals, RescueProposal, "proposals"))
        object.__setattr__(self, "attempts", _typed_tuple(self.attempts, RescueAttempt, "attempts"))
        object.__setattr__(self, "generations", _typed_tuple(self.generations, GenerationAttempt, "generations"))
        if not isinstance(self.termination, RescueTermination):
            raise ValueError("termination must be a RescueTermination")
        if self.semantic_event_id is not None:
            _require_text(self.semantic_event_id, "semantic_event_id")
        if any(item.proposal not in self.proposals for item in self.attempts):
            raise ValueError("attempt proposals must belong to rollout")


@dataclass(frozen=True, slots=True)
class GenericRescueOutcome:
    completed_proof: str | None = None
    successor: SearchNode | None = None
    rollouts: tuple[RescueRollout, ...] = ()
    cancelled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "rollouts", _typed_tuple(self.rollouts, RescueRollout, "rollouts"))
        if sum(value is not None for value in (self.completed_proof, self.successor)) > 1:
            raise ValueError("fallback outcome must have at most one terminal result")


class GenericRescueRunner(Protocol):
    def search(
        self,
        state: SymbolicState,
        goal: SymbolicState,
        tactic_pool: tuple[TacticCandidate, ...],
        ordinary_attempts: tuple[TacticResult, ...],
        evaluator: Callable[[TacticCandidate], TacticEvaluation],
        cancellation: CancellationToken,
        semantic_guidance: str,
        dependency_table: str = "(none)",
        *,
        force_domain_change: bool = False,
    ) -> GenericRescueOutcome: ...


class RescueOutputError(ValueError):
    """Raised when generated analogy JSON violates the fallback contract."""


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or lines[0].strip().casefold() != "```json" or lines[-1].strip() != "```":
        raise RescueOutputError("fenced output must contain exactly one json block")
    inner = "\n".join(lines[1:-1]).strip()
    if "```" in inner:
        raise RescueOutputError("fenced output must contain exactly one json block")
    return inner


def parse_analogy_tactic(
    text: str,
    *,
    proof_state: str | None = None,
) -> tuple[str, RescueProposal]:
    if not isinstance(text, str):
        raise RescueOutputError("analogy fallback output must be text")
    try:
        payload = json.loads(_extract_json_object(text))
    except (json.JSONDecodeError, TypeError) as error:
        raise RescueOutputError("analogy fallback output must be one JSON object") from error
    if not isinstance(payload, dict) or set(payload) != {"analogy", "predicted_state", "tactic"}:
        raise RescueOutputError('analogy fallback output must contain exactly "analogy", "predicted_state", and "tactic"')
    analogy, predicted_state, tactic = payload["analogy"], payload["predicted_state"], payload["tactic"]
    if not isinstance(analogy, str) or not analogy.strip():
        raise RescueOutputError("analogy must be nonblank text")
    if not isinstance(predicted_state, str) or not predicted_state.strip():
        raise RescueOutputError("predicted_state must be nonblank text")
    if not isinstance(tactic, str) or not tactic.strip():
        raise RescueOutputError("tactic must be nonblank text")
    if "\n" in tactic or "\r" in tactic or "\x00" in tactic:
        raise RescueOutputError("tactic must be one safe single line")
    canonical = normalize_lean4_tactic(tactic, proof_state)
    if tactic_is_incomplete(canonical):
        raise RescueOutputError(
            f"tactic {canonical!r} is incomplete; supply its required operands"
        )
    rejection = candidate_rejection_reason(canonical, proof_state)
    if rejection is not None:
        raise RescueOutputError(f"tactic {canonical!r} was filtered: {rejection}")
    return analogy.strip(), RescueProposal(
        TacticCandidate(canonical, 1.0, CandidateOrigin.ANALOGICAL),
        predicted_state.strip(),
    )


def parse_analogy_update(
    text: str,
    *,
    proof_state: str | None = None,
    current: AnalogyState | None,
    force_domain_change: bool = False,
) -> tuple[AnalogyUpdateAction, AnalogyState, RescueProposal]:
    """Parse one scratchpad update and its first tactic without mutating state."""

    try:
        analogy, payload_text = extract_analogy_block(text)
    except AnalogyStateError as error:
        raise RescueOutputError(str(error)) from error
    try:
        payload = json.loads(_extract_json_object(payload_text))
    except (json.JSONDecodeError, TypeError) as error:
        raise RescueOutputError("analogy update must end with one JSON object") from error
    if not isinstance(payload, dict) or set(payload) != {
        "action", "predicted_state", "tactic",
    }:
        raise RescueOutputError(
            'analogy update JSON must contain exactly "action", "predicted_state", and "tactic"'
        )
    try:
        action = AnalogyUpdateAction(payload["action"])
    except (TypeError, ValueError) as error:
        raise RescueOutputError(
            'action must be "revise_steps" or "change_domain"'
        ) from error
    if force_domain_change and action is not AnalogyUpdateAction.CHANGE_DOMAIN:
        raise RescueOutputError("countermodel forces action change_domain")
    if current is None and action is not AnalogyUpdateAction.CHANGE_DOMAIN:
        raise RescueOutputError("missing active analogy requires action change_domain")
    if action is AnalogyUpdateAction.REVISE_STEPS and current is not None:
        if (analogy.domain, analogy.mapping) != (current.domain, current.mapping):
            raise RescueOutputError(
                "revise_steps must preserve the current domain and mapping"
            )
    predicted_state = payload["predicted_state"]
    tactic = payload["tactic"]
    if not isinstance(predicted_state, str) or not predicted_state.strip():
        raise RescueOutputError("predicted_state must be nonblank text")
    if not isinstance(tactic, str) or not tactic.strip():
        raise RescueOutputError("tactic must be nonblank text")
    if "\n" in tactic or "\r" in tactic or "\x00" in tactic:
        raise RescueOutputError("tactic must be one safe single line")
    canonical = normalize_lean4_tactic(tactic, proof_state)
    if tactic_is_incomplete(canonical):
        raise RescueOutputError(
            f"tactic {canonical!r} is incomplete; supply its required operands"
        )
    rejection = candidate_rejection_reason(canonical, proof_state)
    if rejection is not None:
        raise RescueOutputError(f"tactic {canonical!r} was filtered: {rejection}")
    return action, analogy, RescueProposal(
        TacticCandidate(canonical, 1.0, CandidateOrigin.ANALOGICAL),
        predicted_state.strip(),
    )


class AnalogyFallback:
    """Use a bounded generation budget to repair invalid or Lean-rejected tactics."""

    def __init__(
        self,
        generator: Any,
        *,
        max_model_calls: int,
        seed: int,
        trace_sink: Any | None = None,
        analogy_scratchpad: AnalogyScratchpad | None = None,
    ) -> None:
        if isinstance(max_model_calls, bool) or not isinstance(max_model_calls, int) or max_model_calls <= 0:
            raise ValueError("max_model_calls must be a positive integer")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a non-negative integer")
        self._generator = generator
        self._max_model_calls = max_model_calls
        self._seed = seed
        self._trace_sink = trace_sink
        self._analogy_scratchpad = analogy_scratchpad

    def search(
        self,
        state,
        goal,
        tactic_pool,
        ordinary_attempts,
        evaluator,
        cancellation,
        semantic_guidance,
        dependency_table="(none)",
        *,
        force_domain_change=False,
    ) -> GenericRescueOutcome:
        del tactic_pool
        scratchpad = self._analogy_scratchpad
        if force_domain_change and scratchpad is not None:
            scratchpad.abandon_current()
        if cancellation.is_cancelled():
            return GenericRescueOutcome(cancelled=True)
        attempted_order: list[str] = []
        attempted_tactics: set[str] = set()
        for attempt in ordinary_attempts:
            tactic = normalize_lean4_tactic(attempt.candidate.source, state.expression)
            if tactic not in attempted_tactics:
                attempted_tactics.add(tactic)
                attempted_order.append(tactic)
        open_feedback = tuple(
            _result_feedback(item)
            for item in ordinary_attempts
            if item.status is TacticStatus.ACCEPTED
        )
        rejected_feedback = tuple(
            _result_feedback(item)
            for item in ordinary_attempts
            if item.status is not TacticStatus.ACCEPTED
        )
        messages = prompt_catalog().messages(
            "analogy_update" if scratchpad is not None else "analogy_rescue",
            overall_target=render_lean_goal_for_model(goal.expression),
            current_goals=render_lean_goal_for_model(focused_lean_goal(state.expression)),
            accepted="\n".join(state.context) or "(none)",
            open_tactics="\n".join(open_feedback) or "(none)",
            rejected_tactics="\n".join(rejected_feedback) or "(none)",
            tactic_card=lean_tactic_card(state.expression),
            semantic_guidance=semantic_guidance,
            dependency_table=dependency_table,
        )
        if scratchpad is not None:
            user = messages[1]
            if scratchpad.history:
                user = Message(
                    user.role,
                    user.content + "\n\n" + prompt_catalog().text(
                        "proof_search_history",
                        history="\n".join(scratchpad.history),
                    ),
                )
            messages = (
                messages[0],
                Message("system", scratchpad.context_block()),
                user,
            )
        active_messages = messages
        rollouts: list[RescueRollout] = []
        calibrated_update: tuple[AnalogyUpdateAction, AnalogyState] | None = None
        for index in range(self._max_model_calls):
            if cancellation.is_cancelled():
                return GenericRescueOutcome(rollouts=tuple(rollouts), cancelled=True)
            seed = _fallback_seed(self._seed, state, index)
            raw = ""
            try:
                raw = self._generator.generate(active_messages, seed=seed)
                self._trace(state.id, active_messages, seed, raw)
                update_action = None
                update_state = None
                if scratchpad is None:
                    analogy, proposal = parse_analogy_tactic(
                        raw,
                        proof_state=state.expression,
                    )
                else:
                    update_action, update_state, proposal = parse_analogy_update(
                        raw,
                        proof_state=state.expression,
                        current=scratchpad.current,
                        force_domain_change=force_domain_change,
                    )
                    try:
                        scratchpad.validate_transition(update_action, update_state)
                    except AnalogyStateError as error:
                        raise RescueOutputError(str(error)) from error
                    analogy = update_state.render()
                if proposal.candidate.source in attempted_tactics:
                    diagnostic = (
                        f"tactic {proposal.candidate.source!r} was already attempted "
                        "at this proof state"
                    )
                    generation = GenerationAttempt(
                        seed,
                        raw,
                        GenerationStatus.DUPLICATE,
                        index > 0,
                        "duplicate_tactic",
                        diagnostic,
                    )
                    rollouts.append(
                        RescueRollout(
                            f"{state.id}:analogy:{index}",
                            state.id,
                            None,
                            (),
                            (),
                            RescueTermination.DUPLICATE,
                            generations=(generation,),
                            analogy_trace=analogy,
                        )
                    )
                    active_messages = messages + (
                        Message(
                            "user",
                            prompt_catalog().text(
                                "analogy_duplicate_repair",
                                tried_tactics=_bounded_tactic_ledger(attempted_order),
                                diagnostic=diagnostic,
                                previous_response=remove_analogy_blocks(raw),
                                contract=_rescue_output_contract(scratchpad),
                            ),
                        ),
                    )
                    continue
                attempted_tactics.add(proposal.candidate.source)
                attempted_order.append(proposal.candidate.source)
                generation = GenerationAttempt(
                    seed, raw, GenerationStatus.VALID, repair=index > 0
                )
            except RescueOutputError as error:
                generation = GenerationAttempt(seed, raw, GenerationStatus.INVALID, index > 0, "schema", str(error))
                rollouts.append(RescueRollout(f"{state.id}:analogy:{index}", state.id, None, (), (), RescueTermination.INVALID_OUTPUT, generations=(generation,)))
                active_messages = messages + (
                    Message(
                        "user",
                        prompt_catalog().text(
                            "analogy_invalid_repair",
                            diagnostic=error,
                            previous_response=remove_analogy_blocks(raw),
                            contract=_rescue_output_contract(scratchpad),
                        ),
                    ),
                )
                continue
            except Exception as error:
                self._trace(state.id, active_messages, seed, raw, error=error)
                generation = GenerationAttempt(seed, raw, GenerationStatus.RUNTIME_ERROR, index > 0, type(error).__name__, str(error) or type(error).__name__)
                rollouts.append(RescueRollout(f"{state.id}:analogy:{index}", state.id, None, (), (), RescueTermination.GENERATION_ERROR, generations=(generation,)))
                continue
            if cancellation.is_cancelled():
                rollouts.append(
                    RescueRollout(
                        f"{state.id}:analogy:{index}",
                        state.id,
                        None,
                        (proposal,),
                        (),
                        RescueTermination.CANCELLED,
                        generations=(generation,),
                        analogy_trace=analogy,
                    )
                )
                return GenericRescueOutcome(
                    rollouts=tuple(rollouts), cancelled=True
                )
            current_state = _normalized_prediction_state(state.expression)
            predicted_state = _normalized_prediction_state(proposal.predicted_state)
            if predicted_state == current_state:
                generation = replace(
                    generation,
                    prediction_label=PredictionLabel.PREDICTED_NO_CHANGE,
                )
                rollouts.append(
                    RescueRollout(
                        f"{state.id}:analogy:{index}",
                        state.id,
                        None,
                        (proposal,),
                        (),
                        RescueTermination.PREDICTED_NO_CHANGE,
                        generations=(generation,),
                        analogy_trace=analogy,
                    )
                )
                active_messages = messages + (
                    Message(
                        "user",
                        prompt_catalog().text(
                            "analogy_no_change_repair",
                            tried_tactics=_bounded_tactic_ledger(attempted_order),
                            contract=_rescue_output_contract(scratchpad),
                        ),
                    ),
                )
                continue
            evaluation = evaluator(proposal.candidate)
            matched = _prediction_matches(proposal.predicted_state, evaluation)
            generation = replace(
                generation,
                prediction_label=(
                    PredictionLabel.PREDICTION_MATCHED
                    if matched
                    else PredictionLabel.PREDICTION_MISMATCHED
                ),
            )
            attempt = RescueAttempt(proposal, evaluation.result, evaluation.discarded)
            termination = (
                RescueTermination.COMPLETE if evaluation.completed_proof is not None
                else RescueTermination.PROGRESS if evaluation.successor is not None
                else RescueTermination.REJECTED if evaluation.result.status is TacticStatus.ERROR
                else RescueTermination.EXHAUSTED
            )
            rollouts.append(RescueRollout(f"{state.id}:analogy:{index}", state.id, None, (proposal,), (attempt,), termination, generations=(generation,), analogy_trace=analogy))
            if scratchpad is not None:
                scratchpad.record_history(_result_feedback(evaluation.result))
                if (
                    evaluation.completed_proof is not None
                    or evaluation.successor is not None
                ):
                    assert update_action is not None and update_state is not None
                    try:
                        scratchpad.apply(update_action, update_state)
                    except AnalogyStateError as error:
                        raise RescueOutputError(str(error)) from error
            if evaluation.completed_proof is not None:
                return GenericRescueOutcome(completed_proof=evaluation.completed_proof, rollouts=tuple(rollouts))
            if evaluation.successor is not None:
                return GenericRescueOutcome(successor=evaluation.successor, rollouts=tuple(rollouts))
            if scratchpad is not None and matched:
                if calibrated_update is None:
                    assert update_action is not None and update_state is not None
                    calibrated_update = update_action, update_state
            if getattr(evaluation.result, "infrastructure_failure", None) is not None:
                return GenericRescueOutcome(rollouts=tuple(rollouts))
            active_messages = messages + (
                Message(
                    "user",
                    _analogy_correction_feedback(
                        proposal,
                        evaluation,
                        contract=_rescue_output_contract(scratchpad),
                    ),
                ),
            )
        if scratchpad is not None and calibrated_update is not None:
            scratchpad.apply(*calibrated_update)
        return GenericRescueOutcome(rollouts=tuple(rollouts))

    def _trace(self, state_id: str, messages: tuple[Message, ...], seed: int, response: str, *, error: Exception | None = None) -> None:
        if self._trace_sink is not None:
            self._trace_sink({
                "kind": "analogy_model_call",
                "state_id": state_id,
                "seed": seed,
                "messages": [{"role": item.role, "content": item.content} for item in messages],
                "response": response,
                "error": None if error is None else f"{type(error).__name__}: {error}",
            })


def _result_feedback(result: TacticResult) -> str:
    diagnostic = "; ".join(result.diagnostics) or result.status.value
    return f"{result.candidate.source}: {diagnostic}"


def _bounded_diagnostic(text: str, *, limit: int = 500) -> str:
    normalized = " ".join(text.split())
    marker = " [truncated]"
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - len(marker)].rstrip() + marker


def _bounded_tactic_ledger(tactics: list[str], *, count: int = 8, limit: int = 240) -> str:
    rendered = ", ".join(tactics[:count]) or "(none)"
    return rendered if len(rendered) <= limit else rendered[: limit - 1].rstrip() + "…"


def _rescue_output_contract(scratchpad: AnalogyScratchpad | None) -> str:
    return prompt_catalog().text(
        "analogy_rescue_output_contract"
        if scratchpad is None
        else "analogy_update_output_contract"
    )


def _normalized_prediction_state(text: str) -> str:
    return " ".join(render_lean_goal_for_model(focused_lean_goal(text)).split())


def _prediction_matches(predicted_state: str, evaluation: TacticEvaluation) -> bool:
    predicts_solved = predicted_state.strip().upper() == "SOLVED"
    if evaluation.completed_proof is not None:
        return predicts_solved
    observed = (
        evaluation.successor.state
        if evaluation.successor is not None
        else evaluation.result.state
    )
    if predicts_solved or observed is None:
        return False
    return _normalized_prediction_state(predicted_state) == _normalized_prediction_state(
        observed.expression
    )


def _analogy_correction_feedback(
    proposal: RescueProposal,
    evaluation: TacticEvaluation,
    *,
    contract: str,
) -> str:
    catalog = prompt_catalog()
    if evaluation.result.status is TacticStatus.ERROR:
        raw = next(
            (item for item in evaluation.result.diagnostics if item.strip()),
            None,
        )
        if raw is None:
            raw = catalog.text("analogy_missing_lean_diagnostic")
        return catalog.text(
            "analogy_lean_correction_repair",
            tactic=proposal.candidate.source,
            diagnostic=_bounded_diagnostic(raw),
            contract=contract,
        )
    reason = (
        evaluation.discarded.reason.value
        if evaluation.discarded is not None
        else "no_progress"
    )
    return catalog.text(
        "analogy_search_correction_repair",
        tactic=proposal.candidate.source,
        reason=reason,
        contract=contract,
    )


def _fallback_seed(base_seed: int, state: SymbolicState, attempt: int) -> int:
    payload = (
        f"analogy-fallback-v1\x1f{base_seed}\x1f{state.id}\x1f"
        f"{state.expression}\x1f{attempt}"
    )
    return int.from_bytes(
        hashlib.sha256(payload.encode("utf-8")).digest()[:8], "big"
    ) % (2**63)


__all__ = [
    "AnalogyFallback", "GenerationAttempt", "GenerationStatus", "PredictionLabel",
    "GenericRescueOutcome", "GenericRescueRunner", "PolicyActionRecord",
    "ProofRepresentation", "ProofRescueContext", "RescueAttempt",
    "RescueOutputError", "RescueProposal", "RescueRollout", "RescueTermination",
    "TacticEvaluation", "parse_analogy_tactic", "parse_analogy_update",
    "zero_embedding_like",
]
