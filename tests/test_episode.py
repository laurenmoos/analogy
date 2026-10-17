from analogy.records import (
    CandidateOrigin,
    SearchStatus,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)
from analogy.solver.rescue import (
    GenericRescueOutcome,
    PolicyActionRecord,
    RescueAttempt,
    RescueProposal,
    RescueRollout,
    RescueTermination,
)
from analogy.solver.search import (
    AcceptNovelState,
    NeverCancelled,
    ObservableStucknessPolicy,
    CanonicalStateFingerprinter,
    WhitespaceTacticCanonicalizer,
)
from analogy.solver.diversity import DiversityObservation


START = SymbolicState("start", "P", "lean4")
GOAL = SymbolicState("goal", "P", "lean4")
CANDIDATE = TacticCandidate("exact h", 1.0, CandidateOrigin.ORDINARY)
RESCUE_CANDIDATE = TacticCandidate(
    "exact rescued", 1.0, CandidateOrigin.ANALOGICAL
)


class _Ranker:
    def rank(self, _state, _goal):
        return (CANDIDATE,)


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
):
    from analogy.solver.episode import EpisodeContext

    return EpisodeContext(
        tactic_ranker=ranker or _Ranker(),
        verifier=verifier,
        stuckness_policy=ObservableStucknessPolicy(),
        state_fingerprinter=CanonicalStateFingerprinter(),
        tactic_canonicalizer=WhitespaceTacticCanonicalizer(),
        progress_policy=AcceptNovelState(),
        cancellation=NeverCancelled(),
        ordinary_width=ordinary_width,
        max_expansions=4,
        analogy_enabled=analogy_enabled,
        analogy_fallback=rescue or _FailIfCalledFallback(),
        state_diversity=diversity,
    )


def test_episode_returns_immediately_after_ordinary_completion():
    from analogy.solver.episode import run_episode

    class Verifier:
        def run_tactic(self, _state, candidate):
            return TacticResult(candidate, TacticStatus.COMPLETE, proof="exact h")

    result = run_episode(_context(Verifier()), START, GOAL)

    assert result.status is SearchStatus.COMPLETE
    assert result.proof == "exact h"
    assert len(result.ordinary_expansions) == 1


def test_episode_calls_rescue_only_after_ordinary_tactics_are_stuck():
    from analogy.solver.episode import run_episode

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
        def search(self, state, _goal, _pool, _attempts, evaluator, _cancellation):
            events.append("rescue")
            proposal = RescueProposal(RESCUE_CANDIDATE, "solved")
            evaluation = evaluator(RESCUE_CANDIDATE)
            attempt = RescueAttempt(proposal, evaluation.result)
            rollout = RescueRollout(
                "rollout-1",
                state.id,
                PolicyActionRecord(0, 0.0, 0.0, 0.0),
                (proposal,),
                (attempt,),
                RescueTermination.COMPLETE,
            )
            return GenericRescueOutcome(
                completed_proof=evaluation.completed_proof,
                rollouts=(rollout,),
            )

    result = run_episode(
        _context(Verifier(), analogy_enabled=True, rescue=Rescue()), START, GOAL
    )

    assert result.status is SearchStatus.COMPLETE
    assert result.proof == "exact rescued"
    assert events == ["ordinary", "rescue", "analogical"]


def test_disabled_analogy_never_calls_fallback_when_conventional_tactic_is_stuck():
    from analogy.solver.episode import run_episode

    class Verifier:
        def run_tactic(self, _state, candidate):
            return TacticResult(candidate, TacticStatus.ERROR)

    result = run_episode(_context(Verifier(), analogy_enabled=False), START, GOAL)

    assert result.status is SearchStatus.EXHAUSTED
    assert result.rescue_rollouts == ()


def test_enabled_analogy_does_not_call_fallback_when_conventional_tactic_succeeds():
    from analogy.solver.episode import run_episode

    class Verifier:
        def run_tactic(self, _state, candidate):
            return TacticResult(candidate, TacticStatus.COMPLETE, proof="exact h")

    result = run_episode(_context(Verifier(), analogy_enabled=True), START, GOAL)

    assert result.status is SearchStatus.COMPLETE


def test_analogy_successor_returns_to_the_shared_frontier():
    from analogy.solver.episode import run_episode

    events = []
    successor = SymbolicState("rescued", "Q", "lean4")

    class Ranker:
        def rank(self, state, _goal):
            source = "finish" if state.id == "rescued" else "bad"
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
        def search(self, state, _goal, _pool, _attempts, evaluator, _cancellation):
            evaluation = evaluator(RESCUE_CANDIDATE)
            proposal = RescueProposal(RESCUE_CANDIDATE, "Q")
            attempt = RescueAttempt(proposal, evaluation.result)
            rollout = RescueRollout(
                "rollout-progress",
                state.id,
                None,
                (proposal,),
                (attempt,),
                RescueTermination.PROGRESS,
            )
            return GenericRescueOutcome(
                successor=evaluation.successor, rollouts=(rollout,)
            )

    result = run_episode(
        _context(
            Verifier(), analogy_enabled=True, rescue=Rescue(), ranker=Ranker()
        ),
        START,
        GOAL,
    )

    assert result.status is SearchStatus.COMPLETE
    assert result.proof == "by finish"
    assert events == [
        ("start", "ordinary"),
        ("start", "analogical"),
        ("rescued", "ordinary"),
    ]


def test_episode_uses_embedding_novelty_to_explore_distinct_states_first():
    from analogy.solver.episode import run_episode

    first = TacticCandidate("first", 1.0, CandidateOrigin.ORDINARY)
    diverse = TacticCandidate("diverse", 1.0, CandidateOrigin.ORDINARY)
    events = []

    class Ranker:
        def rank(self, state, _goal):
            return (first, diverse) if state.id == "start" else (first,)

    class Verifier:
        def run_tactic(self, state, candidate):
            events.append(state.id)
            if state.id == "start":
                next_id = "near" if candidate.source == "first" else "far"
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

    result = run_episode(
        _context(
            Verifier(), ranker=Ranker(), diversity=Diversity(), ordinary_width=2
        ),
        START,
        GOAL,
    )

    assert result.proof == "diverse"
    assert events == ["start", "start", "far"]
    assert [item.state_id for item in result.diversity_observations] == [
        "start",
        "near",
        "far",
    ]


def test_episode_discards_same_proof_state_even_when_generated_id_differs():
    from analogy.solver.episode import run_episode

    class Verifier:
        def run_tactic(self, _state, candidate):
            return TacticResult(
                candidate,
                TacticStatus.ACCEPTED,
                state=SymbolicState("different-id", START.expression, "lean4"),
            )

    result = run_episode(_context(Verifier()), START, GOAL)

    first = result.ordinary_expansions[0]
    assert first.successors == ()
    assert first.discarded[0].reason.value == "repeated_state"
