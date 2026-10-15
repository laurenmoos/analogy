import json

import pytest
import torch

from analogy.analogy_generation import (
    AnalogyGenerationFailure,
    AnalogyGenerationContext,
    AnalogyGenerator,
    AnalogyTacticGenerator,
    IterationSummary,
    TacticGenerationContext,
    render_hop_prompt,
    render_semantic_repair_prompt,
    render_tactic_prompt,
)
from analogy.analogy_stream import (
    AnalogyOutputError,
    AnalogyStream,
    AnalogyTacticPlan,
    parse_analogy_hop,
    parse_analogy_tactic_plan,
)
from analogy.proof_views import (
    EmbeddedText,
    EmbeddingProvenance,
    ProofStructure,
    ProofViews,
)
from analogy.records import (
    CandidateOrigin,
    Embedding,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)
from analogy.rescue import (
    GenerationStatus,
    ProofRescueContext,
    RescueAttempt,
    RescueProposal,
)
from analogy.rl import PolicyAction


HOP = json.dumps(
    {
        "domain": "river",
        "situation": "a blocked channel",
        "relation_chain": ["flow is blocked", "pressure finds another channel"],
        "mapping": {"channel": "proof branch"},
        "continuation": "open another channel",
        "predicted_consequence": "split the goal",
        "rationale": "branching bypasses the obstruction",
        "stop": False,
    }
)


def embedded(label):
    return EmbeddedText(
        label,
        Embedding("fake", (1.0, 0.0)),
        EmbeddingProvenance("fake", -1, 3, f"{label}-hash"),
    )


def analogy_context(hops=(), summaries=()):
    proof = ProofRescueContext(
        SymbolicState("s", "h : P\n⊢ P", "lean4"),
        SymbolicState("g", "P", "lean4"),
        (),
        ("exact missing",),
    )
    structure = ProofStructure(1, '{"root":"atom"}', ('{"root":"target"}',))
    views = ProofViews(embedded("language"), embedded("structure"), structure)
    return AnalogyGenerationContext(
        proof, views, structure, AnalogyStream(tuple(hops)), tuple(summaries)
    )


def test_later_hop_prompt_contains_views_outcomes_and_surprise_trend():
    hop = parse_analogy_hop(HOP)
    summaries = (
        IterationSummary(0, "predicted one", "actual one", 0.75, None, ("cases h",), ("accepted",)),
        IterationSummary(1, "predicted two", "actual two", 0.61, -0.14, ("simp",), ("rejected",)),
    )

    prompt = render_hop_prompt(analogy_context((hop,), summaries))

    assert "Language view hash:" in prompt
    assert "Lean structural signature:" in prompt
    assert '"actual_transition":"actual two"' in prompt
    assert '"surprise":0.61' in prompt
    assert '"surprise_delta":-0.14' in prompt


def test_hop_prompt_bounds_card_verbosity_for_strict_json_generation():
    prompt = render_hop_prompt(analogy_context())

    assert "at most 120 words total" in prompt
    assert "relation_chain: 2-4 strings" in prompt
    assert "mapping: at most 4 entries" in prompt


def test_tactic_prompt_contains_complete_stream_and_requires_valid_citation():
    first = parse_analogy_hop(HOP, hop_id="hop-0")
    second = parse_analogy_hop(HOP, hop_id="hop-1", parent_id="hop-0")
    context = TacticGenerationContext(
        analogy_context((first, second)), active_hop_id="hop-1"
    )

    prompt = render_tactic_prompt(context)

    assert '"hop_id":"hop-0"' in prompt
    assert '"hop_id":"hop-1"' in prompt
    with pytest.raises(AnalogyOutputError, match="cite"):
        parse_analogy_tactic_plan(
            '{"predicted_state":"done","tactics":["exact h"],'
            '"motivating_hop_id":"outside"}',
            context.analogy.stream,
        )


def test_tactic_prompt_requests_distinct_lean4_candidates_and_blocks_observed_dialects():
    hop = parse_analogy_hop(HOP, hop_id="hop-0")
    context = TacticGenerationContext(
        analogy_context((hop,)), active_hop_id="hop-0"
    )

    prompt = render_tactic_prompt(context, candidate_count=4)

    assert "exactly 4 distinct" in prompt
    assert "Lean 4" in prompt
    assert "intro" in prompt
    assert ".mp" in prompt
    assert ".mpr" in prompt
    assert "Do not use `assume`" in prompt
    assert "comma-separated tactic programs" in prompt


def test_semantic_repair_prompt_contains_rejected_tactics_and_lean_diagnostics():
    hop = parse_analogy_hop(HOP, hop_id="hop-0")
    context = TacticGenerationContext(
        analogy_context((hop,)), active_hop_id="hop-0"
    )
    candidate = TacticCandidate(
        "exact (assume P; h)", 1.0, CandidateOrigin.ANALOGICAL
    )
    proposal = RescueProposal(candidate, "closed")
    attempt = RescueAttempt(
        proposal,
        TacticResult(
            candidate,
            TacticStatus.ERROR,
            diagnostics=("unexpected token ';'",),
        ),
    )

    prompt = render_semantic_repair_prompt(
        context,
        AnalogyTacticPlan("closed", (candidate.source,), "hop-0"),
        (attempt,),
        candidate_count=2,
    )

    assert "exact (assume P; h)" in prompt
    assert "unexpected token ';'" in prompt
    assert "exactly 2 distinct corrected" in prompt
    assert "same current proof state" in prompt


def test_semantic_repair_prompt_has_fallback_when_lean_returns_no_diagnostic():
    hop = parse_analogy_hop(HOP, hop_id="hop-0")
    context = TacticGenerationContext(
        analogy_context((hop,)), active_hop_id="hop-0"
    )
    candidate = TacticCandidate("exact h", 1.0, CandidateOrigin.ANALOGICAL)
    proposal = RescueProposal(candidate, "closed")
    attempt = RescueAttempt(
        proposal, TacticResult(candidate, TacticStatus.ERROR)
    )

    prompt = render_semantic_repair_prompt(
        context,
        AnalogyTacticPlan("closed", (candidate.source,), "hop-0"),
        (attempt,),
        candidate_count=1,
    )

    assert "Lean rejected tactic" in prompt


class SequenceGenerator:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.messages = []

    def generate_with_delta(self, messages, **kwargs):
        self.messages.append(messages)
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return output


def test_tactic_generator_semantic_repair_is_recorded_as_a_repair_attempt():
    corrected = json.dumps(
        {
            "predicted_state": "no goals",
            "tactics": ["intro h; exact h"],
            "motivating_hop_id": "hop-0",
        }
    )
    generator = SequenceGenerator((corrected,))
    service = AnalogyTacticGenerator(
        generator,
        hidden_layer=-1,
        base_seed=7,
        max_repair_calls=1,
    )
    hop = parse_analogy_hop(HOP, hop_id="hop-0")
    context = TacticGenerationContext(
        analogy_context((hop,)), active_hop_id="hop-0"
    )
    candidate = TacticCandidate("assume h; exact h", 1.0, CandidateOrigin.ANALOGICAL)
    proposal = RescueProposal(candidate, "no goals")
    attempt = RescueAttempt(
        proposal,
        TacticResult(candidate, TacticStatus.ERROR, diagnostics=("unexpected identifier",)),
    )
    action = PolicyAction(torch.zeros(2), torch.tensor(0.0), torch.tensor(0.0))

    repaired = service.repair(
        context,
        action,
        failed_plan=AnalogyTacticPlan("no goals", (candidate.source,), "hop-0"),
        failed_attempts=(attempt,),
        iteration_index=0,
        candidate_count=2,
        max_attempts=1,
    )

    assert repaired.plan.tactics == ("intro h; exact h",)
    assert repaired.attempts[0].repair is True


def test_analogy_generator_repairs_invalid_card_and_preserves_raw_attempts():
    generator = SequenceGenerator(("not json", HOP))
    service = AnalogyGenerator(
        generator, hidden_layer=-1, base_seed=7, max_repair_calls=1
    )
    action = PolicyAction(torch.zeros(2), torch.tensor(0.0), torch.tensor(0.0))

    generated = service.generate(analogy_context(), action, iteration_index=0)

    assert generated.hop.domain == "river"
    assert [item.status for item in generated.attempts] == [
        GenerationStatus.INVALID,
        GenerationStatus.VALID,
    ]


def test_analogy_generator_preserves_runtime_failure_diagnostic():
    service = AnalogyGenerator(
        SequenceGenerator((RuntimeError("model offline"),)),
        hidden_layer=-1,
        base_seed=7,
        max_repair_calls=1,
    )
    action = PolicyAction(torch.zeros(2), torch.tensor(0.0), torch.tensor(0.0))

    with pytest.raises(AnalogyGenerationFailure) as captured:
        service.generate(analogy_context(), action, iteration_index=0)

    assert captured.value.attempts[0].status is GenerationStatus.RUNTIME_ERROR
    assert captured.value.attempts[0].diagnostic == "model offline"
