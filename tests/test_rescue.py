from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest
import torch

from analogy.records import (
    CandidateOrigin,
    DiscardReason,
    DiscardedSuccessor,
    SearchNode,
    Embedding,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)
from analogy.rescue import (
    DirectRescueRunner,
    GenerationAttempt,
    GenerationStatus,
    GenericRescueOutcome,
    PolicyActionRecord,
    ProofRescueContext,
    RescueAttempt,
    RescueOutputError,
    RescueProposal,
    RescueRollout,
    RescueTermination,
    RolloutCollector,
    TacticEvaluation,
    derive_rescue_seed,
    parse_rescue_proposals,
    render_direct_rescue_prompt,
    render_repair_prompt,
)
from analogy.rl import PolicyAction


def state(identifier: str, expression: str | None = None) -> SymbolicState:
    return SymbolicState(identifier, expression or f"goal {identifier}", "lean4")


def candidate(source: str = "simp") -> TacticCandidate:
    return TacticCandidate(source, 1.0, CandidateOrigin.ANALOGICAL)


def action() -> PolicyActionRecord:
    return PolicyActionRecord(seed=7, delta_norm=0.25, log_probability=-0.5, entropy=1.0)


def attempt(status: TacticStatus, *, discarded: bool = False) -> RescueAttempt:
    selected = candidate()
    proposal = RescueProposal(selected, "smaller goal")
    next_state = state("next") if status is TacticStatus.ACCEPTED else None
    result = TacticResult(
        selected,
        status,
        state=next_state,
        proof="by simp" if status is TacticStatus.COMPLETE else None,
    )
    dropped = (
        DiscardedSuccessor(result, DiscardReason.REPEATED_STATE)
        if discarded
        else None
    )
    return RescueAttempt(proposal, result, dropped)


def test_parse_rescue_proposals_returns_new_analogical_candidates() -> None:
    parsed = parse_rescue_proposals(
        '{"predicted_state":"one goal remains","tactics":["cases h","simp_all"]}',
        limit=2,
    )

    assert [item.candidate.source for item in parsed] == ["cases h", "simp_all"]
    assert all(item.candidate.origin is CandidateOrigin.ANALOGICAL for item in parsed)
    assert all(item.predicted_state == "one goal remains" for item in parsed)
    assert sum(item.candidate.score for item in parsed) == pytest.approx(1.0)


def test_parser_validates_every_tactic_before_truncating() -> None:
    with pytest.raises(RescueOutputError, match="multiline"):
        parse_rescue_proposals(
            '{"predicted_state":"goal","tactics":["simp","exact h\\nassumption"]}',
            limit=1,
        )


def test_parser_accepts_one_json_fence_without_surrounding_prose() -> None:
    parsed = parse_rescue_proposals(
        '```json\n{"predicted_state":"done","tactics":["exact h"]}\n```',
        limit=1,
    )

    assert parsed[0].candidate.source == "exact h"


def test_generation_attempt_validates_status_and_diagnostic() -> None:
    attempt = GenerationAttempt(7, "not json", GenerationStatus.INVALID, False, "schema", "bad JSON")
    assert attempt.raw_text == "not json"
    with pytest.raises(ValueError, match="diagnostic"):
        GenerationAttempt(7, "text", GenerationStatus.INVALID, error_kind="schema")


def test_repair_prompt_contains_invalid_text_and_diagnostic() -> None:
    prompt = render_repair_prompt("not json", "expected one object")
    assert "not json" in prompt
    assert "expected one object" in prompt
    assert "Return only" in prompt


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "{}",
        '{"predicted_state":"","tactics":["simp"]}',
        '{"predicted_state":"goal","tactics":[]}',
        '{"predicted_state":"goal","tactics":["simp\\nexact h"]}',
        '{"predicted_state":"goal","tactics":["simp"," simp "]}',
        '{"predicted_state":"goal","tactics":["simp"],"extra":true}',
        '[{"predicted_state":"goal","tactics":["simp"]}]',
        '{"predicted_state":"goal","tactics":["sim\\u0000p"]}',
    ],
)
def test_parse_rescue_proposals_rejects_malformed_output(text: str) -> None:
    with pytest.raises(RescueOutputError):
        parse_rescue_proposals(text, limit=2)


@pytest.mark.parametrize("limit", [True, False, 0, -1, 1.5])
def test_parse_rescue_proposals_requires_positive_integer_limit(limit: object) -> None:
    with pytest.raises(ValueError, match="limit"):
        parse_rescue_proposals(
            '{"predicted_state":"goal","tactics":["simp"]}',
            limit=limit,  # type: ignore[arg-type]
        )


def test_direct_rescue_prompt_has_canonical_sections_and_json_contract() -> None:
    context = ProofRescueContext(
        state=SymbolicState("s", "P : Prop\nh : P\n⊢ P", "lean4", ("intro h",)),
        goal=state("g", "P"),
        accepted_tactics=("intro h",),
        failed_tactics=("exact missing",),
    )

    prompt = render_direct_rescue_prompt(context)

    headings = [
        "Overall target:",
        "Current goals:",
        "Accepted prefix:",
        "Failed tactics:",
    ]
    assert [prompt.index(heading) for heading in headings] == sorted(
        prompt.index(heading) for heading in headings
    )
    assert 'exactly the keys "predicted_state" and "tactics"' in prompt
    assert "intro h" in prompt
    assert "exact missing" in prompt


def test_rescue_records_are_immutable_and_normalize_iterables() -> None:
    context = ProofRescueContext(
        state("s"),
        state("g"),
        (item for item in ["intro h"]),  # type: ignore[arg-type]
        (item for item in ["simp"]),  # type: ignore[arg-type]
    )
    assert context.accepted_tactics == ("intro h",)
    with pytest.raises(FrozenInstanceError):
        context.state = state("other")  # type: ignore[misc]


@pytest.mark.parametrize("field", ["delta_norm", "log_probability", "entropy"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True])
def test_action_metadata_requires_finite_real_values(field: str, value: object) -> None:
    values: dict[str, object] = {
        "seed": 7,
        "delta_norm": 0.0,
        "log_probability": 0.0,
        "entropy": 0.0,
    }
    values[field] = value
    with pytest.raises(ValueError, match="finite"):
        PolicyActionRecord(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("seed", [True, -1, 1.5])
def test_action_metadata_requires_nonnegative_integer_seed(seed: object) -> None:
    with pytest.raises(ValueError, match="seed"):
        PolicyActionRecord(seed, 0.0, 0.0, 0.0)  # type: ignore[arg-type]


def test_rescue_attempt_requires_result_for_its_proposal_candidate() -> None:
    proposal = RescueProposal(candidate("simp"), "goal")
    result = TacticResult(candidate("aesop"), TacticStatus.ERROR)
    with pytest.raises(ValueError, match="candidate"):
        RescueAttempt(proposal, result)


def test_rescue_attempt_requires_discard_to_reference_result() -> None:
    first = candidate("simp")
    second = candidate("aesop")
    result = TacticResult(first, TacticStatus.ACCEPTED, state("next"))
    other = TacticResult(second, TacticStatus.ACCEPTED, state("other"))
    discarded = DiscardedSuccessor(other, DiscardReason.REPEATED_STATE)
    with pytest.raises(ValueError, match="discarded"):
        RescueAttempt(RescueProposal(first, "goal"), result, discarded)


@pytest.mark.parametrize("field", ["rollout_id", "state_id"])
def test_rollout_rejects_blank_ids(field: str) -> None:
    values = {
        "rollout_id": "rollout-1",
        "state_id": "state-1",
        "action": action(),
        "proposals": (),
        "attempts": (),
        "termination": RescueTermination.EXHAUSTED,
    }
    values[field] = " "
    with pytest.raises(ValueError, match=field):
        RescueRollout(**values)  # type: ignore[arg-type]


def test_rollout_rejects_invalid_termination_value() -> None:
    with pytest.raises(ValueError, match="termination"):
        RescueRollout("r", "s", action(), (), (), "complete")  # type: ignore[arg-type]


def test_complete_rollout_requires_a_completing_attempt() -> None:
    rejected = attempt(TacticStatus.ERROR)
    with pytest.raises(ValueError, match="complete"):
        RescueRollout(
            "r", "s", action(), (rejected.proposal,), (rejected,), RescueTermination.COMPLETE
        )


@pytest.mark.parametrize(
    "progress_attempt",
    [attempt(TacticStatus.ERROR), attempt(TacticStatus.ACCEPTED, discarded=True)],
)
def test_progress_rollout_requires_frontier_successor(progress_attempt: RescueAttempt) -> None:
    with pytest.raises(ValueError, match="progress"):
        RescueRollout(
            "r",
            "s",
            action(),
            (progress_attempt.proposal,),
            (progress_attempt,),
            RescueTermination.PROGRESS,
        )


def test_rollout_reward_must_be_finite() -> None:
    with pytest.raises(ValueError, match="reward"):
        RescueRollout(
            "r", "s", action(), (), (), RescueTermination.EXHAUSTED, math.nan
        )


def test_generic_outcome_forbids_conflicting_or_cancelled_results() -> None:
    node = SearchNode(state("next"), 0.5)
    with pytest.raises(ValueError, match="terminal"):
        GenericRescueOutcome("by simp", node)
    with pytest.raises(ValueError, match="cancelled"):
        GenericRescueOutcome(successor=node, cancelled=True)


def proof_representation():
    from analogy.rescue import ProofRepresentation

    proof_context = ProofRescueContext(state("stuck"), state("goal"), (), ())
    return ProofRepresentation(
        proof_context,
        "language",
        "structure",
        Embedding("model", (1.0, 0.0)),
        Embedding("model", (0.0, 1.0)),
    )


class FakeEncoder:
    def __init__(self):
        self.calls = []

    def encode(self, rescue_context):
        self.calls.append(rescue_context)
        return proof_representation()


class FakePolicy(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.anchor = torch.nn.Parameter(torch.tensor(0.0))
        self.seeds = []

    def sample(self, representation, generator):
        self.seeds.append(generator.initial_seed())
        scalar = self.anchor * 0.0
        return PolicyAction(torch.zeros(2), scalar, scalar)


class SequenceDeltaGenerator:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def generate_with_delta(self, messages, *, delta, layer, seed):
        self.calls.append((messages, delta, layer, seed))
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return output


class NeverCancelledToken:
    def is_cancelled(self):
        return False


class ToggleCancelledToken:
    def __init__(self, values):
        self.values = iter(values)
        self.last = False

    def is_cancelled(self):
        self.last = next(self.values, self.last)
        return self.last


def ordinary(source):
    return TacticCandidate(source, 1.0, CandidateOrigin.ORDINARY)


def direct_runner(outputs, **overrides):
    values = {
        "encoder": FakeEncoder(),
        "policy": FakePolicy(),
        "generator": SequenceDeltaGenerator(outputs),
        "rollouts_per_state": len(outputs),
        "tactics_per_rollout": 3,
        "max_rescue_verifier_calls": 8,
        "base_seed": 11,
        "hidden_layer": -1,
        "max_model_calls": max(1, len(outputs)),
        "max_repair_calls": 0,
    }
    values.update(overrides)
    return DirectRescueRunner(**values)


def evaluator_for(mapping, calls):
    def evaluate(selected):
        calls.append(selected.source)
        status, payload = mapping[selected.source]
        result = TacticResult(
            selected,
            status,
            state=payload if status is TacticStatus.ACCEPTED else None,
            proof=payload if status is TacticStatus.COMPLETE else None,
        )
        if status is TacticStatus.ACCEPTED:
            return TacticEvaluation(result, successor=SearchNode(payload, selected.score))
        if status is TacticStatus.COMPLETE:
            return TacticEvaluation(result, completed_proof=payload)
        return TacticEvaluation(result)

    return evaluate


def test_derived_rescue_seed_is_stable_bounded_and_operation_specific() -> None:
    first = derive_rescue_seed(7, "state", 2, "action")
    assert first == derive_rescue_seed(7, "state", 2, "action")
    assert 0 <= first < 2**63
    assert first != derive_rescue_seed(7, "state", 2, "generation")


def test_rollout_collector_retains_live_actions_until_drained() -> None:
    collector = RolloutCollector()
    live = PolicyAction(torch.zeros(2), torch.tensor(0.0), torch.tensor(0.0))
    record = RescueRollout(
        "r", "s", action(), (), (), RescueTermination.EXHAUSTED
    )
    collector.add(record, live)

    assert collector.rollouts == (record,)
    assert collector.live_actions == (live,)
    assert collector.drain() == ((record, live),)
    assert collector.rollouts == (record,)
    assert collector.live_actions == ()


def test_direct_runner_generates_new_tactic_and_returns_verified_progress() -> None:
    runner = direct_runner(
        ('{"predicted_state":"h exposed","tactics":["cases h","simp"]}',)
    )
    calls = []
    outcome = runner.search(
        state("stuck"),
        state("goal"),
        (ordinary("exact h"),),
        (TacticResult(ordinary("exact h"), TacticStatus.ERROR),),
        evaluator_for(
            {
                "cases h": (TacticStatus.ACCEPTED, state("split")),
                "simp": (TacticStatus.ERROR, None),
            },
            calls,
        ),
        NeverCancelledToken(),
    )

    assert outcome.successor.state.id == "split"
    assert outcome.rollouts[0].attempts[0].proposal.candidate.source == "cases h"
    assert outcome.rollouts[0].termination is RescueTermination.PROGRESS
    assert calls == ["cases h"]


def test_direct_runner_filters_ordinary_and_cross_rollout_duplicates() -> None:
    runner = direct_runner(
        (
            '{"predicted_state":"same","tactics":["ordinary","new"]}',
            '{"predicted_state":"same","tactics":["new","other"]}',
        )
    )
    calls = []
    outcome = runner.search(
        state("stuck"),
        state("goal"),
        (ordinary("ordinary"),),
        (TacticResult(ordinary("ordinary"), TacticStatus.ERROR),),
        evaluator_for(
            {
                "new": (TacticStatus.ERROR, None),
                "other": (TacticStatus.ERROR, None),
            },
            calls,
        ),
        NeverCancelledToken(),
    )

    assert calls == ["new", "other"]
    assert [item.termination for item in outcome.rollouts] == [
        RescueTermination.REJECTED,
        RescueTermination.REJECTED,
    ]


def test_direct_runner_isolates_malformed_generation_and_can_complete_later() -> None:
    runner = direct_runner(
        (
            "not json",
            '{"predicted_state":"done","tactics":["exact h"]}',
        )
    )
    calls = []
    outcome = runner.search(
        state("stuck"),
        state("goal"),
        (),
        (),
        evaluator_for({"exact h": (TacticStatus.COMPLETE, "by exact h")}, calls),
        NeverCancelledToken(),
    )

    assert outcome.completed_proof == "by exact h"
    assert [item.termination for item in outcome.rollouts] == [
        RescueTermination.INVALID_OUTPUT,
        RescueTermination.COMPLETE,
    ]


def test_direct_runner_records_invalid_output_and_successful_repair() -> None:
    generator = SequenceDeltaGenerator(
        ("not json", '{"predicted_state":"done","tactics":["exact h"]}')
    )
    runner = direct_runner(
        (),
        generator=generator,
        rollouts_per_state=1,
        max_model_calls=2,
        max_repair_calls=1,
    )
    calls = []

    outcome = runner.search(
        state("stuck"),
        state("goal"),
        (),
        (),
        evaluator_for({"exact h": (TacticStatus.COMPLETE, "by exact h")}, calls),
        NeverCancelledToken(),
    )

    assert outcome.completed_proof == "by exact h"
    assert [item.status for item in outcome.rollouts[0].generations] == [
        GenerationStatus.INVALID,
        GenerationStatus.VALID,
    ]
    assert outcome.rollouts[0].generations[0].raw_text == "not json"
    assert outcome.rollouts[0].generations[1].repair
    assert len(generator.calls) == 2


def test_direct_runner_does_not_repair_runtime_generation_error() -> None:
    generator = SequenceDeltaGenerator((RuntimeError("device lost"),))
    runner = direct_runner(
        (),
        generator=generator,
        rollouts_per_state=1,
        max_model_calls=2,
        max_repair_calls=1,
    )

    outcome = runner.search(
        state("stuck"), state("goal"), (), (), evaluator_for({}, []), NeverCancelledToken()
    )

    assert outcome.rollouts[0].termination is RescueTermination.GENERATION_ERROR
    assert outcome.rollouts[0].generations[0].status is GenerationStatus.RUNTIME_ERROR
    assert len(generator.calls) == 1


def test_direct_runner_records_all_rejected_and_exact_budget_exhaustion() -> None:
    runner = direct_runner(
        ('{"predicted_state":"stuck","tactics":["a","b","c"]}',),
        max_rescue_verifier_calls=2,
    )
    calls = []
    outcome = runner.search(
        state("stuck"),
        state("goal"),
        (),
        (),
        evaluator_for(
            {
                "a": (TacticStatus.ERROR, None),
                "b": (TacticStatus.ERROR, None),
                "c": (TacticStatus.ERROR, None),
            },
            calls,
        ),
        NeverCancelledToken(),
    )

    assert calls == ["a", "b"]
    assert outcome.rollouts[0].termination is RescueTermination.BUDGET_EXHAUSTED


def test_direct_runner_does_not_generate_after_verifier_budget_is_spent() -> None:
    generator = SequenceDeltaGenerator(
        (
            '{"predicted_state":"stuck","tactics":["a"]}',
            RuntimeError("second generation must not run"),
        )
    )
    runner = direct_runner(
        (), generator=generator, rollouts_per_state=2, max_rescue_verifier_calls=1
    )
    calls = []

    outcome = runner.search(
        state("stuck"),
        state("goal"),
        (),
        (),
        evaluator_for({"a": (TacticStatus.ERROR, None)}, calls),
        NeverCancelledToken(),
    )

    assert calls == ["a"]
    assert len(generator.calls) == 1
    assert [item.termination for item in outcome.rollouts] == [
        RescueTermination.REJECTED
    ]


def test_direct_runner_cancellation_preserves_prior_rollouts() -> None:
    runner = direct_runner(
        (
            '{"predicted_state":"stuck","tactics":["a"]}',
            '{"predicted_state":"stuck","tactics":["b"]}',
        )
    )
    calls = []
    outcome = runner.search(
        state("stuck"),
        state("goal"),
        (),
        (),
        evaluator_for({"a": (TacticStatus.ERROR, None)}, calls),
        ToggleCancelledToken((False, False, False, True)),
    )

    assert outcome.cancelled
    assert len(outcome.rollouts) >= 1
