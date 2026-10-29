"""Typed records and bounded analogy correction for stuck proof states."""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Protocol

from ...config import AppConfig
from ...model.parser import (
    candidate_rejection_reason,
    normalize_lean4_tactic,
    tactic_is_incomplete,
)
from ...model.model_provider import CompletionScore
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
from ..proof.syntax import InventoryError, proof_goals, proposition_root
from .protocol import (
    extract_story_model,
    extract_working_model_block,
    record_model_history,
    remove_analogy_blocks,
    render_working_model,
    render_scratchpad_context,
    render_story_model,
)
from .candidate_cache import (
    StoryCandidateCache,
    build_candidate_cache_key,
    file_sha256,
    story_candidate_cache_path,
)
from .state import (
    AnalogyScratchpad,
    AnalogyState,
    AnalogyStateError,
    AnalogyUpdateAction,
    MentalModel,
    WorkingMentalModel,
)
from .validation import (
    MentalModelValidationError,
    validate_mental_model,
    validate_story_model,
)
from .structural_scoring import (
    EmbeddingCalibration,
    ProofBelief,
    StructuralScoringError,
    UnscorableMentalModelError,
    abstract_proof_transition,
    build_proof_belief,
    load_embedding_calibration,
    proof_trajectory,
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
    SCORED = "scored"


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
        if (
            not isinstance(self.candidate, TacticCandidate)
            or self.candidate.origin
            not in {CandidateOrigin.ANALOGICAL, CandidateOrigin.DELIBERATIVE}
        ):
            raise ValueError(
                "rescue candidate must have analogical or deliberative origin"
            )
        _require_text(self.predicted_state, "predicted_state")


@dataclass(frozen=True, slots=True)
class _ScoredRevision:
    distance: float
    generation_index: int
    action: AnalogyUpdateAction
    model: MentalModel
    generation: AnalogyGenerationAttempt
    rendered_model: str
    generation_messages: tuple[Message, ...]


def order_analogy_revisions(
    revisions: tuple[Any, ...] | list[Any],
    *,
    selection: str,
    seed: int,
) -> tuple[Any, ...]:
    """Return a deterministic ordering without changing candidate records."""

    if selection not in {"structural", "random"}:
        raise ValueError("selection must be 'structural' or 'random'")
    ordered = list(revisions)
    if selection == "structural":
        ordered.sort(key=lambda item: (item.distance, item.generation_index))
    else:
        random.Random(seed).shuffle(ordered)
    return tuple(ordered)


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
    *,
    force_domain_change: bool | None = None,
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
        force_domain_change=(
            recovery.result is SemanticResult.COUNTERMODEL
            if force_domain_change is None
            else force_domain_change
        ),
        proof_belief=build_proof_belief(search),
        classify=search.classify_rescue_result,
    )


def initialize_analogy_fallback(
    config: AppConfig,
    generator: Any,
    model_events: list[dict[str, Any]],
    scratchpad: AnalogyScratchpad,
    *,
    initial_proposal: RescueProposal | None = None,
    initial_state_id: str | None = None,
) -> AnalogyFallback:
    """Create the analogy-only fallback policy for one problem search."""

    calibration = None
    if config.analogy.calibration_path is not None:
        calibration = load_embedding_calibration(
            config.analogy.calibration_path,
            expected_model_id=(
                config.analogy.model_name or config.model.name
            ),
        )
    return AnalogyFallback(
        generator,
        max_model_calls=config.analogy.max_model_calls,
        seed=config.run.seed,
        trace_sink=model_events.append,
        analogy_scratchpad=scratchpad,
        calibration=calibration,
        selection=getattr(config.analogy, "selection", "structural"),
        candidate_cache=StoryCandidateCache(story_candidate_cache_path(config)),
        cache_model_revision=(
            getattr(generator, "model_revision", None)
            or config.model.revision
            or config.analogy.model_name
            or config.model.name
        ),
        cache_prompt_sha256=file_sha256(config.prompts.path),
        cache_corpus_sha256=file_sha256(config.benchmark.corpus),
        initial_proposal=initial_proposal,
        initial_state_id=initial_state_id,
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


def _parse_tactic_only(
    text: str,
    proof_state: str,
    *,
    attempted_tactics: set[str] | frozenset[str] = frozenset(),
) -> RescueProposal:
    """Parse and validate only the newly proposed current-state tactic."""

    try:
        payload = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError) as error:
        raise RescueOutputError("recovery tactic output must be strict JSON") from error
    requires_decision = _focused_goal_is_disjunction(proof_state)
    required_fields = (
        {"decision", "tactic", "predicted_state"}
        if requires_decision
        else {"tactic", "predicted_state"}
    )
    if not isinstance(payload, dict) or set(payload) != required_fields:
        raise RescueOutputError(
            "recovery tactic JSON must contain exactly "
            + ", ".join(f'"{field}"' for field in sorted(required_fields))
        )
    tactic = payload["tactic"]
    predicted_state = payload["predicted_state"]
    if (
        not isinstance(tactic, str)
        or not tactic.strip()
        or "\n" in tactic
        or "\r" in tactic
        or "\x00" in tactic
    ):
        raise RescueOutputError("tactic must be one nonblank safe line")
    if not isinstance(predicted_state, str) or not predicted_state.strip():
        raise RescueOutputError("predicted_state must be nonblank text")
    canonical = normalize_lean4_tactic(tactic, proof_state)
    if requires_decision:
        _validate_prospective_decision(payload["decision"], canonical)
    canonical_attempts = {
        normalize_lean4_tactic(item, proof_state) for item in attempted_tactics
    }
    if canonical in canonical_attempts:
        raise RescueOutputError(
            f"tactic {canonical!r} was already attempted at this proof state"
        )
    if tactic_is_incomplete(canonical):
        raise RescueOutputError(f"tactic {canonical!r} is incomplete")
    rejection = candidate_rejection_reason(canonical, proof_state)
    if rejection is not None:
        raise RescueOutputError(f"tactic {canonical!r} was filtered: {rejection}")
    return RescueProposal(
        TacticCandidate(canonical, 1.0, CandidateOrigin.ANALOGICAL),
        predicted_state.strip(),
    )


def _focused_goal_is_disjunction(proof_state: str) -> bool:
    try:
        target = proof_goals(proof_state)[0].target
    except (InventoryError, IndexError):
        return False
    return proposition_root(target).operator == "∨"


def _validate_prospective_decision(decision: object, selected_tactic: str) -> None:
    required = {"current_everyday_state", "options", "selection_reason"}
    if not isinstance(decision, dict) or set(decision) != required:
        raise RescueOutputError(
            "decision must contain exactly current_everyday_state, options, and selection_reason"
        )
    for field in ("current_everyday_state", "selection_reason"):
        value = decision[field]
        if not isinstance(value, str) or not value.strip():
            raise RescueOutputError(f"decision {field} must be nonblank text")
    options = decision["options"]
    if not isinstance(options, list) or not options:
        raise RescueOutputError("decision options must be a nonempty list")
    option_fields = {"tactic", "everyday_consequence", "proof_consequence"}
    tactics: list[str] = []
    for option in options:
        if not isinstance(option, dict) or set(option) != option_fields:
            raise RescueOutputError(
                "each decision option must contain exactly tactic, "
                "everyday_consequence, and proof_consequence"
            )
        if any(
            not isinstance(option[field], str) or not option[field].strip()
            for field in option_fields
        ):
            raise RescueOutputError("decision option fields must be nonblank text")
        tactics.append(" ".join(option["tactic"].split()))
    if not {"left", "right"}.issubset(tactics):
        raise RescueOutputError(
            "decision options must compare both left and right consequences"
        )
    if selected_tactic not in tactics:
        raise RescueOutputError("selected tactic must appear in decision options")


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
    required_domain: str | None = None,
    abandoned_domains: tuple[str, ...] = (),
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
        (isinstance(current, MentalModel) or required_domain is not None)
        and not isinstance(analogy, MentalModel)
    ):
        raise RescueOutputError("recovery requires the validated mental-model JSON format")
    if isinstance(analogy, MentalModel):
        try:
            analogy = validate_mental_model(
                analogy,
                proof_state or "",
                required_domain=required_domain,
                abandoned_domains=abandoned_domains,
            )
        except MentalModelValidationError as error:
            raise RescueOutputError(str(error)) from error
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
        calibration: EmbeddingCalibration | None = None,
        selection: str = "structural",
        candidate_cache: StoryCandidateCache | None = None,
        cache_model_revision: str | None = None,
        cache_prompt_sha256: str | None = None,
        cache_corpus_sha256: str | None = None,
        initial_proposal: RescueProposal | None = None,
        initial_state_id: str | None = None,
    ) -> None:
        if isinstance(max_model_calls, bool) or not isinstance(max_model_calls, int) or max_model_calls <= 0:
            raise ValueError("max_model_calls must be a positive integer")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a non-negative integer")
        if not isinstance(analogy_scratchpad, AnalogyScratchpad):
            raise ValueError("analogy_scratchpad must be an AnalogyScratchpad")
        if selection not in {"structural", "random"}:
            raise ValueError("selection must be 'structural' or 'random'")
        self._generator = generator
        self._max_model_calls = max_model_calls
        self._seed = seed
        self._trace_sink = trace_sink
        self._calibration = calibration
        self._selection = selection
        cache_fields = (
            cache_model_revision,
            cache_prompt_sha256,
            cache_corpus_sha256,
        )
        if candidate_cache is not None and any(item is None for item in cache_fields):
            raise ValueError("candidate cache requires model, prompt, and corpus identity")
        self._candidate_cache = candidate_cache
        self._cache_model_revision = cache_model_revision
        self._cache_prompt_sha256 = cache_prompt_sha256
        self._cache_corpus_sha256 = cache_corpus_sha256
        if (initial_proposal is None) != (initial_state_id is None):
            raise ValueError("initial proposal and state id must be supplied together")
        self._initial_proposal = initial_proposal
        self._initial_state_id = initial_state_id
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
        initial_rollouts: list[RescueRollout] = []
        if force_domain_change and self._initial_state_id == state.id:
            self._initial_proposal = None
            self._initial_state_id = None
        if (
            self._initial_proposal is not None
            and self._initial_state_id == state.id
        ):
            proposal = self._initial_proposal
            self._initial_proposal = None
            self._initial_state_id = None
            if proposal.candidate.source not in attempted_tactics:
                attempted_tactics.add(proposal.candidate.source)
                attempted_order.append(proposal.candidate.source)
                evaluation = evaluator(proposal.candidate)
                attempt = RescueAttempt(
                    proposal, evaluation.result, evaluation.discarded
                )
                termination = (
                    RescueTermination.COMPLETE
                    if evaluation.completed_proof is not None
                    else RescueTermination.PROGRESS
                    if evaluation.successor is not None
                    else RescueTermination.REJECTED
                    if evaluation.result.status is TacticStatus.ERROR
                    else RescueTermination.EXHAUSTED
                )
                rollout = RescueRollout(
                    f"{state.id}:analogy:initial",
                    state.id,
                    (proposal,),
                    (attempt,),
                    termination,
                    analogy_trace=render_scratchpad_context(scratchpad),
                )
                initial_rollouts.append(rollout)
                if evaluation.completed_proof is not None:
                    return AnalogyRescueOutcome(
                        completed_proof=evaluation.completed_proof,
                        rollouts=(rollout,),
                    )
                if evaluation.successor is not None:
                    return AnalogyRescueOutcome(
                        successor=evaluation.successor, rollouts=(rollout,)
                    )
        messages = prompt_catalog().messages(
            "analogy_update",
            overall_target=render_lean_goal_for_model(goal.expression),
            current_goals=render_lean_goal_for_model(focused_lean_goal(state.expression)),
            accepted="\n".join(state.context) or "(none)",
            open_tactics="\n".join(open_feedback) or "(none)",
            rejected_tactics="\n".join(rejected_feedback) or "(none)",
            tactic_card=lean_tactic_card(state.expression),
            domain_card=prompt_catalog().text("analogy_domain_card"),
            semantic_guidance=semantic_guidance,
            dependency_table=dependency_table,
            required_domain=(
                scratchpad.assigned_domain
                or (scratchpad.current.domain if scratchpad.current is not None else "(none)")
            ),
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
            return self._search_ranked_mental_models(
                state,
                evaluator,
                cancellation,
                attempted_order,
                attempted_tactics,
                proof_belief,
                semantic_guidance=semantic_guidance,
                dependency_table=dependency_table,
                force_domain_change=force_domain_change,
                prior_rollouts=tuple(initial_rollouts),
            )
        active_messages = messages
        rollouts: list[RescueRollout] = list(initial_rollouts)
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
                    required_domain=scratchpad.assigned_domain,
                    abandoned_domains=scratchpad.abandoned_domains,
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
                            current_domain=(
                                scratchpad.current.domain
                                if scratchpad.current is not None
                                else (scratchpad.abandoned_domains[-1] if scratchpad.abandoned_domains else "(none)")
                            ),
                            required_domain=scratchpad.assigned_domain or "preserve current domain",
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
        attempted_order: list[str],
        attempted_tactics: set[str],
        proof_belief: ProofBelief,
        *,
        semantic_guidance: str,
        dependency_table: str,
        force_domain_change: bool,
        prior_rollouts: tuple[RescueRollout, ...] = (),
    ) -> AnalogyRescueOutcome:
        """Rank current-path stories, then request one current-goal tactic."""

        scratchpad = self.scratchpad
        rollouts: list[RescueRollout] = list(prior_rollouts)
        ranked: list[_ScoredRevision] = []
        trajectory = proof_trajectory(proof_belief)
        if not trajectory.tactics:
            return AnalogyRescueOutcome(rollouts=tuple(rollouts))
        abstract_trajectory = "\n".join(
            f"{index}. {abstract_proof_transition(tactic)}"
            for index, tactic in enumerate(trajectory.tactics, 1)
        )
        required_domain = (
            scratchpad.assigned_domain
            or (
                scratchpad.current.domain
                if isinstance(scratchpad.current, MentalModel)
                else None
            )
        )
        if required_domain is None:
            return AnalogyRescueOutcome(rollouts=tuple(rollouts))
        action = (
            AnalogyUpdateAction.CHANGE_DOMAIN
            if force_domain_change
            else AnalogyUpdateAction.REVISE_MODEL
        )
        base_story_messages = prompt_catalog().messages(
            "analogy_recovery_story",
            required_domain=required_domain,
            abstract_trajectory=abstract_trajectory,
        )
        active_story_messages = base_story_messages
        for index in range(self._max_model_calls):
            if cancellation.is_cancelled():
                _append_unsubmitted_rollouts(
                    rollouts,
                    state.id,
                    sorted(ranked, key=lambda item: item.generation_index),
                )
                return AnalogyRescueOutcome(
                    rollouts=tuple(rollouts), cancelled=True
                )
            seed = _fallback_seed(self._seed, state, index)
            raw = ""
            traced_story_call = False
            try:
                if self._candidate_cache is None:
                    raw = self._generator.generate(active_story_messages, seed=seed)
                else:
                    key = build_candidate_cache_key(
                        model_revision=self._cache_model_revision,
                        prompt_sha256=self._cache_prompt_sha256,
                        corpus_sha256=self._cache_corpus_sha256,
                        state_id=state.id,
                        state_expression=state.expression,
                        run_seed=self._seed,
                        trajectory_states=trajectory.states,
                        trajectory_tactics=trajectory.tactics,
                        domain=required_domain,
                        generation_index=index,
                        messages=active_story_messages,
                    )
                    cached = self._candidate_cache.get_or_generate(
                        key,
                        lambda: self._generator.generate(
                            active_story_messages, seed=seed
                        ),
                    )
                    raw = cached.raw_text
                    if self._trace_sink is not None:
                        self._trace_sink({
                            "kind": "analogy_story_candidate_cache",
                            "phase": "recovery",
                            "state_id": state.id,
                            "generation_index": index,
                            "cache_hit": cached.cache_hit,
                            "cache_key_sha256": cached.key_sha256,
                            "candidate_sha256": cached.content_sha256,
                        })
                candidate_model = validate_story_model(
                    extract_story_model(raw),
                    required_domain=required_domain,
                    proof_transitions=trajectory.tactics,
                    abandoned_domains=scratchpad.abandoned_domains,
                )
                try:
                    scratchpad.validate_transition(action, candidate_model)
                except AnalogyStateError as error:
                    raise RescueOutputError(str(error)) from error
                self._trace(
                    state.id,
                    active_story_messages,
                    seed,
                    raw,
                    kind="analogy_recovery_story_call",
                )
                traced_story_call = True
                rendered = render_working_model(candidate_model)
                score_arguments = {}
                if self._calibration is not None:
                    score_arguments["calibration"] = self._calibration
                fit = score_mental_model_revision(
                    candidate_model,
                    proof_belief,
                    self._generator,
                    **score_arguments,
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
                    state.id,
                    index,
                    seed,
                    candidate_model,
                    fit.distance,
                    fit.coupling,
                    discriminative=getattr(fit, "discriminative", True),
                )
                ranked.append(
                    _ScoredRevision(
                        fit.distance,
                        index,
                        action,
                        candidate_model,
                        generation,
                        rendered,
                        active_story_messages,
                    )
                )
            except (RescueOutputError, MentalModelValidationError, AnalogyStateError) as error:
                if not traced_story_call:
                    self._trace(
                        state.id,
                        active_story_messages,
                        seed,
                        raw,
                        error=error,
                        kind="analogy_recovery_story_call",
                    )
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
                active_story_messages = base_story_messages + (
                    Message(
                        "user",
                        prompt_catalog().text(
                            "analogy_invalid_repair",
                            diagnostic=error,
                            current_domain=(
                                scratchpad.current.domain
                                if scratchpad.current is not None
                                else (scratchpad.abandoned_domains[-1] if scratchpad.abandoned_domains else "(none)")
                            ),
                            required_domain=required_domain,
                            previous_response=raw or "(no response)",
                            contract="Return exactly one valid story JSON object and no other text.",
                        ),
                    ),
                )
            except UnscorableMentalModelError as error:
                generation = AnalogyGenerationAttempt(
                    seed,
                    raw,
                    GenerationStatus.INVALID,
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
                        RescueTermination.INVALID_OUTPUT,
                        generations=(generation,),
                    )
                )
                active_story_messages = base_story_messages + (
                    Message(
                        "user",
                        prompt_catalog().text(
                            "analogy_invalid_repair",
                            diagnostic=error,
                            current_domain=required_domain,
                            required_domain=required_domain,
                            previous_response=raw or "(no response)",
                            contract=(
                                "Return exactly one valid story JSON object. "
                                "Use concrete, meaningfully distinct before and after situations."
                            ),
                        ),
                    ),
                )
            except StructuralScoringError:
                raise
            except Exception as error:
                if not traced_story_call:
                    self._trace(
                        state.id,
                        active_story_messages,
                        seed,
                        raw,
                        error=error,
                        kind="analogy_recovery_story_call",
                    )
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

        ordered = order_analogy_revisions(
            ranked,
            selection=self._selection,
            seed=_fallback_seed(self._seed + 20_000, state, 0),
        )
        for rank, candidate in enumerate(ordered):
            if cancellation.is_cancelled():
                _append_unsubmitted_rollouts(rollouts, state.id, ordered[rank:])
                return AnalogyRescueOutcome(
                    rollouts=tuple(rollouts), cancelled=True
                )
            tactic_messages = prompt_catalog().messages(
                "analogy_recovery_tactic",
                current_goals=render_lean_goal_for_model(
                    focused_lean_goal(state.expression)
                ),
                tried_tactics=_bounded_tactic_ledger(attempted_order),
                semantic_guidance=semantic_guidance,
                dependency_table=dependency_table,
                tactic_card=lean_tactic_card(state.expression),
                story_model=render_story_model(candidate.model),
            )
            tactic_seed = _fallback_seed(
                self._seed + 10_000, state, candidate.generation_index
            )
            raw_tactic = ""
            try:
                raw_tactic = self._generator.generate(
                    tactic_messages, seed=tactic_seed
                )
                proposal = _parse_tactic_only(
                    raw_tactic,
                    state.expression,
                    attempted_tactics=attempted_tactics,
                )
                proposal = replace(
                    proposal,
                    candidate=replace(proposal.candidate, model_rank=rank + 1),
                )
                self._trace(
                    state.id,
                    tactic_messages,
                    tactic_seed,
                    raw_tactic,
                    kind="analogy_recovery_tactic_call",
                )
            except Exception as error:
                self._trace(
                    state.id,
                    tactic_messages,
                    tactic_seed,
                    raw_tactic,
                    error=error,
                    kind="analogy_recovery_tactic_call",
                )
                tactic_generation = AnalogyGenerationAttempt(
                    tactic_seed,
                    raw_tactic,
                    GenerationStatus.INVALID,
                    rank > 0,
                    type(error).__name__,
                    str(error) or type(error).__name__,
                )
                rollouts.append(
                    RescueRollout(
                        f"{state.id}:analogy:{candidate.generation_index}",
                        state.id,
                        (),
                        (),
                        RescueTermination.INVALID_OUTPUT,
                        generations=(candidate.generation, tactic_generation),
                        analogy_trace=candidate.rendered_model,
                    )
                )
                continue
            if proposal.candidate.source in attempted_tactics:
                diagnostic = (
                    f"tactic {proposal.candidate.source!r} was already attempted "
                    "at this proof state"
                )
                tactic_generation = AnalogyGenerationAttempt(
                    tactic_seed,
                    raw_tactic,
                    GenerationStatus.DUPLICATE,
                    rank > 0,
                    "duplicate_tactic",
                    diagnostic,
                )
                rollouts.append(
                    RescueRollout(
                        f"{state.id}:analogy:{candidate.generation_index}",
                        state.id,
                        (),
                        (),
                        RescueTermination.DUPLICATE,
                        generations=(candidate.generation, tactic_generation),
                        analogy_trace=candidate.rendered_model,
                    )
                )
                continue
            attempted_tactics.add(proposal.candidate.source)
            attempted_order.append(proposal.candidate.source)
            tactic_generation = AnalogyGenerationAttempt(
                tactic_seed,
                raw_tactic,
                GenerationStatus.VALID,
                rank > 0,
            )
            if _normalized_prediction_state(proposal.predicted_state) == _normalized_prediction_state(state.expression):
                tactic_generation = replace(
                    tactic_generation,
                    prediction_label=PredictionLabel.PREDICTED_NO_CHANGE,
                )
                rollouts.append(
                    RescueRollout(
                        f"{state.id}:analogy:{candidate.generation_index}",
                        state.id,
                        (proposal,),
                        (),
                        RescueTermination.PREDICTED_NO_CHANGE,
                        generations=(candidate.generation, tactic_generation),
                        analogy_trace=candidate.rendered_model,
                    )
                )
                continue
            self._score_selected_tactic(
                state.id,
                tactic_messages,
                proposal,
                generation_index=candidate.generation_index,
                structural_rank=rank + 1,
            )
            if cancellation.is_cancelled():
                _append_unsubmitted_rollouts(
                    rollouts,
                    state.id,
                    [candidate, *ordered[rank + 1 :]],
                )
                return AnalogyRescueOutcome(
                    rollouts=tuple(rollouts), cancelled=True
                )
            evaluation = evaluator(proposal.candidate)
            tactic_generation = replace(
                tactic_generation,
                prediction_label=(
                    PredictionLabel.PREDICTION_MATCHED
                    if _prediction_matches(proposal.predicted_state, evaluation)
                    else PredictionLabel.PREDICTION_MISMATCHED
                ),
            )
            attempt = RescueAttempt(
                proposal, evaluation.result, evaluation.discarded
            )
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
                    f"{state.id}:analogy:{candidate.generation_index}",
                    state.id,
                    (proposal,),
                    (attempt,),
                    termination,
                    generations=(candidate.generation, tactic_generation),
                    analogy_trace=candidate.rendered_model,
                )
            )
            if evaluation.completed_proof is not None or evaluation.successor is not None:
                record_model_history(scratchpad, _result_feedback(evaluation.result))
                scratchpad.apply(candidate.action, candidate.model)
            if evaluation.completed_proof is not None:
                _append_unsubmitted_rollouts(rollouts, state.id, ordered[rank + 1 :])
                return AnalogyRescueOutcome(
                    completed_proof=evaluation.completed_proof,
                    rollouts=tuple(rollouts),
                )
            if evaluation.successor is not None:
                _append_unsubmitted_rollouts(rollouts, state.id, ordered[rank + 1 :])
                return AnalogyRescueOutcome(
                    successor=evaluation.successor, rollouts=tuple(rollouts)
                )
            if getattr(evaluation.result, "infrastructure_failure", None) is not None:
                _append_unsubmitted_rollouts(rollouts, state.id, ordered[rank + 1 :])
                return AnalogyRescueOutcome(rollouts=tuple(rollouts))
        return AnalogyRescueOutcome(rollouts=tuple(rollouts))

    def _trace(self, state_id: str, messages: tuple[Message, ...], seed: int, response: str, *, error: Exception | None = None, kind: str = "analogy_model_call") -> None:
        if self._trace_sink is not None:
            self._trace_sink({
                "kind": kind,
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
        *,
        discriminative: bool,
    ) -> None:
        if self._trace_sink is not None:
            self._trace_sink(
                {
                    "kind": "mental_model_structural_score",
                    "alignment_kind": "fixed_order",
                    "state_id": state_id,
                    "generation_index": generation_index,
                    "seed": seed,
                    "domain": model.domain,
                    "distance": distance,
                    "discriminative": discriminative,
                    "coupling": [list(row) for row in coupling],
                }
            )

    def _score_selected_tactic(
        self,
        state_id: str,
        messages: tuple[Message, ...],
        proposal: RescueProposal,
        *,
        generation_index: int,
        structural_rank: int,
    ) -> None:
        """Score one selected tactic without changing structural ordering."""

        event: dict[str, Any] = {
            "kind": "analogy_tactic_score",
            "state_id": state_id,
            "generation_index": generation_index,
            "structural_rank": structural_rank,
            "tactic": proposal.candidate.source,
            "completion": proposal.candidate.source,
            "total_log_likelihood": None,
            "mean_log_likelihood": None,
            "token_count": None,
            "error": None,
        }
        try:
            scorer = getattr(self._generator, "score_completions", None)
            if scorer is None:
                raise TypeError("model provider has no completion likelihood scorer")
            scores = tuple(
                scorer(
                    messages,
                    (proposal.candidate.source,),
                )
            )
            if len(scores) != 1 or not isinstance(scores[0], CompletionScore):
                raise ValueError("completion scorer returned an invalid result")
            score = scores[0]
            if (
                not math.isfinite(score.total_log_likelihood)
                or not math.isfinite(score.mean_log_likelihood)
                or score.token_count <= 0
            ):
                raise ValueError("completion scorer returned a non-finite score")
            event.update(
                {
                    "total_log_likelihood": score.total_log_likelihood,
                    "mean_log_likelihood": score.mean_log_likelihood,
                    "token_count": score.token_count,
                }
            )
        except Exception as error:
            event["error"] = f"{type(error).__name__}: {error}"
        if self._trace_sink is not None:
            self._trace_sink(event)


def _append_unsubmitted_rollouts(
    rollouts: list[RescueRollout],
    state_id: str,
    candidates: list[_ScoredRevision],
) -> None:
    for candidate in candidates:
        rollouts.append(
            RescueRollout(
                f"{state_id}:analogy:{candidate.generation_index}",
                state_id,
                (),
                (),
                RescueTermination.SCORED,
                generations=(candidate.generation,),
                analogy_trace=candidate.rendered_model,
            )
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
    "order_analogy_revisions",
    "submit_analogy_rescue_to_lean",
]
