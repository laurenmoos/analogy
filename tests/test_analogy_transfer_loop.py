"""Exercise the real staged services with only model/Lean inference replaced."""

import json
from dataclasses import replace

import pytest
import torch

from analogy.analogy_transfer_loop import GeneratedDomainAnalogyLoopRunner
from analogy.analogy_transfer_generation import (
    SourceAnalogyGenerator, AlignmentGenerator, ProofTransferGenerator,
    TransferredTacticGenerator,
)
from analogy.loop import NeverCancelled
from analogy.proof_views import build_policy_representation, compare_transitions
from analogy.records import (
    Embedding, SearchNode, SymbolicState, TacticResult, TacticStatus, DiscardReason,
)
from analogy.rescue import RescueTermination, TacticEvaluation
from analogy.rl import PolicyAction
from analogy.surprise import SurpriseController, SurpriseConfig, StopReason
from test_analogy_transfer_generation import SOURCE, ALIGNMENT, TRANSFER, STRUCTURE, RecordingBackend


def state(label="C"):
    return SymbolicState(label, f"h : {label}\n⊢ {label}", "lean4")


class Encoder:
    def embed_text(self, text, *, layer):
        return Embedding("fixture", (1.0, 0.0), layer=layer)


class Evaluator:
    def __init__(self, *, finish=False, repeat=False):
        self.finish = finish
        self.repeat = repeat
        self.calls = []

    def evaluate_at(self, current, candidate):
        self.calls.append((current.expression, candidate.source))
        if candidate.source == "assumption":
            result = TacticResult(candidate, TacticStatus.COMPLETE, proof="by assumption") if self.finish else TacticResult(candidate, TacticStatus.ERROR, diagnostics=("no matching assumption",))
            return TacticEvaluation(result, completed_proof=result.proof)
        successor = state("C" if self.repeat else "D" if current.id == "C" else "E")
        result = TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)
        return TacticEvaluation(result, successor=SearchNode(successor, 1.0, current.id, candidate.source))


def outputs(count=2):
    result = []
    for index in range(count):
        transfer = dict(json.loads(TRANSFER), active_subgoal="C" if index == 0 else "D")
        plan = {"predicted_state": "D" if index == 0 else "E", "tactics": [f"apply h{index}.mpr"], "motivating_transfer_id": f"transfer-{index}"}
        result.extend((SOURCE, ALIGNMENT, json.dumps(transfer), json.dumps(plan)))
    return result


def runner(raw=None, *, model_budget=20, lean_budget=8, min_iterations=2, token=None):
    backend = RecordingBackend(outputs() if raw is None else raw)
    observed = []

    def build(observation, structure, context):
        observed.append(observation)
        return build_policy_representation(
            context, structure, Encoder(), -1,
            previous_state=observation.previous_state, last_tactic=observation.last_tactic,
            latest_source=observation.latest_source, latest_alignment=observation.latest_alignment,
            latest_transfer=observation.latest_transfer,
        )

    result = GeneratedDomainAnalogyLoopRunner(
        structure_builder=lambda current: STRUCTURE,
        observation_builder=build,
        action_sampler=lambda observation, index: PolicyAction(torch.zeros(2), torch.tensor(0.0), torch.tensor(0.0)),
        source_generator=SourceAnalogyGenerator(backend),
        alignment_generator=AlignmentGenerator(backend),
        transfer_generator=ProofTransferGenerator(backend),
        tactic_generator=TransferredTacticGenerator(backend),
        transition_comparator=lambda before, predicted, actual: compare_transitions(before, predicted, actual, Encoder(), -1),
        controller=SurpriseController(SurpriseConfig(min_iterations, 2, 0.2, 1, 0.0, 2)),
        tactics_per_iteration=1, max_model_calls=model_budget, max_lean_calls=lean_budget,
        structure_lean_cost=1,
    )
    return result, backend, observed


def run(result, evaluator=None, token=None):
    return result.search(state(), SymbolicState("goal", "C → S", "lean4"), (), (), evaluator or Evaluator(), token or NeverCancelled())


def test_generated_loop_refreshes_complete_observation_after_verified_successor():
    result, backend, observations = runner()
    outcome = run(result)
    second = observations[1]
    assert second.fixed_target == "C → S"
    assert second.previous_state == "h : C\n⊢ C"
    assert second.current_state == "h : D\n⊢ D"
    assert second.last_tactic == "apply h0.mpr"
    assert second.accepted_tactics == ("apply h0.mpr",)
    assert second.latest_transfer.transfer_id == "transfer-0"
    assert second.rejected_tactics == (("assumption", ("no matching assumption",)),)
    assert (second.remaining_model_calls, second.remaining_lean_calls) == (16, 4)
    assert second.surprise == 0.0
    trace = outcome.rollouts[0].analogy_trace
    assert trace.iterations[1].source.analogy_id == "source-1"
    assert trace.iterations[1].representation.transition_embedding.values != (0.0, 0.0)
    assert trace.stop_reason is StopReason.MAX_ITERATIONS
    assert (trace.model_calls, trace.lean_calls) == (8, 6)


def test_conventional_finisher_closes_before_low_surprise_stop_or_new_analogy():
    result, backend, observations = runner(min_iterations=1)
    outcome = run(result, Evaluator(finish=True))
    assert outcome.completed_proof == "by assumption"
    assert len(observations) == 1
    assert len(backend.calls) == 4
    assert outcome.rollouts[0].attempts[-1].proposal.candidate.source == "assumption"
    assert outcome.rollouts[0].attempts[-1].stage == "conventional_finisher"
    assert outcome.rollouts[0].analogy_trace.stop_reason is StopReason.COMPLETE


@pytest.mark.parametrize("stage", range(4))
def test_failed_stage_records_repairs_and_never_calls_downstream(stage):
    result, backend, _ = runner(outputs()[:stage] + ["invalid", "still invalid"])
    outcome = run(result)
    trace = outcome.rollouts[0].analogy_trace
    assert len(backend.calls) == stage + 2
    assert len(trace.iterations) == 1
    iteration = trace.iterations[0]
    assert iteration.failed_stage == ("source", "alignment", "transfer", "tactic")[stage]
    assert (trace.model_calls, trace.lean_calls) == (stage + 2, 1)
    assert len(outcome.rollouts[0].generations) == stage + 2
    assert outcome.rollouts[0].termination is RescueTermination.INVALID_OUTPUT
    assert len(result.drain_trajectories()[0][1]) == 1


@pytest.mark.parametrize("budget", [1, 2, 3, 4])
def test_model_budget_stops_between_stages_but_still_allows_lean_and_finisher(budget):
    result, backend, _ = runner(model_budget=budget)
    evaluator = Evaluator(finish=True)
    outcome = run(result, evaluator)
    assert len(backend.calls) == budget
    assert len(outcome.rollouts[0].analogy_trace.iterations) == 1
    assert len(evaluator.calls) == (2 if budget == 4 else 0)
    assert outcome.rollouts[0].analogy_trace.stop_reason is (StopReason.COMPLETE if budget == 4 else StopReason.MODEL_BUDGET)


def test_repeated_accepted_state_is_discarded_without_finisher_or_progress():
    result, _, observations = runner()
    evaluator = Evaluator(repeat=True)
    outcome = run(result, evaluator)
    assert outcome.successor is None
    assert len(observations) == len(evaluator.calls) == 1
    attempt = outcome.rollouts[0].attempts[0]
    assert attempt.discarded.reason is DiscardReason.REPEATED_STATE
    assert outcome.rollouts[0].analogy_trace.iterations[0].repeated_state is True
    assert outcome.rollouts[0].termination is RescueTermination.EXHAUSTED


def test_lean_budget_includes_structure_and_finishing_calls():
    result, _, observations = runner(lean_budget=2)
    evaluator = Evaluator(finish=True)
    outcome = run(result, evaluator)
    assert outcome.successor.state.id == "D"
    assert outcome.completed_proof is None
    assert len(evaluator.calls) == 1
    assert outcome.rollouts[0].analogy_trace.lean_calls == 2
    assert outcome.rollouts[0].analogy_trace.stop_reason is StopReason.LEAN_BUDGET


def test_shuffled_iteration_preserves_generated_source_and_used_source_separately():
    result, backend, _ = runner()
    result.source_transform = lambda source, need, observation: replace(source, analogy_id="shuffled-other-source-0")
    outcome = run(result, Evaluator(finish=True))
    iteration = outcome.rollouts[0].analogy_trace.iterations[0]
    assert iteration.generated_source.analogy_id == "source-0"
    assert iteration.source.analogy_id == "shuffled-other-source-0"
    assert iteration.alignment.source_analogy_id == "shuffled-other-source-0"
    assert len(backend.calls) == iteration.model_calls == 4


def test_cancellation_prevents_schema_repair_and_preserves_partial_iteration():
    result, backend, _ = runner(["invalid", SOURCE])
    class CancelAfterFirstCall:
        def is_cancelled(self):
            return bool(backend.calls)
    outcome = run(result, token=CancelAfterFirstCall())
    assert len(backend.calls) == 1
    assert outcome.cancelled
    trace = outcome.rollouts[0].analogy_trace
    assert trace.model_calls == 1
    assert len(trace.iterations) == 1
    assert trace.stop_reason is StopReason.CANCELLED


def test_incompatible_shuffle_retains_source_and_count_without_downstream_calls():
    result, backend, _ = runner()
    def incompatible(*args):
        raise ValueError("no compatible source")
    result.source_transform = incompatible
    outcome = run(result)
    iteration = outcome.rollouts[0].analogy_trace.iterations[0]
    assert iteration.failed_stage == "source_shuffle"
    assert iteration.failure_diagnostic == "ValueError: no compatible source"
    assert iteration.generated_source is not None
    assert iteration.alignment is None
    assert len(backend.calls) == iteration.model_calls == 1
    assert outcome.rollouts[0].termination is RescueTermination.GENERATION_ERROR


def test_early_structure_failure_records_the_lean_call_and_diagnostic():
    result, backend, _ = runner()
    def fail_structure(current):
        raise RuntimeError("Lean unavailable")
    result.structure_builder = fail_structure
    outcome = run(result)
    trace = outcome.rollouts[0].analogy_trace
    assert trace.lean_calls == 1
    assert trace.model_calls == len(backend.calls) == 0
    assert trace.iterations[0].failed_stage == "structure"
    assert trace.iterations[0].failure_diagnostic == "RuntimeError: Lean unavailable"
    assert outcome.rollouts[0].termination is RescueTermination.GENERATION_ERROR


def test_verifier_exception_preserves_all_model_stages_and_the_spent_lean_call():
    result, backend, _ = runner()
    class BrokenEvaluator:
        def evaluate_at(self, current, candidate):
            raise RuntimeError("Lean crashed")
    outcome = run(result, BrokenEvaluator())
    trace = outcome.rollouts[0].analogy_trace
    assert (trace.model_calls, trace.lean_calls) == (4, 2)
    assert trace.iterations[0].failed_stage == "verification"
    assert trace.iterations[0].tactic_plan is not None
    assert trace.iterations[0].failure_diagnostic == "RuntimeError: Lean crashed"
    assert outcome.rollouts[0].termination is RescueTermination.GENERATION_ERROR


def test_cycle_back_to_initial_state_returns_only_the_prior_novel_successor():
    result, _, observations = runner()
    class CycleEvaluator(Evaluator):
        def evaluate_at(self, current, candidate):
            self.repeat = current.id == "D" and candidate.source != "assumption"
            return super().evaluate_at(current, candidate)
    outcome = run(result, CycleEvaluator())
    assert outcome.successor.state.id == "D"
    assert len(observations) == 2
    trace = outcome.rollouts[0].analogy_trace
    assert trace.iterations[1].repeated_state
    assert trace.iterations[1].attempts[0].result.state.id == "C"
    assert trace.iterations[1].attempts[0].discarded.reason is DiscardReason.REPEATED_STATE
    assert trace.stop_reason is StopReason.NO_SUCCESSOR


def test_completed_and_partial_generated_iterations_are_serializable(tmp_path):
    from analogy.training import RescueRunWriter
    result, _, _ = runner([SOURCE, "invalid", "invalid"])
    rollout = run(result).rollouts[0]
    writer = RescueRunWriter.create(tmp_path, "partial", {})
    writer.append_analogy_iteration(rollout.analogy_trace.iterations[0], epoch=1,
        phase="train", problem_id="fixture", rollout_id=rollout.rollout_id)
    payload = json.loads((writer.path / "analogy_iterations.jsonl").read_text())
    assert payload["source"]["domain"] == "warehouse logistics"
    assert payload["alignment"] is None
    assert payload["model_calls"] == 3
    assert len(payload["alignment_generation_attempts"]) == 2
