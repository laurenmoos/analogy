"""Records and small policies used by the shared proof-search episode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .diversity import DiversityObservation
from .rescue import RescueRollout
from .semantic import ForcedMoveEvent, SemanticPrune, SemanticRecoveryEvent
from ..utils.records import (
    CancellationToken,
    DiscardReason,
    ExpansionOutcome,
    SearchStatus,
    StuckReason,
    StucknessDecision,
    TacticCandidate,
    TacticCanonicalizer,
    TacticResult,
    TacticStatus,
    SymbolicState,
    _require_text,
    _typed_tuple,
)


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
        object.__setattr__(self, "ordinary_expansions", _typed_tuple(self.ordinary_expansions, ExpansionOutcome, "ordinary_expansions"))
        object.__setattr__(self, "stuckness_decisions", _typed_tuple(self.stuckness_decisions, StucknessDecision, "stuckness_decisions"))
        object.__setattr__(self, "rescue_rollouts", _typed_tuple(self.rescue_rollouts, RescueRollout, "rescue_rollouts"))
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


class TacticRanker(Protocol):
    def rank(
        self,
        state: SymbolicState,
        goal: SymbolicState,
        *,
        excluded_tactics: set[str] | frozenset[str] = frozenset(),
    ) -> Sequence[TacticCandidate]: ...


class LeanVerifier(Protocol):
    def run_tactic(self, state: SymbolicState, candidate: TacticCandidate) -> TacticResult: ...


class StucknessPolicy(Protocol):
    def evaluate(self, outcome: ExpansionOutcome) -> StucknessDecision: ...


class StateFingerprinter(Protocol):
    def fingerprint(self, state: SymbolicState) -> str: ...


class ProgressPolicy(Protocol):
    def accepts(self, before: SymbolicState, after: SymbolicState, goal: SymbolicState) -> bool: ...


class StateDiversity(Protocol):
    def observe(self, state: SymbolicState) -> DiversityObservation: ...


class StuckStateSink(Protocol):
    def capture(
        self,
        state: SymbolicState,
        goal: SymbolicState,
        outcome: ExpansionOutcome,
        decision: StucknessDecision,
    ) -> None: ...


class ObservableStucknessPolicy:
    """Declare stuckness from a completed ordinary top-one attempt."""

    def evaluate(self, outcome: ExpansionOutcome) -> StucknessDecision:
        if outcome.completed_proof is not None or outcome.successors:
            return StucknessDecision(outcome.node.state.id, False)
        reasons: list[StuckReason] = []
        if outcome.attempts and all(item.status is TacticStatus.ERROR for item in outcome.attempts):
            reasons.append(StuckReason.ALL_TACTICS_ERRORED)
        discarded_reasons = {item.reason for item in outcome.discarded}
        accepted = [item for item in outcome.attempts if item.status is TacticStatus.ACCEPTED]
        if accepted and discarded_reasons == {DiscardReason.REPEATED_STATE}:
            reasons.append(StuckReason.ONLY_REPEATED_STATES)
        if DiscardReason.NO_PROGRESS in discarded_reasons:
            reasons.append(StuckReason.NO_PROGRESS)
        reasons.append(StuckReason.CANDIDATES_EXHAUSTED)
        return StucknessDecision(outcome.node.state.id, True, tuple(reasons))


class CanonicalStateFingerprinter:
    def fingerprint(self, state: SymbolicState) -> str:
        from .replay import proof_state_fingerprint

        return proof_state_fingerprint(state.expression)


class WhitespaceTacticCanonicalizer:
    def canonicalize(self, source: str) -> str:
        return " ".join(source.split())


class AcceptNovelState:
    def accepts(self, before: SymbolicState, after: SymbolicState, goal: SymbolicState) -> bool:
        return True


class NeverCancelled:
    def is_cancelled(self) -> bool:
        return False
