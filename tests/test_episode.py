from dataclasses import replace

from analogy.utils.records import (
    CandidateOrigin,
    SearchStatus,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)
from analogy.solver.analogy.rescue import (
    AnalogyRescueOutcome,
    RescueAttempt,
    RescueProposal,
    RescueRollout,
    RescueTermination,
    generate_analogy_rescue,
    submit_analogy_rescue_to_lean,
)
from analogy.solver.analogy.state import (
    initialize_analogy_scratchpad,
    update_analogy,
)
from analogy.solver.search import (
    AcceptNovelState,
    NeverCancelled,
    ObservableStucknessPolicy,
    CanonicalStateFingerprinter,
    WhitespaceTacticCanonicalizer,
    submit_tactics_to_lean,
    initialize_search,
)
from analogy.main import load_config
from analogy.utils.records import Embedding
from pathlib import Path
from analogy.solver.scoring import DiversityObservation
from analogy.solver.search import (
    start_search,
    update_search_with_scored_proof_states,
)
from analogy.solver.search import generate_and_score_tactics
from analogy.solver.proof.semantic import (
    ExhaustiveSemanticChecker,
    SemanticAssessment,
    SemanticCheckError,
    SemanticResult,
)


START = SymbolicState("start", "P : Prop\nh : P\n⊢ P", "lean4")
GOAL = SymbolicState("goal", "P", "lean4")
CANDIDATE = TacticCandidate("exact h", 1.0, CandidateOrigin.ORDINARY)
RESCUE_CANDIDATE = TacticCandidate(
    "exact rescued", 1.0, CandidateOrigin.ANALOGICAL
)
_VERIFIERS_BY_RANKER = {}
ROOT = Path(__file__).parents[1]


def _run_incremental(context, initial=START, goal=GOAL, *, analogy=False):
    verifier = _VERIFIERS_BY_RANKER[id(context.tactic_ranker)]
    scratchpad = initialize_analogy_scratchpad() if analogy else None
    if scratchpad is not None:
        context.analogy_fallback.scratchpad = scratchpad
    search = start_search(context, initial, goal)
    while search.has_work():
        tactics = generate_and_score_tactics(search)
        results = submit_tactics_to_lean(verifier, tactics)
        update_search_with_scored_proof_states(search, results)
        if search.is_stuck():
            assert scratchpad is not None
            scratchpad = update_analogy(search, scratchpad)
            rescue = generate_analogy_rescue(search, scratchpad)
            result = submit_analogy_rescue_to_lean(verifier, rescue)
            update_search_with_scored_proof_states(search, result)
    return search.result


class _Ranker:
    def rank(self, _state, _goal):
        return (CANDIDATE,)


def test_initialize_search_reuses_precomputed_proof_states():
    class Proof:
        def initial_state(self):
            raise AssertionError("initial state must be reused")

        def goal_state(self):
            raise AssertionError("goal state must be reused")

    class Generator:
        def embed_text(self, _text, *, layer):
            return Embedding("test", (1.0,), layer=layer)

    search = initialize_search(
        load_config(ROOT / "config.yaml"),
        Generator(),
        Proof(),
        _Ranker(),
        initial_state=START,
        goal_state=GOAL,
    )

    assert search.goal is GOAL
    assert START.id in search.nodes_by_id


class _FailIfCalledFallback:
    def search(self, *_args, **_kwargs):
        raise AssertionError("analogy fallback must not be called")


def _context(
    verifier,
    *,
    analogy_enabled=False,
    rescue=None,
    ranker=None,
    diversity=None,
    ordinary_width=1,
    semantic_checker=None,
    max_expansions=4,
):
    from analogy.solver.search import EpisodeContext

    context = EpisodeContext(
        tactic_ranker=ranker or _Ranker(),
        stuckness_policy=ObservableStucknessPolicy(),
        state_fingerprinter=CanonicalStateFingerprinter(),
        tactic_canonicalizer=WhitespaceTacticCanonicalizer(),
        progress_policy=AcceptNovelState(),
        cancellation=NeverCancelled(),
        ordinary_width=ordinary_width,
        max_expansions=max_expansions,
        analogy_fallback=(rescue or _FailIfCalledFallback()) if analogy_enabled else None,
        state_diversity=diversity,
        semantic_checker=semantic_checker or ExhaustiveSemanticChecker(),
    )
    _VERIFIERS_BY_RANKER[id(context.tactic_ranker)] = verifier
    return context


def test_episode_returns_immediately_after_ordinary_completion():
    class Verifier:
        def run_tactic(self, _state, candidate):
            return TacticResult(candidate, TacticStatus.COMPLETE, proof="exact h")

    class SemanticMustNotRun:
        def assess(self, _state):
            raise AssertionError("semantic checking must wait for stuckness")

    result = _run_incremental(
        _context(Verifier(), semantic_checker=SemanticMustNotRun())
    )

    assert result.status is SearchStatus.COMPLETE
    assert result.proof == "exact h"
    assert len(result.ordinary_expansions) == 1
    assert result.semantic_recoveries == ()


def test_episode_calls_rescue_only_after_ordinary_tactics_are_stuck():
    events = []

    class Verifier:
        def run_tactic(self, _state, candidate):
            events.append(candidate.origin.value)
            if candidate.origin is CandidateOrigin.ANALOGICAL:
                return TacticResult(
                    candidate, TacticStatus.COMPLETE, proof="exact rescued"
                )
            return TacticResult(candidate, TacticStatus.ERROR)

    class Rescue:
        def search(self, state, _goal, _attempts, evaluator, _cancellation, semantic_guidance, **_kwargs):
            events.append("rescue")
            assert semantic_guidance == "SAT: no countermodel; continue from this state."
            proposal = RescueProposal(RESCUE_CANDIDATE, "solved")
            evaluation = evaluator(RESCUE_CANDIDATE)
            attempt = RescueAttempt(proposal, evaluation.result)
            rollout = RescueRollout(
                "rollout-1",
                state.id,
                (proposal,),
                (attempt,),
                RescueTermination.COMPLETE,
            )
            return AnalogyRescueOutcome(
                completed_proof=evaluation.completed_proof,
                rollouts=(rollout,),
            )

    result = _run_incremental(
        _context(Verifier(), analogy_enabled=True, rescue=Rescue()),
        analogy=True,
    )

    assert result.status is SearchStatus.COMPLETE
    assert result.proof == "exact rescued"
    assert events == ["ordinary", "rescue", "analogical"]
    assert result.semantic_recoveries[0].trigger_state_id == "start"
    assert result.semantic_recoveries[0].rescue_state_id == "start"
    assert result.rescue_rollouts[0].semantic_event_id == "semantic:0"


def test_disabled_analogy_never_calls_fallback_when_conventional_tactic_is_stuck():
    class Verifier:
        def run_tactic(self, _state, candidate):
            return TacticResult(candidate, TacticStatus.ERROR)

    result = _run_incremental(_context(Verifier(), analogy_enabled=False))

    assert result.status is SearchStatus.EXHAUSTED
    assert result.rescue_rollouts == ()
    assert len(result.semantic_recoveries) == 1
    assert result.semantic_recoveries[0].result is SemanticResult.VALID


def test_enabled_analogy_does_not_call_fallback_when_conventional_tactic_succeeds():
    class Verifier:
        def run_tactic(self, _state, candidate):
            return TacticResult(candidate, TacticStatus.COMPLETE, proof="exact h")

    result = _run_incremental(_context(Verifier(), analogy_enabled=True))

    assert result.status is SearchStatus.COMPLETE


def test_analogy_successor_returns_to_the_shared_frontier():
    events = []
    successor = SymbolicState("rescued", "Q", "lean4")

    class Ranker:
        def rank(self, state, _goal):
            source = "assumption" if state.id == "rescued" else "rfl"
            return (TacticCandidate(source, 1.0, CandidateOrigin.ORDINARY),)

    class Verifier:
        def run_tactic(self, state, candidate):
            events.append((state.id, candidate.origin.value))
            if state.id == "rescued":
                return TacticResult(candidate, TacticStatus.COMPLETE, proof="by finish")
            if candidate.origin is CandidateOrigin.ANALOGICAL:
                return TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)
            return TacticResult(candidate, TacticStatus.ERROR)

    class Rescue:
        def search(self, state, _goal, _attempts, evaluator, _cancellation, semantic_guidance, **_kwargs):
            assert semantic_guidance == "SAT: no countermodel; continue from this state."
            evaluation = evaluator(RESCUE_CANDIDATE)
            proposal = RescueProposal(RESCUE_CANDIDATE, "Q")
            attempt = RescueAttempt(proposal, evaluation.result)
            rollout = RescueRollout(
                "rollout-progress",
                state.id,
                (proposal,),
                (attempt,),
                RescueTermination.PROGRESS,
            )
            return AnalogyRescueOutcome(
                successor=evaluation.successor, rollouts=(rollout,)
            )

    result = _run_incremental(
        _context(
            Verifier(), analogy_enabled=True, rescue=Rescue(), ranker=Ranker()
        ),
        analogy=True,
    )

    assert result.status is SearchStatus.COMPLETE
    assert result.proof == "by finish"
    assert events == [
        ("start", "ordinary"),
        ("start", "analogical"),
        ("rescued", "ordinary"),
    ]


def test_episode_uses_embedding_novelty_to_explore_distinct_states_first():
    first = TacticCandidate("assumption", 1.0, CandidateOrigin.ORDINARY)
    diverse = TacticCandidate("rfl", 1.0, CandidateOrigin.ORDINARY)
    events = []

    class Ranker:
        def rank(self, state, _goal):
            return (first, diverse) if state.id == "start" else (first,)

    class Verifier:
        def run_tactic(self, state, candidate):
            events.append(state.id)
            if state.id == "start":
                next_id = "near" if candidate.source == "assumption" else "far"
                return TacticResult(
                    candidate,
                    TacticStatus.ACCEPTED,
                    state=SymbolicState(next_id, next_id, "lean4"),
                )
            if state.id == "far":
                return TacticResult(candidate, TacticStatus.COMPLETE, proof="diverse")
            return TacticResult(candidate, TacticStatus.ERROR)

    class Diversity:
        def observe(self, state):
            novelty = {"start": 1.0, "near": 0.1, "far": 0.9}[state.id]
            return DiversityObservation(state.id, novelty, (), (), "test")

    class SemanticChecker:
        def __init__(self):
            self.calls = []

        def assess(self, state):
            self.calls.append(state.id)
            return SemanticAssessment(state.id, SemanticResult.VALID)

    semantic = SemanticChecker()

    result = _run_incremental(
        _context(
            Verifier(),
            ranker=Ranker(),
            diversity=Diversity(),
            ordinary_width=2,
            semantic_checker=semantic,
        ),
    )

    assert result.proof == "diverse"
    assert events == ["start", "start", "far"]
    assert semantic.calls == ["near", "far"]
    assert [item.state_id for item in result.diversity_observations] == [
        "start",
        "near",
        "far",
    ]


def test_frontier_uses_latest_step_score_before_novelty():
    low = TacticCandidate("assumption", 0.2, CandidateOrigin.ORDINARY)
    high = TacticCandidate("rfl", 0.8, CandidateOrigin.ORDINARY)
    events = []

    class Ranker:
        def rank(self, state, _goal):
            return (low, high) if state.id == "start" else (CANDIDATE,)

    class Verifier:
        def run_tactic(self, state, candidate):
            events.append(state.id)
            if state.id == "start":
                return TacticResult(
                    candidate,
                    TacticStatus.ACCEPTED,
                    state=SymbolicState(
                        "low" if candidate.source == "assumption" else "high",
                        (
                            "P L : Prop\nh : P\n⊢ P"
                            if candidate.source == "assumption"
                            else "P H : Prop\nh : P\n⊢ P"
                        ),
                        "lean4",
                    ),
                )
            if state.id == "high":
                return TacticResult(candidate, TacticStatus.COMPLETE, proof="high")
            return TacticResult(candidate, TacticStatus.ERROR)

    class Diversity:
        def observe(self, state):
            return DiversityObservation(
                state.id,
                {"start": 1.0, "low": 1.0, "high": 0.0}[state.id],
                (),
                (),
                "test",
            )

    result = _run_incremental(
        _context(
            Verifier(),
            ranker=Ranker(),
            diversity=Diversity(),
            ordinary_width=2,
        ),
    )

    assert result.proof == "high"
    assert events == ["start", "start", "high"]


def test_episode_discards_same_proof_state_even_when_generated_id_differs():
    class Verifier:
        def run_tactic(self, _state, candidate):
            return TacticResult(
                candidate,
                TacticStatus.ACCEPTED,
                state=SymbolicState("different-id", START.expression, "lean4"),
            )

    result = _run_incremental(_context(Verifier()))

    first = result.ordinary_expansions[0]
    assert first.successors == ()
    assert first.discarded[0].reason.value == "repeated_state"


def test_false_stuck_state_restarts_analogy_at_nearest_valid_ancestor():
    middle = SymbolicState("middle", "P Q : Prop\nh : P\n⊢ P", "lean4")
    false_leaf = SymbolicState("false", "P : Prop\n⊢ P", "lean4")
    seen = []

    class Ranker:
        def rank(self, state, _goal):
            source = {"start": "apply h", "middle": "assumption"}.get(
                state.id, "rfl"
            )
            return (TacticCandidate(source, 1.0, CandidateOrigin.ORDINARY),)

    class Verifier:
        def run_tactic(self, state, candidate):
            if candidate.origin is CandidateOrigin.ANALOGICAL:
                return TacticResult(candidate, TacticStatus.COMPLETE, proof="rescued")
            successor = {"start": middle, "middle": false_leaf}.get(state.id)
            if successor is None:
                return TacticResult(candidate, TacticStatus.ERROR)
            return TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)

    class Rescue:
        def search(self, state, _goal, _attempts, evaluator, _cancellation, semantic_guidance, *, force_domain_change=False, **_kwargs):
            seen.append(state.id)
            assert force_domain_change is True
            assert semantic_guidance == (
                "SAT restart: assumption led to a countermodel (P=false); "
                "choose a different plan from this last valid state."
            )
            proposal = RescueProposal(RESCUE_CANDIDATE, "done")
            evaluation = evaluator(RESCUE_CANDIDATE)
            return AnalogyRescueOutcome(
                completed_proof=evaluation.completed_proof,
                rollouts=(
                    RescueRollout(
                        "restart",
                        state.id,
                        (proposal,),
                        (RescueAttempt(proposal, evaluation.result),),
                        RescueTermination.COMPLETE,
                    ),
                ),
            )

    result = _run_incremental(
        _context(Verifier(), analogy_enabled=True, rescue=Rescue(), ranker=Ranker()),
        analogy=True,
    )

    assert result.status is SearchStatus.COMPLETE
    assert seen == ["middle"]
    event = result.semantic_recoveries[0]
    assert event.trigger_state_id == "middle"
    assert event.result is SemanticResult.COUNTERMODEL
    assert event.countermodel == (("P", False),)
    assert event.rescue_state_id == "middle"
    assert event.abandoned_tactics == ("assumption",)
    assert result.rescue_rollouts[0].semantic_event_id == event.event_id


def test_false_path_walks_multiple_levels_and_orders_abandoned_tactics():
    false_middle = SymbolicState("false-middle", "P : Prop\n⊢ P", "lean4")
    false_leaf = SymbolicState("false-leaf", "P Q : Prop\n⊢ P", "lean4")

    class Ranker:
        def rank(self, state, _goal):
            source = {
                "start": "apply h",
                "false-middle": "assumption",
            }.get(state.id, "rfl")
            return (TacticCandidate(source, 1.0, CandidateOrigin.ORDINARY),)

    class Verifier:
        def run_tactic(self, state, candidate):
            if candidate.origin is CandidateOrigin.ANALOGICAL:
                return TacticResult(candidate, TacticStatus.COMPLETE, proof="rescued")
            successor = {
                "start": false_middle,
                "false-middle": false_leaf,
            }.get(state.id)
            if successor is None:
                return TacticResult(candidate, TacticStatus.ERROR)
            return TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)

    class Rescue:
        def search(self, state, _goal, _attempts, evaluator, _cancellation, semantic_guidance, **_kwargs):
            assert state.id == "start"
            assert semantic_guidance.startswith("SAT restart: apply h led")
            proposal = RescueProposal(RESCUE_CANDIDATE, "done")
            evaluation = evaluator(RESCUE_CANDIDATE)
            return AnalogyRescueOutcome(
                completed_proof=evaluation.completed_proof,
                rollouts=(
                    RescueRollout(
                        "restart-root",
                        state.id,
                        (proposal,),
                        (RescueAttempt(proposal, evaluation.result),),
                        RescueTermination.COMPLETE,
                    ),
                ),
            )

    result = _run_incremental(
        _context(Verifier(), analogy_enabled=True, rescue=Rescue(), ranker=Ranker()),
        analogy=True,
    )

    event = result.semantic_recoveries[0]
    assert event.rescue_state_id == "start"
    assert event.abandoned_tactics == ("apply h",)


def test_false_root_terminates_as_semantic_failure_without_analogy():
    false_root = SymbolicState("root", "P : Prop\n⊢ P", "lean4")

    class Verifier:
        def run_tactic(self, _state, candidate):
            return TacticResult(candidate, TacticStatus.ERROR)

    result = _run_incremental(
        _context(Verifier(), analogy_enabled=True), initial=false_root
    )

    assert result.status is SearchStatus.SEMANTIC_FAILURE
    assert result.rescue_rollouts == ()
    assert result.semantic_recoveries[0].rescue_state_id is None


def test_semantic_parse_error_terminates_with_explicit_event():
    malformed = SymbolicState("malformed", "not a Lean goal", "lean4")

    class Verifier:
        def run_tactic(self, _state, candidate):
            return TacticResult(candidate, TacticStatus.ERROR)

    result = _run_incremental(_context(Verifier()), initial=malformed)

    assert result.status is SearchStatus.SEMANTIC_FAILURE
    assert result.semantic_recoveries[0].result is None
    assert "goal" in result.semantic_recoveries[0].error


def test_semantic_assessments_are_cached_while_resolving_sibling_paths():
    left = SymbolicState("left", "left", "lean4")
    right = SymbolicState("right", "right", "lean4")

    class Ranker:
        def rank(self, state, _goal):
            if state.id == "start":
                return (
                    TacticCandidate("assumption", 1.0, CandidateOrigin.ORDINARY),
                    TacticCandidate("rfl", 0.9, CandidateOrigin.ORDINARY),
                )
            return (CANDIDATE,)

    class Verifier:
        def run_tactic(self, state, candidate):
            if state.id == "start":
                successor = left if candidate.source == "assumption" else right
                return TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)
            return TacticResult(candidate, TacticStatus.ERROR)

    class Checker:
        def __init__(self):
            self.calls = []

        def assess(self, state):
            self.calls.append(state.id)
            if state.id == "start":
                return SemanticAssessment(state.id, SemanticResult.VALID)
            return SemanticAssessment(
                state.id, SemanticResult.COUNTERMODEL, ((state.id, False),), 0
            )

    checker = Checker()
    result = _run_incremental(
        _context(
            Verifier(),
            ranker=Ranker(),
            ordinary_width=2,
            semantic_checker=checker,
            max_expansions=5,
        ),
    )

    assert result.status is SearchStatus.EXHAUSTED
    assert checker.calls == ["left", "right", "start"]
    assert [event.rescue_state_id for event in result.semantic_recoveries] == [
        "start",
    ]


def test_countermodel_refuted_open_successor_is_pruned_before_frontier_expansion():
    calls = []
    false_state = SymbolicState("false", "P Q : Prop\nh : P\n⊢ Q", "lean4")

    class Verifier:
        def run_tactic(self, state, candidate):
            calls.append((state.id, candidate.source))
            if state.id == "start":
                return TacticResult(candidate, TacticStatus.ACCEPTED, state=false_state)
            raise AssertionError("a refuted successor must never be expanded")

    result = _run_incremental(_context(Verifier()))

    assert calls == [("start", "exact h")]
    assert result.status is SearchStatus.EXHAUSTED
    assert len(result.semantic_prunes) == 1
    assert result.semantic_prunes[0].successor_state_id == "false"
    assert result.semantic_prunes[0].tactic == "exact h"
    assert result.ordinary_expansions[0].discarded[0].reason.value == "semantic_refuted"


def test_unknown_open_successor_remains_on_frontier():
    quantified = SymbolicState("quantified", "P : α → Prop\n⊢ ∀ x, P x", "lean4")
    calls = []

    class Verifier:
        def run_tactic(self, state, candidate):
            calls.append(state.id)
            if state.id == "start":
                return TacticResult(candidate, TacticStatus.ACCEPTED, state=quantified)
            return TacticResult(candidate, TacticStatus.COMPLETE, proof="done")

    result = _run_incremental(_context(Verifier()))

    assert result.status is SearchStatus.COMPLETE
    assert calls == ["start", "quantified"]
    assert result.semantic_prunes == ()


def test_cross_countermodels_force_disjunction_destructuring_before_analogy():
    root = SymbolicState(
        "root",
        "P Q R : Prop\nhPQ : P ∨ Q\n⊢ R",
        "lean4",
    )
    left_false = SymbolicState("left-false", "P Q R : Prop\nh : P\n⊢ R", "lean4")
    right_false = SymbolicState("right-false", "P Q R : Prop\nh : Q\n⊢ R", "lean4")
    split = SymbolicState("split", "case inl\nP Q R : Prop\nhP : P\n⊢ R\n\ncase inr\nP Q R : Prop\nhQ : Q\n⊢ R", "lean4")
    attempted = []

    class Ranker:
        def rank(self, state, _goal):
            if state.id == "root":
                return (
                    TacticCandidate("assumption", 1.0, CandidateOrigin.ORDINARY),
                    TacticCandidate("rfl", 0.9, CandidateOrigin.ORDINARY),
                )
            return (CANDIDATE,)

    class Verifier:
        def run_tactic(self, state, candidate):
            attempted.append((state.id, candidate.source))
            if state.id == "root" and candidate.source == "assumption":
                return TacticResult(candidate, TacticStatus.ACCEPTED, state=left_false)
            if state.id == "root" and candidate.source == "rfl":
                return TacticResult(candidate, TacticStatus.ACCEPTED, state=right_false)
            if state.id == "root" and candidate.source == "rcases hPQ with hP | hQ":
                return TacticResult(candidate, TacticStatus.ACCEPTED, state=split)
            if state.id == "split":
                return TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
            raise AssertionError((state.id, candidate.source))

    class Checker:
        def assess(self, state):
            if state.id == "left-false":
                return SemanticAssessment(
                    state.id,
                    SemanticResult.COUNTERMODEL,
                    (("P", True), ("Q", False), ("R", False)),
                    0,
                )
            if state.id == "right-false":
                return SemanticAssessment(
                    state.id,
                    SemanticResult.COUNTERMODEL,
                    (("P", False), ("Q", True), ("R", False)),
                    0,
                )
            return SemanticAssessment(state.id, SemanticResult.VALID)

    result = _run_incremental(
        _context(
            Verifier(),
            ranker=Ranker(),
            ordinary_width=2,
            semantic_checker=Checker(),
        ),
        root,
    )

    assert result.status is SearchStatus.COMPLETE
    assert ("root", "rcases hPQ with hP | hQ") in attempted
    assert len(result.forced_moves) == 1
    assert result.forced_moves[0].hypothesis == "hPQ"


def test_incremental_search_honors_cancellation_before_generation():
    class Cancelled:
        def is_cancelled(self):
            return True

    context = replace(_context(object()), cancellation=Cancelled())

    result = _run_incremental(context)

    assert result.status is SearchStatus.CANCELLED
    assert result.ordinary_expansions == ()


def test_incremental_search_honors_expansion_budget_before_next_generation():
    successor = SymbolicState("next", "P : Prop\nh : P\n⊢ P", "lean4")

    class Verifier:
        def run_tactic(self, state, candidate):
            assert state.id == "start"
            return TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)

    result = _run_incremental(_context(Verifier(), max_expansions=1))

    assert result.status is SearchStatus.BUDGET_EXHAUSTED
    assert len(result.ordinary_expansions) == 1


def test_incremental_search_records_no_progress_discard():
    successor = SymbolicState("next", "P Q : Prop\nh : P\n⊢ P", "lean4")

    class Verifier:
        def run_tactic(self, _state, candidate):
            return TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)

    class RejectProgress:
        def accepts(self, _before, _after, _goal):
            return False

    context = replace(_context(Verifier()), progress_policy=RejectProgress())

    result = _run_incremental(context)

    assert result.status is SearchStatus.EXHAUSTED
    assert result.ordinary_expansions[0].discarded[0].reason.value == "no_progress"


def test_incremental_search_records_an_empty_candidate_pool_as_exhausted():
    class EmptyRanker:
        def rank(self, _state, _goal, **_kwargs):
            return ()

    result = _run_incremental(_context(object(), ranker=EmptyRanker()))

    assert result.status is SearchStatus.EXHAUSTED
    assert len(result.ordinary_expansions) == 1
    assert result.ordinary_expansions[0].candidate_pool == ()


def test_all_refuted_disjunct_choices_skip_trailing_candidates():
    root = SymbolicState("root", "P Q : Prop\n⊢ P ∨ Q", "lean4")
    left_false = SymbolicState("left-false", "P Q : Prop\n⊢ P", "lean4")
    right_false = SymbolicState("right-false", "P Q : Prop\n⊢ Q", "lean4")
    attempted = []

    class Ranker:
        def rank(self, _state, _goal, **_kwargs):
            return tuple(
                TacticCandidate(source, 1.0, CandidateOrigin.ORDINARY)
                for source in ("left", "right", "assumption", "rfl")
            )

    class Verifier:
        def run_tactic(self, _state, candidate):
            attempted.append(candidate.source)
            successor = left_false if candidate.source == "left" else right_false
            return TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)

    class Checker:
        def assess(self, state):
            if state.id == "left-false":
                return SemanticAssessment(state.id, SemanticResult.COUNTERMODEL, (("P", False),), 0)
            if state.id == "right-false":
                return SemanticAssessment(state.id, SemanticResult.COUNTERMODEL, (("Q", False),), 0)
            return SemanticAssessment(state.id, SemanticResult.VALID)

    result = _run_incremental(
        _context(Verifier(), ranker=Ranker(), ordinary_width=4, semantic_checker=Checker()),
        initial=root,
    )

    assert attempted == ["left", "right"]
    assert [item.candidate.source for item in result.ordinary_expansions[0].skipped_candidates] == [
        "assumption",
        "rfl",
    ]
    assert {item.reason for item in result.ordinary_expansions[0].skipped_candidates} == {
        "early_all_disjunct_recovery"
    }
    assert result.status is SearchStatus.EXHAUSTED


def test_accepted_forced_move_does_not_suppress_stuck_only_analogy():
    root = SymbolicState("root", "P Q R : Prop\nhPQ : P ∨ Q\n⊢ R", "lean4")
    left_false = SymbolicState("left-false", "P Q R : Prop\n⊢ R", "lean4")
    right_false = SymbolicState("right-false", "P Q R : Prop\n⊢ R", "lean4")
    split = SymbolicState("split", "P Q R : Prop\nhP : P\n⊢ R", "lean4")
    rescue_calls = []

    class Ranker:
        def rank(self, state, _goal, **_kwargs):
            if state.id == "root":
                return tuple(TacticCandidate(item, 1.0, CandidateOrigin.ORDINARY) for item in ("assumption", "rfl"))
            return (CANDIDATE,)

    class Verifier:
        def run_tactic(self, state, candidate):
            if state.id == "root" and candidate.source == "assumption":
                return TacticResult(candidate, TacticStatus.ACCEPTED, state=left_false)
            if state.id == "root" and candidate.source == "rfl":
                return TacticResult(candidate, TacticStatus.ACCEPTED, state=right_false)
            if state.id == "root" and candidate.source == "rcases hPQ with hP | hQ":
                return TacticResult(candidate, TacticStatus.ACCEPTED, state=split)
            return TacticResult(candidate, TacticStatus.COMPLETE, proof="done")

    class Checker:
        def assess(self, state):
            if state.id in {"left-false", "right-false"}:
                values = (("P", True), ("Q", False), ("R", False)) if state.id == "left-false" else (("P", False), ("Q", True), ("R", False))
                return SemanticAssessment(state.id, SemanticResult.COUNTERMODEL, values, 0)
            return SemanticAssessment(state.id, SemanticResult.VALID)

    class Rescue:
        def search(self, state, *_args, **_kwargs):
            rescue_calls.append(state.id)
            return AnalogyRescueOutcome()

    result = _run_incremental(
        _context(Verifier(), analogy_enabled=True, rescue=Rescue(), ranker=Ranker(),
                 ordinary_width=2, semantic_checker=Checker()),
        root,
        analogy=True,
    )

    assert rescue_calls == ["root"]
    assert result.status is SearchStatus.COMPLETE


def test_failed_forced_move_is_shared_with_analogy_duplicate_ledger():
    root = SymbolicState("root", "P Q R : Prop\nhPQ : P ∨ Q\n⊢ R", "lean4")
    left_false = SymbolicState("left-false", "P Q R : Prop\nh : P\n⊢ R", "lean4")
    right_false = SymbolicState("right-false", "P Q R : Prop\nh : Q\n⊢ R", "lean4")
    submitted = []

    class Ranker:
        def rank(self, _state, _goal, **_kwargs):
            return (
                TacticCandidate("assumption", 1.0, CandidateOrigin.ORDINARY),
                TacticCandidate("rfl", 0.9, CandidateOrigin.ORDINARY),
            )

    class Verifier:
        def run_tactic(self, _state, candidate):
            submitted.append(candidate.source)
            if candidate.source == "assumption":
                return TacticResult(candidate, TacticStatus.ACCEPTED, state=left_false)
            if candidate.source == "rfl":
                return TacticResult(candidate, TacticStatus.ACCEPTED, state=right_false)
            return TacticResult(candidate, TacticStatus.ERROR, diagnostics=("forced failed",))

    class Checker:
        def assess(self, state):
            if state.id == "left-false":
                return SemanticAssessment(state.id, SemanticResult.COUNTERMODEL, (("P", True), ("Q", False), ("R", False)), 0)
            if state.id == "right-false":
                return SemanticAssessment(state.id, SemanticResult.COUNTERMODEL, (("P", False), ("Q", True), ("R", False)), 0)
            return SemanticAssessment(state.id, SemanticResult.VALID)

    class Rescue:
        def search(self, _state, _goal, attempts, _evaluator, _cancellation, _guidance, **_kwargs):
            assert [item.candidate.source for item in attempts] == [
                "assumption",
                "rfl",
                "rcases hPQ with hP | hQ",
            ]
            return AnalogyRescueOutcome()

    result = _run_incremental(
        _context(
            Verifier(),
            analogy_enabled=True,
            rescue=Rescue(),
            ranker=Ranker(),
            ordinary_width=2,
            semantic_checker=Checker(),
        ),
        root,
        analogy=True,
    )

    assert submitted.count("rcases hPQ with hP | hQ") == 1
    assert result.forced_moves[0].outcome == "rejected"


def test_unknown_stuck_state_skips_rescue_and_keeps_other_frontier_work():
    root = SymbolicState("root", "P : Prop\n⊢ P", "lean4")
    unknown = SymbolicState("unknown", "n : Nat\n⊢ n = n", "lean4")
    alternative = SymbolicState("alternative", "P : Prop\nh : P\n⊢ P", "lean4")
    calls = []

    class Ranker:
        def rank(self, state, _goal, **_kwargs):
            if state.id == "root":
                return (
                    TacticCandidate("assumption", 1.0, CandidateOrigin.ORDINARY),
                    TacticCandidate("rfl", 0.9, CandidateOrigin.ORDINARY),
                )
            return (CANDIDATE,)

    class Verifier:
        def run_tactic(self, state, candidate):
            calls.append(state.id)
            if state.id == "root":
                successor = unknown if candidate.source == "assumption" else alternative
                return TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)
            if state.id == "unknown":
                return TacticResult(candidate, TacticStatus.ERROR)
            return TacticResult(candidate, TacticStatus.COMPLETE, proof="exact h")

    result = _run_incremental(
        _context(Verifier(), ranker=Ranker(), ordinary_width=2, max_expansions=4),
        initial=root,
    )

    assert result.status is SearchStatus.COMPLETE
    assert calls == ["root", "root", "unknown", "alternative"]
    assert result.semantic_recoveries[-1].result is SemanticResult.UNKNOWN


def test_checker_exception_becomes_semantic_failure():
    class Verifier:
        def run_tactic(self, _state, candidate):
            return TacticResult(candidate, TacticStatus.ERROR)

    class Checker:
        def assess(self, _state):
            raise SemanticCheckError("checker exploded")

    result = _run_incremental(_context(Verifier(), semantic_checker=Checker()))

    assert result.status is SearchStatus.SEMANTIC_FAILURE
    assert result.semantic_recoveries[0].error == "checker exploded"
