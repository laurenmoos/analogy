"""Immutable terminal evidence produced by one proof search."""

from __future__ import annotations

from dataclasses import dataclass

from ...utils.records import (
    ExpansionOutcome,
    SearchStatus,
    StucknessDecision,
    _require_text,
    _typed_tuple,
)
from ..analogy.rescue import RescueRollout
from ..scoring import DiversityObservation
from ..proof.semantic import ForcedMoveEvent, SemanticPrune, SemanticRecoveryEvent


@dataclass(frozen=True, slots=True)
class SearchResult:
    status: SearchStatus
    proof: str | None
    ordinary_expansions: tuple[ExpansionOutcome, ...] = ()
    stuckness_decisions: tuple[StucknessDecision, ...] = ()
    rescue_rollouts: tuple[RescueRollout, ...] = ()
    diversity_observations: tuple[DiversityObservation, ...] = ()
    semantic_recoveries: tuple[SemanticRecoveryEvent, ...] = ()
    semantic_prunes: tuple[SemanticPrune, ...] = ()
    forced_moves: tuple[ForcedMoveEvent, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, SearchStatus):
            raise ValueError("status must be a SearchStatus")
        if self.status is SearchStatus.COMPLETE:
            _require_text(self.proof, "proof")
        elif self.proof is not None:
            raise ValueError("non-complete search results must not include proof")
        object.__setattr__(
            self,
            "ordinary_expansions",
            _typed_tuple(
                self.ordinary_expansions,
                ExpansionOutcome,
                "ordinary_expansions",
            ),
        )
        object.__setattr__(
            self,
            "stuckness_decisions",
            _typed_tuple(
                self.stuckness_decisions,
                StucknessDecision,
                "stuckness_decisions",
            ),
        )
        object.__setattr__(
            self,
            "rescue_rollouts",
            _typed_tuple(self.rescue_rollouts, RescueRollout, "rescue_rollouts"),
        )
        object.__setattr__(
            self,
            "diversity_observations",
            _typed_tuple(
                self.diversity_observations,
                DiversityObservation,
                "diversity_observations",
            ),
        )
        object.__setattr__(
            self,
            "semantic_recoveries",
            _typed_tuple(
                self.semantic_recoveries,
                SemanticRecoveryEvent,
                "semantic_recoveries",
            ),
        )
        object.__setattr__(
            self,
            "semantic_prunes",
            _typed_tuple(self.semantic_prunes, SemanticPrune, "semantic_prunes"),
        )
        object.__setattr__(
            self,
            "forced_moves",
            _typed_tuple(self.forced_moves, ForcedMoveEvent, "forced_moves"),
        )


__all__ = ["SearchResult"]
