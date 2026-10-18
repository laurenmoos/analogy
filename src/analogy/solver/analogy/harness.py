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
    prompt_catalog,
    render_lean_goal_for_model,
)
from ...utils.records import SymbolicState
from ..model_policy import TacticRanker, initialize_model_policy
from ..proof.semantic import SemanticResult
from .protocol import extract_mental_model_block
from .rescue import AnalogyFallback, initialize_analogy_fallback
from .state import AnalogyScratchpad

if TYPE_CHECKING:
    from ..proof.adapter import LeanGymAdapter
    from ..search import SearchSession


class AnalogyHarnessInitializationError(RuntimeError):
    """Raised when initialization cannot produce any valid analogy track."""


class AnalogyScoreError(ValueError):
    """Raised when the opaque scorer violates its finite-real contract."""


@dataclass(frozen=True, slots=True)
class AnalogyInitializationTrace:
    seed: int
    messages: tuple[Any, ...]
    raw_text: str | None
    status: str
    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class AnalogyTrack:
    id: str
    scratchpad: AnalogyScratchpad
    model_policy: TacticRanker
    fallback: AnalogyFallback
    initialization: AnalogyInitializationTrace

    def __post_init__(self) -> None:
        if self.model_policy.analogy_scratchpad is not self.scratchpad:
            raise ValueError("analogy model policy must own the track scratchpad")
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


def initialize_analogy_harness(
    config: AppConfig,
    generator: TextGenerator,
    proof: LeanGymAdapter,
    model_events: list[dict[str, Any]],
    initial_state: SymbolicState,
    goal_state: SymbolicState,
    *,
    scorer: AnalogyScorer | None = None,
) -> AnalogyHarness:
    """Generate independent analogy tracks before ordinary search begins."""

    tracks: list[AnalogyTrack] = []
    accepted_domains: list[str] = []
    for index in range(config.analogy.max_model_calls):
        seed = config.run.seed + index
        messages = prompt_catalog().messages(
            "analogy_initialize",
            overall_target=render_lean_goal_for_model(goal_state.expression),
            current_goals=render_lean_goal_for_model(
                focused_lean_goal(initial_state.expression)
            ),
            excluded_domains=(
                "\n".join(f"- {domain}" for domain in accepted_domains)
                or "- (none)"
            ),
        )
        raw_text: str | None = None
        try:
            raw_text = generator.generate(messages, seed=seed)
            state, remainder = extract_mental_model_block(raw_text)
            if remainder.strip():
                raise ValueError("initialization output must contain only the analogy block")
            if state.domain in accepted_domains:
                raise ValueError("initialization reused an accepted analogy domain")
        except Exception as error:
            trace = AnalogyInitializationTrace(
                seed,
                messages,
                raw_text,
                "invalid",
                f"{type(error).__name__}: {error}",
            )
            model_events.append(_trace_event(index, trace))
            continue

        scratchpad = AnalogyScratchpad(state)
        model_policy = initialize_model_policy(
            config,
            generator,
            proof,
            model_events,
            analogy_scratchpad=scratchpad,
        )
        fallback = initialize_analogy_fallback(
            config, generator, model_events, scratchpad
        )
        trace = AnalogyInitializationTrace(seed, messages, raw_text, "valid")
        track = AnalogyTrack(
            f"analogy-{index + 1}",
            scratchpad,
            model_policy,
            fallback,
            trace,
        )
        tracks.append(track)
        accepted_domains.append(state.domain)
        model_events.append(_trace_event(index, trace))

    if not tracks:
        raise AnalogyHarnessInitializationError("no valid analogies were generated")
    return AnalogyHarness(tuple(tracks), scorer or EqualAnalogyScorer())


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
    """Prepare every isolated track for the search's pending recovery."""

    recovery = search.recovery
    if recovery is None:
        raise RuntimeError("search has no pending analogy rescue")
    if recovery.result is not SemanticResult.COUNTERMODEL:
        return analogies
    if recovery.event_id in analogies.prepared_recoveries:
        return analogies
    for track in analogies.tracks:
        track.scratchpad.abandon_current()
    return replace(
        analogies,
        prepared_recoveries=analogies.prepared_recoveries | {recovery.event_id},
    )


def activate_analogy(search: SearchSession, analogy: AnalogyTrack) -> None:
    """Use one track without changing the shared search state."""

    if (
        analogy.model_policy.analogy_scratchpad is not analogy.scratchpad
        or analogy.fallback.scratchpad is not analogy.scratchpad
    ):
        raise ValueError("analogy policy and fallback must share the same scratchpad")
    search.context = replace(
        search.context,
        tactic_ranker=analogy.model_policy,
        analogy_fallback=analogy.fallback,
    )


def _trace_event(index: int, trace: AnalogyInitializationTrace) -> dict[str, Any]:
    return {
        "kind": "analogy_initialization_call",
        "track_id": f"analogy-{index + 1}",
        "messages": [
            {"role": message.role, "content": message.content}
            for message in trace.messages
        ],
        "seed": trace.seed,
        "raw_text": trace.raw_text,
        "status": trace.status,
        "diagnostic": trace.diagnostic,
    }


__all__ = [
    "AnalogyHarness",
    "AnalogyHarnessInitializationError",
    "AnalogyScoreError",
    "AnalogyScorer",
    "AnalogyTrack",
    "EqualAnalogyScorer",
    "ScoredAnalogy",
    "activate_analogy",
    "initialize_analogy_harness",
    "score_analogies",
    "select_analogy",
    "update_analogies",
]
