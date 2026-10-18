"""Protocols and default policies used by the proof-search engine."""

from __future__ import annotations

from typing import Protocol, Sequence

from ..proof.diversity import DiversityObservation
from ...utils.records import (
    DiscardReason,
    ExpansionOutcome,
    StuckReason,
    StucknessDecision,
    TacticCandidate,
    TacticStatus,
    SymbolicState,
)


class TacticRanker(Protocol):
    def rank(
        self,
        state: SymbolicState,
        goal: SymbolicState,
        *,
        excluded_tactics: set[str] | frozenset[str] = frozenset(),
    ) -> Sequence[TacticCandidate]: ...


class StucknessPolicy(Protocol):
    def evaluate(self, outcome: ExpansionOutcome) -> StucknessDecision: ...


class StateFingerprinter(Protocol):
    def fingerprint(self, state: SymbolicState) -> str: ...


class ProgressPolicy(Protocol):
    def accepts(self, before: SymbolicState, after: SymbolicState, goal: SymbolicState) -> bool: ...


class StateDiversity(Protocol):
    def observe(self, state: SymbolicState) -> DiversityObservation: ...


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
        from ..proof.state import proof_state_fingerprint

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
