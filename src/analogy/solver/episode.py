"""Incremental state transitions for one proof-search episode."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from heapq import heappop, heappush
from itertools import count
from typing import Iterable

from ..utils.records import (
    CancellationToken,
    CandidateOrigin,
    DiscardReason,
    DiscardedSuccessor,
    ExpansionOutcome,
    SearchNode,
    SearchStatus,
    SkippedCandidate,
    StucknessDecision,
    SymbolicState,
    TacticCandidate,
    TacticCanonicalizer,
    TacticResult,
    TacticStatus,
)
from .analogy.rescue import (
    AnalogyRescueResult,
    AnalogyRescueOutcome,
    AnalogyRescueRunner,
    RescueRollout,
    RescueTermination,
    TacticEvaluation,
)
from .proof.semantic import (
    SemanticAssessment,
    SemanticCheckError,
    SemanticChecker,
    SemanticRecoveryEvent,
    SemanticResult,
    SemanticPrune,
    build_countermodel_table,
    ForcedMoveEvent,
    forced_disjunction_tactic,
)
from .search import (
    ProgressPolicy,
    SearchResult,
    StateFingerprinter,
    StateDiversity,
    StucknessPolicy,
    TacticRanker,
)


@dataclass(frozen=True, slots=True)
class EpisodeContext:
    tactic_ranker: TacticRanker
    stuckness_policy: StucknessPolicy
    state_fingerprinter: StateFingerprinter
    tactic_canonicalizer: TacticCanonicalizer
    progress_policy: ProgressPolicy
    cancellation: CancellationToken
    analogy_fallback: AnalogyRescueRunner | None = None
    ordinary_width: int = 1
    max_expansions: int | None = None
    state_diversity: StateDiversity | None = None
    semantic_checker: SemanticChecker | None = None

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
        if self.semantic_checker is None:
            raise ValueError("semantic_checker is required")


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
            (-(node.score - 0.02 * node.depth), -novelty, next(self._order), node),
        )

    def extend(self, nodes: Iterable[SearchNode]) -> None:
        for node in nodes:
            self.push(node)

    def pop(self) -> SearchNode:
        return heappop(self._items)[3]


@dataclass(frozen=True, slots=True)
class TacticBatch:
    node: SearchNode
    candidates: tuple[TacticCandidate, ...]
    candidate_pool: tuple[TacticCandidate, ...]
    forced_hypothesis: str | None = None

    def __post_init__(self) -> None:
        if any(item not in self.candidate_pool for item in self.candidates):
            raise ValueError("submitted candidates must belong to the candidate pool")


@dataclass(frozen=True, slots=True)
class TacticBatchResult:
    batch: TacticBatch
    results: tuple[TacticResult, ...]

    def __post_init__(self) -> None:
        if tuple(item.candidate for item in self.results) != self.batch.candidates:
            raise ValueError("tactic results must match the submitted candidates")


@dataclass(slots=True)
class SearchSession:
    context: EpisodeContext
    goal: SymbolicState
    frontier: _Frontier
    visited: set[str]
    expansions: list[ExpansionOutcome] = field(default_factory=list)
    decisions: list[StucknessDecision] = field(default_factory=list)
    rescue_rollouts: list[RescueRollout] = field(default_factory=list)
    semantic_recoveries: list[SemanticRecoveryEvent] = field(default_factory=list)
    semantic_prunes: list[SemanticPrune] = field(default_factory=list)
    forced_moves: list[ForcedMoveEvent] = field(default_factory=list)
    nodes_by_id: dict[str, SearchNode] = field(default_factory=dict)
    expansions_by_id: dict[str, ExpansionOutcome] = field(default_factory=dict)
    semantic_cache: dict[str, SemanticAssessment] = field(default_factory=dict)
    tried_by_state: dict[str, set[str]] = field(default_factory=dict)
    node: SearchNode | None = None
    candidate_pool: tuple[TacticCandidate, ...] | None = None
    active_candidates: tuple[TacticCandidate, ...] = ()
    candidate_index: int = 0
    attempts: list[TacticResult] = field(default_factory=list)
    successors: list[SearchNode] = field(default_factory=list)
    discarded: list[DiscardedSuccessor] = field(default_factory=list)
    skipped: list[SkippedCandidate] = field(default_factory=list)
    refuted_disjuncts: set[str] = field(default_factory=set)
    pending_batch: TacticBatch | None = None
    recovery: SemanticRecoveryEvent | None = None
    rescue_node: SearchNode | None = None
    rescue_ordinary: ExpansionOutcome | None = None
    forced_successor: SearchNode | None = None
    terminal_result: SearchResult | None = None

    def has_work(self) -> bool:
        if self.terminal_result is not None:
            return False
        if self.pending_batch is not None or self.node is not None:
            return True
        if self.context.cancellation.is_cancelled():
            self.finish(SearchStatus.CANCELLED)
            return False
        if (
            self.context.max_expansions is not None
            and len(self.expansions) >= self.context.max_expansions
        ):
            self.finish(SearchStatus.BUDGET_EXHAUSTED)
            return False
        if not self.frontier:
            self.finish(SearchStatus.EXHAUSTED)
            return False
        self.node = self.frontier.pop()
        return True

    def is_stuck(self) -> bool:
        return (
            self.rescue_node is not None
            and self.pending_batch is None
            and self.terminal_result is None
        )

    @property
    def result(self) -> SearchResult:
        if self.terminal_result is None:
            raise RuntimeError("search has not terminated")
        return self.terminal_result

    @property
    def tried_tactics(self) -> set[str]:
        if self.node is None:
            raise RuntimeError("search has no active node")
        return self.tried_by_state.setdefault(self.node.state.id, set())

    def begin_candidate_pool(self, pool: tuple[TacticCandidate, ...]) -> None:
        if self.node is None or self.candidate_pool is not None:
            raise RuntimeError("candidate pool does not match the search phase")
        self.candidate_pool = pool
        self.active_candidates = pool[: self.context.ordinary_width]

    def next_tactic_batch(self) -> TacticBatch:
        if self.pending_batch is not None:
            batch = self.pending_batch
            self.pending_batch = None
            return batch
        if self.node is None or self.candidate_pool is None:
            raise RuntimeError("search has no generated candidate pool")
        if not self.active_candidates:
            return TacticBatch(self.node, (), self.candidate_pool)
        candidate = self.active_candidates[self.candidate_index]
        self.tried_tactics.add(
            self.context.tactic_canonicalizer.canonicalize(candidate.source)
        )
        return TacticBatch(self.node, (candidate,), self.candidate_pool)

    def finish(self, status: SearchStatus, proof: str | None = None) -> None:
        self.terminal_result = SearchResult(
            status=status,
            proof=proof,
            ordinary_expansions=tuple(self.expansions),
            stuckness_decisions=tuple(self.decisions),
            rescue_rollouts=tuple(self.rescue_rollouts),
            diversity_observations=tuple(self.frontier.observations),
            semantic_recoveries=tuple(self.semantic_recoveries),
            semantic_prunes=tuple(self.semantic_prunes),
            forced_moves=tuple(self.forced_moves),
        )

    def classify_rescue_result(
        self,
        state: SymbolicState,
        result: TacticResult,
    ) -> TacticEvaluation:
        if self.rescue_node is None:
            raise RuntimeError("search has no pending rescue node")
        node = (
            self.rescue_node
            if state == self.rescue_node.state
            else SearchNode(state, self.rescue_node.score)
        )
        completed, successor, discarded = _classify(
            self.context,
            node,
            self.goal,
            result,
            self.visited,
            self.semantic_cache,
            self.semantic_prunes,
        )
        return TacticEvaluation(result, completed, successor, discarded)

    def clear_active_node(self) -> None:
        self.node = None
        self.candidate_pool = None
        self.active_candidates = ()
        self.candidate_index = 0
        self.attempts.clear()
        self.successors.clear()
        self.discarded.clear()
        self.skipped.clear()
        self.refuted_disjuncts.clear()
        self.pending_batch = None
        self.recovery = None
        self.rescue_node = None
        self.rescue_ordinary = None
        self.forced_successor = None


def start_search(
    context: EpisodeContext,
    initial_state: SymbolicState,
    goal: SymbolicState,
) -> SearchSession:
    frontier = _Frontier(initial_state, context.state_diversity)
    return SearchSession(
        context=context,
        goal=goal,
        frontier=frontier,
        visited={context.state_fingerprinter.fingerprint(initial_state)},
        nodes_by_id={initial_state.id: SearchNode(initial_state, 1.0)},
    )


def update_search(
    search: SearchSession,
    submitted: TacticBatchResult | AnalogyRescueResult,
) -> None:
    if search.terminal_result is not None:
        raise RuntimeError("cannot update a terminated search")
    if isinstance(submitted, AnalogyRescueResult):
        _update_analogy_rescue(search, submitted)
        return
    batch = submitted.batch
    if search.node is None or batch.node != search.node:
        raise ValueError("tactic results belong to a stale search node")
    if not submitted.results:
        _finish_ordinary_expansion(search, None)
        return
    result = submitted.results[0]

    if batch.forced_hypothesis is not None:
        _update_forced_move(search, batch, result)
        return

    search.attempts.append(result)
    completed, successor, rejected = _classify(
        search.context,
        search.node,
        search.goal,
        result,
        search.visited,
        search.semantic_cache,
        search.semantic_prunes,
    )
    if successor is not None:
        search.successors.append(successor)
    if rejected is not None:
        search.discarded.append(rejected)
        if rejected.reason is DiscardReason.SEMANTIC_REFUTED:
            head = result.candidate.source.split(maxsplit=1)[0]
            if head in {"left", "right"}:
                search.refuted_disjuncts.add(head)

    search.candidate_index += 1
    exhausted = search.candidate_index >= len(search.active_candidates)
    if search.refuted_disjuncts == {"left", "right"}:
        search.skipped.extend(
            SkippedCandidate(item, "early_all_disjunct_recovery")
            for item in search.active_candidates[search.candidate_index :]
        )
        exhausted = True
    if completed is not None or exhausted:
        _finish_ordinary_expansion(search, completed)


def _finish_ordinary_expansion(
    search: SearchSession, completed_proof: str | None
) -> None:
    assert search.node is not None
    assert search.candidate_pool is not None
    ordinary = ExpansionOutcome(
        search.node,
        tuple(search.attempts),
        tuple(search.successors),
        tuple(search.discarded),
        completed_proof,
        search.candidate_pool,
        tuple(search.skipped),
    )
    search.expansions.append(ordinary)
    search.expansions_by_id[search.node.state.id] = ordinary
    for successor in ordinary.successors:
        _index_node(search.nodes_by_id, successor)
    if completed_proof is not None:
        search.finish(SearchStatus.COMPLETE, completed_proof)
        return
    search.frontier.extend(ordinary.successors)

    stuckness = search.context.stuckness_policy.evaluate(ordinary)
    search.decisions.append(stuckness)
    if not stuckness.is_stuck:
        search.clear_active_node()
        return
    recovery, rescue_node, rescue_ordinary = _resolve_semantic_recovery(
        search.context.semantic_checker,
        search.node,
        ordinary,
        search.nodes_by_id,
        search.expansions_by_id,
        search.semantic_cache,
        len(search.semantic_recoveries),
        search.semantic_prunes,
    )
    search.semantic_recoveries.append(recovery)
    if rescue_node is None or rescue_ordinary is None:
        if recovery.result is SemanticResult.UNKNOWN:
            search.clear_active_node()
        else:
            search.finish(SearchStatus.SEMANTIC_FAILURE)
        return

    search.recovery = recovery
    search.rescue_node = rescue_node
    search.rescue_ordinary = rescue_ordinary
    forced = forced_disjunction_tactic(
        rescue_node.state,
        recovery.countermodel_table,
    ) if recovery.countermodel_table is not None else None
    if forced is not None:
        hypothesis, source = forced
        canonical = search.context.tactic_canonicalizer.canonicalize(source)
        tried = search.tried_by_state.setdefault(rescue_node.state.id, set())
        if canonical not in tried:
            tried.add(canonical)
            candidate = TacticCandidate(source, 1.0, CandidateOrigin.ORDINARY)
            search.node = rescue_node
            search.pending_batch = TacticBatch(
                rescue_node,
                (candidate,),
                rescue_ordinary.candidate_pool + (candidate,),
                hypothesis,
            )
            return
    if search.context.analogy_fallback is None:
        search.clear_active_node()


def _update_forced_move(
    search: SearchSession,
    batch: TacticBatch,
    result: TacticResult,
) -> None:
    assert search.node is not None
    assert search.recovery is not None
    assert search.rescue_ordinary is not None
    completed, successor, discarded = _classify(
        search.context,
        search.node,
        search.goal,
        result,
        search.visited,
        search.semantic_cache,
        search.semantic_prunes,
    )
    outcome = (
        "complete" if completed is not None
        else "progress" if successor is not None
        else "rejected" if result.status is TacticStatus.ERROR
        else "discarded"
    )
    search.forced_moves.append(
        ForcedMoveEvent(
            search.node.state.id,
            batch.forced_hypothesis,
            result.candidate.source,
            result,
            outcome,
        )
    )
    search.rescue_ordinary = replace(
        search.rescue_ordinary,
        attempts=search.rescue_ordinary.attempts + (result,),
    )
    if completed is not None:
        search.finish(SearchStatus.COMPLETE, completed)
        return
    if successor is not None:
        _index_node(search.nodes_by_id, successor)
        search.forced_successor = successor
    if search.context.analogy_fallback is None:
        if successor is not None:
            search.frontier.push(successor)
        search.clear_active_node()


def _update_analogy_rescue(
    search: SearchSession,
    submitted: AnalogyRescueResult,
) -> None:
    if search.recovery is None or search.rescue_node is None:
        raise RuntimeError("search has no pending analogy rescue")
    outcome = submitted.outcome
    outcome = replace(
        outcome,
        rollouts=tuple(
            replace(rollout, semantic_event_id=search.recovery.event_id)
            for rollout in outcome.rollouts
        ),
    )
    search.rescue_rollouts.extend(outcome.rollouts)
    _validate_rescue(search.rescue_node, outcome)
    if outcome.cancelled:
        search.finish(SearchStatus.CANCELLED)
        return
    if outcome.completed_proof is not None:
        search.finish(SearchStatus.COMPLETE, outcome.completed_proof)
        return
    if search.forced_successor is not None:
        search.frontier.push(search.forced_successor)
    if outcome.successor is not None:
        _index_node(search.nodes_by_id, outcome.successor)
        search.frontier.push(outcome.successor)
    search.clear_active_node()


def _index_node(nodes_by_id: dict[str, SearchNode], node: SearchNode) -> None:
    existing = nodes_by_id.get(node.state.id)
    if existing is not None and existing != node:
        raise ValueError(f"state ID {node.state.id!r} identifies multiple search nodes")
    nodes_by_id[node.state.id] = node


def _resolve_semantic_recovery(
    checker: SemanticChecker,
    trigger: SearchNode,
    trigger_expansion: ExpansionOutcome,
    nodes_by_id: dict[str, SearchNode],
    expansions_by_id: dict[str, ExpansionOutcome],
    cache: dict[str, SemanticAssessment],
    event_index: int,
    semantic_prunes: list[SemanticPrune],
) -> tuple[SemanticRecoveryEvent, SearchNode | None, ExpansionOutcome | None]:
    event_id = f"semantic:{event_index}"

    def assess(node: SearchNode) -> SemanticAssessment:
        cached = cache.get(node.state.id)
        if cached is not None:
            return cached
        assessment = checker.assess(node.state)
        if assessment.state_id != node.state.id:
            raise SemanticCheckError("semantic assessment references the wrong state")
        cache[node.state.id] = assessment
        return assessment

    try:
        trigger_assessment = assess(trigger)
        if trigger_assessment.result is SemanticResult.UNKNOWN:
            return (
                SemanticRecoveryEvent(
                    event_id,
                    trigger.state.id,
                    SemanticResult.UNKNOWN,
                ),
                None,
                None,
            )
        if trigger_assessment.result is SemanticResult.VALID:
            refuted = [
                item for item in semantic_prunes
                if item.parent_state_id == trigger.state.id
            ]
            if refuted:
                first = refuted[0]
                return (
                    SemanticRecoveryEvent(
                        event_id,
                        trigger.state.id,
                        SemanticResult.COUNTERMODEL,
                        first.countermodel,
                        trigger.state.id,
                        tuple(item.tactic for item in refuted),
                        countermodel_table=build_countermodel_table(
                            trigger.state,
                            refuted,
                        ),
                    ),
                    trigger,
                    trigger_expansion,
                )
            return (
                SemanticRecoveryEvent(
                    event_id,
                    trigger.state.id,
                    SemanticResult.VALID,
                    rescue_state_id=trigger.state.id,
                ),
                trigger,
                trigger_expansion,
            )

        current = trigger
        abandoned: list[str] = []
        seen = {current.state.id}
        while current.parent_state_id is not None:
            if current.tactic is None:
                raise SemanticCheckError("non-root search node lacks its incoming tactic")
            abandoned.append(current.tactic)
            parent = nodes_by_id.get(current.parent_state_id)
            if parent is None:
                raise SemanticCheckError("search ancestry references an unknown parent")
            if parent.state.id in seen:
                raise SemanticCheckError("search ancestry contains a cycle")
            seen.add(parent.state.id)
            current = parent
            assessment = assess(current)
            if assessment.result is SemanticResult.UNKNOWN:
                return (
                    SemanticRecoveryEvent(
                        event_id,
                        trigger.state.id,
                        SemanticResult.UNKNOWN,
                        countermodel=(),
                        abandoned_tactics=tuple(reversed(abandoned)),
                    ),
                    None,
                    None,
                )
            if assessment.result is SemanticResult.VALID:
                expansion = expansions_by_id.get(current.state.id)
                if expansion is None:
                    raise SemanticCheckError("valid rescue ancestor has no expansion")
                return (
                    SemanticRecoveryEvent(
                        event_id,
                        trigger.state.id,
                        SemanticResult.COUNTERMODEL,
                        trigger_assessment.countermodel,
                        current.state.id,
                        tuple(reversed(abandoned)),
                    ),
                    current,
                    expansion,
                )
        return (
            SemanticRecoveryEvent(
                event_id,
                trigger.state.id,
                SemanticResult.COUNTERMODEL,
                trigger_assessment.countermodel,
                None,
                tuple(reversed(abandoned)),
            ),
            None,
            None,
        )
    except SemanticCheckError as error:
        return (
            SemanticRecoveryEvent(
                event_id,
                trigger.state.id,
                None,
                error=str(error),
            ),
            None,
            None,
        )


def _classify(
    context: EpisodeContext,
    node: SearchNode,
    goal: SymbolicState,
    result: TacticResult,
    visited: set[str],
    semantic_cache: dict[str, SemanticAssessment],
    semantic_prunes: list[SemanticPrune],
):
    if result.status is TacticStatus.ERROR:
        return None, None, None
    if result.status is TacticStatus.COMPLETE:
        return result.proof, None, None
    assert result.state is not None
    try:
        assessment = semantic_cache.get(result.state.id)
        if assessment is None:
            assessment = context.semantic_checker.assess(result.state)
            semantic_cache[result.state.id] = assessment
        if assessment.result is SemanticResult.COUNTERMODEL:
            assert assessment.invalid_goal_index is not None
            semantic_prunes.append(
                SemanticPrune(
                    node.state.id,
                    result.state.id,
                    result.candidate.source,
                    assessment.countermodel,
                    assessment.invalid_goal_index,
                )
            )
            return (
                None,
                None,
                DiscardedSuccessor(result, DiscardReason.SEMANTIC_REFUTED),
            )
    except SemanticCheckError:
        pass
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
            result.candidate.score,
            node.state.id,
            result.candidate.source,
            node.depth + 1,
        ),
        None,
    )


def _validate_rescue(node: SearchNode, outcome: AnalogyRescueOutcome) -> None:
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


__all__ = [
    "EpisodeContext",
    "SearchSession",
    "TacticBatch",
    "TacticBatchResult",
    "start_search",
    "update_search",
]
