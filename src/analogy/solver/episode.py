"""One shared environment-interaction loop for training and inference."""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from typing import Iterable

from ..records import (
    CancellationToken,
    CandidateOrigin,
    DiscardReason,
    DiscardedSuccessor,
    ExpansionOutcome,
    SearchNode,
    SearchStatus,
    StucknessDecision,
    SymbolicState,
    TacticCandidate,
    TacticCanonicalizer,
    TacticResult,
    TacticStatus,
)
from .rescue import (
    GenericRescueOutcome,
    GenericRescueRunner,
    RescueRollout,
    RescueTermination,
    TacticEvaluation,
)
from .search import (
    LeanVerifier,
    ProgressPolicy,
    SearchResult,
    StateFingerprinter,
    StateDiversity,
    StucknessPolicy,
    StuckStateSink,
    TacticRanker,
)


@dataclass(frozen=True, slots=True)
class EpisodeContext:
    tactic_ranker: TacticRanker
    verifier: LeanVerifier
    stuckness_policy: StucknessPolicy
    state_fingerprinter: StateFingerprinter
    tactic_canonicalizer: TacticCanonicalizer
    progress_policy: ProgressPolicy
    cancellation: CancellationToken
    analogy_enabled: bool
    analogy_fallback: GenericRescueRunner
    ordinary_width: int = 1
    max_expansions: int | None = None
    stuck_state_sink: StuckStateSink | None = None
    state_diversity: StateDiversity | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.ordinary_width, bool)
            or not isinstance(self.ordinary_width, int)
            or self.ordinary_width <= 0
        ):
            raise ValueError("ordinary_width must be a positive integer")
        if (
            self.max_expansions is not None
            and (
                isinstance(self.max_expansions, bool)
                or not isinstance(self.max_expansions, int)
                or self.max_expansions <= 0
            )
        ):
            raise ValueError("max_expansions must be a positive integer or None")
        if not isinstance(self.analogy_enabled, bool):
            raise ValueError("analogy_enabled must be a boolean")


class _Frontier:
    def __init__(self, initial_state: SymbolicState, diversity: StateDiversity | None) -> None:
        self._items: list[tuple[float, float, int, SearchNode]] = []
        self._order = count()
        self._diversity = diversity
        self.observations = []
        self.push(SearchNode(initial_state, 1.0))

    def __bool__(self) -> bool:
        return bool(self._items)

    def push(self, node: SearchNode) -> None:
        novelty = 0.0
        if self._diversity is not None:
            observation = self._diversity.observe(node.state)
            novelty = observation.novelty
            self.observations.append(observation)
        heappush(
            self._items,
            (-novelty, -node.score, next(self._order), node),
        )

    def extend(self, nodes: Iterable[SearchNode]) -> None:
        for node in nodes:
            self.push(node)

    def pop(self) -> SearchNode:
        return heappop(self._items)[3]


def run_episode(
    context: EpisodeContext,
    initial_state: SymbolicState,
    goal: SymbolicState,
) -> SearchResult:
    """Generate tactics, ask Lean, rescue stuck states, and return one episode."""

    frontier = _Frontier(initial_state, context.state_diversity)
    visited = {context.state_fingerprinter.fingerprint(initial_state)}
    expansions: list[ExpansionOutcome] = []
    decisions: list[StucknessDecision] = []
    rescue_rollouts: list[RescueRollout] = []

    def finish(status: SearchStatus, proof: str | None = None) -> SearchResult:
        return SearchResult(
            status,
            proof,
            tuple(expansions),
            tuple(decisions),
            tuple(rescue_rollouts),
            tuple(frontier.observations),
        )

    while frontier:
        if context.cancellation.is_cancelled():
            return finish(SearchStatus.CANCELLED)
        if (
            context.max_expansions is not None
            and len(expansions) >= context.max_expansions
        ):
            return finish(SearchStatus.BUDGET_EXHAUSTED)

        node = frontier.pop()

        ordinary = _ordinary_step(context, node, goal, visited)
        expansions.append(ordinary)
        if ordinary.completed_proof is not None:
            return finish(SearchStatus.COMPLETE, ordinary.completed_proof)
        frontier.extend(ordinary.successors)

        stuckness = context.stuckness_policy.evaluate(ordinary)
        decisions.append(stuckness)
        if not stuckness.is_stuck:
            continue
        if context.stuck_state_sink is not None:
            context.stuck_state_sink.capture(node.state, goal, ordinary, stuckness)

        rescue = _rescue_step(context, node, goal, ordinary, visited)
        if rescue is None:
            continue
        rescue_rollouts.extend(rescue.rollouts)
        _validate_rescue(node, rescue)

        if rescue.cancelled:
            return finish(SearchStatus.CANCELLED)
        if rescue.completed_proof is not None:
            return finish(SearchStatus.COMPLETE, rescue.completed_proof)
        if rescue.successor is not None:
            frontier.push(rescue.successor)

    return finish(SearchStatus.EXHAUSTED)


def _ordinary_step(
    context: EpisodeContext,
    node: SearchNode,
    goal: SymbolicState,
    visited: set[str],
) -> ExpansionOutcome:
    pool = _candidate_pool(context, node.state, goal)
    attempts: list[TacticResult] = []
    successors: list[SearchNode] = []
    discarded: list[DiscardedSuccessor] = []
    completed: str | None = None

    for candidate in pool[: context.ordinary_width]:
        result = _verify(context, node.state, candidate)
        attempts.append(result)
        completed, successor, rejected = _classify(
            context, node, goal, result, visited
        )
        if successor is not None:
            successors.append(successor)
        if rejected is not None:
            discarded.append(rejected)
        if completed is not None:
            break

    return ExpansionOutcome(
        node,
        tuple(attempts),
        tuple(successors),
        tuple(discarded),
        completed,
        pool,
    )


def _rescue_step(
    context: EpisodeContext,
    node: SearchNode,
    goal: SymbolicState,
    ordinary: ExpansionOutcome,
    visited: set[str],
):
    if not context.analogy_enabled:
        return None
    evaluator = lambda candidate: _evaluate(
        context, node, goal, visited, candidate
    )
    evaluator.evaluate_at = lambda state, candidate: _evaluate(  # type: ignore[attr-defined]
        context, SearchNode(state, node.score), goal, visited, candidate
    )
    return context.analogy_fallback.search(
        node.state,
        goal,
        ordinary.candidate_pool,
        ordinary.attempts,
        evaluator,
        context.cancellation,
    )


def _candidate_pool(
    context: EpisodeContext, state: SymbolicState, goal: SymbolicState
) -> tuple[TacticCandidate, ...]:
    seen: set[str] = set()
    candidates: list[TacticCandidate] = []
    for candidate in context.tactic_ranker.rank(state, goal):
        if candidate.origin is not CandidateOrigin.ORDINARY:
            raise ValueError("conventional tactic policy must return ordinary candidates")
        key = context.tactic_canonicalizer.canonicalize(candidate.source)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)
    return tuple(candidates)


def _verify(
    context: EpisodeContext, state: SymbolicState, candidate: TacticCandidate
) -> TacticResult:
    result = context.verifier.run_tactic(state, candidate)
    if result.candidate != candidate:
        raise ValueError("verifier result must reference the requested candidate")
    return result


def _evaluate(
    context: EpisodeContext,
    node: SearchNode,
    goal: SymbolicState,
    visited: set[str],
    candidate: TacticCandidate,
) -> TacticEvaluation:
    result = _verify(context, node.state, candidate)
    completed, successor, discarded = _classify(
        context, node, goal, result, visited
    )
    return TacticEvaluation(result, completed, successor, discarded)


def _classify(
    context: EpisodeContext,
    node: SearchNode,
    goal: SymbolicState,
    result: TacticResult,
    visited: set[str],
):
    if result.status is TacticStatus.ERROR:
        return None, None, None
    if result.status is TacticStatus.COMPLETE:
        return result.proof, None, None
    assert result.state is not None
    fingerprint = context.state_fingerprinter.fingerprint(result.state)
    if fingerprint in visited:
        return None, None, DiscardedSuccessor(result, DiscardReason.REPEATED_STATE)
    visited.add(fingerprint)
    if not context.progress_policy.accepts(node.state, result.state, goal):
        return None, None, DiscardedSuccessor(result, DiscardReason.NO_PROGRESS)
    return (
        None,
        SearchNode(
            result.state,
            node.score * result.candidate.score,
            node.state.id,
            result.candidate.source,
        ),
        None,
    )


def _validate_rescue(node: SearchNode, outcome: GenericRescueOutcome) -> None:
    if any(rollout.state_id != node.state.id for rollout in outcome.rollouts):
        raise ValueError("generic rescue rollouts must belong to the rescued state")
    if outcome.completed_proof is not None:
        verified = any(
            rollout.termination is RescueTermination.COMPLETE
            and any(
                attempt.result.status is TacticStatus.COMPLETE
                and attempt.result.proof == outcome.completed_proof
                for attempt in rollout.attempts
            )
            for rollout in outcome.rollouts
        )
        if not verified:
            raise ValueError("completed rescue outcome requires a verified rollout")
    if outcome.successor is not None:
        verified = any(
            rollout.termination is RescueTermination.PROGRESS
            and any(
                attempt.result.status is TacticStatus.ACCEPTED
                and attempt.discarded is None
                and attempt.result.state == outcome.successor.state
                and outcome.successor.tactic == attempt.proposal.candidate.source
                for attempt in rollout.attempts
            )
            for rollout in outcome.rollouts
        )
        if not verified:
            raise ValueError("successor rescue outcome requires a verified rollout")


__all__ = ["EpisodeContext", "run_episode"]
