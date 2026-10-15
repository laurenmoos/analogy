from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from analogy.daydream import CuriosityOutcome
from analogy.rescue import (
    GenericRescueOutcome,
    PolicyActionRecord,
    RescueAttempt,
    RescueProposal,
    RescueRollout,
    RescueTermination,
)
from analogy.records import SymbolicState
from analogy.loop import (
    AcceptNovelState, CandidateOrigin, DiscardReason, DiscardedSuccessor,
    ExpansionOutcome, LocalRescueSearch, NeverCancelled,
    ObservableStucknessPolicy, SearchNode, SearchResult, SearchStatus,
    StateIdFingerprinter, StuckReason, StucknessDecision, TacticCandidate,
    TacticResult, TacticStatus, WhitespaceTacticCanonicalizer, _PriorityFrontier,
)


def state(identifier: str) -> SymbolicState:
    return SymbolicState(identifier, f"goal {identifier}", "lean4")


def candidate(source: str = "intro", score: float = 0.8) -> TacticCandidate:
    return TacticCandidate(source, score, CandidateOrigin.ORDINARY)


def test_search_records_preserve_a_readable_trace() -> None:
    tactic = candidate()
    result = TacticResult(tactic, TacticStatus.ACCEPTED, state("b"))
    node = SearchNode(state("a"), 1.0)
    successor = SearchNode(state("b"), 0.8, "a", "intro")
    discarded = DiscardedSuccessor(result, DiscardReason.REPEATED_STATE)
    expansion = ExpansionOutcome(node, (result,), (successor,), (discarded,), candidate_pool=(tactic,))
    decision = StucknessDecision("a", True, (StuckReason.CANDIDATES_EXHAUSTED,))
    search = SearchResult(SearchStatus.EXHAUSTED, None, (expansion,), (decision,))
    assert search.ordinary_expansions[0].candidate_pool == (tactic,)
    with pytest.raises(FrozenInstanceError):
        tactic.source = "exact h"  # type: ignore[misc]


@pytest.mark.parametrize("score", [True, math.nan, math.inf, -0.01, 1.01])
def test_tactic_candidate_requires_a_probability(score: object) -> None:
    with pytest.raises(ValueError, match="score"):
        TacticCandidate("intro", score, CandidateOrigin.ORDINARY)  # type: ignore[arg-type]


@pytest.mark.parametrize("score", [True, math.nan, math.inf, -0.01, 1.01])
def test_search_node_requires_a_probability(score: object) -> None:
    with pytest.raises(ValueError, match="score"):
        SearchNode(state("a"), score)  # type: ignore[arg-type]


def test_tactic_result_enforces_status_payloads() -> None:
    tactic = candidate()
    with pytest.raises(ValueError, match="state"):
        TacticResult(tactic, TacticStatus.ACCEPTED)
    with pytest.raises(ValueError, match="proof"):
        TacticResult(tactic, TacticStatus.COMPLETE)
    with pytest.raises(ValueError, match="must not"):
        TacticResult(tactic, TacticStatus.ERROR, state("b"))


def test_only_accepted_results_can_be_discarded_successors() -> None:
    with pytest.raises(ValueError, match="accepted"):
        DiscardedSuccessor(TacticResult(candidate(), TacticStatus.ERROR), DiscardReason.NO_PROGRESS)


def test_stuckness_decision_requires_reasons_exactly_when_stuck() -> None:
    with pytest.raises(ValueError, match="reasons"):
        StucknessDecision("a", True, ())
    with pytest.raises(ValueError, match="reasons"):
        StucknessDecision("a", False, (StuckReason.NO_PROGRESS,))


def test_search_result_matches_its_status() -> None:
    with pytest.raises(ValueError, match="proof"):
        SearchResult(SearchStatus.COMPLETE, None)
    with pytest.raises(ValueError, match="proof"):
        SearchResult(SearchStatus.EXHAUSTED, "by exact h")


def outcome_with(*results: TacticResult, discarded=()) -> ExpansionOutcome:
    return ExpansionOutcome(SearchNode(state("a"), 1.0), results, discarded=discarded)


def test_observable_stuckness_reports_errors_repetition_and_no_progress() -> None:
    error = TacticResult(candidate(), TacticStatus.ERROR)
    assert ObservableStucknessPolicy().evaluate(outcome_with(error)).reasons == (
        StuckReason.ALL_TACTICS_ERRORED, StuckReason.CANDIDATES_EXHAUSTED,
    )
    accepted = TacticResult(candidate(), TacticStatus.ACCEPTED, state("b"))
    repeated = DiscardedSuccessor(accepted, DiscardReason.REPEATED_STATE)
    assert ObservableStucknessPolicy().evaluate(outcome_with(accepted, discarded=(repeated,))).reasons == (
        StuckReason.ONLY_REPEATED_STATES, StuckReason.CANDIDATES_EXHAUSTED,
    )
    stalled = DiscardedSuccessor(accepted, DiscardReason.NO_PROGRESS)
    assert StuckReason.NO_PROGRESS in ObservableStucknessPolicy().evaluate(
        outcome_with(accepted, discarded=(stalled,))
    ).reasons


def test_default_identity_and_control_policies_are_explicit() -> None:
    assert StateIdFingerprinter().fingerprint(state("a")) == "a"
    assert WhitespaceTacticCanonicalizer().canonicalize("  exact   h  ") == "exact h"
    assert AcceptNovelState().accepts(state("a"), state("b"), state("goal"))
    assert not NeverCancelled().is_cancelled()


class FakeTacticRanker:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []

    def rank(self, current, goal):
        self.calls.append(current.id)
        return self.candidates.get(current.id, ())


class FakeVerifier:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def run_tactic(self, current, tactic):
        key = (current.id, tactic.source)
        self.calls.append(key)
        return self.results[key]


class FailingCuriosity:
    def search(self, *args):
        raise AssertionError("curiosity must not run")


class RecordingCuriosity:
    def __init__(self, outcome: CuriosityOutcome):
        self.outcome = outcome
        self.calls = []

    def search(self, current, goal, tactic_pool, ordinary_attempts, evaluator, cancellation):
        self.calls.append((current, goal, tactic_pool, ordinary_attempts, evaluator, cancellation))
        return self.outcome


class EvaluatingCuriosity(RecordingCuriosity):
    def __init__(self, source: str):
        super().__init__(CuriosityOutcome())
        self.source = source
        self.evaluations = []

    def search(self, current, goal, tactic_pool, ordinary_attempts, evaluator, cancellation):
        self.calls.append((current, goal, tactic_pool, ordinary_attempts, evaluator, cancellation))
        selected = next(item for item in tactic_pool if item.source == self.source)
        evaluation = evaluator(selected)
        self.evaluations.append(evaluation)
        return CuriosityOutcome(evaluation.completed_proof, evaluation.successor)


def rollout_for(selected, evaluation):
    proposal = RescueProposal(selected, "predicted next goals")
    attempt = RescueAttempt(proposal, evaluation.result, evaluation.discarded)
    if evaluation.completed_proof is not None:
        termination = RescueTermination.COMPLETE
    elif evaluation.successor is not None:
        termination = RescueTermination.PROGRESS
    elif evaluation.result.status is TacticStatus.ERROR:
        termination = RescueTermination.REJECTED
    else:
        termination = RescueTermination.EXHAUSTED
    return RescueRollout(
        "rollout-1",
        "start",
        PolicyActionRecord(7, 0.0, 0.0, 0.0),
        (proposal,),
        (attempt,),
        termination,
    )


class ProposingRolloutRescue:
    def __init__(self, tactic_source: str):
        self.tactic_source = tactic_source
        self.calls = []

    def search(self, current, goal, tactic_pool, ordinary_attempts, evaluator, cancellation):
        selected = TacticCandidate(
            self.tactic_source, 1.0, CandidateOrigin.ANALOGICAL
        )
        evaluation = evaluator(selected)
        self.calls.append((current, tactic_pool, evaluation))
        rollout = rollout_for(selected, evaluation)
        return GenericRescueOutcome(
            completed_proof=evaluation.completed_proof,
            successor=evaluation.successor,
            rollouts=(rollout,),
        )


class FixedRolloutRescue:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def search(self, *args):
        self.calls.append(args)
        return self.outcome


class RejectAllProgress:
    def accepts(self, before, after, goal):
        return False


def make_search(tactic_ranker, verifier, **overrides) -> LocalRescueSearch:
    arguments = {
        "tactic_ranker": tactic_ranker, "verifier": verifier,
        "stuckness_policy": ObservableStucknessPolicy(),
        "curiosity_search": FailingCuriosity(),
        "state_fingerprinter": StateIdFingerprinter(),
        "tactic_canonicalizer": WhitespaceTacticCanonicalizer(),
        "progress_policy": AcceptNovelState(), "cancellation": NeverCancelled(),
    }
    arguments.update(overrides)
    return LocalRescueSearch(**arguments)


def test_ordinary_completion_never_invokes_curiosity() -> None:
    exact = candidate("exact h", 0.9)
    verifier = FakeVerifier({("start", "exact h"): TacticResult(exact, TacticStatus.COMPLETE, proof="by exact h")})
    result = make_search(FakeTacticRanker({"start": (exact,)}), verifier).search(state("start"), state("goal"))
    assert result.proof == "by exact h"


def test_generic_rescue_may_verify_tactic_outside_ordinary_pool() -> None:
    ordinary = candidate("bad", 1.0)
    generated = TacticCandidate(
        "exact h", 1.0, CandidateOrigin.ANALOGICAL
    )
    verifier = FakeVerifier(
        {
            ("start", "bad"): TacticResult(ordinary, TacticStatus.ERROR),
            ("start", "exact h"): TacticResult(
                generated, TacticStatus.COMPLETE, proof="by exact h"
            ),
        }
    )

    result = make_search(
        FakeTacticRanker({"start": (ordinary,)}),
        verifier,
        curiosity_search=None,
        rollout_rescue=ProposingRolloutRescue("exact h"),
    ).search(state("start"), state("goal"))

    assert result.status is SearchStatus.COMPLETE
    assert verifier.calls == [("start", "bad"), ("start", "exact h")]
    assert len(result.rescue_rollouts) == 1


def test_disabled_generic_rescue_preserves_complete_search_result() -> None:
    exact = candidate("exact h", 0.9)
    verifier = FakeVerifier(
        {
            ("start", "exact h"): TacticResult(
                exact, TacticStatus.COMPLETE, proof="by exact h"
            )
        }
    )

    result = make_search(
        FakeTacticRanker({"start": (exact,)}), verifier, rollout_rescue=None
    ).search(state("start"), state("goal"))

    expected_expansion = ExpansionOutcome(
        SearchNode(state("start"), 1.0),
        (TacticResult(exact, TacticStatus.COMPLETE, proof="by exact h"),),
        completed_proof="by exact h",
        candidate_pool=(exact,),
    )
    assert result == SearchResult(
        SearchStatus.COMPLETE, "by exact h", (expected_expansion,)
    )


def test_search_rejects_two_rescue_implementations() -> None:
    with pytest.raises(ValueError, match="one rescue"):
        make_search(
            FakeTacticRanker({}),
            FakeVerifier({}),
            curiosity_search=FailingCuriosity(),
            rollout_rescue=FixedRolloutRescue(GenericRescueOutcome()),
        )


def test_generic_rescue_rejects_verifier_result_for_wrong_candidate() -> None:
    ordinary = candidate("bad")
    generated = TacticCandidate("new", 1.0, CandidateOrigin.ANALOGICAL)
    wrong = TacticCandidate("wrong", 1.0, CandidateOrigin.ANALOGICAL)
    verifier = FakeVerifier(
        {
            ("start", "bad"): TacticResult(ordinary, TacticStatus.ERROR),
            ("start", "new"): TacticResult(wrong, TacticStatus.ERROR),
        }
    )
    with pytest.raises(ValueError, match="requested candidate"):
        make_search(
            FakeTacticRanker({"start": (ordinary,)}),
            verifier,
            curiosity_search=None,
            rollout_rescue=ProposingRolloutRescue(generated.source),
        ).search(state("start"), state("goal"))


def test_repeated_generic_successor_is_discarded_and_not_reexpanded() -> None:
    ordinary = candidate("bad")
    generated = TacticCandidate("loop", 1.0, CandidateOrigin.ANALOGICAL)
    verifier = FakeVerifier(
        {
            ("start", "bad"): TacticResult(ordinary, TacticStatus.ERROR),
            ("start", "loop"): TacticResult(
                generated, TacticStatus.ACCEPTED, state("start")
            ),
        }
    )
    rescue = ProposingRolloutRescue("loop")

    result = make_search(
        FakeTacticRanker({"start": (ordinary,)}),
        verifier,
        curiosity_search=None,
        rollout_rescue=rescue,
    ).search(state("start"), state("goal"))

    assert result.status is SearchStatus.EXHAUSTED
    evaluation = rescue.calls[0][2]
    assert evaluation.successor is None
    assert evaluation.discarded.reason is DiscardReason.REPEATED_STATE
    assert len(result.ordinary_expansions) == 1


def test_novel_generic_successor_returns_to_ordinary_frontier() -> None:
    ordinary = candidate("bad")
    generated = TacticCandidate("escape", 1.0, CandidateOrigin.ANALOGICAL)
    finish = candidate("finish", 1.0)
    verifier = FakeVerifier(
        {
            ("start", "bad"): TacticResult(ordinary, TacticStatus.ERROR),
            ("start", "escape"): TacticResult(
                generated, TacticStatus.ACCEPTED, state("rescued")
            ),
            ("rescued", "finish"): TacticResult(
                finish, TacticStatus.COMPLETE, proof="rescued proof"
            ),
        }
    )

    result = make_search(
        FakeTacticRanker({"start": (ordinary,), "rescued": (finish,)}),
        verifier,
        curiosity_search=None,
        rollout_rescue=ProposingRolloutRescue("escape"),
    ).search(state("start"), state("goal"))

    assert result.proof == "rescued proof"
    assert verifier.calls[-1] == ("rescued", "finish")


def test_generic_rescue_never_runs_after_ordinary_progress() -> None:
    progress = candidate("intro h")
    finish = candidate("exact h")
    verifier = FakeVerifier(
        {
            ("start", "intro h"): TacticResult(
                progress, TacticStatus.ACCEPTED, state("next")
            ),
            ("next", "exact h"): TacticResult(
                finish, TacticStatus.COMPLETE, proof="by intro h; exact h"
            ),
        }
    )
    rescue = FixedRolloutRescue(GenericRescueOutcome())

    result = make_search(
        FakeTacticRanker({"start": (progress,), "next": (finish,)}),
        verifier,
        curiosity_search=None,
        rollout_rescue=rescue,
    ).search(state("start"), state("goal"))

    assert result.status is SearchStatus.COMPLETE
    assert rescue.calls == []


class SequenceCancellation:
    def __init__(self, values):
        self.values = iter(values)

    def is_cancelled(self):
        return next(self.values, self.values)


def test_cancellation_before_search_has_no_rescue_trace() -> None:
    result = make_search(
        FakeTacticRanker({}),
        FakeVerifier({}),
        curiosity_search=None,
        rollout_rescue=FixedRolloutRescue(GenericRescueOutcome()),
        cancellation=SequenceCancellation((True,)),
    ).search(state("start"), state("goal"))

    assert result.status is SearchStatus.CANCELLED
    assert result.rescue_rollouts == ()


def test_cancelled_generic_rescue_preserves_completed_rollout_trace() -> None:
    ordinary = candidate("bad")
    rejected = TacticCandidate("also bad", 1.0, CandidateOrigin.ANALOGICAL)
    failed = TacticResult(rejected, TacticStatus.ERROR)
    proposal = RescueProposal(rejected, "still stuck")
    rollout = RescueRollout(
        "r",
        "start",
        PolicyActionRecord(1, 0.0, 0.0, 0.0),
        (proposal,),
        (RescueAttempt(proposal, failed),),
        RescueTermination.REJECTED,
    )
    rescue = FixedRolloutRescue(
        GenericRescueOutcome(rollouts=(rollout,), cancelled=True)
    )
    verifier = FakeVerifier(
        {("start", "bad"): TacticResult(ordinary, TacticStatus.ERROR)}
    )

    result = make_search(
        FakeTacticRanker({"start": (ordinary,)}),
        verifier,
        curiosity_search=None,
        rollout_rescue=rescue,
    ).search(state("start"), state("goal"))

    assert result.status is SearchStatus.CANCELLED
    assert result.rescue_rollouts == (rollout,)


def test_ordinary_novel_successor_returns_to_frontier_without_curiosity() -> None:
    intro, ignored, exact = candidate("intro h", 0.8), candidate("simp", 0.7), candidate("exact h", 0.9)
    verifier = FakeVerifier({
        ("start", "intro h"): TacticResult(intro, TacticStatus.ACCEPTED, state("introduced")),
        ("introduced", "exact h"): TacticResult(exact, TacticStatus.COMPLETE, proof="by intro h; exact h"),
    })
    ranker = FakeTacticRanker({"start": (intro, ignored), "introduced": (exact,)})
    result = make_search(ranker, verifier).search(state("start"), state("goal"))
    assert result.status is SearchStatus.COMPLETE
    assert verifier.calls == [("start", "intro h"), ("introduced", "exact h")]


def test_priority_frontier_uses_score_then_insertion_order() -> None:
    frontier = _PriorityFrontier()
    frontier.push(SearchNode(state("first"), 0.5)); frontier.push(SearchNode(state("high"), 0.9)); frontier.push(SearchNode(state("second"), 0.5))
    assert [frontier.pop_best().state.id for _ in range(3)] == ["high", "first", "second"]


def test_ordinary_path_attempts_only_top_candidate_and_preserves_pool() -> None:
    first, second = candidate("first", 0.9), candidate("second", 0.8)
    verifier = FakeVerifier({("start", "first"): TacticResult(first, TacticStatus.ERROR)})
    curiosity = RecordingCuriosity(CuriosityOutcome())
    result = make_search(FakeTacticRanker({"start": (first, second)}), verifier, curiosity_search=curiosity).search(state("start"), state("goal"))
    assert verifier.calls == [("start", "first")]
    assert result.ordinary_expansions[0].candidate_pool == (first, second)
    assert curiosity.calls[0][2] == (first, second)


def test_policy_pool_is_canonicalized_once_in_original_order() -> None:
    first, duplicate, second = candidate("  simp ", 0.9), candidate("simp", 0.2), candidate("aesop", 0.8)
    verifier = FakeVerifier({("start", "  simp "): TacticResult(first, TacticStatus.ERROR)})
    curiosity = RecordingCuriosity(CuriosityOutcome())
    make_search(FakeTacticRanker({"start": (first, duplicate, second)}), verifier, curiosity_search=curiosity).search(state("start"), state("goal"))
    assert verifier.calls == [("start", "  simp ")]
    assert curiosity.calls[0][2] == (first, second)
    assert curiosity.calls[0][2][0].score == 0.9


def test_empty_policy_pool_does_not_start_curiosity() -> None:
    curiosity = RecordingCuriosity(CuriosityOutcome())
    result = make_search(FakeTacticRanker({"start": ()}), FakeVerifier({}), curiosity_search=curiosity).search(state("start"), state("goal"))
    assert result.status is SearchStatus.EXHAUSTED
    assert curiosity.calls == []


def test_curiosity_completion_is_returned() -> None:
    first, second = candidate("first", 0.9), candidate("exact h", 0.8)
    verifier = FakeVerifier({
        ("start", "first"): TacticResult(first, TacticStatus.ERROR),
        ("start", "exact h"): TacticResult(second, TacticStatus.COMPLETE, proof="by exact h"),
    })
    result = make_search(FakeTacticRanker({"start": (first, second)}), verifier, curiosity_search=EvaluatingCuriosity("exact h")).search(state("start"), state("goal"))
    assert result.proof == "by exact h"


def test_curiosity_progress_returns_to_ordinary_frontier() -> None:
    first, escape, finish = candidate("first", 0.9), candidate("escape", 0.8), candidate("finish", 1.0)
    verifier = FakeVerifier({
        ("start", "first"): TacticResult(first, TacticStatus.ERROR),
        ("start", "escape"): TacticResult(escape, TacticStatus.ACCEPTED, state("rescued")),
        ("rescued", "finish"): TacticResult(finish, TacticStatus.COMPLETE, proof="rescued proof"),
    })
    ranker = FakeTacticRanker({"start": (first, escape), "rescued": (finish,)})
    result = make_search(ranker, verifier, curiosity_search=EvaluatingCuriosity("escape")).search(state("start"), state("goal"))
    assert result.proof == "rescued proof"
    assert ranker.calls == ["start", "rescued"]


def test_no_progress_curiosity_evaluation_is_traced() -> None:
    first, wander = candidate("first", 0.9), candidate("wander", 0.8)
    verifier = FakeVerifier({
        ("start", "first"): TacticResult(first, TacticStatus.ERROR),
        ("start", "wander"): TacticResult(wander, TacticStatus.ACCEPTED, state("elsewhere")),
    })
    curiosity = EvaluatingCuriosity("wander")
    result = make_search(FakeTacticRanker({"start": (first, wander)}), verifier, curiosity_search=curiosity, progress_policy=RejectAllProgress()).search(state("start"), state("goal"))
    assert result.status is SearchStatus.EXHAUSTED
    assert curiosity.evaluations[0].discarded.reason is DiscardReason.NO_PROGRESS


def test_verifier_must_return_the_requested_candidate() -> None:
    requested, different = candidate("intro"), candidate("simp")
    verifier = FakeVerifier({("start", "intro"): TacticResult(different, TacticStatus.ERROR)})
    with pytest.raises(ValueError, match="requested candidate"):
        make_search(FakeTacticRanker({"start": (requested,)}), verifier).search(state("start"), state("goal"))


class ExplodingCuriosity:
    def search(self, *args):
        raise RuntimeError("curiosity unavailable")


def test_dependency_exceptions_propagate() -> None:
    first = candidate("first")
    verifier = FakeVerifier({("start", "first"): TacticResult(first, TacticStatus.ERROR)})
    with pytest.raises(RuntimeError, match="curiosity unavailable"):
        make_search(FakeTacticRanker({"start": (first,)}), verifier, curiosity_search=ExplodingCuriosity()).search(state("start"), state("goal"))


def test_package_root_exports_local_rescue_api() -> None:
    from analogy import LocalRescueSearch as PublicSearch, SearchResult as PublicResult
    assert PublicSearch is LocalRescueSearch
    assert PublicResult is SearchResult


def test_loop_uses_shared_record_types() -> None:
    from analogy import loop, records
    assert loop.TacticCandidate is records.TacticCandidate
    assert loop.ExpansionOutcome is records.ExpansionOutcome


def test_expansion_records_the_fixed_candidate_pool() -> None:
    first, second = candidate("first", 0.9), candidate("second", 0.8)
    expansion = ExpansionOutcome(SearchNode(state("a"), 1.0), candidate_pool=(first, second))
    assert expansion.candidate_pool == (first, second)


def test_policy_pool_rejects_nonordinary_candidates() -> None:
    analogical = TacticCandidate("invented", 0.9, CandidateOrigin.ANALOGICAL)
    verifier = FakeVerifier({("start", "invented"): TacticResult(analogical, TacticStatus.ERROR)})
    with pytest.raises(ValueError, match="conventional"):
        make_search(
            FakeTacticRanker({"start": (analogical,)}),
            verifier,
            curiosity_search=RecordingCuriosity(CuriosityOutcome()),
        ).search(state("start"), state("goal"))


def test_search_budget_stops_before_an_additional_expansion() -> None:
    first = candidate("intro h", 1.0)
    finish = candidate("exact h", 1.0)
    verifier = FakeVerifier(
        {
            ("start", "intro h"): TacticResult(
                first, TacticStatus.ACCEPTED, state("introduced")
            ),
            ("introduced", "exact h"): TacticResult(
                finish, TacticStatus.COMPLETE, proof="by intro h; exact h"
            ),
        }
    )
    result = make_search(
        FakeTacticRanker({"start": (first,), "introduced": (finish,)}),
        verifier,
        max_expansions=1,
    ).search(state("start"), state("goal"))

    assert result.status is SearchStatus.BUDGET_EXHAUSTED
    assert verifier.calls == [("start", "intro h")]
