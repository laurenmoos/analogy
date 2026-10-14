from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from analogy.daydream import CuriosityOutcome
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
