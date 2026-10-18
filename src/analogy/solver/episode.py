"""One shared environment-interaction loop for training and inference."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from heapq import heappop, heappush
from inspect import Parameter, signature
from itertools import count
from typing import Iterable

from ..model.parser import candidate_rejection_reason, normalize_lean4_tactic
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
    render_countermodel_table,
    render_semantic_guidance,
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
    analogy_fallback: AnalogyRescueRunner | None = None
    ordinary_width: int = 1
    max_expansions: int | None = None
    stuck_state_sink: StuckStateSink | None = None
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
        return self.rescue_node is not None and self.terminal_result is None

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


def update_search(search: SearchSession, submitted: TacticBatchResult) -> None:
    if search.terminal_result is not None:
        raise RuntimeError("cannot update a terminated search")
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
    if search.context.stuck_state_sink is not None:
        search.context.stuck_state_sink.capture(
            search.node.state, search.goal, ordinary, stuckness
        )
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
    semantic_recoveries: list[SemanticRecoveryEvent] = []
    semantic_prunes: list[SemanticPrune] = []
    forced_moves: list[ForcedMoveEvent] = []
    nodes_by_id = {initial_state.id: SearchNode(initial_state, 1.0)}
    expansions_by_id: dict[str, ExpansionOutcome] = {}
    semantic_cache: dict[str, SemanticAssessment] = {}
    tried_by_state: dict[str, set[str]] = {}

    def finish(status: SearchStatus, proof: str | None = None) -> SearchResult:
        return SearchResult(
            status=status,
            proof=proof,
            ordinary_expansions=tuple(expansions),
            stuckness_decisions=tuple(decisions),
            rescue_rollouts=tuple(rescue_rollouts),
            diversity_observations=tuple(frontier.observations),
            semantic_recoveries=tuple(semantic_recoveries),
            semantic_prunes=tuple(semantic_prunes),
            forced_moves=tuple(forced_moves),
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

        state_tried = tried_by_state.setdefault(node.state.id, set())
        ordinary = _ordinary_step(
            context,
            node,
            goal,
            visited,
            state_tried,
            semantic_cache,
            semantic_prunes,
        )
        expansions.append(ordinary)
        expansions_by_id[node.state.id] = ordinary
        for successor in ordinary.successors:
            _index_node(nodes_by_id, successor)
        if ordinary.completed_proof is not None:
            return finish(SearchStatus.COMPLETE, ordinary.completed_proof)
        frontier.extend(ordinary.successors)

        stuckness = context.stuckness_policy.evaluate(ordinary)
        decisions.append(stuckness)
        if not stuckness.is_stuck:
            continue
        if context.stuck_state_sink is not None:
            context.stuck_state_sink.capture(node.state, goal, ordinary, stuckness)

        recovery, rescue_node, rescue_ordinary = _resolve_semantic_recovery(
            context.semantic_checker,
            node,
            ordinary,
            nodes_by_id,
            expansions_by_id,
            semantic_cache,
            len(semantic_recoveries),
            semantic_prunes,
        )
        semantic_recoveries.append(recovery)
        if rescue_node is None or rescue_ordinary is None:
            if recovery.result is SemanticResult.UNKNOWN:
                continue
            return finish(SearchStatus.SEMANTIC_FAILURE)

        forced_successor: SearchNode | None = None
        forced = _forced_move_step(
            context,
            rescue_node,
            goal,
            recovery,
            visited,
            semantic_cache,
            semantic_prunes,
            tried_by_state.setdefault(rescue_node.state.id, set()),
        )
        if forced is not None:
            event, evaluation = forced
            forced_moves.append(event)
            if evaluation.completed_proof is not None:
                return finish(SearchStatus.COMPLETE, evaluation.completed_proof)
            rescue_ordinary = replace(
                rescue_ordinary,
                attempts=rescue_ordinary.attempts + (event.result,),
            )
            if evaluation.successor is not None:
                _index_node(nodes_by_id, evaluation.successor)
                forced_successor = evaluation.successor

        rescue = _rescue_step(
            context,
            rescue_node,
            goal,
            rescue_ordinary,
            visited,
            render_semantic_guidance(recovery),
            render_countermodel_table(recovery.countermodel_table),
            semantic_cache,
            semantic_prunes,
            force_domain_change=recovery.result is SemanticResult.COUNTERMODEL,
        )
        if rescue is None:
            if forced_successor is not None:
                frontier.push(forced_successor)
            continue
        rescue = replace(
            rescue,
            rollouts=tuple(
                replace(rollout, semantic_event_id=recovery.event_id)
                for rollout in rescue.rollouts
            ),
        )
        rescue_rollouts.extend(rescue.rollouts)
        _validate_rescue(rescue_node, rescue)

        if rescue.cancelled:
            return finish(SearchStatus.CANCELLED)
        if rescue.completed_proof is not None:
            return finish(SearchStatus.COMPLETE, rescue.completed_proof)
        if forced_successor is not None:
            frontier.push(forced_successor)
        if rescue.successor is not None:
            _index_node(nodes_by_id, rescue.successor)
            frontier.push(rescue.successor)

    return finish(SearchStatus.EXHAUSTED)


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


def _ordinary_step(
    context: EpisodeContext,
    node: SearchNode,
    goal: SymbolicState,
    visited: set[str],
    tried_tactics: set[str],
    semantic_cache: dict[str, SemanticAssessment],
    semantic_prunes: list[SemanticPrune],
) -> ExpansionOutcome:
    pool = _candidate_pool(context, node.state, goal, tried_tactics)
    attempts: list[TacticResult] = []
    successors: list[SearchNode] = []
    discarded: list[DiscardedSuccessor] = []
    completed: str | None = None
    refuted_disjuncts: set[str] = set()
    skipped: list[SkippedCandidate] = []

    active_pool = pool[: context.ordinary_width]
    for candidate_index, candidate in enumerate(active_pool):
        tried_tactics.add(context.tactic_canonicalizer.canonicalize(candidate.source))
        result = _verify(context, node.state, candidate)
        attempts.append(result)
        completed, successor, rejected = _classify(
            context,
            node,
            goal,
            result,
            visited,
            semantic_cache,
            semantic_prunes,
        )
        if successor is not None:
            successors.append(successor)
        if rejected is not None:
            discarded.append(rejected)
            if rejected.reason is DiscardReason.SEMANTIC_REFUTED:
                head = result.candidate.source.split(maxsplit=1)[0]
                if head in {"left", "right"}:
                    refuted_disjuncts.add(head)
        if completed is not None:
            break
        if refuted_disjuncts == {"left", "right"}:
            skipped.extend(
                SkippedCandidate(item, "early_all_disjunct_recovery")
                for item in active_pool[candidate_index + 1 :]
            )
            break

    return ExpansionOutcome(
        node,
        tuple(attempts),
        tuple(successors),
        tuple(discarded),
        completed,
        pool,
        tuple(skipped),
    )


def _rescue_step(
    context: EpisodeContext,
    node: SearchNode,
    goal: SymbolicState,
    ordinary: ExpansionOutcome,
    visited: set[str],
    semantic_guidance: str,
    dependency_table: str,
    semantic_cache: dict[str, SemanticAssessment],
    semantic_prunes: list[SemanticPrune],
    force_domain_change: bool = False,
):
    if context.analogy_fallback is None:
        return None
    evaluator = lambda candidate: _evaluate(
        context, node, goal, visited, candidate, semantic_cache, semantic_prunes
    )
    evaluator.evaluate_at = lambda state, candidate: _evaluate(  # type: ignore[attr-defined]
        context,
        SearchNode(state, node.score),
        goal,
        visited,
        candidate,
        semantic_cache,
        semantic_prunes,
    )
    search = context.analogy_fallback.search
    parameters = signature(search).parameters.values()
    accepts_keywords = any(item.kind is Parameter.VAR_KEYWORD for item in parameters)
    names = {item.name for item in parameters}
    keyword_arguments = {}
    if accepts_keywords or "dependency_table" in names:
        keyword_arguments["dependency_table"] = dependency_table
    if accepts_keywords or "force_domain_change" in names:
        keyword_arguments["force_domain_change"] = force_domain_change
    return search(
        node.state,
        goal,
        ordinary.candidate_pool,
        ordinary.attempts,
        evaluator,
        context.cancellation,
        semantic_guidance,
        **keyword_arguments,
    )


def _forced_move_step(
    context: EpisodeContext,
    node: SearchNode,
    goal: SymbolicState,
    recovery: SemanticRecoveryEvent,
    visited: set[str],
    semantic_cache: dict[str, SemanticAssessment],
    semantic_prunes: list[SemanticPrune],
    tried_tactics: set[str],
) -> tuple[ForcedMoveEvent, TacticEvaluation] | None:
    table = recovery.countermodel_table
    if table is None:
        return None
    forced = forced_disjunction_tactic(node.state, table)
    if forced is None:
        return None
    hypothesis, source = forced
    canonical = context.tactic_canonicalizer.canonicalize(source)
    if canonical in tried_tactics:
        return None
    tried_tactics.add(canonical)
    candidate = TacticCandidate(source, 1.0, CandidateOrigin.ORDINARY)
    evaluation = _evaluate(
        context,
        node,
        goal,
        visited,
        candidate,
        semantic_cache,
        semantic_prunes,
    )
    outcome = (
        "complete" if evaluation.completed_proof is not None
        else "progress" if evaluation.successor is not None
        else "rejected" if evaluation.result.status is TacticStatus.ERROR
        else "discarded"
    )
    return (
        ForcedMoveEvent(
            node.state.id,
            hypothesis,
            source,
            evaluation.result,
            outcome,
        ),
        evaluation,
    )


def _candidate_pool(
    context: EpisodeContext,
    state: SymbolicState,
    goal: SymbolicState,
    tried_tactics: set[str],
) -> tuple[TacticCandidate, ...]:
    seen: set[str] = set(tried_tactics)
    candidates: list[TacticCandidate] = []
    try:
        ranked = context.tactic_ranker.rank(
            state,
            goal,
            excluded_tactics=frozenset(tried_tactics),
        )
    except TypeError as error:
        if "excluded_tactics" not in str(error):
            raise
        ranked = context.tactic_ranker.rank(state, goal)
    for candidate in ranked:
        if candidate.origin is not CandidateOrigin.ORDINARY:
            raise ValueError("conventional tactic policy must return ordinary candidates")
        normalized = normalize_lean4_tactic(candidate.source, state.expression)
        if candidate_rejection_reason(
            normalized,
            state.expression,
            excluded_tactics=frozenset(tried_tactics),
        ) is not None:
            continue
        candidate = replace(candidate, source=normalized)
        key = context.tactic_canonicalizer.canonicalize(normalized)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)
    return tuple(candidates)


def _verify(
    context: EpisodeContext, state: SymbolicState, candidate: TacticCandidate
) -> TacticResult:
    normalized = normalize_lean4_tactic(candidate.source, state.expression)
    reason = candidate_rejection_reason(normalized, state.expression)
    candidate = replace(candidate, source=normalized)
    if reason is not None:
        return TacticResult(
            candidate,
            TacticStatus.ERROR,
            diagnostics=(f"filtered before Lean: {reason}",),
        )
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
    semantic_cache: dict[str, SemanticAssessment],
    semantic_prunes: list[SemanticPrune],
) -> TacticEvaluation:
    result = _verify(context, node.state, candidate)
    completed, successor, discarded = _classify(
        context,
        node,
        goal,
        result,
        visited,
        semantic_cache,
        semantic_prunes,
    )
    return TacticEvaluation(result, completed, successor, discarded)


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
    "run_episode",
    "start_search",
    "update_search",
]
