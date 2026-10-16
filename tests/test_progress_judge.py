from __future__ import annotations

import json

import pytest

from analogy.progress_judge import (
    AuditIteration,
    AuditStage,
    LLMProcessJudge,
    LeanAttemptEvidence,
    parse_audit_judgment,
)


class SequenceGenerator:
    def __init__(self, outputs: tuple[str, ...]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[tuple[object, ...], int]] = []

    def generate(self, messages, *, seed: int) -> str:
        self.calls.append((messages, seed))
        return self.outputs.pop(0)


def payload(**overrides: object) -> str:
    value = {
        "stage": "proof_progress",
        "progress_score": 0.6,
        "analogy_quality": 0.75,
        "confidence": 0.8,
        "diagnosis": "The accepted rewrite exposes the needed implication.",
        "evidence": "The successor replaces the iff goal with its forward direction.",
        "suggested_fix": "Prefer analogies that expose the remaining direction.",
    }
    value.update(overrides)
    return json.dumps(value)


def iteration(
    *,
    actual: str | None = "h : P ↔ Q\n⊢ P → Q",
    discard_reasons: tuple[str, ...] = (),
) -> AuditIteration:
    return AuditIteration(
        run_id="run-7",
        problem_id="iff_intro_03",
        phase="analogy_loop",
        rollout_id="rollout-2",
        iteration_index=1,
        target="(P ↔ Q) → (P → Q)",
        state_before="h : P ↔ Q\n⊢ P → Q",
        analogy_card='{"domain":"bridge","continuation":"cross forward"}',
        predicted_transition="reduce the iff to its forward implication",
        tactics=("exact h.mp", "apply h.mp"),
        lean_outcomes=("error: type mismatch", "accepted"),
        lean_diagnostics=("application type mismatch",),
        discard_reasons=discard_reasons,
        actual_transition=actual,
        surprise=0.2,
        stop_decision="continue",
        lean_attempts=(
            LeanAttemptEvidence(
                source="exact h.mp",
                status="error",
                diagnostics=("application type mismatch",),
                discard_reason=None,
                successor=None,
            ),
            LeanAttemptEvidence(
                source="apply h.mp",
                status="accepted",
                diagnostics=(),
                discard_reason=None,
                successor=actual,
            ),
        ),
    )


def test_parser_rejects_extra_keys_invalid_stage_and_out_of_range_scores() -> None:
    assert parse_audit_judgment(payload()).stage is AuditStage.PROOF_PROGRESS

    with pytest.raises(ValueError, match="exactly"):
        parse_audit_judgment(payload(extra="not allowed"))
    with pytest.raises(ValueError, match="stage"):
        parse_audit_judgment(payload(stage="reward"))
    with pytest.raises(ValueError, match="progress_score"):
        parse_audit_judgment(payload(progress_score=1.01))
    with pytest.raises(ValueError, match="analogy_quality"):
        parse_audit_judgment(payload(analogy_quality=float("nan")))
    with pytest.raises(ValueError, match="confidence"):
        parse_audit_judgment(payload(confidence=-0.01))
    duplicate = payload().replace(
        '"stage": "proof_progress",',
        '"stage": "working", "stage": "proof_progress",',
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_audit_judgment(duplicate)


def test_grounded_judge_prompt_preserves_unavailable_stages_and_objective_statuses():
    from dataclasses import replace
    item = replace(iteration(), record_kind="grounded_generated", stage_payloads={
        "grounding": {"status": "failed", "payload": None},
        "lean": {"status": "unavailable", "payload": []},
    }, tactics=(), lean_outcomes=(), lean_attempts=(), actual_transition=None)
    generator = SequenceGenerator((payload(stage="tactic_translation"),))
    LLMProcessJudge(generator, base_seed=1).evaluate(item)
    prompt = generator.calls[0][0][-1].content
    assert '"status": "unavailable"' in prompt
    assert '"status": "failed"' in prompt
    assert "not zero-quality proof attempts" in prompt
    assert "infrastructure" in prompt
    assert "observer only" in prompt


def test_grounded_judge_seeds_isolate_proof_paths():
    from dataclasses import replace
    generator = SequenceGenerator((payload(), payload()))
    judge = LLMProcessJudge(generator, base_seed=1)
    judge.evaluate(replace(iteration(), proof_path_id="path-one"))
    judge.evaluate(replace(iteration(), proof_path_id="path-two"))
    assert generator.calls[0][1] != generator.calls[1][1]


def test_prompt_contains_process_evidence_for_accepted_and_rejected_iterations() -> None:
    accepted_generator = SequenceGenerator((payload(),))
    accepted = LLMProcessJudge(
        accepted_generator, base_seed=11, max_repair_calls=1
    ).evaluate(iteration())

    assert accepted.judgment is not None
    prompt = accepted_generator.calls[0][0][-1].content
    for fragment in (
        "(P ↔ Q) → (P → Q)",
        "bridge",
        "exact h.mp",
        "application type mismatch",
        "accepted",
        "Actual successor",
        "`complete` is objective proof completion",
        "new remaining goal after the tactic",
        "status is `complete`, stage must be `working`",
        "accepted successor",
        "stage must be `working` or `proof_progress`",
        "Executed Lean attempts",
    ):
        assert fragment in prompt
    assert "reference proof" not in prompt.lower()

    rejected_generator = SequenceGenerator((payload(stage="lean_execution"),))
    rejected = LLMProcessJudge(
        rejected_generator, base_seed=11, max_repair_calls=1
    ).evaluate(iteration(actual=None, discard_reasons=("repeated_state",)))

    assert rejected.judgment is not None
    rejected_prompt = rejected_generator.calls[0][0][-1].content
    assert "No accepted successor" in rejected_prompt
    assert "application type mismatch" in rejected_prompt
    assert "repeated_state" in rejected_prompt


def test_malformed_output_is_repaired_once_with_deterministic_operation_seeds() -> None:
    first_generator = SequenceGenerator(("not json", payload(stage="working")))
    first = LLMProcessJudge(
        first_generator, base_seed=23, max_repair_calls=1
    ).evaluate(iteration())
    second_generator = SequenceGenerator((payload(),))
    LLMProcessJudge(second_generator, base_seed=23, max_repair_calls=1).evaluate(
        iteration()
    )

    assert first.judgment is not None
    assert first.judgment.stage is AuditStage.WORKING
    assert first.raw_outputs == ("not json", payload(stage="working"))
    assert first.failure is None
    assert len(first_generator.calls) == 2
    assert first_generator.calls[0][1] == second_generator.calls[0][1]
    assert first_generator.calls[0][1] != first_generator.calls[1][1]
    repair_context = "\n".join(
        message.content for message in first_generator.calls[1][0]
    )
    assert "not json" in repair_context
    assert "Return only" in repair_context


def test_persistent_malformed_output_preserves_raw_attempts_and_failure() -> None:
    generator = SequenceGenerator(("bad one", "bad two"))

    attempt = LLMProcessJudge(
        generator, base_seed=5, max_repair_calls=1
    ).evaluate(iteration())

    assert attempt.judgment is None
    assert attempt.raw_outputs == ("bad one", "bad two")
    assert attempt.failure is not None
    assert "JSON" in attempt.failure
    assert len(generator.calls) == 2


def test_numeric_overflow_is_repaired_instead_of_escaping_failure_accounting() -> None:
    generator = SequenceGenerator(
        (payload(progress_score=10**400), payload(stage="working", progress_score=1))
    )

    attempt = LLMProcessJudge(
        generator, base_seed=5, max_repair_calls=1
    ).evaluate(iteration())

    assert attempt.judgment is not None
    assert attempt.judgment.progress_score == 1
    assert len(attempt.raw_outputs) == 2
    assert "finite number" in "\n".join(
        message.content for message in generator.calls[1][0]
    )


def test_call_budget_includes_repair_attempts() -> None:
    generator = SequenceGenerator(("bad", payload()))

    attempt = LLMProcessJudge(
        generator, base_seed=5, max_repair_calls=1
    ).evaluate(iteration(), max_calls=1)

    assert attempt.judgment is None
    assert attempt.raw_outputs == ("bad",)
    assert len(generator.calls) == 1


def test_judge_validates_constructor_and_text_lengths() -> None:
    with pytest.raises(ValueError, match="max_repair_calls"):
        LLMProcessJudge(SequenceGenerator(()), base_seed=0, max_repair_calls=-1)
    with pytest.raises(ValueError, match="diagnosis"):
        parse_audit_judgment(payload(diagnosis="x" * 401))
