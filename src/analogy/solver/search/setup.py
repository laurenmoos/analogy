"""Construction of one configured proof-search session."""

from __future__ import annotations

from ...config import AppConfig
from ...model.model_provider import TextGenerator
from ...utils.records import SymbolicState
from ..analogy.rescue import AnalogyFallback
from ..model_policy import TacticRanker
from ..proof.adapter import LeanGymAdapter
from ..scoring import FrozenEmbeddingDiversity
from ..proof.semantic import ExhaustiveSemanticChecker
from .policies import (
    AcceptNovelState,
    CanonicalStateFingerprinter,
    NeverCancelled,
    ObservableStucknessPolicy,
    WhitespaceTacticCanonicalizer,
)
from .session import EpisodeContext, SearchSession, start_search


def initialize_search(
    config: AppConfig,
    generator: TextGenerator,
    proof: LeanGymAdapter,
    model_policy: TacticRanker,
    *,
    analogy_fallback: AnalogyFallback | None = None,
    initial_state: SymbolicState | None = None,
    goal_state: SymbolicState | None = None,
) -> SearchSession:
    """Create a search session without running any search steps."""

    context = EpisodeContext(
        tactic_ranker=model_policy,
        stuckness_policy=ObservableStucknessPolicy(),
        state_fingerprinter=CanonicalStateFingerprinter(),
        tactic_canonicalizer=WhitespaceTacticCanonicalizer(),
        progress_policy=AcceptNovelState(),
        cancellation=NeverCancelled(),
        analogy_fallback=analogy_fallback,
        ordinary_width=config.search.top_k,
        max_expansions=config.search.max_steps,
        state_diversity=FrozenEmbeddingDiversity(
            generator, layer=config.model.hidden_layer, neighbors=3
        ),
        semantic_checker=ExhaustiveSemanticChecker(),
    )
    return start_search(
        context,
        initial_state if initial_state is not None else proof.initial_state(),
        goal_state if goal_state is not None else proof.goal_state(),
    )


__all__ = ["initialize_search"]
