"""Best-first proof search with stuck-only, constrained curiosity selection."""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from typing import Iterable, Protocol, Sequence

from .daydream import CuriosityRunner, DaydreamRound
from .rescue import (
    GenericRescueRunner,
    RescueRollout,
    TacticEvaluation,
)
from .records import (
    CancellationToken,
    CandidateOrigin,
    DiscardReason,
    DiscardedSuccessor,
    ExpansionOutcome,
    SearchNode,
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
    daydream_rounds: tuple[DaydreamRound, ...] = ()
    rescue_rollouts: tuple[RescueRollout, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, SearchStatus):
            raise ValueError("status must be a SearchStatus")
        if self.status is SearchStatus.COMPLETE:
            _require_text(self.proof, "proof")
        elif self.proof is not None:
            raise ValueError("non-complete search results must not include proof")
        object.__setattr__(self, "ordinary_expansions", _typed_tuple(self.ordinary_expansions, ExpansionOutcome, "ordinary_expansions"))
        object.__setattr__(self, "stuckness_decisions", _typed_tuple(self.stuckness_decisions, StucknessDecision, "stuckness_decisions"))
        object.__setattr__(self, "daydream_rounds", _typed_tuple(self.daydream_rounds, DaydreamRound, "daydream_rounds"))
        object.__setattr__(self, "rescue_rollouts", _typed_tuple(self.rescue_rollouts, RescueRollout, "rescue_rollouts"))


class TacticRanker(Protocol):
    def rank(self, state: SymbolicState, goal: SymbolicState) -> Sequence[TacticCandidate]: ...


class LeanVerifier(Protocol):
    def run_tactic(self, state: SymbolicState, candidate: TacticCandidate) -> TacticResult: ...


class StucknessPolicy(Protocol):
    def evaluate(self, outcome: ExpansionOutcome) -> StucknessDecision: ...


class StateFingerprinter(Protocol):
    def fingerprint(self, state: SymbolicState) -> str: ...


class ProgressPolicy(Protocol):
    def accepts(self, before: SymbolicState, after: SymbolicState, goal: SymbolicState) -> bool: ...


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


class StateIdFingerprinter:
    def fingerprint(self, state: SymbolicState) -> str:
        return state.id


class WhitespaceTacticCanonicalizer:
    def canonicalize(self, source: str) -> str:
        return " ".join(source.split())


class AcceptNovelState:
    def accepts(self, before: SymbolicState, after: SymbolicState, goal: SymbolicState) -> bool:
        return True


class NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


class _PriorityFrontier:
    """Deterministic max-priority queue with insertion-order ties."""

    def __init__(self) -> None:
        self._items: list[tuple[float, int, SearchNode]] = []
        self._order = count()

    def __bool__(self) -> bool:
        return bool(self._items)

    def push(self, node: SearchNode) -> None:
        heappush(self._items, (-node.score, next(self._order), node))

    def extend(self, nodes: Iterable[SearchNode]) -> None:
        for node in nodes:
            self.push(node)

    def pop_best(self) -> SearchNode:
        return heappop(self._items)[2]


class LocalRescueSearch:
    """Best-first proof search whose curiosity path only reorders a fixed pool."""

    def __init__(
        self,
        *,
        tactic_ranker: TacticRanker,
        verifier: LeanVerifier,
        stuckness_policy: StucknessPolicy,
        curiosity_search: CuriosityRunner | None,
        state_fingerprinter: StateFingerprinter,
        tactic_canonicalizer: TacticCanonicalizer,
        progress_policy: ProgressPolicy,
        cancellation: CancellationToken,
        ordinary_width: int = 1,
        max_expansions: int | None = None,
        rollout_rescue: GenericRescueRunner | None = None,
    ) -> None:
        if isinstance(ordinary_width, bool) or not isinstance(ordinary_width, int) or ordinary_width <= 0:
            raise ValueError("ordinary_width must be a positive integer")
        if (
            max_expansions is not None
            and (
                isinstance(max_expansions, bool)
                or not isinstance(max_expansions, int)
                or max_expansions <= 0
            )
        ):
            raise ValueError("max_expansions must be a positive integer or None")
        if curiosity_search is not None and rollout_rescue is not None:
            raise ValueError("configure at most one rescue implementation")
        self._tactic_ranker = tactic_ranker
        self._verifier = verifier
        self._stuckness_policy = stuckness_policy
        self._curiosity_search = curiosity_search
        self._rollout_rescue = rollout_rescue
        self._state_fingerprinter = state_fingerprinter
        self._tactic_canonicalizer = tactic_canonicalizer
        self._progress_policy = progress_policy
        self._cancellation = cancellation
        self._ordinary_width = ordinary_width
        self._max_expansions = max_expansions

    def search(self, initial_state: SymbolicState, goal: SymbolicState) -> SearchResult:
        frontier = _PriorityFrontier()
        frontier.push(SearchNode(initial_state, 1.0))
        visited = {self._state_fingerprinter.fingerprint(initial_state)}
        expansions: list[ExpansionOutcome] = []
        decisions: list[StucknessDecision] = []
        rounds: list[DaydreamRound] = []
        rescue_rollouts: list[RescueRollout] = []

        while frontier:
            if self._cancellation.is_cancelled():
                return SearchResult(SearchStatus.CANCELLED, None, tuple(expansions), tuple(decisions), tuple(rounds), tuple(rescue_rollouts))
            if (
                self._max_expansions is not None
                and len(expansions) >= self._max_expansions
            ):
                return SearchResult(
                    SearchStatus.BUDGET_EXHAUSTED,
                    None,
                    tuple(expansions),
                    tuple(decisions),
                    tuple(rounds),
                    tuple(rescue_rollouts),
                )
            node = frontier.pop_best()
            ordinary = self._expand_normally(node, goal, visited)
            expansions.append(ordinary)
            if ordinary.completed_proof is not None:
                return SearchResult(SearchStatus.COMPLETE, ordinary.completed_proof, tuple(expansions), tuple(decisions), tuple(rounds), tuple(rescue_rollouts))

            frontier.extend(ordinary.successors)
            decision = self._stuckness_policy.evaluate(ordinary)
            decisions.append(decision)
            if not decision.is_stuck or not ordinary.candidate_pool:
                continue

            if self._rollout_rescue is not None:
                rescue = self._rollout_rescue.search(
                    node.state,
                    goal,
                    ordinary.candidate_pool,
                    ordinary.attempts,
                    lambda selected: self._evaluate_for_rescue(
                        node, goal, visited, selected
                    ),
                    self._cancellation,
                )
                rescue_rollouts.extend(rescue.rollouts)
            elif self._curiosity_search is not None:
                rescue = self._curiosity_search.search(
                    node.state,
                    goal,
                    ordinary.candidate_pool,
                    ordinary.attempts,
                    lambda selected: self._evaluate_for_curiosity(
                        node, goal, visited, selected
                    ),
                    self._cancellation,
                )
                rounds.extend(rescue.rounds)
            else:
                continue
            if rescue.cancelled:
                return SearchResult(SearchStatus.CANCELLED, None, tuple(expansions), tuple(decisions), tuple(rounds), tuple(rescue_rollouts))
            if rescue.completed_proof is not None:
                return SearchResult(SearchStatus.COMPLETE, rescue.completed_proof, tuple(expansions), tuple(decisions), tuple(rounds), tuple(rescue_rollouts))
            if rescue.successor is not None:
                frontier.push(rescue.successor)

        return SearchResult(SearchStatus.EXHAUSTED, None, tuple(expansions), tuple(decisions), tuple(rounds), tuple(rescue_rollouts))

    def _candidate_pool(self, state: SymbolicState, goal: SymbolicState) -> tuple[TacticCandidate, ...]:
        seen: set[str] = set()
        pool: list[TacticCandidate] = []
        for candidate in self._tactic_ranker.rank(state, goal):
            if candidate.origin is not CandidateOrigin.ORDINARY:
                raise ValueError("conventional tactic policy must return ordinary candidates")
            key = self._tactic_canonicalizer.canonicalize(candidate.source)
            if key in seen:
                continue
            seen.add(key)
            pool.append(candidate)
        return tuple(pool)

    def _expand_normally(self, node: SearchNode, goal: SymbolicState, visited: set[str]) -> ExpansionOutcome:
        pool = self._candidate_pool(node.state, goal)
        if not pool:
            return ExpansionOutcome(node, candidate_pool=pool)
        attempts: list[TacticResult] = []
        successors: list[SearchNode] = []
        discarded_results: list[DiscardedSuccessor] = []
        completed: str | None = None
        for candidate in pool[: self._ordinary_width]:
            result = self._verify_candidate(node.state, candidate)
            attempts.append(result)
            completed, successor, discarded = self._classify_result(
                node, goal, result, visited
            )
            if successor is not None:
                successors.append(successor)
            if discarded is not None:
                discarded_results.append(discarded)
            if completed is not None:
                break
        return ExpansionOutcome(
            node,
            tuple(attempts),
            tuple(successors),
            tuple(discarded_results),
            completed,
            pool,
        )

    def _classify_result(self, node: SearchNode, goal: SymbolicState, result: TacticResult, visited: set[str]) -> tuple[str | None, SearchNode | None, DiscardedSuccessor | None]:
        if result.status is TacticStatus.ERROR:
            return None, None, None
        if result.status is TacticStatus.COMPLETE:
            return result.proof, None, None
        assert result.state is not None
        fingerprint = self._state_fingerprinter.fingerprint(result.state)
        if fingerprint in visited:
            return None, None, DiscardedSuccessor(result, DiscardReason.REPEATED_STATE)
        visited.add(fingerprint)
        if not self._progress_policy.accepts(node.state, result.state, goal):
            return None, None, DiscardedSuccessor(result, DiscardReason.NO_PROGRESS)
        return None, SearchNode(result.state, node.score * result.candidate.score, node.state.id, result.candidate.source), None

    def _evaluate_for_rescue(self, node: SearchNode, goal: SymbolicState, visited: set[str], candidate: TacticCandidate) -> TacticEvaluation:
        result = self._verify_candidate(node.state, candidate)
        completed, successor, discarded = self._classify_result(node, goal, result, visited)
        return TacticEvaluation(result, completed, successor, discarded)

    def _evaluate_for_curiosity(self, node: SearchNode, goal: SymbolicState, visited: set[str], candidate: TacticCandidate) -> TacticEvaluation:
        return self._evaluate_for_rescue(node, goal, visited, candidate)

    def _verify_candidate(self, state: SymbolicState, candidate: TacticCandidate) -> TacticResult:
        result = self._verifier.run_tactic(state, candidate)
        if result.candidate != candidate:
            raise ValueError("verifier result must reference the requested candidate")
        return result
