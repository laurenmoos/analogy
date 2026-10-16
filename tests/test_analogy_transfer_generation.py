"""Stage boundaries, strict parsing, and bounded source-transfer generation."""

import json
from dataclasses import replace

import pytest
import torch

from analogy.analogy_generation import AnalogyGenerationFailure
from analogy.analogy_transfer import (
    AnalogyObservation, TransferOutputError, derive_proof_need,
    parse_alignment, parse_proof_reasoning_transfer, parse_source_analogy,
)
from analogy.analogy_transfer_generation import (
    ALIGNMENT_PROMPT_VERSION, SOURCE_PROMPT_VERSION, TACTIC_PROMPT_VERSION,
    TRANSFER_PROMPT_VERSION, AlignmentGenerator, ProofTransferGenerator,
    SourceAnalogyGenerator, TransferredTacticGenerator,
    parse_transferred_tactic_plan, render_alignment_prompt, render_source_prompt,
    render_transfer_prompt, render_transferred_tactic_prompt,
)
from analogy.analogy_transfer_generation import (
    GROUNDING_PROMPT_VERSION, GroundedPlanGenerator, render_grounding_prompt,
)
from analogy.proof_inventory import build_local_inventory
from analogy.proof_views import ProofStructure
from analogy.records import SymbolicState
from analogy.rescue import GenerationStatus
from analogy.rl import PolicyAction


STRUCTURE = ProofStructure(1, '{"root":"atom"}', ('{"root":"target"}',))
NEED = derive_proof_need(STRUCTURE, "C", fixed_target="C → S")
SOURCE = json.dumps({
    "domain": "warehouse logistics",
    "source_before": "A worker needs a spare wheel; a matching wheel is on the shelf.",
    "source_after": "The worker takes the matching wheel from the shelf.",
    "source_roles": ["requested wheel", "shelved wheel"],
    "relation_chain": ["The shelved wheel matches the request.", "Taking it fulfills the request."],
    "transformation": {
        "operation": "reuse", "input_arity": "one", "output_arity": "one",
        "permutation": "none", "information_flow": ["preserve"],
        "focus_before": "required item", "focus_after": "available item",
        "domain_neutral_summary": "Use the available matching resource.",
    },
    "rationale": "An available matching resource meets the outstanding need.",
})
ALIGNMENT = json.dumps({
    "mapping": {"requested wheel": "active_subgoal", "shelved wheel": "matching_hypothesis"},
    "invariant": "Available resource meets the need.",
    "source_operation": "Take the wheel.", "proof_operation": "Use the matching premise.",
    "limitation": "Physical movement is incidental.", "confidence": 0.75,
})
TRANSFER = json.dumps({
    "active_subgoal": "C", "premises": ["matching_hypothesis"],
    "reasoning_steps": ["The matching premise establishes the active requirement."],
    "predicted_next_subgoal": "No remaining goals.", "completion_expected": True,
})
TACTICS = json.dumps({
    "predicted_state": "no goals", "tactics": ["exact h"],
    "motivating_transfer_id": "transfer-2",
})


def observation():
    return AnalogyObservation(
        "C → S", "h : C\n⊢ C", "⊢ C → S", "intro h", ("intro h",),
        (("exact missing", ("unknown identifier",)),), None, None, None,
        None, None, None, None, False, 2, 8, 6,
    )


def records():
    source = parse_source_analogy(SOURCE, analogy_id="source-2", need=NEED)
    alignment = parse_alignment(ALIGNMENT, source=source, need=NEED, alignment_id="alignment-2")
    transfer = parse_proof_reasoning_transfer(TRANSFER, need=NEED, alignment=alignment,
                                             transfer_id="transfer-2")
    return source, alignment, transfer


class RecordingBackend:
    """Replace only expensive model inference; retain real parsing and services."""

    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def _respond(self, messages, seed, delta):
        self.calls.append((messages, seed, delta))
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return output

    def generate(self, messages, *, seed):
        return self._respond(messages, seed, None)

    def generate_with_delta(self, messages, *, delta, layer, seed):
        assert layer == -1
        return self._respond(messages, seed, delta)


def test_source_prompt_keeps_rationale_inside_the_concrete_source_contract():
    prompt = render_source_prompt(observation(), NEED)
    assert "including rationale" in prompt
    assert "words proof, Lean, or tactic" in prompt
    assert "Explain the fit using only domain-neutral transformation language" in prompt


def generate(stage, backend, **kwargs):
    source, alignment, transfer = records()
    options = dict(hidden_layer=-1, base_seed=7, max_repair_calls=3)
    if stage == "source":
        action = PolicyAction(torch.tensor([0.25, 0.5], requires_grad=True),
                              torch.tensor(0.0), torch.tensor(0.0))
        return SourceAnalogyGenerator(backend, **options).generate(
            observation(), NEED, action, analogy_index=2, **kwargs)
    if stage == "alignment":
        return AlignmentGenerator(backend, **options).generate(
            observation(), NEED, source, analogy_index=2, **kwargs)
    if stage == "transfer":
        return ProofTransferGenerator(backend, **options).generate(
            observation(), NEED, source, alignment, analogy_index=2, **kwargs)
    return TransferredTacticGenerator(backend, **options).generate(
        SymbolicState("s", "h : C\n⊢ C", "lean4"), STRUCTURE,
        source, alignment, transfer, candidate_count=1, **kwargs)


STAGES = [("source", SOURCE, "source", SOURCE_PROMPT_VERSION),
          ("alignment", ALIGNMENT, "alignment", ALIGNMENT_PROMPT_VERSION),
          ("transfer", TRANSFER, "transfer", TRANSFER_PROMPT_VERSION),
          ("tactic", TACTICS, "plan", TACTIC_PROMPT_VERSION)]


def test_latent_action_is_used_only_for_source_generation():
    backend = RecordingBackend((SOURCE, ALIGNMENT, TRANSFER, TACTICS))
    generated = [generate(stage, backend, max_attempts=2) for stage, *_ in STAGES]
    assert [result.attempts[0].status for result in generated] == [GenerationStatus.VALID] * 4
    assert torch.equal(backend.calls[0][2], torch.tensor([0.25, 0.5]))
    assert backend.calls[0][2].requires_grad is False
    assert [call[2] for call in backend.calls[1:]] == [None, None, None]
    assert len({call[1] for call in backend.calls}) == 4
    assert generated[0].source.analogy_id == "source-2"
    assert generated[1].alignment.alignment_id == "alignment-2"
    assert generated[2].transfer.transfer_id == "transfer-2"
    assert generated[3].plan.motivating_transfer_id == "transfer-2"


@pytest.mark.parametrize("stage,raw,field,version", STAGES)
def test_each_stage_preserves_invalid_and_repaired_attempts_with_version(stage, raw, field, version):
    backend = RecordingBackend(("not json", raw))
    result = generate(stage, backend, max_attempts=8)
    assert getattr(result, field) is not None
    assert result.prompt_version == version
    assert [attempt.raw_text for attempt in result.attempts] == ["not json", raw]
    assert [attempt.status for attempt in result.attempts] == [GenerationStatus.INVALID, GenerationStatus.VALID]
    assert [attempt.repair for attempt in result.attempts] == [False, True]
    assert result.attempts[1].seed == result.attempts[0].seed + 1
    first_prompt = backend.calls[0][0][1].content
    repair_prompt = backend.calls[1][0][1].content
    assert version in first_prompt and first_prompt in repair_prompt
    assert "not json" in repair_prompt
    if stage == "source":
        assert all(call[2] is not None for call in backend.calls)
    else:
        assert all(call[2] is None for call in backend.calls)


@pytest.mark.parametrize("stage,raw,field,version", STAGES)
@pytest.mark.parametrize("budget,expected", [(1, 1), (2, 2), (9, 2)])
def test_each_stage_has_at_most_one_schema_repair_within_budget(stage, raw, field, version, budget, expected):
    backend = RecordingBackend(("not json", "still invalid", raw))
    with pytest.raises(AnalogyGenerationFailure) as error:
        generate(stage, backend, max_attempts=budget)
    assert len(backend.calls) == len(error.value.attempts) == expected
    assert all(attempt.status is GenerationStatus.INVALID for attempt in error.value.attempts)


@pytest.mark.parametrize("stage,raw,field,version", STAGES)
def test_runtime_failures_preserve_diagnostic_without_retry(stage, raw, field, version):
    backend = RecordingBackend((RuntimeError("model offline"), raw))
    with pytest.raises(AnalogyGenerationFailure) as error:
        generate(stage, backend, max_attempts=2)
    assert len(backend.calls) == 1
    assert error.value.attempts[0].status is GenerationStatus.RUNTIME_ERROR
    assert error.value.attempts[0].diagnostic == "model offline"


def test_prompts_keep_source_alignment_transfer_and_tactic_schemas_separate():
    source, alignment, transfer = records()
    prompt = render_source_prompt(observation(), NEED)
    assert "concrete source domain" in prompt and "Do not restate the proof" in prompt
    assert all(key in prompt for key in json.loads(SOURCE))
    assert '"rejected_tactics"' in prompt and "unknown identifier" in prompt
    assert '"previous_state"' in prompt and "intro h" in prompt
    aligned = render_alignment_prompt(observation(), NEED, source)
    assert "requested wheel" in aligned and "matching_hypothesis" in aligned
    assert all(key in aligned for key in json.loads(ALIGNMENT))
    transferred = render_transfer_prompt(observation(), NEED, source, alignment)
    assert "tactic-free" in transferred
    assert "alignment-2" in transferred and "source-2" in transferred
    assert all(key in transferred for key in json.loads(TRANSFER))
    tactic_prompt = render_transferred_tactic_prompt(
        SymbolicState("s", "h : C\n⊢ C", "lean4"), STRUCTURE,
        source, alignment, transfer, candidate_count=1)
    assert "transfer-2" in tactic_prompt and "motivating_transfer_id" in tactic_prompt
    assert "exactly 1 distinct" in tactic_prompt and "Lean 4" in tactic_prompt


@pytest.mark.parametrize("raw", [
    TACTICS.replace("transfer-2", "hop-2"),
    TACTICS.replace("motivating_transfer_id", "motivating_hop_id"),
    TACTICS[:-1] + ', "unknown":true}',
    TACTICS[:-1] + ', "predicted_state":"duplicate"}',
    TACTICS.replace('["exact h"]', '["exact h", " exact   h "]'),
    TACTICS.replace('["exact h"]', '[]'),
    TACTICS.replace('["exact h"]', '["exact h\\nexact h"]'),
    TACTICS.replace('["exact h"]', '["exact h\\rexact h"]'),
    TACTICS.replace('"no goals"', 'null'),
    '[]', "not json",
])
def test_tactic_parser_requires_strict_schema_single_line_unique_tactics_and_transfer_citation(raw):
    with pytest.raises(TransferOutputError):
        parse_transferred_tactic_plan(raw, records()[2])


def test_tactic_parser_canonicalizes_valid_tactics_and_cites_transfer():
    plan = parse_transferred_tactic_plan(TACTICS.replace("exact h", " exact   h "), records()[2])
    assert plan.tactics == ("exact h",)
    assert plan.predicted_state == "no goals"
    assert plan.motivating_transfer_id == "transfer-2"


def test_tactic_generation_repairs_wrong_candidate_count():
    backend = RecordingBackend((TACTICS.replace('["exact h"]', '["exact h", "assumption"]'), TACTICS))
    result = generate("tactic", backend, max_attempts=2)
    assert result.plan.tactics == ("exact h",)
    assert result.attempts[0].status is GenerationStatus.INVALID


@pytest.mark.parametrize("budget", [0, -1, True, 1.5])
def test_invalid_attempt_budget_never_calls_model(budget):
    backend = RecordingBackend((SOURCE,))
    with pytest.raises(ValueError, match="max_attempts"):
        generate("source", backend, max_attempts=budget)
    assert backend.calls == []


def test_source_generation_passes_need_to_proposition_leakage_guard():
    leaking = SOURCE.replace("A worker needs", "C needs")
    backend = RecordingBackend((leaking, SOURCE))
    result = generate("source", backend, max_attempts=2)
    assert result.attempts[0].status is GenerationStatus.INVALID
    assert "proposition" in result.attempts[0].diagnostic


def test_transfer_generation_rejects_wrong_active_subgoal():
    backend = RecordingBackend((TRANSFER.replace('"active_subgoal": "C"', '"active_subgoal": "C → S"'), TRANSFER))
    result = generate("transfer", backend, max_attempts=2)
    assert result.attempts[0].status is GenerationStatus.INVALID
    assert result.transfer.active_subgoal == "C"


def test_transfer_and_tactic_generators_reject_mismatched_record_chain_before_model_call():
    source, alignment, transfer = records()
    backend = RecordingBackend((TRANSFER, TACTICS))
    with pytest.raises(TransferOutputError):
        ProofTransferGenerator(backend).generate(
            observation(), NEED, source, replace(alignment, source_analogy_id="outside"), analogy_index=2)
    with pytest.raises(TransferOutputError):
        TransferredTacticGenerator(backend).generate(
            SymbolicState("s", "h : C\n⊢ C", "lean4"), STRUCTURE,
            source, alignment, replace(transfer, alignment_id="outside"), candidate_count=1)
    assert backend.calls == []


def test_explicitly_disabled_schema_repair_preserves_the_first_invalid_attempt():
    backend = RecordingBackend(("not json", SOURCE))
    action = PolicyAction(torch.zeros(2), torch.tensor(0.0), torch.tensor(0.0))
    with pytest.raises(AnalogyGenerationFailure) as error:
        SourceAnalogyGenerator(backend, max_repair_calls=0).generate(
            observation(), NEED, action, analogy_index=0, max_attempts=2)
    assert len(backend.calls) == len(error.value.attempts) == 1
    assert error.value.attempts[0].raw_text == "not json"


@pytest.mark.parametrize("count", [0, -1, True, 1.5])
def test_invalid_candidate_count_never_calls_model(count):
    source, alignment, transfer = records()
    backend = RecordingBackend((TACTICS,))
    with pytest.raises(ValueError, match="candidate_count"):
        TransferredTacticGenerator(backend).generate(
            SymbolicState("s", "h : C\n⊢ C", "lean4"), STRUCTURE,
            source, alignment, transfer, candidate_count=count)
    assert backend.calls == []


def test_stage_seeds_are_repeatable_and_analogy_index_changes_tactic_seed():
    source, alignment, transfer = records()
    backend = RecordingBackend((TACTICS, TACTICS, TACTICS))
    service = TransferredTacticGenerator(backend, base_seed=7)
    for index in (2, 2, 3):
        result = service.generate(
            SymbolicState("s", "h : C\n⊢ C", "lean4"), STRUCTURE,
            source, alignment, transfer, candidate_count=1, analogy_index=index)
        assert result.attempts[0].seed == backend.calls[-1][1]
    seeds = [call[1] for call in backend.calls]
    assert seeds[0] == seeds[1]
    assert seeds[2] != seeds[0]


def grounded_context():
    inv = build_local_inventory(SymbolicState("s", "h : C\n⊢ C", "lean4"), STRUCTURE,
                                fixed_target="C → S")
    payload = {
        "motivating_transfer_id": "transfer-2", "state_fingerprint": inv.state_fingerprint,
        "bindings": [{"binding_id": "binding-0", "proof_role": "matching_hypothesis",
                      "inventory_id": "local:0", "proposition": "C", "source_role": None,
                      "justification": "The matching premise is available."}],
        "steps": [{"step_id": "step-0", "operator": "exact_local", "inputs": ["binding-0"],
                   "input_propositions": ["C"], "output_proposition": "C",
                   "transfer_step_index": 0, "closes_subgoal": True}],
        "expected_next_subgoal": "No remaining goals.", "completion_expected": True,
        "surface_hints": [],
    }
    return inv, records()[2], payload


def test_grounding_prompt_renders_exact_inventory_transfer_and_typed_contract():
    inv, transfer, _ = grounded_context()
    prompt = render_grounding_prompt(inv, transfer)
    assert inv.state_fingerprint in prompt and '"identifier": "h"' in prompt
    assert '"inventory_id": "local:0"' in prompt and '"proposition": "C"' in prompt
    assert '"transfer_id": "transfer-2"' in prompt
    assert GROUNDING_PROMPT_VERSION in prompt
    assert "Do not emit raw Lean" in prompt and "Copy inventory IDs exactly" in prompt
    assert "exact_local" in prompt and "rewrite_backward" in prompt


def test_grounded_generator_repairs_unknown_id_once_without_latent_action():
    inv, transfer, payload = grounded_context()
    valid = json.dumps(payload)
    payload["bindings"][0]["inventory_id"] = "local:999"
    invalid = json.dumps(payload)
    backend = RecordingBackend((invalid, valid))
    result = GroundedPlanGenerator(backend, base_seed=7, max_repair_calls=5).generate(
        inv, transfer, plan_id="caller-plan", analogy_index=2, max_attempts=9)
    assert result.plan.plan_id == "caller-plan"
    assert result.prompt_version == GROUNDING_PROMPT_VERSION
    assert [attempt.status for attempt in result.attempts] == [GenerationStatus.INVALID, GenerationStatus.VALID]
    assert [attempt.raw_text for attempt in result.attempts] == [invalid, valid]
    assert [attempt.repair for attempt in result.attempts] == [False, True]
    assert all(call[2] is None for call in backend.calls)
    assert result.attempts[1].seed == result.attempts[0].seed + 1
    assert backend.calls[0][0][1].content in backend.calls[1][0][1].content


@pytest.mark.parametrize("budget,count", [(1, 1), (2, 2), (9, 2)])
def test_persistent_grounding_failure_never_returns_a_plan_for_compilation(budget, count):
    inv, transfer, payload = grounded_context()
    payload["bindings"][0]["inventory_id"] = "missing"
    backend = RecordingBackend((json.dumps(payload),) * 3)
    compiled = []
    with pytest.raises(AnalogyGenerationFailure) as error:
        result = GroundedPlanGenerator(backend).generate(
            inv, transfer, plan_id="plan", max_attempts=budget)
        compiled.append(result.plan)
    assert compiled == []
    assert len(backend.calls) == len(error.value.attempts) == count
    assert all(attempt.status is GenerationStatus.INVALID for attempt in error.value.attempts)


def test_grounded_generator_checks_cancellation_before_repair():
    inv, transfer, _ = grounded_context()

    class Cancellation:
        def __init__(self):
            self.checks = 0

        def is_cancelled(self):
            self.checks += 1
            return self.checks > 1

    backend = RecordingBackend(("not json", "not json"))
    with pytest.raises(AnalogyGenerationFailure, match="cancelled") as error:
        GroundedPlanGenerator(backend).generate(inv, transfer, plan_id="plan",
                                               cancellation=Cancellation())
    assert len(backend.calls) == len(error.value.attempts) == 1


def test_grounded_generator_rejects_stale_transfer_before_model_call():
    inv, transfer, _ = grounded_context()
    backend = RecordingBackend(())
    with pytest.raises(TransferOutputError, match="active subgoal"):
        GroundedPlanGenerator(backend).generate(
            inv, replace(transfer, active_subgoal="D"), plan_id="plan")
    assert backend.calls == []
