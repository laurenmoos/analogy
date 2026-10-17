from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

import pytest

from analogy.experiments.activation_generation import (
    ActivationAnalogyGenerator,
    CandidateGenerationFailure,
    build_tactic_union,
    generate_neutral_slate,
    generate_prompted_slate,
    generate_selected_slate,
    generate_shuffled_slate,
    render_activation_tactic_messages,
    shuffle_alignment,
)
from analogy.experiments.activation_records import load_activation_eval_config
from analogy.inference.prompts import PromptCatalog
from analogy.records import SymbolicState
from analogy.solver.proof_views import ProofStructure
from analogy.solver.rescue import ANALOGY_FAMILIES, ProofRescueContext
from analogy.transfer.domain import derive_proof_need


ROOT = Path(__file__).parents[1]
CONTEXT = ProofRescueContext(
    SymbolicState("state", "P → P", "lean4"), SymbolicState("goal", "P → P", "lean4"),
    ("skip",), ("rfl -- rejected",),
)
STRUCTURE = ProofStructure(1, '{"root":"implication","children":[{"root":"atom"},{"root":"target"}]}', ())
NEED = derive_proof_need(STRUCTURE, CONTEXT.state.expression, fixed_target=CONTEXT.goal.expression)


class ScriptedProvider:
    """Only the external model execution/tokenizer boundary is substituted."""

    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []
        self.counted = []

    def generate(self, messages, *, seed):
        self.calls.append((messages, seed))
        response = next(self.outputs)
        if isinstance(response, Exception):
            raise response
        return response

    def count_token_positions(self, messages, completion):
        self.counted.append((messages, completion))
        return 101

    def generate_with_usage(self, messages, *, seed):
        from analogy.inference.model_provider import GenerationResult
        text = self.generate(messages, seed=seed)
        return GenerationResult(text, self.count_token_positions(messages, text))


def source(domain="clockwork"):
    return json.dumps({
        "domain": domain, "source_before": "A wheel waits beside a socket.",
        "source_after": "The wheel enters the socket and turns the gear.",
        "source_roles": ["wheel", "socket"],
        "relation_chain": ["The wheel enters the socket.", "The gear begins turning."],
        "transformation": {"operation": "introduce", "input_arity": "one", "output_arity": "one",
                           "permutation": "none", "information_flow": ["add"],
                           "focus_before": "waiting input", "focus_after": "moving assembly",
                           "domain_neutral_summary": "Supply an input to continue."},
        "rationale": "A fitting input enables continuation.",
    })


def alignment():
    return json.dumps({
        "mapping": {"wheel": "introduced_assumption", "socket": "active_subgoal"},
        "invariant": "An input enables continuation.", "source_operation": "Insert the wheel.",
        "proof_operation": "Expose an input.", "limitation": "Mechanical motion does not establish truth.",
        "confidence": 0.7,
    })


def transfer(active="P → P"):
    return json.dumps({"active_subgoal": active, "premises": [],
                       "reasoning_steps": ["Expose the conditional input."],
                       "predicted_next_subgoal": "The remaining requirement.", "completion_expected": False})


def tactics(*items):
    return json.dumps({"predicted_state": "remaining goals", "tactics": list(items or ("intro hp",))})


def config(**kwargs):
    return replace(load_activation_eval_config(ROOT / "config.activation-analogy.yaml"), **kwargs)


def generated():
    provider = ScriptedProvider([source(), alignment(), transfer(), tactics()])
    return ActivationAnalogyGenerator(provider, config(domain_cues=("mechanical",), analogy_candidates=1)).generate_candidates(
        CONTEXT, STRUCTURE, NEED, seed=9,
    )[0]


def test_generates_one_complete_candidate_per_domain_with_exact_raw_attempts_and_cost():
    raw = [value for domain in config().domain_cues for value in (source(domain), alignment(), transfer(), tactics())]
    provider = ScriptedProvider(raw)
    candidates = ActivationAnalogyGenerator(provider, config()).generate_candidates(CONTEXT, STRUCTURE, NEED, seed=9)
    assert len(candidates) == 4
    assert len({item.source.analogy_id for item in candidates}) == 4
    assert len({item.record.candidate_id for item in candidates}) == 4
    assert all(item.record.role_mapping == (("wheel", "introduced_assumption"), ("socket", "active_subgoal")) for item in candidates)
    assert all(item.record.limitation == "Mechanical motion does not establish truth." for item in candidates)
    assert len(provider.calls) == 16
    assert [seed for _, seed in provider.calls] == list(range(9, 25))
    assert sum(item.processed_token_positions for item in candidates) == 1616
    assert [output for item in candidates for output in item.raw_outputs] == raw
    assert [attempt.raw_text for item in candidates for attempt in item.attempts] == raw
    assert provider.counted == [(messages, output) for item in candidates for messages, output in zip(item.messages, item.raw_outputs)]
    for index, candidate in enumerate(candidates):
        assert config().domain_cues[index] in candidate.messages[0][1].content
        assert "P → P" not in candidate.messages[0][1].content
        payload = json.loads(candidate.messages[3][1].content.split("Complete explicit candidate:\n", 1)[1].split("\nReturn ", 1)[0])
        assert payload["source"]["source_roles"] == ["wheel", "socket"]
        assert payload["alignment"]["limitation"] == candidate.record.limitation
        assert payload["transfer"]["active_subgoal"] == "P → P"


@pytest.mark.parametrize("stage,index,bad", [
    ("source", 0, "{bad"), ("alignment", 1, "{}"),
    ("transfer", 2, transfer("wrong subgoal")), ("tactic", 3, "{}"),
])
def test_malformed_stage_is_typed_and_never_retried(stage, index, bad):
    outputs = [source(), alignment(), transfer(), tactics()][:index] + [bad]
    provider = ScriptedProvider(outputs)
    result = ActivationAnalogyGenerator(provider, config(domain_cues=("mechanical",), analogy_candidates=1)).generate_candidates(
        CONTEXT, STRUCTURE, NEED, seed=2,
    )[0]
    assert isinstance(result, CandidateGenerationFailure)
    assert result.stage == stage
    assert len(provider.calls) == index + 1
    assert result.raw_outputs == tuple(outputs)
    assert result.processed_token_positions == 101 * (index + 1)
    assert result.attempts[-1].status.value == "invalid"
    assert result.diagnostic


def test_failed_candidate_does_not_consume_next_domain_as_repair():
    provider = ScriptedProvider(["{}", source("ecology"), alignment(), transfer(), tactics()])
    result = ActivationAnalogyGenerator(provider, config(domain_cues=("mechanical", "ecological"), analogy_candidates=2)).generate_candidates(
        CONTEXT, STRUCTURE, NEED, seed=10,
    )
    assert isinstance(result[0], CandidateGenerationFailure)
    assert result[1].record.source_domain == "ecology"
    assert [seed for _, seed in provider.calls] == [10, 14, 15, 16, 17]


def test_empty_generation_is_preserved_and_charged_as_invalid_not_runtime_error():
    provider = ScriptedProvider([""])
    result = ActivationAnalogyGenerator(provider, config(domain_cues=("mechanical",), analogy_candidates=1)).generate_candidates(
        CONTEXT, STRUCTURE, NEED, seed=0,
    )[0]
    assert isinstance(result, CandidateGenerationFailure)
    assert result.raw_outputs == ("",)
    assert result.processed_token_positions == 101
    assert result.attempts[0].status.value == "invalid"
    assert provider.counted == [(result.messages[0], "")]


def test_runtime_failure_preserves_diagnostic_and_unknown_usage_without_retry():
    provider = ScriptedProvider([TimeoutError("provider timed out")])
    slate = generate_neutral_slate(provider, CONTEXT, seed=3)
    assert slate.tactics == ()
    assert slate.error == "provider timed out"
    assert slate.attempts[0].status.value == "runtime_error"
    assert slate.processed_token_positions == 0
    assert len(provider.calls) == 1


def test_context_need_mismatch_cannot_send_stale_subgoal_to_model():
    provider = ScriptedProvider([])
    with pytest.raises(ValueError, match="need"):
        ActivationAnalogyGenerator(provider, config()).generate_candidates(
            CONTEXT, STRUCTURE, replace(NEED, active_subgoal="other"), seed=0,
        )
    assert provider.calls == []


def test_matched_controls_use_existing_catalog_and_prompted_family_rotation():
    catalog = PromptCatalog.load(ROOT / "prompts.yaml")
    provider = ScriptedProvider([tactics("intro hp")])
    neutral = generate_neutral_slate(provider, CONTEXT, seed=4, catalog=catalog)
    assert neutral.messages[0] == catalog.messages(
        "direct_rescue", overall_target="P → P", current_goals="P → P", accepted="skip", failed="rfl -- rejected",
    )
    assert neutral.processed_token_positions == 101
    offset = int(hashlib.sha256("P → P".encode()).hexdigest(), 16) % len(ANALOGY_FAMILIES)
    for index in range(6):
        family = ANALOGY_FAMILIES[(offset + index) % len(ANALOGY_FAMILIES)]
        raw = json.dumps({"analogy": f"[{family}] a fitting input", "predicted_state": "remaining goals", "tactics": ["intro hp"]})
        prompted = generate_prompted_slate(ScriptedProvider([raw]), CONTEXT, seed=4 + index, attempt_index=index, catalog=catalog)
        assert prompted.analogy == f"[{family}] a fitting input"
        assert prompted.messages[0] == catalog.messages(
            "analogy_rescue", overall_target="P → P", current_goals="P → P", accepted="skip", failed="rfl -- rejected", analogy_family=family,
        )


def test_prompted_family_mismatch_preserves_raw_failure_without_repair():
    raw = json.dumps({"analogy": "[unconfigured] a wheel", "predicted_state": "next", "tactics": ["intro hp"]})
    slate = generate_prompted_slate(ScriptedProvider([raw]), CONTEXT, seed=1)
    assert slate.error is not None
    assert slate.tactics == ()
    assert slate.raw_outputs == (raw,)
    assert slate.processed_token_positions == 101


def test_selected_and_shuffled_serialize_complete_candidate_without_mutation():
    candidate = generated()
    before = asdict(candidate)
    selected = generate_selected_slate(ScriptedProvider([tactics()]), CONTEXT, candidate, seed=8)
    shuffled = generate_shuffled_slate(ScriptedProvider([tactics()]), CONTEXT, candidate, seed=8)
    selected_json = selected.messages[0][1].content.split("Complete explicit candidate:\n")[1].split("\nReturn ")[0]
    shuffled_json = shuffled.messages[0][1].content.split("Complete explicit candidate:\n")[1].split("\nReturn ")[0]
    left, right = json.loads(selected_json), json.loads(shuffled_json)
    assert right["alignment"]["mapping"] == [["wheel", "active_subgoal"], ["socket", "introduced_assumption"]]
    assert right["alignment"]["proof_operation"] != left["alignment"]["proof_operation"]
    assert right["transfer"]["reasoning_steps"] != left["transfer"]["reasoning_steps"]
    assert asdict(candidate) == before
    assert shuffle_alignment(candidate.alignment, problem_id="goal", seed=8) == shuffle_alignment(candidate.alignment, problem_id="goal", seed=8)


def test_previous_state_ablation_changes_only_one_explicit_field():
    candidate = generated()
    with_previous = render_activation_tactic_messages(CONTEXT, candidate, previous_state="⊢ P → P", last_tactic="intro hp")
    omitted = render_activation_tactic_messages(CONTEXT, candidate, previous_state="⊢ P → P", last_tactic="intro hp", include_previous=False)
    unavailable = render_activation_tactic_messages(CONTEXT, candidate)
    assert with_previous[0] == omitted[0] == unavailable[0]
    field = "Previous state / accepted transition:\n⊢ P → P\nAccepted transition: intro hp"
    assert field in with_previous[1].content
    assert with_previous[1].content.replace(field, "Previous state / accepted transition:\n(omitted)") == omitted[1].content
    assert unavailable == omitted


def test_union_normalizes_neutral_first_and_stops_at_eight():
    neutral = generate_neutral_slate(ScriptedProvider([tactics("by exact hp", "exact and.left h", "cases h with hp hq", "intro p")]), CONTEXT, seed=0)
    first = replace(generated(), tactics=generate_neutral_slate(ScriptedProvider([tactics("exact hp", "exact And.left h", "apply f", "exact q")]), CONTEXT, seed=0).tactics)
    second = replace(generated(), tactics=generate_neutral_slate(ScriptedProvider([tactics("rfl", "constructor", "simp", "aesop")]), CONTEXT, seed=0).tactics)
    assert build_tactic_union(neutral, (first, second)) == (
        "exact hp", "exact And.left h", "cases h", "intro p", "apply f", "exact q", "rfl", "constructor",
    )


def test_slates_never_exceed_four_tactics():
    slate = generate_neutral_slate(ScriptedProvider([tactics("intro p", "rfl", "simp", "aesop", "constructor")]), CONTEXT, seed=0)
    assert len(slate.tactics) == 4
    with pytest.raises(ValueError):
        generate_neutral_slate(ScriptedProvider([]), CONTEXT, seed=0, tactics_per_slate=5)
