import json

import torch

from analogy.analogy_transfer_loop import GeneratedDomainAnalogyLoopRunner, StoryOperatorIteration
from analogy.grounding import GroundingOperator
from analogy.proof_inventory import InventoryEntry, InventoryKind, LocalProofInventory
from analogy.proof_views import ProofStructure
from analogy.prompts import PromptCatalog
from analogy.records import CandidateOrigin, SearchNode, SymbolicState, TacticResult, TacticStatus
from analogy.rescue import TacticEvaluation
from analogy.rl import PolicyAction
from analogy.story_operator import StoryOperatorGenerator
from analogy.surprise import SurpriseConfig, SurpriseController


class _Backend:
    def __init__(self):
        self.calls = []

    def generate_with_delta(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if len(self.calls) > 1:
            raise AssertionError("production made more than one call")
        return json.dumps({
            "story": "Three matching keys hang beside the requested lock.",
            "operator": "exact_local",
            "bindings": [{"role": "key", "inventory_id": "local:0"}],
        })


class _Cancellation:
    def __init__(self, cancelled=False):
        self.cancelled = cancelled

    def is_cancelled(self):
        return self.cancelled


def _runner(tmp_path, backend):
    path = tmp_path / "prompts.yaml"
    path.write_text("""prompts:
  story_operator:
    version: story-v1
    system: Return strict JSON.
    user: 'Need={{need_json}} Items={{inventory_json}} Ops={{operators_json}}'
""")
    inventory = LocalProofInventory(
        "fingerprint", "P", "P",
        tuple(InventoryEntry(f"local:{i}", InventoryKind.LOCAL, f"h{i}", "P",
                             '{"root":"target","children":[]}') for i in range(3)),
        (InventoryEntry("active_subgoal", InventoryKind.ACTIVE_SUBGOAL, None, "P",
                        '{"root":"target","children":[]}'),),
    )
    action = PolicyAction(torch.zeros(2), torch.tensor(0.0), torch.tensor(0.0))
    return GeneratedDomainAnalogyLoopRunner(
        structure_builder=lambda _state: ProofStructure(
            1, '{"root":"target","children":[]}',
            tuple('{"root":"target","children":[]}' for _ in range(3))),
        observation_builder=lambda *_args: object(),
        action_sampler=lambda *_args: action,
        decision_generator=StoryOperatorGenerator(
            backend, PromptCatalog.load(path), (GroundingOperator.EXACT_LOCAL,)),
        transition_comparator=lambda *_args: None,
        controller=SurpriseController(SurpriseConfig(1, 1, 0.2, 1, 0.01, 1)),
        max_model_calls=1, max_lean_calls=3,
        inventory_builder=lambda *_args, **_kwargs: inventory,
    )


def _evaluation(candidate):
    if candidate.source == "exact h0":
        return TacticEvaluation(
            TacticResult(candidate, TacticStatus.COMPLETE, proof="by exact h0"),
            completed_proof="by exact h0",
        )
    if candidate.source == "exact h1":
        return TacticEvaluation(TacticResult(candidate, TacticStatus.ERROR, diagnostics=("bad",)))
    state = SymbolicState("successor", "⊢ Q", "lean4")
    result = TacticResult(candidate, TacticStatus.ACCEPTED, state=state)
    return TacticEvaluation(result, successor=SearchNode(state, 1, None, candidate.source))


def test_runner_uses_one_call_and_verifies_all_candidates_after_completion(tmp_path):
    backend = _Backend()
    runner = _runner(tmp_path, backend)
    state = SymbolicState("start", "h0 h1 h2 : P\n⊢ P", "lean4")
    goal = SymbolicState("goal", "P", "lean4")
    outcome = runner.search(state, goal, (), (), _evaluation, _Cancellation())

    iteration = outcome.rollouts[0].analogy_trace.iterations[0]
    assert isinstance(iteration, StoryOperatorIteration)
    assert len(backend.calls) == 1
    assert [attempt.result.status for attempt in iteration.attempts] == [
        TacticStatus.COMPLETE, TacticStatus.ERROR, TacticStatus.ACCEPTED,
    ]
    assert iteration.model_calls == 1
    assert iteration.lean_calls == 3
    assert iteration.expected_operator is GroundingOperator.EXACT_LOCAL
    assert "expected_operator" not in backend.calls[0][0][1].content
    assert outcome.completed_proof == "by exact h0"


def test_cancelled_before_generation_makes_no_model_or_lean_call(tmp_path):
    backend = _Backend()
    runner = _runner(tmp_path, backend)
    outcome = runner.search(
        SymbolicState("start", "h : P\n⊢ P", "lean4"),
        SymbolicState("goal", "P", "lean4"), (), (), _evaluation, _Cancellation(True),
    )
    assert backend.calls == []
    assert outcome.cancelled


def test_cancellation_is_allowed_to_stop_an_active_candidate_batch(tmp_path):
    backend = _Backend()
    runner = _runner(tmp_path, backend)
    cancellation = _Cancellation()
    lean_calls = []

    def evaluate(candidate):
        lean_calls.append(candidate.source)
        cancellation.cancelled = True
        return TacticEvaluation(TacticResult(candidate, TacticStatus.ERROR, diagnostics=("bad",)))

    outcome = runner.search(
        SymbolicState("start", "h0 h1 h2 : P\n⊢ P", "lean4"),
        SymbolicState("goal", "P", "lean4"), (), (), evaluate, cancellation,
    )
    assert lean_calls == ["exact h0"]
    assert outcome.cancelled


def test_infrastructure_exception_is_allowed_to_stop_candidate_batch(tmp_path):
    backend = _Backend()
    runner = _runner(tmp_path, backend)
    lean_calls = []

    def evaluate(candidate):
        lean_calls.append(candidate.source)
        if len(lean_calls) == 2:
            raise RuntimeError("Lean process exited")
        return TacticEvaluation(TacticResult(candidate, TacticStatus.ERROR, diagnostics=("bad",)))

    outcome = runner.search(
        SymbolicState("start", "h0 h1 h2 : P\n⊢ P", "lean4"),
        SymbolicState("goal", "P", "lean4"), (), (), evaluate, _Cancellation(),
    )
    iteration = outcome.rollouts[0].analogy_trace.iterations[0]
    assert lean_calls == ["exact h0", "exact h1"]
    assert iteration.failed_stage == "lean_attempt"
    assert "Lean process exited" in iteration.failure_diagnostic

