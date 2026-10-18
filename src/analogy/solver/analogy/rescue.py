"""Typed records and bounded analogy correction for stuck proof states."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Protocol

from ...config import AppConfig
from ...model.parser import (
    candidate_rejection_reason,
    normalize_lean4_tactic,
    tactic_is_incomplete,
)
from ...model.prompt_runner import GenerationAttempt, GenerationStatus
from ...model.prompts import (
    focused_lean_goal,
    lean_tactic_card,
    prompt_catalog,
    render_lean_goal_for_model,
)
from ...utils.records import (
    CancellationToken,
    CandidateOrigin,
    DiscardedSuccessor,
    Message,
    SearchNode,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
    _require_text,
    _typed_tuple,
)
from ..proof.semantic import (
    SemanticResult,
    render_countermodel_table,
    render_semantic_guidance,
)
from .protocol import (
    extract_working_model_block,
    record_model_history,
    remove_analogy_blocks,
    render_working_model,
    render_scratchpad_context,
)
from .state import (
    AnalogyScratchpad,
    AnalogyState,
    AnalogyStateError,
    AnalogyUpdateAction,
    MentalModel,
    WorkingMentalModel,
)
from .structural_scoring import (
    ProofBelief,
    StructuralScoringError,
    build_proof_belief,
    score_mental_model_revision,
)

if TYPE_CHECKING:
    from ..search import SearchSession
    from ..proof.adapter import TacticSubmitter


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


class PredictionLabel(str, Enum):
    PREDICTED_NO_CHANGE = "predicted_no_change"
    PREDICTION_MATCHED = "prediction_matched"
    PREDICTION_MISMATCHED = "prediction_mismatched"


@dataclass(frozen=True, slots=True)
class AnalogyGenerationAttempt(GenerationAttempt):
    prediction_label: PredictionLabel | None = None
    structural_distance: float | None = None
    structural_coupling: tuple[tuple[float, ...], ...] | None = None

    def __post_init__(self) -> None:
        GenerationAttempt.__post_init__(self)
        if self.prediction_label is not None and not isinstance(
            self.prediction_label, PredictionLabel
        ):
            raise ValueError("prediction_label must be a PredictionLabel or None")
        if self.structural_distance is not None:
            if (
                isinstance(self.structural_distance, bool)
                or not isinstance(self.structural_distance, (int, float))
                or self.structural_distance < 0
            ):
                raise ValueError("structural_distance must be non-negative or None")
        if self.structural_coupling is not None:
            object.__setattr__(
                self,
                "structural_coupling",
                tuple(tuple(float(value) for value in row) for row in self.structural_coupling),
            )


@dataclass(frozen=True, slots=True)
class RescueProposal:
    candidate: TacticCandidate
    predicted_state: str

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, TacticCandidate) or self.candidate.origin is not CandidateOrigin.ANALOGICAL:
            raise ValueError("rescue candidate must have analogical origin")
        _require_text(self.predicted_state, "predicted_state")


@dataclass(frozen=True, slots=True)
class RescueAttempt:
    proposal: RescueProposal
    result: TacticResult
    discarded: DiscardedSuccessor | None = None

    def __post_init__(self) -> None:
        if self.result.candidate != self.proposal.candidate:
            raise ValueError("result candidate must match proposal candidate")
        if self.discarded is not None and self.discarded.result != self.result:
            raise ValueError("discarded must reference the attempt result")


@dataclass(frozen=True, slots=True)
class RescueRollout:
    rollout_id: str
    state_id: str
    proposals: tuple[RescueProposal, ...]
    attempts: tuple[RescueAttempt, ...]
    termination: RescueTermination
    generations: tuple[AnalogyGenerationAttempt, ...] = ()
    analogy_trace: Any | None = None
    semantic_event_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.rollout_id, "rollout_id")
        _require_text(self.state_id, "state_id")
        object.__setattr__(self, "proposals", _typed_tuple(self.proposals, RescueProposal, "proposals"))
        object.__setattr__(self, "attempts", _typed_tuple(self.attempts, RescueAttempt, "attempts"))
        object.__setattr__(
            self,
            "generations",
            _typed_tuple(
                self.generations, AnalogyGenerationAttempt, "generations"
            ),
        )
        if not isinstance(self.termination, RescueTermination):
            raise ValueError("termination must be a RescueTermination")
        if self.semantic_event_id is not None:
            _require_text(self.semantic_event_id, "semantic_event_id")
        if any(item.proposal not in self.proposals for item in self.attempts):
            raise ValueError("attempt proposals must belong to rollout")


@dataclass(frozen=True, slots=True)
class AnalogyRescueOutcome:
    completed_proof: str | None = None
    successor: SearchNode | None = None
    rollouts: tuple[RescueRollout, ...] = ()
    cancelled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "rollouts", _typed_tuple(self.rollouts, RescueRollout, "rollouts"))
        if sum(value is not None for value in (self.completed_proof, self.successor)) > 1:
            raise ValueError("fallback outcome must have at most one terminal result")


@dataclass(frozen=True, slots=True)
class AnalogyRescue:
    """Lazy rescue plan generated for one semantically recoverable stuck state."""

    runner: AnalogyRescueRunner
    state: SymbolicState
    goal: SymbolicState
    ordinary_attempts: tuple[TacticResult, ...]
    cancellation: CancellationToken
    semantic_guidance: str
    dependency_table: str
    force_domain_change: bool
    proof_belief: ProofBelief
    classify: Callable[[SymbolicState, TacticResult], TacticEvaluation]


@dataclass(frozen=True, slots=True)
class AnalogyRescueResult:
    outcome: AnalogyRescueOutcome


class AnalogyRescueRunner(Protocol):
    scratchpad: AnalogyScratchpad

    def search(
        self,
        state: SymbolicState,
        goal: SymbolicState,
        ordinary_attempts: tuple[TacticResult, ...],
        evaluator: Callable[[TacticCandidate], TacticEvaluation],
        cancellation: CancellationToken,
        semantic_guidance: str,
        dependency_table: str = "(none)",
        *,
        force_domain_change: bool = False,
        proof_belief: ProofBelief | None = None,
    ) -> AnalogyRescueOutcome: ...


def generate_analogy_rescue(
    search: SearchSession,
    scratchpad: AnalogyScratchpad,
) -> AnalogyRescue:
    """Build the lazy rescue plan for the search's current stuck state."""

    runner = search.context.analogy_fallback
    recovery = search.recovery
    rescue_node = search.rescue_node
    ordinary = search.rescue_ordinary
    if runner is None or recovery is None or rescue_node is None or ordinary is None:
        raise RuntimeError("search has no pending analogy rescue")
    if runner.scratchpad is not scratchpad:
        raise ValueError("analogy policy and rescue must share one scratchpad")
    return AnalogyRescue(
        runner=runner,
        state=rescue_node.state,
        goal=search.goal,
        ordinary_attempts=ordinary.attempts,
        cancellation=search.context.cancellation,
        semantic_guidance=render_semantic_guidance(recovery),
        dependency_table=render_countermodel_table(recovery.countermodel_table),
        force_domain_change=recovery.result is SemanticResult.COUNTERMODEL,
        proof_belief=build_proof_belief(search),
        classify=search.classify_rescue_result,
    )


def initialize_analogy_fallback(
    config: AppConfig,
    generator: Any,
    model_events: list[dict[str, Any]],
    scratchpad: AnalogyScratchpad,
) -> AnalogyFallback:
    """Create the analogy-only fallback policy for one problem search."""

    return AnalogyFallback(
        generator,
        max_model_calls=config.analogy.max_model_calls,
        seed=config.run.seed,
        trace_sink=model_events.append,
        analogy_scratchpad=scratchpad,
        embedding_layer=config.model.hidden_layer,
    )


def submit_analogy_rescue_to_lean(
    proof: TacticSubmitter,
    rescue: AnalogyRescue,
) -> AnalogyRescueResult:
    """Drive one bounded rescue plan, keeping every Lean call in submission."""

    def evaluate(candidate: TacticCandidate) -> TacticEvaluation:
        result = proof.run_tactic(rescue.state, candidate)
        return rescue.classify(rescue.state, result)

    outcome = rescue.runner.search(
        rescue.state,
        rescue.goal,
        rescue.ordinary_attempts,
        evaluate,
        rescue.cancellation,
        rescue.semantic_guidance,
        dependency_table=rescue.dependency_table,
        force_domain_change=rescue.force_domain_change,
        proof_belief=rescue.proof_belief,
    )
    return AnalogyRescueResult(outcome)


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


def parse_analogy_update(
    text: str,
    *,
    proof_state: str | None = None,
    current: WorkingMentalModel | None,
    force_domain_change: bool = False,
) -> tuple[AnalogyUpdateAction, WorkingMentalModel, RescueProposal]:
    """Parse one scratchpad update and its first tactic without mutating state."""

    try:
        analogy, payload_text = extract_working_model_block(text)
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
            'action must be "revise_model", "revise_steps", or "change_domain"'
        ) from error
    if force_domain_change and action is not AnalogyUpdateAction.CHANGE_DOMAIN:
        raise RescueOutputError("countermodel forces action change_domain")
    if current is None and action is not AnalogyUpdateAction.CHANGE_DOMAIN:
        raise RescueOutputError("missing active analogy requires action change_domain")
    if (
        action is AnalogyUpdateAction.REVISE_STEPS
        and isinstance(analogy, AnalogyState)
        and isinstance(current, AnalogyState)
    ):
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
        analogy_scratchpad: AnalogyScratchpad,
        trace_sink: Any | None = None,
        embedding_layer: int | None = None,
    ) -> None:
        if isinstance(max_model_calls, bool) or not isinstance(max_model_calls, int) or max_model_calls <= 0:
            raise ValueError("max_model_calls must be a positive integer")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a non-negative integer")
        if not isinstance(analogy_scratchpad, AnalogyScratchpad):
            raise ValueError("analogy_scratchpad must be an AnalogyScratchpad")
        self._generator = generator
        self._max_model_calls = max_model_calls
        self._seed = seed
        self._trace_sink = trace_sink
        self._embedding_layer = embedding_layer
        self.scratchpad = analogy_scratchpad

    def search(
        self,
        state,
        goal,
        ordinary_attempts,
        evaluator,
        cancellation,
        semantic_guidance,
        dependency_table="(none)",
        *,
        force_domain_change=False,
        proof_belief: ProofBelief | None = None,
    ) -> AnalogyRescueOutcome:
        scratchpad = self.scratchpad
        if force_domain_change:
            scratchpad.abandon_current()
        if cancellation.is_cancelled():
            return AnalogyRescueOutcome(cancelled=True)
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
            "analogy_update",
            overall_target=render_lean_goal_for_model(goal.expression),
            current_goals=render_lean_goal_for_model(focused_lean_goal(state.expression)),
            accepted="\n".join(state.context) or "(none)",
            open_tactics="\n".join(open_feedback) or "(none)",
            rejected_tactics="\n".join(rejected_feedback) or "(none)",
            tactic_card=lean_tactic_card(state.expression),
            semantic_guidance=semantic_guidance,
            dependency_table=dependency_table,
        )
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
            Message("system", render_scratchpad_context(scratchpad)),
            user,
        )
        if proof_belief is not None:
            if self._embedding_layer is None:
                raise ValueError(
                    "embedding_layer is required for structural mental-model scoring"
                )
            return self._search_ranked_mental_models(
                state,
                evaluator,
                cancellation,
                messages,
                attempted_order,
                attempted_tactics,
                proof_belief,
                force_domain_change=force_domain_change,
            )
        active_messages = messages
        rollouts: list[RescueRollout] = []
        calibrated_update: tuple[AnalogyUpdateAction, WorkingMentalModel] | None = None
        for index in range(self._max_model_calls):
            if cancellation.is_cancelled():
                return AnalogyRescueOutcome(
                    rollouts=tuple(rollouts), cancelled=True
                )
            seed = _fallback_seed(self._seed, state, index)
            raw = ""
            try:
                raw = self._generator.generate(active_messages, seed=seed)
                self._trace(state.id, active_messages, seed, raw)
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
                analogy = render_working_model(update_state)
                if proposal.candidate.source in attempted_tactics:
                    diagnostic = (
                        f"tactic {proposal.candidate.source!r} was already attempted "
                        "at this proof state"
                    )
                    generation = AnalogyGenerationAttempt(
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
                                contract=_rescue_output_contract(),
                            ),
                        ),
                    )
                    continue
                attempted_tactics.add(proposal.candidate.source)
                attempted_order.append(proposal.candidate.source)
                generation = AnalogyGenerationAttempt(
                    seed, raw, GenerationStatus.VALID, repair=index > 0
                )
            except RescueOutputError as error:
                generation = AnalogyGenerationAttempt(
                    seed,
                    raw,
                    GenerationStatus.INVALID,
                    index > 0,
                    "schema",
                    str(error),
                )
                rollouts.append(RescueRollout(f"{state.id}:analogy:{index}", state.id, (), (), RescueTermination.INVALID_OUTPUT, generations=(generation,)))
                active_messages = messages + (
                    Message(
                        "user",
                        prompt_catalog().text(
                            "analogy_invalid_repair",
                            diagnostic=error,
                            previous_response=remove_analogy_blocks(raw),
                            contract=_rescue_output_contract(),
                        ),
                    ),
                )
                continue
            except Exception as error:
                self._trace(state.id, active_messages, seed, raw, error=error)
                generation = AnalogyGenerationAttempt(
                    seed,
                    raw,
                    GenerationStatus.RUNTIME_ERROR,
                    index > 0,
                    type(error).__name__,
                    str(error) or type(error).__name__,
                )
                rollouts.append(RescueRollout(f"{state.id}:analogy:{index}", state.id, (), (), RescueTermination.GENERATION_ERROR, generations=(generation,)))
                continue
            if cancellation.is_cancelled():
                rollouts.append(
                    RescueRollout(
                        f"{state.id}:analogy:{index}",
                        state.id,
                        (proposal,),
                        (),
                        RescueTermination.CANCELLED,
                        generations=(generation,),
                        analogy_trace=analogy,
                    )
                )
                return AnalogyRescueOutcome(
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
                            contract=_rescue_output_contract(),
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
            rollouts.append(RescueRollout(f"{state.id}:analogy:{index}", state.id, (proposal,), (attempt,), termination, generations=(generation,), analogy_trace=analogy))
            record_model_history(
                scratchpad, _result_feedback(evaluation.result)
            )
            if (
                evaluation.completed_proof is not None
                or evaluation.successor is not None
            ):
                try:
                    scratchpad.apply(update_action, update_state)
                except AnalogyStateError as error:
                    raise RescueOutputError(str(error)) from error
            if evaluation.completed_proof is not None:
                return AnalogyRescueOutcome(
                    completed_proof=evaluation.completed_proof,
                    rollouts=tuple(rollouts),
                )
            if evaluation.successor is not None:
                return AnalogyRescueOutcome(
                    successor=evaluation.successor, rollouts=tuple(rollouts)
                )
            if matched:
                if calibrated_update is None:
                    calibrated_update = update_action, update_state
            if getattr(evaluation.result, "infrastructure_failure", None) is not None:
                return AnalogyRescueOutcome(rollouts=tuple(rollouts))
            active_messages = messages + (
                Message(
                    "user",
                    _analogy_correction_feedback(
                        proposal,
                        evaluation,
                        contract=_rescue_output_contract(),
                    ),
                ),
            )
        if calibrated_update is not None:
            scratchpad.apply(*calibrated_update)
        return AnalogyRescueOutcome(rollouts=tuple(rollouts))

    def _search_ranked_mental_models(
        self,
        state: SymbolicState,
        evaluator: Callable[[TacticCandidate], TacticEvaluation],
        cancellation: CancellationToken,
        messages: tuple[Message, ...],
        attempted_order: list[str],
        attempted_tactics: set[str],
        proof_belief: ProofBelief,
        *,
        force_domain_change: bool,
    ) -> AnalogyRescueOutcome:
        """Generate and score the whole revision batch before asking Lean."""

        scratchpad = self.scratchpad
        rollouts: list[RescueRollout] = []
        ranked: list[
            tuple[
                float,
                int,
                AnalogyUpdateAction,
                MentalModel,
                RescueProposal,
                AnalogyGenerationAttempt,
                str,
            ]
        ] = []
        for index in range(self._max_model_calls):
            if cancellation.is_cancelled():
                return AnalogyRescueOutcome(
                    rollouts=tuple(rollouts), cancelled=True
                )
            seed = _fallback_seed(self._seed, state, index)
            raw = ""
            try:
                raw = self._generator.generate(messages, seed=seed)
                self._trace(state.id, messages, seed, raw)
                action, candidate_model, proposal = parse_analogy_update(
                    raw,
                    proof_state=state.expression,
                    current=scratchpad.current,
                    force_domain_change=force_domain_change,
                )
                if not isinstance(candidate_model, MentalModel):
                    raise RescueOutputError(
                        "structural revision scoring requires a mental model"
                    )
                try:
                    scratchpad.validate_transition(action, candidate_model)
                except AnalogyStateError as error:
                    raise RescueOutputError(str(error)) from error
                rendered = render_working_model(candidate_model)
                if proposal.candidate.source in attempted_tactics:
                    diagnostic = (
                        f"tactic {proposal.candidate.source!r} was already attempted "
                        "at this proof state"
                    )
                    generation = AnalogyGenerationAttempt(
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
                            (),
                            (),
                            RescueTermination.DUPLICATE,
                            generations=(generation,),
                            analogy_trace=rendered,
                        )
                    )
                    continue
                attempted_tactics.add(proposal.candidate.source)
                attempted_order.append(proposal.candidate.source)
                if _normalized_prediction_state(proposal.predicted_state) == _normalized_prediction_state(state.expression):
                    generation = AnalogyGenerationAttempt(
                        seed,
                        raw,
                        GenerationStatus.VALID,
                        index > 0,
                        prediction_label=PredictionLabel.PREDICTED_NO_CHANGE,
                    )
                    rollouts.append(
                        RescueRollout(
                            f"{state.id}:analogy:{index}",
                            state.id,
                            (proposal,),
                            (),
                            RescueTermination.PREDICTED_NO_CHANGE,
                            generations=(generation,),
                            analogy_trace=rendered,
                        )
                    )
                    continue
                fit = score_mental_model_revision(
                    candidate_model,
                    proof_belief,
                    self._generator,
                    layer=self._embedding_layer,
                )
                generation = AnalogyGenerationAttempt(
                    seed,
                    raw,
                    GenerationStatus.VALID,
                    index > 0,
                    structural_distance=fit.distance,
                    structural_coupling=fit.coupling,
                )
                self._trace_structural_fit(
                    state.id, index, seed, candidate_model, fit.distance, fit.coupling
                )
                ranked.append(
                    (
                        fit.distance,
                        index,
                        action,
                        candidate_model,
                        proposal,
                        generation,
                        rendered,
                    )
                )
            except RescueOutputError as error:
                generation = AnalogyGenerationAttempt(
                    seed,
                    raw,
                    GenerationStatus.INVALID,
                    index > 0,
                    "schema",
                    str(error),
                )
                rollouts.append(
                    RescueRollout(
                        f"{state.id}:analogy:{index}",
                        state.id,
                        (),
                        (),
                        RescueTermination.INVALID_OUTPUT,
                        generations=(generation,),
                    )
                )
            except StructuralScoringError as error:
                generation = AnalogyGenerationAttempt(
                    seed,
                    raw,
                    GenerationStatus.RUNTIME_ERROR,
                    index > 0,
                    "structural_scoring",
                    str(error),
                )
                rollouts.append(
                    RescueRollout(
                        f"{state.id}:analogy:{index}",
                        state.id,
                        (),
                        (),
                        RescueTermination.GENERATION_ERROR,
                        generations=(generation,),
                    )
                )
            except Exception as error:
                self._trace(state.id, messages, seed, raw, error=error)
                generation = AnalogyGenerationAttempt(
                    seed,
                    raw,
                    GenerationStatus.RUNTIME_ERROR,
                    index > 0,
                    type(error).__name__,
                    str(error) or type(error).__name__,
                )
                rollouts.append(
                    RescueRollout(
                        f"{state.id}:analogy:{index}",
                        state.id,
                        (),
                        (),
                        RescueTermination.GENERATION_ERROR,
                        generations=(generation,),
                    )
                )

        for _, index, action, candidate_model, proposal, generation, rendered in sorted(
            ranked, key=lambda item: (item[0], item[1])
        ):
            if cancellation.is_cancelled():
                return AnalogyRescueOutcome(
                    rollouts=tuple(rollouts), cancelled=True
                )
            evaluation = evaluator(proposal.candidate)
            generation = replace(
                generation,
                prediction_label=(
                    PredictionLabel.PREDICTION_MATCHED
                    if _prediction_matches(proposal.predicted_state, evaluation)
                    else PredictionLabel.PREDICTION_MISMATCHED
                ),
            )
            attempt = RescueAttempt(proposal, evaluation.result, evaluation.discarded)
            termination = (
                RescueTermination.COMPLETE
                if evaluation.completed_proof is not None
                else RescueTermination.PROGRESS
                if evaluation.successor is not None
                else RescueTermination.REJECTED
                if evaluation.result.status is TacticStatus.ERROR
                else RescueTermination.EXHAUSTED
            )
            rollouts.append(
                RescueRollout(
                    f"{state.id}:analogy:{index}",
                    state.id,
                    (proposal,),
                    (attempt,),
                    termination,
                    generations=(generation,),
                    analogy_trace=rendered,
                )
            )
            record_model_history(scratchpad, _result_feedback(evaluation.result))
            if evaluation.completed_proof is not None or evaluation.successor is not None:
                scratchpad.apply(action, candidate_model)
            if evaluation.completed_proof is not None:
                return AnalogyRescueOutcome(
                    completed_proof=evaluation.completed_proof,
                    rollouts=tuple(rollouts),
                )
            if evaluation.successor is not None:
                return AnalogyRescueOutcome(
                    successor=evaluation.successor, rollouts=tuple(rollouts)
                )
            if getattr(evaluation.result, "infrastructure_failure", None) is not None:
                return AnalogyRescueOutcome(rollouts=tuple(rollouts))
        return AnalogyRescueOutcome(rollouts=tuple(rollouts))

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

    def _trace_structural_fit(
        self,
        state_id: str,
        generation_index: int,
        seed: int,
        model: MentalModel,
        distance: float,
        coupling: tuple[tuple[float, ...], ...],
    ) -> None:
        if self._trace_sink is not None:
            self._trace_sink(
                {
                    "kind": "mental_model_structural_score",
                    "state_id": state_id,
                    "generation_index": generation_index,
                    "seed": seed,
                    "domain": model.domain,
                    "distance": distance,
                    "coupling": [list(row) for row in coupling],
                }
            )


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


def _rescue_output_contract() -> str:
    return prompt_catalog().text("analogy_update_output_contract")


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
    "AnalogyFallback", "AnalogyGenerationAttempt", "AnalogyRescue",
    "AnalogyRescueOutcome", "AnalogyRescueResult", "AnalogyRescueRunner",
    "PredictionLabel", "RescueAttempt", "RescueOutputError",
    "RescueProposal", "RescueRollout", "RescueTermination", "TacticEvaluation",
    "generate_analogy_rescue", "parse_analogy_update",
    "initialize_analogy_fallback",
    "submit_analogy_rescue_to_lean",
]
