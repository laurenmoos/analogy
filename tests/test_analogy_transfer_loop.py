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


def runner(raw=None, *, model_budget=20, lean_budget=8, min_iterations=2, token=None, **decoder_options):
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
        **decoder_options,
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


def test_completing_finisher_records_the_terminal_transition_from_its_own_state():
    result, _, _ = runner()
    outcome = run(result, Evaluator(finish=True))
    iteration = outcome.rollouts[0].analogy_trace.iterations[0]
    assert iteration.actual_transition == "no goals"
    assert iteration.predicted_transition == "no goals"
    assert iteration.transition_before == "h : D\n⊢ D"
    assert iteration.comparison is None  # completion is authoritative; no extra embedding call
    assert iteration.tactic_plan.predicted_state == "D"
    assert iteration.attempts[0].result.state.expression == "h : D\n⊢ D"
    assert iteration.decision.reason is StopReason.COMPLETE
    assert (iteration.model_calls, iteration.lean_calls) == (4, 3)


@pytest.mark.parametrize("stage", range(4))
def test_failed_stages_preserve_all_generated_prompt_versions(stage):
    from dataclasses import asdict
    result, _, _ = runner(outputs()[:stage] + ["invalid", "invalid"])
    row = asdict(run(result).rollouts[0].analogy_trace.iterations[0])
    assert dict(row["prompt_versions"]) == {
        "source": "source-analogy-v2", "alignment": "analogy-alignment-v1",
        "transfer": "proof-reasoning-transfer-v1", "tactic": "transferred-tactic-v1",
    }


def test_partial_finisher_refreshes_transition_surprise_and_controller_before_next_action():
    raw = outputs()
    raw[6] = json.dumps(dict(json.loads(raw[6]), active_subgoal="E"))
    raw[7] = json.dumps(dict(json.loads(raw[7]), predicted_state="F"))
    result, backend, observations = runner(raw, min_iterations=1)
    comparisons = []

    def compare(before, predicted, actual):
        comparisons.append((before, predicted, actual))
        comparison = compare_transitions(before, predicted, actual, Encoder(), -1)
        # Partial finishing leaves an unexpected open goal. The stale analogy
        # transition would instead appear converged and stop before action two.
        return replace(comparison, surprise=0.8 if predicted == "no goals" else 0.1)

    class PartialFinisher(Evaluator):
        def evaluate_at(self, current, candidate):
            target = None
            if current.id == "D" and candidate.source == "assumption":
                target = state("E")
            elif current.id == "E" and candidate.source != "assumption":
                target = state("F")
            if target is None:
                return super().evaluate_at(current, candidate)
            self.calls.append((current.expression, candidate.source))
            checked = TacticResult(candidate, TacticStatus.ACCEPTED, state=target)
            return TacticEvaluation(checked, successor=SearchNode(target, 1.0, current.id, candidate.source))

    result.transition_comparator = compare
    outcome = run(result, PartialFinisher())
    assert len(observations) == 2
    second = observations[1]
    assert (second.previous_state, second.current_state, second.last_tactic) == (
        "h : D\n⊢ D", "h : E\n⊢ E", "assumption",
    )
    assert second.predicted_transition == "no goals"
    assert second.actual_transition == "h : E\n⊢ E"
    assert second.accepted_tactics == ("apply h0.mpr", "assumption")
    assert second.latest_transfer.transfer_id == "transfer-0"
    assert second.surprise == 0.8
    assert second.surprise_delta is None
    assert second.repeated_state is False
    assert (second.remaining_model_calls, second.remaining_lean_calls) == (16, 4)
    assert comparisons == [
        ("h : D\n⊢ D", "no goals", "h : E\n⊢ E"),
        ("h : E\n⊢ E", "F", "h : F\n⊢ F"),
    ]
    trace = outcome.rollouts[0].analogy_trace
    assert trace.iterations[0].decision.continue_loop
    assert trace.iterations[0].decision.state.values == (0.8,)
    assert trace.iterations[0].tactic_plan.predicted_state == "D"
    assert trace.iterations[0].attempts[-1].stage == "conventional_finisher"
    assert trace.iterations[1].surprise_delta == pytest.approx(0.7)
    assert trace.iterations[1].decision.state.values == (0.8, 0.1)
    assert outcome.successor.state.id == "F"
    assert (trace.model_calls, trace.lean_calls, len(backend.calls)) == (8, 6, 8)


def test_repeated_finisher_preserves_the_last_novel_transition_for_next_observation():
    result, _, observations = runner()
    comparisons = []

    def compare(before, predicted, actual):
        comparisons.append((before, predicted, actual))
        return compare_transitions(before, predicted, actual, Encoder(), -1)

    class RepeatedFinisher(Evaluator):
        def evaluate_at(self, current, candidate):
            if current.id == "D" and candidate.source == "assumption":
                checked = TacticResult(candidate, TacticStatus.ACCEPTED, state=state("C"))
                return TacticEvaluation(checked, successor=SearchNode(state("C"), 1.0, current.id, "assumption"))
            return super().evaluate_at(current, candidate)

    result.transition_comparator = compare
    outcome = run(result, RepeatedFinisher())
    second = observations[1]
    assert (second.previous_state, second.current_state, second.last_tactic) == (
        "h : C\n⊢ C", "h : D\n⊢ D", "apply h0.mpr",
    )
    assert (second.predicted_transition, second.actual_transition) == ("D", "h : D\n⊢ D")
    assert second.accepted_tactics == ("apply h0.mpr",)
    assert second.repeated_state is True
    assert second.surprise == 0.0
    assert comparisons[0] == ("h : C\n⊢ C", "D", "h : D\n⊢ D")
    first = outcome.rollouts[0].analogy_trace.iterations[0]
    assert first.attempts[-1].discarded.reason is DiscardReason.REPEATED_STATE
    assert first.decision.continue_loop
    assert first.decision.state.values == (0.0,)


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


def grounded_payload(expression="h : C\n⊢ C", *, operator="exact_local", proposition="C", index=0):
    from analogy.replay import proof_state_fingerprint
    return dict(motivating_transfer_id=f"transfer-{index}",
        state_fingerprint=proof_state_fingerprint(expression),
        bindings=[dict(binding_id="binding-0", proof_role="matching_hypothesis",
            inventory_id="local:0", proposition=proposition, source_role=None, justification="Available premise.")],
        steps=[dict(step_id="step-0", operator=operator, inputs=["binding-0"],
            input_propositions=[proposition], output_proposition="C", transfer_step_index=0, closes_subgoal=True)],
        expected_next_subgoal="No remaining goals.", completion_expected=True, surface_hints=[])


def grounded_runner(payload=None, *, extra=(), **options):
    from analogy.analogy_transfer_generation import GroundedPlanGenerator
    raw = [SOURCE, ALIGNMENT, TRANSFER, json.dumps(payload or grounded_payload()), *extra]
    result, backend, observations = runner(raw, **options)
    # Reuse the same inference recorder across independently parsed services.
    result.decoder = "grounded"
    result.grounded_plan_generator = GroundedPlanGenerator(backend)
    return result, backend, observations


class GroundedEvaluator:
    def __init__(self, accepted="exact h"):
        self.calls = []
        self.accepted = accepted

    def evaluate_at(self, current, candidate):
        self.calls.append(candidate.source)
        if candidate.source == self.accepted:
            result = TacticResult(candidate, TacticStatus.COMPLETE, proof="by " + candidate.source)
            return TacticEvaluation(result, completed_proof=result.proof)
        return TacticEvaluation(TacticResult(candidate, TacticStatus.ERROR, diagnostics=("type mismatch",)))


def test_grounded_loop_compiles_before_spending_lean_calls():
    result, backend, _ = grounded_runner()
    evaluator = GroundedEvaluator()
    outcome = run(result, evaluator)
    iteration = outcome.rollouts[0].analogy_trace.iterations[0]
    assert iteration.inventory.active_subgoal == "C"
    assert iteration.grounded_plan.motivating_transfer_id == "transfer-0"
    assert [item.text for item in iteration.compiled_tactics] == ["exact h"]
    assert iteration.compiled_tactics[0].binding_ids == ("binding-0",)
    assert iteration.compiler_version == "grounded-compiler-v1"
    assert iteration.grounded_plan_schema_version == "grounded-plan-v1"
    assert dict(iteration.prompt_versions)["grounding"] == "grounding-v1"
    assert iteration.tactic_plan is None
    assert evaluator.calls == ["exact h"]
    assert len(backend.calls) == 4
    assert outcome.completed_proof == "by exact h"


def test_grounded_compile_failure_uses_no_candidate_lean_call_and_is_not_trainable():
    result, backend, _ = grounded_runner(grounded_payload(operator="rewrite_forward"))
    evaluator = GroundedEvaluator()
    iteration = run(result, evaluator).rollouts[0].analogy_trace.iterations[0]
    assert evaluator.calls == []
    assert iteration.lean_calls == 1  # current verified structure only
    assert iteration.failed_stage == "compilation"
    assert iteration.compilation_diagnostics[0].code == "unsupported_operator"
    assert not iteration.training_eligible
    assert iteration.inventory is not None and iteration.grounded_plan is not None


def test_grounded_lean_repair_replaces_only_the_plan_using_same_inventory():
    expression = "h : C ∧ C\n⊢ C"
    initial = grounded_payload(expression, operator="select_left", proposition="C ∧ C")
    repaired = grounded_payload(expression, operator="select_right", proposition="C ∧ C")
    result, backend, _ = grounded_runner(initial, extra=[json.dumps(repaired)])
    evaluator = GroundedEvaluator("exact h.right")
    outcome = result.search(SymbolicState("C", expression, "lean4"), state(), (), (), evaluator, NeverCancelled())
    iteration = outcome.rollouts[0].analogy_trace.iterations[0]
    assert evaluator.calls == ["exact h.left", "exact h.right"]
    assert iteration.grounded_plan.steps[0].operator.value == "select_left"
    assert iteration.repaired_grounded_plan.steps[0].operator.value == "select_right"
    assert len(iteration.grounding_repair_attempts) == 1
    assert iteration.grounding_repair_attempts[0].repair
    assert (iteration.model_calls, iteration.lean_calls) == (5, 3)
    prompt = backend.calls[-1][0][-1].content
    assert "type mismatch" in prompt and "binding-0" in prompt and "exact h.left" in prompt
    assert outcome.completed_proof == "by exact h.right"


@pytest.mark.parametrize("invalid", ["fingerprint", "binding"])
def test_grounded_stale_repair_is_rejected_without_another_lean_call(invalid):
    repaired = grounded_payload()
    if invalid == "fingerprint":
        repaired["state_fingerprint"] = "old-state"
    else:
        repaired["bindings"][0]["inventory_id"] = "local:999"
    result, backend, _ = grounded_runner(extra=[json.dumps(repaired)])
    evaluator = GroundedEvaluator("never")
    iteration = run(result, evaluator).rollouts[0].analogy_trace.iterations[0]
    assert evaluator.calls == ["exact h"]
    assert len(backend.calls) == 5
    assert iteration.failed_stage == "grounding_repair"
    assert len(iteration.grounding_repair_attempts) == 1
    assert not iteration.training_eligible


def test_grounded_cancellation_before_repair_prevents_model_call():
    result, backend, _ = grounded_runner(extra=[json.dumps(grounded_payload())])
    evaluator = GroundedEvaluator("never")
    class Token:
        def is_cancelled(self):
            return bool(evaluator.calls)
    outcome = run(result, evaluator, Token())
    assert len(backend.calls) == 4
    assert outcome.cancelled


def test_grounded_repair_deduplicates_previously_rejected_candidate_before_charging():
    result, backend, _ = grounded_runner(extra=[json.dumps(grounded_payload())])
    evaluator = GroundedEvaluator("never")
    iteration = run(result, evaluator).rollouts[0].analogy_trace.iterations[0]
    assert evaluator.calls == ["exact h"]
    assert (iteration.model_calls, iteration.lean_calls) == (5, 2)


def test_grounded_infrastructure_failure_is_excluded_from_training_and_repair():
    result, backend, _ = grounded_runner()
    class Broken:
        def evaluate_at(self, current, candidate):
            raise RuntimeError("Lean crashed")
    iteration = run(result, Broken()).rollouts[0].analogy_trace.iterations[0]
    assert not iteration.training_eligible
    assert len(backend.calls) == 4
    assert iteration.grounded_plan is not None


@pytest.mark.parametrize("limit", ["model", "lean", "disabled"])
def test_grounded_repair_shares_call_budgets_and_respects_disabled_setting(limit):
    options = {"model_budget": 4} if limit == "model" else {"lean_budget": 2} if limit == "lean" else {}
    result, backend, _ = grounded_runner(extra=[json.dumps(grounded_payload())], **options)
    if limit == "disabled":
        result.grounding_repair_calls = 0
    evaluator = GroundedEvaluator("never")
    trace = run(result, evaluator).rollouts[0].analogy_trace
    assert evaluator.calls == ["exact h"]
    assert len(backend.calls) == trace.model_calls == 4
    assert trace.lean_calls == 2


def test_grounded_repeated_state_is_discarded_without_diagnostic_repair():
    result, backend, _ = grounded_runner(extra=[json.dumps(grounded_payload())])
    evaluator = Evaluator(repeat=True)
    outcome = run(result, evaluator)
    assert outcome.successor is None
    iteration = outcome.rollouts[0].analogy_trace.iterations[0]
    assert iteration.repeated_state
    assert len(backend.calls) == 4
    assert len(evaluator.calls) == 1


def test_grounded_verified_successor_refreshes_inventory_and_trajectory():
    # The first typed intro creates a real open obligation. The second analogy
    # consumes the newly introduced local using the refreshed stable ID.
    from analogy.replay import proof_state_fingerprint
    expression = "⊢ C → C"
    successor = SymbolicState("D", "analogy_h : C\n⊢ C", "lean4")
    source = json.loads(SOURCE)
    source["transformation"].update(operation="introduce", input_arity="one", output_arity="one")
    first_transfer = dict(json.loads(TRANSFER), active_subgoal="C → C", premises=[],
        predicted_next_subgoal="C", completion_expected=False)
    first_plan = grounded_payload(expression)
    first_plan.update(bindings=[], expected_next_subgoal="C", completion_expected=False)
    first_plan["steps"][0].update(operator="introduce", inputs=[], input_propositions=[], closes_subgoal=False)
    second_plan = grounded_payload(successor.expression, index=1)
    result, backend, observations = grounded_runner(first_plan)
    # Source/alignment schemas remain exercised; use the declared intro role.
    from analogy.analogy_transfer import derive_proof_need
    from analogy.proof_views import ProofStructure
    intro_structure = ProofStructure(1, '{"root":"implication","children":[{"root":"atom"},{"root":"atom"}]}', ())
    roles = derive_proof_need(intro_structure, "C → C", fixed_target="C → C").proof_roles
    alignment = json.loads(ALIGNMENT)
    alignment["mapping"]["shelved wheel"] = next(role for role in roles if role != "active_subgoal")
    backend.outputs = iter([json.dumps(source), json.dumps(alignment), json.dumps(first_transfer), json.dumps(first_plan),
        SOURCE, ALIGNMENT, TRANSFER, json.dumps(second_plan)])
    result.structure_builder = lambda current: intro_structure if current.expression == expression else STRUCTURE
    class IntroEvaluator(GroundedEvaluator):
        def evaluate_at(self, current, candidate):
            if candidate.source == "intro analogy_h":
                self.calls.append(candidate.source)
                checked = TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)
                return TacticEvaluation(checked, successor=SearchNode(successor, 1.0, current.id, candidate.source))
            return super().evaluate_at(current, candidate)
    evaluator = IntroEvaluator("exact analogy_h")
    outcome = result.search(SymbolicState("C", expression, "lean4"), state(), (), (), evaluator, NeverCancelled())
    iterations = outcome.rollouts[0].analogy_trace.iterations
    assert len(iterations) == 2
    assert iterations[0].inventory.state_fingerprint == proof_state_fingerprint(expression)
    assert iterations[1].inventory.state_fingerprint == proof_state_fingerprint(successor.expression)
    assert iterations[1].inventory.locals[0].identifier == "analogy_h"
    assert observations[1].previous_state == expression
    assert observations[1].latest_transfer.transfer_id == "transfer-0"
    assert observations[1].accepted_tactics == ("intro analogy_h",)
    assert evaluator.calls == ["intro analogy_h", "assumption", "exact analogy_h"]
    assert outcome.completed_proof == "by exact analogy_h"


@pytest.mark.parametrize("value", [True, 1.0, 2, -1])
def test_grounded_runner_rejects_noninteger_or_unbounded_diagnostic_repair_limit(value):
    with pytest.raises(ValueError, match="grounding_repair_calls"):
        runner(grounding_repair_calls=value)


def test_grounded_unknown_initial_binding_preserves_inventory_without_lean_attempt():
    payload = grounded_payload()
    payload["bindings"][0]["inventory_id"] = "local:999"
    result, backend, _ = grounded_runner(payload, extra=[json.dumps(payload)])
    evaluator = GroundedEvaluator()
    iteration = run(result, evaluator).rollouts[0].analogy_trace.iterations[0]
    assert iteration.failed_stage == "grounding"
    assert iteration.inventory.active_subgoal == "C"
    assert iteration.grounded_plan is None
    assert len(iteration.grounding_generation_attempts) == 2
    assert (iteration.model_calls, iteration.lean_calls) == (5, 1)
    assert evaluator.calls == []


def test_grounded_repaired_compile_failure_terminates_with_both_plans_and_no_fallback():
    result, backend, _ = grounded_runner(extra=[json.dumps(grounded_payload(operator="rewrite_forward"))])
    evaluator = GroundedEvaluator("never")
    iteration = run(result, evaluator).rollouts[0].analogy_trace.iterations[0]
    assert evaluator.calls == ["exact h"]
    assert (iteration.model_calls, iteration.lean_calls) == (5, 2)
    assert iteration.grounded_plan is not None and iteration.repaired_grounded_plan is not None
    assert iteration.repair_compilation_diagnostics[0].code == "unsupported_operator"
    assert iteration.failed_stage == "compilation"
    assert not iteration.training_eligible


def test_grounded_loop_preserves_whitespace_within_quoted_identifiers():
    expression = "«local  proof» : C\n⊢ C"
    result, _, _ = grounded_runner(grounded_payload(expression))
    evaluator = GroundedEvaluator("exact «local  proof»")
    outcome = result.search(SymbolicState("C", expression, "lean4"), state(), (), (), evaluator, NeverCancelled())
    assert evaluator.calls == ["exact «local  proof»"]
    assert outcome.completed_proof == "by exact «local  proof»"


@pytest.mark.parametrize("partial", [False, True])
def test_grounded_complete_and_partial_artifacts_are_serializable(tmp_path, partial):
    from analogy.training import RescueRunWriter
    payload = grounded_payload(operator="rewrite_forward" if partial else "exact_local")
    result, _, _ = grounded_runner(payload)
    rollout = run(result, GroundedEvaluator()).rollouts[0]
    writer = RescueRunWriter.create(tmp_path, "grounded", {})
    writer.append_analogy_iteration(rollout.analogy_trace.iterations[0], epoch=1,
        phase="train", problem_id="fixture", rollout_id=rollout.rollout_id)
    record = json.loads((writer.path / "analogy_iterations.jsonl").read_text())
    assert record["inventory"]["locals"][0]["identifier"] == "h"
    assert record["grounded_plan"]["bindings"][0]["inventory_id"] == "local:0"
    if partial:
        assert record["compilation_diagnostics"][0]["code"] == "unsupported_operator"
    else:
        assert record["compiled_tactics"][0]["text"] == "exact h"
        assert record["attempts"][0]["stage"] == "grounded_compiler"


@pytest.mark.parametrize("status", ["timeout", "internal_error"])
def test_grounded_typed_lean_infrastructure_failure_does_not_repair_or_train(status):
    from analogy.lean_gym.models import Problem, VerificationResult, VerificationStatus
    from analogy.lean_gym.verifier import LeanGymAdapter
    result, backend, _ = grounded_runner(extra=[json.dumps(grounded_payload())])
    class FailedVerifier:
        def try_tactic(self, *args):
            return VerificationResult(VerificationStatus(status), False, None, (), 1, 0)
    adapter = LeanGymAdapter(Problem("failed", "Failed", "(C : Prop) (h : C)", "C",
        "test", "test", "test", 1), FailedVerifier())
    adapter.register_replay_state(state())
    class InfrastructureEvaluator:
        def evaluate_at(self, current, candidate):
            return TacticEvaluation(adapter.run_tactic(current, candidate))
    rollout = run(result, InfrastructureEvaluator()).rollouts[0]
    iteration = rollout.analogy_trace.iterations[0]
    assert (len(backend.calls), iteration.model_calls, iteration.lean_calls) == (4, 4, 2)
    assert len(iteration.attempts) == 1
    assert iteration.attempts[0].result.infrastructure_failure == status
    assert not iteration.training_eligible
    assert iteration.failed_stage == "verification"
    assert rollout.termination is RescueTermination.GENERATION_ERROR


def test_raw_iteration_serialization_keeps_historical_schema():
    from dataclasses import asdict
    result, _, _ = runner()
    iteration = run(result, Evaluator(finish=True)).rollouts[0].analogy_trace.iterations[0]
    record = asdict(iteration)
    assert set(record).isdisjoint({"inventory", "grounded_plan", "compiled_tactics",
        "compilation_diagnostics", "grounding_generation_attempts", "grounding_repair_attempts",
        "repaired_grounded_plan", "repaired_compiled_tactics", "repair_compilation_diagnostics",
        "compiler_version", "grounded_plan_schema_version"})
    assert record["tactic_plan"]["tactics"] == ("apply h0.mpr",)


def test_grounded_loop_continues_after_two_nonterminal_transitions_with_pinned_lean():
    from analogy.analogy_transfer_generation import GroundedPlanGenerator
    from analogy.lean_gym.models import Problem
    from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
    from analogy.lean_gym.verifier import LeanGymAdapter, LeanSubprocessVerifier
    problem = Problem("grounded_loop", "GroundedLoop", "(C : Prop)", "C → C ∧ C",
        "test", "test", "and", 1)
    adapter = LeanGymAdapter(problem, LeanSubprocessVerifier(LEAN_PROJECT_ROOT))
    initial = adapter.initial_state()
    records = []
    for active, expected, role in (("C → C ∧ C", "C ∧ C", "introduced_assumption"),
                                   ("C ∧ C", "C", "conjunction_constructor"),
                                   ("C", "No remaining goals.", "matching_hypothesis")):
        alignment = json.loads(ALIGNMENT)
        alignment["mapping"]["shelved wheel"] = role
        transfer = dict(json.loads(TRANSFER), active_subgoal=active,
            premises=[role] if active == "C" else [], predicted_next_subgoal=expected,
            completion_expected=active == "C")
        records.extend((SOURCE, json.dumps(alignment), json.dumps(transfer), "grounding-fixture"))
    class InventoryBackend(RecordingBackend):
        def _respond(self, messages, seed, delta):
            raw = super()._respond(messages, seed, delta)
            if raw != "grounding-fixture":
                return raw
            inventory = json.loads(messages[-1].content.split("Current verified local proof inventory: ", 1)[1].splitlines()[0])
            index = (len(self.calls) - 1) // 4
            payload = grounded_payload(index=index)
            payload["state_fingerprint"] = inventory["state_fingerprint"]
            if index < 2:
                payload.update(bindings=[], completion_expected=False,
                    expected_next_subgoal="C ∧ C" if index == 0 else "C")
                payload["steps"][0].update(operator="introduce" if index == 0 else "split",
                    inputs=[], input_propositions=[], output_proposition=payload["expected_next_subgoal"],
                    closes_subgoal=False)
            else:
                payload["bindings"][0]["inventory_id"] = "local:1"
            return json.dumps(payload)
    backend = InventoryBackend(records)
    result, _, observations = runner()
    result.decoder = "grounded"
    result.source_generator = SourceAnalogyGenerator(backend)
    result.alignment_generator = AlignmentGenerator(backend)
    result.transfer_generator = ProofTransferGenerator(backend)
    result.grounded_plan_generator = GroundedPlanGenerator(backend)
    result.structure_builder = adapter.proof_structure_for
    result.controller = SurpriseController(SurpriseConfig(3, 3, 0.2, 1, 0.0, 3))
    class LeanEvaluator:
        def evaluate_at(self, current, candidate):
            checked = adapter.run_tactic(current, candidate)
            if checked.status is TacticStatus.COMPLETE:
                return TacticEvaluation(checked, completed_proof=checked.proof)
            if checked.status is TacticStatus.ACCEPTED:
                return TacticEvaluation(checked, successor=SearchNode(checked.state, 1.0, current.id, candidate.source))
            return TacticEvaluation(checked)
    outcome = result.search(initial, SymbolicState("goal", problem.target, "lean4"), (), (), LeanEvaluator(), NeverCancelled())
    iterations = outcome.rollouts[0].analogy_trace.iterations
    assert outcome.completed_proof is not None
    assert len(iterations) == 3
    assert len(backend.calls) == 12
    assert (iterations[-1].model_calls, iterations[-1].lean_calls) == (12, 8)
    assert [item.compiled_tactics[0].text for item in iterations] == [
        "intro analogy_h", "constructor", "exact analogy_h"]
    assert observations[2].accepted_tactics == ("intro analogy_h", "constructor", "assumption")
    assert observations[2].latest_transfer.transfer_id == "transfer-1"
    assert len({item.inventory.state_fingerprint for item in iterations}) == 3
    assert iterations[0].actual_transition != "no goals"
    assert iterations[1].actual_transition != "no goals"


def test_grounded_implication_fallback_uses_its_own_predicted_successor():
    expression = "h : C → D\np : C\n⊢ D"
    payload = grounded_payload(expression, operator="apply_implication", proposition="C → D")
    payload["bindings"].append(dict(binding_id="binding-1", proof_role="witness",
        inventory_id="local:1", proposition="C", source_role=None, justification="Available input."))
    payload["steps"][0].update(inputs=["binding-0", "binding-1"],
        input_propositions=["C → D", "C"], output_proposition="D")
    result, backend, _ = grounded_runner(payload, lean_budget=3)
    backend.outputs = iter([SOURCE, ALIGNMENT,
        json.dumps(dict(json.loads(TRANSFER), active_subgoal="D")), json.dumps(payload)])
    result.tactics_per_iteration = 2
    successor = SymbolicState("next", "p : C\n⊢ C", "lean4")
    class FallbackEvaluator(GroundedEvaluator):
        def evaluate_at(self, current, candidate):
            if candidate.source == "apply h":
                self.calls.append(candidate.source)
                checked = TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)
                return TacticEvaluation(checked, successor=SearchNode(successor, 1.0, current.id, candidate.source))
            return super().evaluate_at(current, candidate)
    evaluator = FallbackEvaluator("never")
    outcome = result.search(SymbolicState("C", expression, "lean4"), state(), (), (), evaluator, NeverCancelled())
    iteration = outcome.rollouts[0].analogy_trace.iterations[0]
    assert evaluator.calls == ["exact h p", "apply h"]
    assert [item.proposal.predicted_state for item in iteration.attempts] == ["No remaining goals.", "C"]
    assert iteration.predicted_transition == "C"
    assert outcome.successor.state is successor
