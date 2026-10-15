from __future__ import annotations

from dataclasses import replace
from dataclasses import asdict

import pytest
import torch
import json

from analogy.analogy_generation import GeneratedHop, GeneratedTacticPlan
from analogy.analogy_loop import AnalogyLoopRunner
from analogy.analogy_stream import AnalogyHop, AnalogyTacticPlan
from analogy.loop import NeverCancelled
from analogy.proof_views import (
    EmbeddedText,
    EmbeddingProvenance,
    ProofStructure,
    ProofViews,
    TransitionComparison,
)
from analogy.records import (
    CandidateOrigin,
    DiscardReason,
    DiscardedSuccessor,
    Embedding,
    SearchNode,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)
from analogy.rescue import GenerationAttempt, GenerationStatus, TacticEvaluation
from analogy.rl import PolicyAction
from analogy.surprise import StopReason, SurpriseConfig, SurpriseController
from analogy.training import RescueRunWriter


def state(identifier: str, expression: str | None = None) -> SymbolicState:
    return SymbolicState(identifier, expression or f"goal {identifier}", "lean4")


def embedded(text: str) -> EmbeddedText:
    embedding = Embedding("fake", (1.0, 0.0), text_hash=f"hash-{text}")
    return EmbeddedText(
        text,
        embedding,
        EmbeddingProvenance("fake", None, None, f"hash-{text}"),
    )


class Views:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, context):
        self.calls.append(context.state.id)
        structure = ProofStructure(1, '{"kind":"goal"}', ())
        return ProofViews(embedded("language"), embedded("structure"), structure)


class Actions:
    def __call__(self, views, iteration_index):
        del views, iteration_index
        return PolicyAction(torch.zeros(2), torch.tensor(0.0), torch.tensor(0.0))


class Hops:
    def __init__(self, stops=()):
        self.stops = tuple(stops)
        self.contexts = []

    def generate(self, context, action, *, iteration_index, max_attempts=None):
        assert max_attempts is None or max_attempts > 0
        del action
        self.contexts.append(context)
        parent = context.stream.hops[-1].hop_id if context.stream.hops else None
        hop = AnalogyHop(
            f"hop-{iteration_index}", parent, "river", "blocked flow",
            ("pressure seeks a channel",), (("channel", "proof branch"),),
            "open a branch", f"p{iteration_index + 1}", "structural match",
            self.stops[iteration_index] if iteration_index < len(self.stops) else False,
        )
        attempt = GenerationAttempt(iteration_index, "{}", GenerationStatus.VALID)
        return GeneratedHop(hop, (attempt,))


class Plans:
    def __init__(self, predicted):
        self.predicted = tuple(predicted)
        self.contexts = []

    def generate(self, context, action, *, iteration_index, max_attempts=None):
        assert max_attempts is None or max_attempts > 0
        del action
        self.contexts.append(context)
        plan = AnalogyTacticPlan(
            self.predicted[iteration_index],
            (f"step_{iteration_index + 1}",),
            context.active_hop_id,
        )
        attempt = GenerationAttempt(iteration_index + 100, "{}", GenerationStatus.VALID)
        return GeneratedTacticPlan(plan, (attempt,))


class Repairs:
    def __init__(self, tactics=("fixed",), *, predicted=None):
        self.tactics = tuple(tactics)
        self.predicted = predicted
        self.calls = []

    def repair(
        self,
        context,
        action,
        *,
        failed_plan,
        failed_attempts,
        iteration_index,
        candidate_count,
        max_attempts=None,
        cancellation=None,
    ):
        del action, cancellation
        self.calls.append(
            (context, failed_plan, failed_attempts, candidate_count, max_attempts)
        )
        plan = AnalogyTacticPlan(
            self.predicted or failed_plan.predicted_state,
            self.tactics,
            context.active_hop_id,
        )
        generation = GenerationAttempt(
            iteration_index + 200,
            "{}",
            GenerationStatus.VALID,
            repair=True,
        )
        return GeneratedTacticPlan(plan, (generation,))


class Comparisons:
    def __init__(self, surprises):
        self.surprises = tuple(surprises)
        self.calls = []

    def __call__(self, before, predicted, actual):
        self.calls.append((before, predicted, actual))
        surprise = self.surprises[len(self.calls) - 1]
        return TransitionComparison(embedded(predicted), embedded(actual), surprise)


class SequentialEvaluator:
    def __init__(self, actual=("a1", "a2", "a3"), *, discard=False):
        self.actual = tuple(actual)
        self.discard = discard
        self.calls = []

    def __call__(self, candidate):
        from analogy.records import DiscardReason, DiscardedSuccessor

        index = len(self.calls)
        next_state = state(f"s{index + 1}", self.actual[index])
        result = TacticResult(candidate, TacticStatus.ACCEPTED, next_state)
        self.calls.append(candidate.source)
        if self.discard:
            return TacticEvaluation(
                result,
                discarded=DiscardedSuccessor(result, DiscardReason.REPEATED_STATE),
            )
        return TacticEvaluation(result, successor=SearchNode(next_state, 0.5))


class BoundaryCancellation:
    def __init__(self, cancel_on_call: int):
        self.cancel_on_call = cancel_on_call
        self.calls = 0

    def is_cancelled(self):
        self.calls += 1
        return self.calls >= self.cancel_on_call


def loop_runner(
    *, predicted=("p1", "p2", "p3"), surprises=(0.8, 0.1, 0.09),
    stops=(), min_iterations=1, max_iterations=8, threshold=0.2, patience=2,
    max_model_calls=20, max_lean_calls=20,
    iteration_sink=None, tactic_reranker=None, tactic_repairer=None,
):
    return AnalogyLoopRunner(
        proof_view_builder=Views(),
        action_sampler=Actions(),
        analogy_generator=Hops(stops),
        tactic_generator=Plans(predicted),
        transition_comparator=Comparisons(surprises),
        controller=SurpriseController(
            SurpriseConfig(
                min_iterations=min_iterations,
                max_iterations=max_iterations,
                surprise_threshold=threshold,
                surprise_patience=patience,
                min_surprise_improvement=0.0,
                improvement_patience=8,
            )
        ),
        tactics_per_iteration=2,
        max_model_calls=max_model_calls,
        max_lean_calls=max_lean_calls,
        iteration_sink=iteration_sink,
        tactic_reranker=tactic_reranker,
        tactic_repairer=tactic_repairer,
    )


def test_loop_conditions_tactics_and_stops_after_surprise_patience():
    runner = loop_runner()
    outcome = runner.search(
        state("stuck"), state("goal"), (), (), SequentialEvaluator(), NeverCancelled()
    )

    trace = outcome.rollouts[0].analogy_trace
    assert [item.surprise for item in trace.iterations] == [0.8, 0.1, 0.09]
    assert trace.stop_reason is StopReason.SURPRISE_CONVERGED
    assert all(
        item.tactic_plan.motivating_hop_id == item.hop.hop_id
        for item in trace.iterations
    )
    assert len(runner.tactic_generator.contexts[2].analogy.stream.hops) == 3
    assert len(runner.analogy_generator.contexts[2].prior_iterations) == 2
    assert outcome.successor.state.expression == "a3"
    row = asdict(trace.iterations[0])
    assert {
        "iteration_index", "state_before", "views", "hop",
        "hop_generation_attempts", "action", "tactic_plan",
        "tactic_generation_attempts", "attempts", "comparison",
        "surprise_delta", "decision", "model_calls", "lean_calls",
        "elapsed_seconds",
    } <= row.keys()


def test_later_tactics_are_verified_from_the_latest_successor():
    runner = loop_runner()
    base = SequentialEvaluator()
    seen_states = []

    def evaluator(candidate):
        raise AssertionError("closed loop must use the state-aware evaluator")

    def evaluate_at(current, candidate):
        seen_states.append(current.id)
        return base(candidate)

    evaluator.evaluate_at = evaluate_at
    runner.search(
        state("stuck"), state("goal"), (), (), evaluator, NeverCancelled()
    )

    assert seen_states == ["stuck", "s1", "s2"]


def test_optional_reranker_changes_verification_order_and_uses_model_budget():
    seen = []

    def rerank(context, plan):
        seen.append((context.active_hop_id, plan.tactics))
        return replace(plan, tactics=("second", "first"))

    runner = loop_runner(
        predicted=("p1",), max_iterations=1, tactic_reranker=rerank
    )
    evaluator = SequentialEvaluator()
    outcome = runner.search(
        state("stuck"), state("goal"), (), (), evaluator, NeverCancelled()
    )

    trace = outcome.rollouts[0].analogy_trace
    assert seen == [("hop-0", ("step_1",))]
    assert trace.iterations[0].tactic_plan.tactics == ("second", "first")
    assert evaluator.calls == ["second"]
    assert trace.model_calls == 3


def test_loop_flushes_one_complete_jsonl_row_per_iteration(tmp_path):
    writer = RescueRunWriter.create(tmp_path, "trace", {"seed": 7})
    sink = writer.analogy_iteration_sink(
        epoch=0, phase="train", problem_id="p", rollout_id="r"
    )
    runner = loop_runner(iteration_sink=sink)

    runner.search(
        state("stuck"), state("goal"), (), (), SequentialEvaluator(), NeverCancelled()
    )

    rows = [
        json.loads(line)
        for line in (writer.path / "analogy_iterations.jsonl").read_text().splitlines()
    ]
    assert [row["iteration_index"] for row in rows] == [0, 1, 2]
    assert all(row["hop_generation_attempts"] and row["attempts"] for row in rows)
    assert all("views" in row and "decision" in row and "elapsed_seconds" in row for row in rows)


def test_live_sink_receives_each_search_root_rollout_id():
    seen = []

    def sink(rollout_id, iteration):
        seen.append((rollout_id, iteration.iteration_index))

    runner = loop_runner(
        predicted=("p1",), max_iterations=1, iteration_sink=sink
    )
    for root in ("first", "second"):
        runner.search(
            state(root), state("goal"), (), (), SequentialEvaluator(), NeverCancelled()
        )

    assert seen == [("first:analogy-loop", 0), ("second:analogy-loop", 0)]


@pytest.mark.parametrize("cancel_on_call", [1, 2, 3, 4])
def test_loop_honors_each_cancellation_boundary(cancel_on_call):
    runner = loop_runner()
    evaluator = SequentialEvaluator()
    outcome = runner.search(
        state("stuck"), state("goal"), (), (), evaluator,
        BoundaryCancellation(cancel_on_call),
    )

    assert outcome.cancelled
    assert outcome.rollouts[0].analogy_trace.stop_reason is StopReason.CANCELLED
    assert len(evaluator.calls) == (1 if cancel_on_call == 4 else 0)


def test_repeated_successor_stops_without_comparing_transition():
    runner = loop_runner()
    outcome = runner.search(
        state("stuck"), state("goal"), (), (),
        SequentialEvaluator(discard=True), NeverCancelled(),
    )
    trace = outcome.rollouts[0].analogy_trace
    assert trace.stop_reason is StopReason.NO_SUCCESSOR
    assert trace.iterations[0].surprise is None
    assert runner.transition_comparator.calls == []


def test_rejected_plan_uses_diagnostics_to_repair_and_complete_within_budgets():
    repairer = Repairs(("fixed", "unused"))
    runner = loop_runner(
        predicted=("done",),
        max_iterations=1,
        max_model_calls=3,
        max_lean_calls=3,
        tactic_repairer=repairer,
    )
    seen = []

    def evaluator(candidate):
        seen.append(candidate.source)
        if candidate.source == "fixed":
            result = TacticResult(candidate, TacticStatus.COMPLETE, proof="by fixed")
            return TacticEvaluation(result, completed_proof="by fixed")
        result = TacticResult(
            candidate,
            TacticStatus.ERROR,
            diagnostics=("unknown identifier 'assume'",),
        )
        return TacticEvaluation(result)

    outcome = runner.search(
        state("stuck"), state("goal"), (), (), evaluator, NeverCancelled()
    )

    iteration = outcome.rollouts[0].analogy_trace.iterations[0]
    assert outcome.completed_proof == "by fixed"
    assert seen == ["step_1", "fixed"]
    assert iteration.repair_plan.tactics == ("fixed", "unused")
    assert iteration.repair_generation_attempts[0].repair is True
    assert iteration.repair_succeeded is True
    assert iteration.model_calls == 3
    assert iteration.lean_calls == 2
    assert repairer.calls[0][2][0].result.diagnostics == (
        "unknown identifier 'assume'",
    )


def test_successful_repair_compares_the_repaired_predicted_transition():
    repairer = Repairs(("fixed",), predicted="repaired prediction")
    runner = loop_runner(
        predicted=("wrong prediction",),
        max_iterations=1,
        max_model_calls=3,
        max_lean_calls=3,
        tactic_repairer=repairer,
    )

    def evaluator(candidate):
        if candidate.source == "fixed":
            next_state = state("next", "actual repaired state")
            result = TacticResult(candidate, TacticStatus.ACCEPTED, next_state)
            return TacticEvaluation(result, successor=SearchNode(next_state, 0.5))
        return TacticEvaluation(
            TacticResult(candidate, TacticStatus.ERROR, diagnostics=("bad",))
        )

    outcome = runner.search(
        state("stuck"), state("goal"), (), (), evaluator, NeverCancelled()
    )

    iteration = outcome.rollouts[0].analogy_trace.iterations[0]
    assert iteration.repair_plan is not None
    assert runner.transition_comparator.calls == [
        ("goal stuck", iteration.repair_plan.predicted_state, "actual repaired state")
    ]
    assert iteration.predicted_transition == iteration.repair_plan.predicted_state


def test_rejected_repair_refines_analogy_on_same_state_with_diagnostics():
    repairer = Repairs(("still_bad",))
    runner = loop_runner(
        predicted=("p1", "p2"),
        max_iterations=2,
        max_model_calls=8,
        max_lean_calls=6,
        tactic_repairer=repairer,
    )

    def evaluator(candidate):
        result = TacticResult(
            candidate,
            TacticStatus.ERROR,
            diagnostics=(f"Lean rejected {candidate.source}",),
        )
        return TacticEvaluation(result)

    outcome = runner.search(
        state("stuck"), state("goal"), (), (), evaluator, NeverCancelled()
    )

    trace = outcome.rollouts[0].analogy_trace
    assert len(trace.iterations) == 2
    assert trace.iterations[0].refined_same_state is True
    assert trace.iterations[1].state_before.id == "stuck"
    second_context = runner.analogy_generator.contexts[1]
    assert second_context.proof.failed_tactics == ("step_1", "still_bad")
    assert second_context.prior_iterations[0].lean_diagnostics == (
        "Lean rejected step_1",
        "Lean rejected still_bad",
    )


def test_discarded_repair_successor_refines_same_state_and_reports_discard_reason():
    repairer = Repairs(("repeated",))
    runner = loop_runner(
        predicted=("p1", "p2"),
        max_iterations=2,
        max_model_calls=8,
        max_lean_calls=8,
        tactic_repairer=repairer,
    )
    calls = 0

    def evaluator(candidate):
        nonlocal calls
        calls += 1
        if calls % 2:
            return TacticEvaluation(
                TacticResult(candidate, TacticStatus.ERROR, diagnostics=("bad",))
            )
        repeated = state("repeat", "same state")
        result = TacticResult(candidate, TacticStatus.ACCEPTED, repeated)
        return TacticEvaluation(
            result,
            discarded=DiscardedSuccessor(result, DiscardReason.REPEATED_STATE),
        )

    outcome = runner.search(
        state("stuck"), state("goal"), (), (), evaluator, NeverCancelled()
    )

    trace = outcome.rollouts[0].analogy_trace
    assert len(trace.iterations) == 2
    assert trace.iterations[0].refined_same_state is True
    assert runner.analogy_generator.contexts[1].prior_iterations[0].discard_reasons == (
        "repeated_state",
    )


def test_duplicate_tactics_consume_only_one_lean_call():
    class DuplicatePlans(Plans):
        def generate(self, context, action, *, iteration_index, max_attempts=None):
            generated = super().generate(
                context,
                action,
                iteration_index=iteration_index,
                max_attempts=max_attempts,
            )
            return GeneratedTacticPlan(
                replace(
                    generated.plan,
                    tactics=("exact h", " exact   h ", "exact h"),
                ),
                generated.attempts,
            )

    runner = loop_runner(predicted=("p1",), max_iterations=1)
    runner.tactic_generator = DuplicatePlans(("p1",))
    seen = []

    def reject(candidate):
        seen.append(candidate.source)
        return TacticEvaluation(TacticResult(candidate, TacticStatus.ERROR))

    outcome = runner.search(
        state("stuck"), state("goal"), (), (), reject, NeverCancelled()
    )

    assert seen == ["exact h"]
    assert outcome.rollouts[0].analogy_trace.lean_calls == 1


def test_semantic_repair_is_skipped_when_model_or_lean_budget_is_exhausted():
    for model_calls, lean_calls in ((2, 4), (4, 1)):
        repairer = Repairs()
        runner = loop_runner(
            predicted=("p1",),
            max_iterations=1,
            max_model_calls=model_calls,
            max_lean_calls=lean_calls,
            tactic_repairer=repairer,
        )

        def reject(candidate):
            return TacticEvaluation(
                TacticResult(candidate, TacticStatus.ERROR, diagnostics=("bad",))
            )

        runner.search(
            state("stuck"), state("goal"), (), (), reject, NeverCancelled()
        )

        assert repairer.calls == []


def test_cancellation_after_initial_rejection_skips_semantic_repair():
    repairer = Repairs()
    runner = loop_runner(
        predicted=("p1",), tactic_repairer=repairer, max_iterations=1
    )

    class Token:
        cancelled = False

        def is_cancelled(self):
            return self.cancelled

    token = Token()

    def reject(candidate):
        token.cancelled = True
        return TacticEvaluation(
            TacticResult(candidate, TacticStatus.ERROR, diagnostics=("bad",))
        )

    outcome = runner.search(
        state("stuck"), state("goal"), (), (), reject, token
    )

    assert outcome.cancelled
    assert repairer.calls == []


def test_model_stop_is_advisory_until_minimum_iterations():
    runner = loop_runner(stops=(True, True), min_iterations=2, surprises=(0.8, 0.7))
    outcome = runner.search(
        state("stuck"), state("goal"), (), (), SequentialEvaluator(("a1", "a2")), NeverCancelled()
    )
    assert outcome.rollouts[0].analogy_trace.stop_reason is StopReason.MODEL_STOP
    assert len(outcome.rollouts[0].analogy_trace.iterations) == 2


def test_max_iterations_is_a_hard_stop():
    runner = loop_runner(max_iterations=2, surprises=(0.8, 0.7))
    outcome = runner.search(
        state("stuck"), state("goal"), (), (), SequentialEvaluator(("a1", "a2")), NeverCancelled()
    )
    assert outcome.rollouts[0].analogy_trace.stop_reason is StopReason.MAX_ITERATIONS


@pytest.mark.parametrize(
    ("model_calls", "lean_calls", "reason", "iterations"),
    [(2, 20, StopReason.MODEL_BUDGET, 1), (20, 1, StopReason.LEAN_BUDGET, 1)],
)
def test_hard_budgets_stop_the_loop(model_calls, lean_calls, reason, iterations):
    runner = loop_runner(max_model_calls=model_calls, max_lean_calls=lean_calls)
    outcome = runner.search(
        state("stuck"), state("goal"), (), (), SequentialEvaluator(), NeverCancelled()
    )
    trace = outcome.rollouts[0].analogy_trace
    assert trace.stop_reason is reason
    assert len(trace.iterations) == iterations

