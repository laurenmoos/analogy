"""Isolated analogy candidates and the opaque boundary used to score them."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from numbers import Real
from typing import TYPE_CHECKING, Any, Protocol

from ...config import AppConfig
from ...model.model_provider import TextGenerator
from ...model.prompts import (
    focused_lean_goal,
    lean_tactic_card,
    prompt_catalog,
    render_lean_goal_for_model,
)
from ...utils.records import Message, SymbolicState
from ..proof.semantic import SemanticResult
from ..search.tactic_generator import TacticGenerator, initialize_tactic_generator
from .protocol import extract_story_model, render_mental_model
from .domains import ANALOGY_DOMAINS, next_unused_domain
from .candidate_cache import (
    StoryCandidateCache,
    build_candidate_cache_key,
    file_sha256,
    story_candidate_cache_path,
)
from .rescue import (
    AnalogyFallback,
    RescueProposal,
    _parse_tactic_only,
    initialize_analogy_fallback,
)
from .state import AnalogyScratchpad, MentalModel
from .validation import MentalModelValidationError, validate_story_model
from .structural_scoring import (
    ProofBelief,
    UnscorableMentalModelError,
    abstract_proof_transition,
    build_proof_belief,
    load_embedding_calibration,
    proof_trajectory,
    score_mental_model_revision,
)

if TYPE_CHECKING:
    from ..proof.adapter import LeanGymAdapter
    from ..search import SearchSession


class AnalogyHarnessInitializationError(RuntimeError):
    """Raised when initialization cannot produce any valid analogy track."""


class AnalogyStoryInitializationError(AnalogyHarnessInitializationError):
    """Raised only when the identifier-blind story stage is exhausted."""


class AnalogyScoreError(ValueError):
    """Raised when the opaque scorer violates its finite-real contract."""


UNSCORABLE_ANALOGY_SCORE = -float.fromhex("0x1.fffffffffffffp+1023")


@dataclass(frozen=True, slots=True)
class AnalogyInitializationTrace:
    seed: int
    messages: tuple[Any, ...]
    raw_text: str | None
    status: str
    diagnostic: str | None = None
    diagnostic_class: str | None = None


@dataclass(frozen=True, slots=True)
class AnalogyTrack:
    id: str
    scratchpad: AnalogyScratchpad
    tactic_generator: TacticGenerator
    fallback: AnalogyFallback
    initialization: AnalogyInitializationTrace
    initial_proposal: RescueProposal

    def __post_init__(self) -> None:
        if self.tactic_generator.analogy_scratchpad is not self.scratchpad:
            raise ValueError("analogy tactic generator must own the track scratchpad")
        if self.fallback.scratchpad is not self.scratchpad:
            raise ValueError("analogy fallback must own the track scratchpad")


class AnalogyScorer(Protocol):
    def score(
        self, track: AnalogyTrack, search: SearchSession | None
    ) -> float: ...


class EqualAnalogyScorer:
    """Neutral placeholder until analogy scoring semantics are specified."""

    def score(
        self, track: AnalogyTrack, search: SearchSession | None
    ) -> float:
        return 0.0


@dataclass(frozen=True, slots=True)
class StructuralAnalogyScorer:
    """Rank active tracks by the frozen ordered-transition fit score."""

    embedder: Any

    def score(
        self, track: AnalogyTrack, search: SearchSession | None
    ) -> float:
        if search is None:
            return 0.0
        model = track.scratchpad.current
        if not isinstance(model, MentalModel):
            raise AnalogyScoreError(
                "structural analogy scoring requires a mental model"
            )
        try:
            fit = score_mental_model_revision(
                model,
                build_proof_belief(search),
                self.embedder,
            )
        except UnscorableMentalModelError:
            return UNSCORABLE_ANALOGY_SCORE
        return -fit.distance


@dataclass(frozen=True, slots=True)
class AnalogyHarness:
    tracks: tuple[AnalogyTrack, ...]
    scorer: AnalogyScorer
    prepared_recoveries: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.tracks:
            raise AnalogyHarnessInitializationError("no valid analogies were generated")


@dataclass(frozen=True, slots=True)
class ScoredAnalogy:
    track: AnalogyTrack
    score: float


def initialize_story_model(
    config: AppConfig,
    generator: TextGenerator,
    model_events: list[dict[str, Any]],
    state_id: str,
    proof_belief: ProofBelief,
    *,
    required_domain: str = ANALOGY_DOMAINS[0],
    max_story_attempts: int = 2,
    skip_expensive_score: bool = False,
) -> tuple[MentalModel, AnalogyInitializationTrace]:
    """Generate, validate, reconstruct, and score an identifier-blind story."""

    return _initialize_story_models(
        config,
        generator,
        model_events,
        state_id,
        proof_belief,
        required_domain=required_domain,
        max_story_attempts=max_story_attempts,
        skip_expensive_score=skip_expensive_score,
        collect_all=False,
    )[0]


def initialize_story_models(
    config: AppConfig,
    generator: TextGenerator,
    model_events: list[dict[str, Any]],
    state_id: str,
    proof_belief: ProofBelief,
    *,
    required_domain: str = ANALOGY_DOMAINS[0],
    max_story_attempts: int = 2,
    skip_expensive_score: bool = False,
) -> tuple[tuple[MentalModel, AnalogyInitializationTrace], ...]:
    """Generate every valid initial story within the fixed call budget."""

    return _initialize_story_models(
        config,
        generator,
        model_events,
        state_id,
        proof_belief,
        required_domain=required_domain,
        max_story_attempts=max_story_attempts,
        skip_expensive_score=skip_expensive_score,
        collect_all=True,
    )


def _initialize_story_models(
    config: AppConfig,
    generator: TextGenerator,
    model_events: list[dict[str, Any]],
    state_id: str,
    proof_belief: ProofBelief,
    *,
    required_domain: str,
    max_story_attempts: int,
    skip_expensive_score: bool,
    collect_all: bool,
) -> tuple[tuple[MentalModel, AnalogyInitializationTrace], ...]:
    """Shared implementation for first-valid and full-cohort initialization."""

    if max_story_attempts not in {1, 2}:
        raise ValueError("max_story_attempts must be one or two")
    trajectory = proof_trajectory(proof_belief)
    if not trajectory.tactics:
        model_events.append({
            "kind": "analogy_initialization_skipped",
            "state_id": state_id,
            "reason": "zero_transition_trajectory",
        })
        raise AnalogyStoryInitializationError(
            "no accepted proof transitions are available for structural scoring"
        )
    abstract_trajectory = "\n".join(
        f"{index}. {abstract_proof_transition(tactic)}"
        for index, tactic in enumerate(trajectory.tactics, 1)
    )
    base_messages = prompt_catalog().messages(
        "analogy_initialize",
        domain_card=prompt_catalog().text("analogy_domain_card"),
        excluded_domains="- (none)",
        required_domain=required_domain,
        abstract_trajectory=abstract_trajectory,
    )
    active_messages = base_messages
    previous_error_class: str | None = None
    candidate_cache = StoryCandidateCache(story_candidate_cache_path(config))
    model_revision = (
        getattr(generator, "model_revision", None)
        or config.model.revision
        or config.analogy.model_name
        or config.model.name
    )
    prompt_sha256 = file_sha256(config.prompts.path)
    corpus_sha256 = file_sha256(config.benchmark.corpus)
    valid: list[tuple[MentalModel, AnalogyInitializationTrace]] = []
    for index in range(max_story_attempts):
        seed = config.run.seed + index
        raw_text: str | None = None
        try:
            key = build_candidate_cache_key(
                model_revision=model_revision,
                prompt_sha256=prompt_sha256,
                corpus_sha256=corpus_sha256,
                state_id=state_id,
                state_expression=trajectory.states[-1],
                run_seed=config.run.seed,
                trajectory_states=trajectory.states,
                trajectory_tactics=trajectory.tactics,
                domain=required_domain,
                generation_index=index,
                messages=active_messages,
            )
            cached = candidate_cache.get_or_generate(
                key,
                lambda: generator.generate(active_messages, seed=seed),
            )
            raw_text = cached.raw_text
            model_events.append({
                "kind": "analogy_story_candidate_cache",
                "phase": "initialization",
                "state_id": state_id,
                "generation_index": index,
                "cache_hit": cached.cache_hit,
                "cache_key_sha256": cached.key_sha256,
                "candidate_sha256": cached.content_sha256,
            })
            story = extract_story_model(raw_text)
            state = validate_story_model(
                story,
                required_domain=required_domain,
                proof_transitions=trajectory.tactics,
            )
        except Exception as error:
            error_class = _initialization_error_class(error)
            trace = AnalogyInitializationTrace(
                seed,
                active_messages,
                raw_text,
                "invalid",
                f"{type(error).__name__}: {error}",
                error_class,
            )
            model_events.append(_trace_event(index, trace))
            previous_error_class = error_class
            if index == max_story_attempts - 1:
                break
            active_messages = base_messages + (
                Message(
                    "user",
                    prompt_catalog().text(
                        "analogy_invalid_repair",
                        diagnostic=error,
                        current_domain="(none)",
                        required_domain=required_domain,
                        previous_response=raw_text or "(no response)",
                        contract="Return exactly one valid story JSON object and no other text.",
                    ),
                ),
            )
            continue
        trace = AnalogyInitializationTrace(seed, active_messages, raw_text, "valid")
        model_events.append(_trace_event(index, trace))
        _record_initial_structural_score(
            config,
            generator,
            model_events,
            state_id,
            state,
            proof_belief,
            skip_expensive_score=skip_expensive_score,
            generation_index=index,
        )
        valid.append((state, trace))
        if not collect_all:
            return tuple(valid)
        active_messages = base_messages
        previous_error_class = None
    if valid:
        return tuple(valid)
    raise AnalogyStoryInitializationError(
        "no valid analogies were generated"
        + (f"; last diagnostic class: {previous_error_class}" if previous_error_class else "")
    )


def initialize_analogy_harness(
    config: AppConfig,
    generator: TextGenerator,
    proof: LeanGymAdapter,
    model_events: list[dict[str, Any]],
    initial_state: SymbolicState,
    goal_state: SymbolicState,
    *,
    proof_belief: ProofBelief,
    scorer: AnalogyScorer | None = None,
    analogy_generator: TextGenerator | None = None,
    max_story_attempts: int = 2,
) -> AnalogyHarness:
    """Generate a cohort of validated analogies for first-stuck selection."""

    analogy_generator = analogy_generator or generator
    required_domain = ANALOGY_DOMAINS[0]
    initialized = initialize_story_models(
        config,
        analogy_generator,
        model_events,
        initial_state.id,
        proof_belief,
        required_domain=required_domain,
        max_story_attempts=max_story_attempts,
        skip_expensive_score=scorer is not None,
    )
    tracks: list[AnalogyTrack] = []
    for state, valid_trace in initialized:
        generation_index = valid_trace.seed - config.run.seed
        track_id = f"analogy-{generation_index + 1}"
        tactic_messages = prompt_catalog().messages(
            "analogy_initial_tactic",
            overall_target=render_lean_goal_for_model(goal_state.expression),
            current_goals=render_lean_goal_for_model(
                focused_lean_goal(initial_state.expression)
            ),
            mental_model=render_mental_model(state),
            tactic_card=lean_tactic_card(initial_state.expression),
        )
        tactic_seed = config.run.seed + 10_000 + generation_index
        raw_tactic = analogy_generator.generate(tactic_messages, seed=tactic_seed)
        try:
            proposal = _parse_initial_tactic(raw_tactic, initial_state.expression)
        except Exception as error:
            model_events.append({
                "kind": "analogy_initial_tactic_call",
                "track_id": track_id,
                "generation_index": generation_index,
                "state_id": initial_state.id,
                "seed": tactic_seed,
                "messages": _messages_payload(tactic_messages),
                "raw_text": raw_tactic,
                "status": "invalid",
                "diagnostic": f"{type(error).__name__}: {error}",
            })
            continue
        model_events.append({
            "kind": "analogy_initial_tactic_call",
            "track_id": track_id,
            "generation_index": generation_index,
            "state_id": initial_state.id,
            "seed": tactic_seed,
            "messages": _messages_payload(tactic_messages),
            "raw_text": raw_tactic,
            "status": "valid",
            "tactic": proposal.candidate.source,
        })
        scratchpad = AnalogyScratchpad(state)
        tactic_generator = initialize_tactic_generator(
            config, generator, proof, model_events, analogy_scratchpad=scratchpad
        )
        fallback = initialize_analogy_fallback(
            config,
            analogy_generator,
            model_events,
            scratchpad,
            initial_proposal=proposal,
            initial_state_id=initial_state.id,
        )
        tracks.append(AnalogyTrack(
            track_id,
            scratchpad,
            tactic_generator,
            fallback,
            valid_trace,
            proposal,
        ))
    if not tracks:
        raise AnalogyHarnessInitializationError(
            "no valid initial analogy tactics were generated"
        )
    return AnalogyHarness(
        tuple(tracks), scorer or StructuralAnalogyScorer(generator)
    )


def score_analogies(
    search: SearchSession | None,
    analogies: AnalogyHarness,
) -> tuple[ScoredAnalogy, ...]:
    """Score tracks through the intentionally opaque scoring interface."""

    scored: list[ScoredAnalogy] = []
    for track in analogies.tracks:
        raw_score = analogies.scorer.score(track, search)
        if isinstance(raw_score, bool) or not isinstance(raw_score, Real):
            raise AnalogyScoreError("analogy score must be a finite real number")
        score = float(raw_score)
        if not math.isfinite(score):
            raise AnalogyScoreError("analogy score must be a finite real number")
        scored.append(ScoredAnalogy(track, score))
    return tuple(scored)


def select_analogy(scored: tuple[ScoredAnalogy, ...]) -> AnalogyTrack:
    """Choose the first maximum, preserving cohort order as the tie-breaker."""

    if not scored:
        raise AnalogyScoreError("cannot select from empty analogy scores")
    return max(scored, key=lambda item: item.score).track


def update_analogies(
    search: SearchSession,
    analogies: AnalogyHarness,
) -> AnalogyHarness:
    """Assign the next unused canonical domain for a countermodel recovery."""

    recovery = search.recovery
    if recovery is None:
        raise RuntimeError("search has no pending analogy rescue")
    if recovery.result is not SemanticResult.COUNTERMODEL:
        return analogies
    if recovery.event_id in analogies.prepared_recoveries:
        return analogies
    scratchpad = analogies.tracks[0].scratchpad
    used = list(scratchpad.abandoned_domains)
    if scratchpad.current is not None:
        used.append(scratchpad.current.domain)
    scratchpad.abandon_current(next_unused_domain(used))
    return replace(
        analogies,
        prepared_recoveries=analogies.prepared_recoveries | {recovery.event_id},
    )


def activate_analogy(search: SearchSession, analogy: AnalogyTrack) -> None:
    """Install one recovery track without changing ordinary tactic ranking."""

    if (
        analogy.tactic_generator.analogy_scratchpad is not analogy.scratchpad
        or analogy.fallback.scratchpad is not analogy.scratchpad
    ):
        raise ValueError(
            "analogy tactic generator and fallback must share the same scratchpad"
        )
    search.context = replace(
        search.context,
        analogy_fallback=analogy.fallback,
    )


def _trace_event(index: int, trace: AnalogyInitializationTrace) -> dict[str, Any]:
    return {
        "kind": "analogy_initialization_call",
        "track_id": "analogy-1",
        "attempt_index": index + 1,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in trace.messages
        ],
        "seed": trace.seed,
        "raw_text": trace.raw_text,
        "status": trace.status,
        "diagnostic": trace.diagnostic,
        "diagnostic_class": trace.diagnostic_class,
    }


def _initialization_error_class(error: Exception) -> str:
    if isinstance(error, MentalModelValidationError):
        return error.code
    return "schema"


def _messages_payload(messages: tuple[Message, ...]) -> list[dict[str, str]]:
    return [{"role": item.role, "content": item.content} for item in messages]


def _parse_initial_tactic(text: str, proof_state: str) -> RescueProposal:
    return _parse_tactic_only(text, proof_state)


def _record_initial_structural_score(
    config: AppConfig,
    generator: TextGenerator,
    model_events: list[dict[str, Any]],
    state_id: str,
    model: MentalModel,
    belief: ProofBelief,
    *,
    skip_expensive_score: bool,
    generation_index: int,
) -> None:
    if skip_expensive_score:
        model_events.append({
            "kind": "mental_model_structural_score",
            "alignment_kind": "fixed_order",
            "phase": "initialization",
            "state_id": state_id,
            "generation_index": generation_index,
            "domain": model.domain,
            "distance": 0.0,
            "coupling": [],
            "method": "injected_scorer",
        })
        return
    arguments: dict[str, Any] = {}
    if config.analogy.calibration_path is not None:
        arguments["calibration"] = load_embedding_calibration(
            config.analogy.calibration_path,
            expected_model_id=config.analogy.model_name or config.model.name,
        )
    fit = score_mental_model_revision(model, belief, generator, **arguments)
    model_events.append({
        "kind": "mental_model_structural_score",
        "alignment_kind": "fixed_order",
        "phase": "initialization",
        "state_id": state_id,
        "generation_index": generation_index,
        "domain": model.domain,
        "distance": fit.distance,
        "discriminative": fit.discriminative,
        "coupling": [list(row) for row in fit.coupling],
        "method": "ordered-transition-resized-correlation-v1",
    })


__all__ = [
    "AnalogyHarness",
    "AnalogyHarnessInitializationError",
    "AnalogyStoryInitializationError",
    "AnalogyScoreError",
    "AnalogyScorer",
    "AnalogyTrack",
    "EqualAnalogyScorer",
    "ScoredAnalogy",
    "StructuralAnalogyScorer",
    "activate_analogy",
    "initialize_analogy_harness",
    "initialize_story_model",
    "initialize_story_models",
    "score_analogies",
    "select_analogy",
    "update_analogies",
]
