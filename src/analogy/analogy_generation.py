"""Strict analogy-card and analogy-conditioned tactic generation services."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from torch import Tensor

from .analogy_stream import (
    AnalogyHop,
    AnalogyOutputError,
    AnalogyStream,
    AnalogyTacticPlan,
    parse_analogy_hop,
    parse_analogy_tactic_plan,
)
from .proof_views import ProofStructure, ProofViews, render_structure_view
from .records import Message
from .prompts import prompt_catalog
from .rescue import GenerationAttempt, GenerationStatus, ProofRescueContext
from .rescue import RescueAttempt


@dataclass(frozen=True, slots=True)
class IterationSummary:
    iteration_index: int
    predicted_transition: str
    actual_transition: str
    surprise: float
    surprise_delta: float | None
    generated_tactics: tuple[str, ...]
    lean_outcomes: tuple[str, ...]
    lean_diagnostics: tuple[str, ...] = ()
    discard_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalogyGenerationContext:
    proof: ProofRescueContext
    views: ProofViews
    structure: ProofStructure
    stream: AnalogyStream
    prior_iterations: tuple[IterationSummary, ...]


@dataclass(frozen=True, slots=True)
class TacticGenerationContext:
    analogy: AnalogyGenerationContext
    active_hop_id: str


@dataclass(frozen=True, slots=True)
class GeneratedHop:
    hop: AnalogyHop
    attempts: tuple[GenerationAttempt, ...]


@dataclass(frozen=True, slots=True)
class GeneratedTacticPlan:
    plan: AnalogyTacticPlan
    attempts: tuple[GenerationAttempt, ...]


class AnalogyGenerationFailure(AnalogyOutputError):
    def __init__(self, message: str, attempts: tuple[GenerationAttempt, ...]) -> None:
        super().__init__(message)
        self.attempts = attempts


def render_hop_prompt(context: AnalogyGenerationContext) -> str:
    return prompt_catalog().messages("analogy_hop", **_hop_prompt_values(context))[1].content


def _hop_prompt_values(context: AnalogyGenerationContext) -> dict[str, object]:
    history = json.dumps(
        [asdict(item) for item in context.stream.hops],
        sort_keys=True,
        separators=(",", ":"),
    )
    prior = json.dumps(
        [asdict(item) for item in context.prior_iterations],
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "overall_target": context.proof.goal.expression,
        "current_state": context.proof.state.expression,
        "language_hash": context.views.language.provenance.text_hash,
        "structure_hash": context.views.structure.provenance.text_hash,
        "structure": render_structure_view(context.structure),
        "accepted": json.dumps(context.proof.accepted_tactics),
        "failed": json.dumps(context.proof.failed_tactics),
        "history": history,
        "prior": prior,
    }


def render_tactic_prompt(
    context: TacticGenerationContext, *, candidate_count: int = 4
) -> str:
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or candidate_count <= 0
    ):
        raise ValueError("candidate_count must be a positive integer")
    return prompt_catalog().messages(
        "analogy_tactic", **_tactic_prompt_values(context, candidate_count)
    )[1].content


def _tactic_prompt_values(context: TacticGenerationContext, candidate_count: int) -> dict[str, object]:
    stream = json.dumps(
        [asdict(item) for item in context.analogy.stream.hops],
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "current_state": context.analogy.proof.state.expression,
        "structure": render_structure_view(context.analogy.structure),
        "stream": stream,
        "hop_id": context.active_hop_id,
        "candidate_count": candidate_count,
    }


def render_semantic_repair_prompt(
    context: TacticGenerationContext,
    failed_plan: AnalogyTacticPlan,
    failed_attempts: tuple[RescueAttempt, ...],
    *,
    candidate_count: int,
) -> str:
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or candidate_count <= 0
    ):
        raise ValueError("candidate_count must be a positive integer")
    rejected = [
        {
            "tactic": attempt.proposal.candidate.source,
            "diagnostics": list(attempt.result.diagnostics) or ["Lean rejected tactic"],
        }
        for attempt in failed_attempts
    ]
    stream = json.dumps(
        [asdict(item) for item in context.analogy.stream.hops],
        sort_keys=True,
        separators=(",", ":"),
    )
    return prompt_catalog().messages(
        "analogy_tactic_repair",
        current_state=context.analogy.proof.state.expression,
        structure=render_structure_view(context.analogy.structure),
        stream=stream,
        failed_plan=json.dumps(asdict(failed_plan), sort_keys=True),
        rejected=json.dumps(rejected, sort_keys=True),
        candidate_count=candidate_count,
    )[1].content
class AnalogyGenerator:
    def __init__(
        self,
        generator: Any,
        *,
        hidden_layer: int,
        base_seed: int,
        max_repair_calls: int,
    ) -> None:
        self.generator = generator
        self.hidden_layer = hidden_layer
        self.base_seed = base_seed
        self.max_repair_calls = max_repair_calls

    def generate(
        self, context, action, *, iteration_index: int, max_attempts: int | None = None
    ) -> GeneratedHop:
        parent_id = context.stream.hops[-1].hop_id if context.stream.hops else None
        return _generate_strict(
            generator=self.generator,
            messages=prompt_catalog().messages("analogy_hop", **_hop_prompt_values(context)),
            delta=action.delta,
            hidden_layer=self.hidden_layer,
            seed=self.base_seed + iteration_index * 10,
            max_repair_calls=self.max_repair_calls,
            max_attempts=max_attempts,
            parser=lambda raw: parse_analogy_hop(
                raw, hop_id=f"hop-{iteration_index}", parent_id=parent_id
            ),
            result_type=GeneratedHop,
        )


class AnalogyTacticGenerator:
    def __init__(
        self,
        generator: Any,
        *,
        hidden_layer: int,
        base_seed: int,
        max_repair_calls: int,
        candidate_count: int = 4,
    ) -> None:
        self.generator = generator
        self.hidden_layer = hidden_layer
        self.base_seed = base_seed
        self.max_repair_calls = max_repair_calls
        if (
            isinstance(candidate_count, bool)
            or not isinstance(candidate_count, int)
            or candidate_count <= 0
        ):
            raise ValueError("candidate_count must be a positive integer")
        self.candidate_count = candidate_count

    def generate(
        self, context, action, *, iteration_index: int, max_attempts: int | None = None
    ) -> GeneratedTacticPlan:
        return _generate_strict(
            generator=self.generator,
            messages=prompt_catalog().messages(
                "analogy_tactic", **_tactic_prompt_values(context, self.candidate_count)
            ),
            delta=action.delta,
            hidden_layer=self.hidden_layer,
            seed=self.base_seed + iteration_index * 10 + 5,
            max_repair_calls=self.max_repair_calls,
            max_attempts=max_attempts,
            parser=lambda raw: parse_analogy_tactic_plan(raw, context.analogy.stream),
            result_type=GeneratedTacticPlan,
        )

    def repair(
        self,
        context,
        action,
        *,
        failed_plan: AnalogyTacticPlan,
        failed_attempts: tuple[RescueAttempt, ...],
        iteration_index: int,
        candidate_count: int | None = None,
        max_attempts: int | None = None,
        cancellation: Any | None = None,
    ) -> GeneratedTacticPlan:
        count = self.candidate_count if candidate_count is None else candidate_count
        return _generate_strict(
            generator=self.generator,
            messages=prompt_catalog().messages("analogy_tactic_repair",
                current_state=context.analogy.proof.state.expression,
                structure=render_structure_view(context.analogy.structure),
                stream=json.dumps([asdict(item) for item in context.analogy.stream.hops], sort_keys=True, separators=(",", ":")),
                failed_plan=json.dumps(asdict(failed_plan), sort_keys=True),
                rejected=json.dumps([{"tactic": item.proposal.candidate.source,
                    "diagnostics": list(item.result.diagnostics) or ["Lean rejected tactic"]}
                    for item in failed_attempts], sort_keys=True),
                candidate_count=count),
            delta=action.delta,
            hidden_layer=self.hidden_layer,
            seed=self.base_seed + iteration_index * 10 + 6,
            max_repair_calls=self.max_repair_calls,
            max_attempts=max_attempts,
            parser=lambda raw: parse_analogy_tactic_plan(
                raw, context.analogy.stream
            ),
            result_type=GeneratedTacticPlan,
            initial_repair=True,
            cancellation=cancellation,
        )


def _generate_strict(
    *, generator, messages, delta: Tensor | None, hidden_layer, seed,
    max_repair_calls, max_attempts, parser, result_type, initial_repair=False,
    cancellation=None, schema_errors=(AnalogyOutputError,), preserve_repair_context=False,
):
    if max_attempts is not None and (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts <= 0
    ):
        raise ValueError("max_attempts must be a positive integer or None")
    attempts: list[GenerationAttempt] = []
    active_messages = messages
    attempt_limit = max_repair_calls + 1
    if max_attempts is not None:
        attempt_limit = min(attempt_limit, max_attempts)
    for repair_index in range(attempt_limit):
        if cancellation is not None and cancellation.is_cancelled():
            raise AnalogyGenerationFailure(
                "generation cancelled before the next model attempt",
                tuple(attempts),
            )
        active_seed = seed + repair_index
        try:
            if delta is None:
                raw = generator.generate(active_messages, seed=active_seed)
            else:
                raw = generator.generate_with_delta(
                    active_messages,
                    delta=delta.detach(),
                    layer=hidden_layer,
                    seed=active_seed,
                )
        except Exception as error:
            attempts.append(
                GenerationAttempt(
                    active_seed,
                    "",
                    GenerationStatus.RUNTIME_ERROR,
                    repair=initial_repair or bool(repair_index),
                    error_kind=type(error).__name__,
                    diagnostic=str(error) or type(error).__name__,
                )
            )
            raise AnalogyGenerationFailure(
                "analogy generation failed at runtime", tuple(attempts)
            ) from error
        try:
            parsed = parser(raw)
        except schema_errors as error:
            attempts.append(
                GenerationAttempt(
                    active_seed,
                    raw,
                    GenerationStatus.INVALID,
                    repair=initial_repair or bool(repair_index),
                    error_kind="schema",
                    diagnostic=str(error),
                )
            )
            active_messages = prompt_catalog().messages(
                "schema_repair",
                original_prompt=(messages[1].content if preserve_repair_context else ""),
                diagnostic=error,
                raw=raw,
            )
            continue
        attempts.append(
            GenerationAttempt(
                active_seed,
                raw,
                GenerationStatus.VALID,
                repair=initial_repair or bool(repair_index),
            )
        )
        return result_type(parsed, tuple(attempts))
    raise AnalogyGenerationFailure(
        "generation remained invalid after bounded repair", tuple(attempts)
    )


__all__ = [
    "AnalogyGenerationContext", "AnalogyGenerationFailure", "AnalogyGenerator", "AnalogyTacticGenerator",
    "GeneratedHop", "GeneratedTacticPlan", "IterationSummary",
    "TacticGenerationContext", "render_hop_prompt", "render_semantic_repair_prompt",
    "render_tactic_prompt",
]
