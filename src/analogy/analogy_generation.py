"""Strict analogy-card and analogy-conditioned tactic generation services."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

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
    return (
        f"Overall target:\n{context.proof.goal.expression}\n"
        f"Current proof state:\n{context.proof.state.expression}\n"
        f"Language view hash: {context.views.language.provenance.text_hash}\n"
        f"Structure view hash: {context.views.structure.provenance.text_hash}\n"
        f"Lean structural signature: {render_structure_view(context.structure)}\n"
        f"Accepted tactics: {json.dumps(context.proof.accepted_tactics)}\n"
        f"Failed tactics: {json.dumps(context.proof.failed_tactics)}\n"
        f"Prior analogy hops: {history}\n"
        f"Prior verified iterations: {prior}\n"
        "Generate the next analogy refinement. Return exactly one JSON object with "
        "domain, situation, relation_chain, mapping, continuation, "
        "predicted_consequence, rationale, and boolean stop. Keep the JSON at most "
        "120 words total: domain at most 3 words; situation at most 18 words; "
        "relation_chain: 2-4 strings of at most 10 words each; mapping: at most "
        "4 entries with short values; continuation, predicted_consequence, and "
        "rationale at most 15 words each."
    )


def render_tactic_prompt(
    context: TacticGenerationContext, *, candidate_count: int = 4
) -> str:
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or candidate_count <= 0
    ):
        raise ValueError("candidate_count must be a positive integer")
    stream = json.dumps(
        [asdict(item) for item in context.analogy.stream.hops],
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        f"Current proof state:\n{context.analogy.proof.state.expression}\n"
        f"Lean structural signature: {render_structure_view(context.analogy.structure)}\n"
        f"Complete analogy stream: {stream}\n"
        f"Active motivating hop: {context.active_hop_id}\n"
        f"Generate exactly {candidate_count} distinct single-line Lean 4 tactics, "
        "ordered strongest first and conditioned on the complete analogy stream. "
        "Use Lean 4 syntax only. Prefer short terms built from `intro`, `exact`, "
        "`apply`, `fun`, `.mp`, `.mpr`, and `rw` when they fit the state. "
        "Do not use `assume`, `iff.elim`, `iff.elim_left`, semicolon-packed terms, "
        "or comma-separated tactic programs. Return exactly predicted_state, "
        "tactics, and motivating_hop_id as JSON."
    )


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
    return (
        f"Current proof state (repair the same current proof state):\n"
        f"{context.analogy.proof.state.expression}\n"
        f"Lean structural signature: {render_structure_view(context.analogy.structure)}\n"
        f"Complete analogy stream: {stream}\n"
        f"Rejected plan: {json.dumps(asdict(failed_plan), sort_keys=True)}\n"
        f"Lean verifier feedback: {json.dumps(rejected, sort_keys=True)}\n"
        f"Generate exactly {candidate_count} distinct corrected single-line Lean 4 "
        "tactics. Preserve the analogy's line of reasoning, but fix the concrete "
        "syntax or type error identified by Lean. Do not repeat a rejected tactic. "
        "Use Lean 4 syntax only; prefer `intro`, `exact`, `apply`, `fun`, `.mp`, "
        "`.mpr`, and `rw` when appropriate. Return exactly predicted_state, "
        "tactics, and motivating_hop_id as JSON."
    )
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
            messages=(
                Message("system", "Generate one explicit analogy reasoning card, not a tactic."),
                Message("user", render_hop_prompt(context)),
            ),
            action=action,
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
            messages=(
                Message("system", "Decode the cited analogy into Lean tactics."),
                Message(
                    "user",
                    render_tactic_prompt(
                        context, candidate_count=self.candidate_count
                    ),
                ),
            ),
            action=action,
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
    ) -> GeneratedTacticPlan:
        count = self.candidate_count if candidate_count is None else candidate_count
        return _generate_strict(
            generator=self.generator,
            messages=(
                Message(
                    "system",
                    "Repair rejected analogy-conditioned tactics using Lean 4 diagnostics.",
                ),
                Message(
                    "user",
                    render_semantic_repair_prompt(
                        context,
                        failed_plan,
                        failed_attempts,
                        candidate_count=count,
                    ),
                ),
            ),
            action=action,
            hidden_layer=self.hidden_layer,
            seed=self.base_seed + iteration_index * 10 + 6,
            max_repair_calls=self.max_repair_calls,
            max_attempts=max_attempts,
            parser=lambda raw: parse_analogy_tactic_plan(
                raw, context.analogy.stream
            ),
            result_type=GeneratedTacticPlan,
            initial_repair=True,
        )


def _generate_strict(
    *, generator, messages, action, hidden_layer, seed, max_repair_calls, max_attempts,
    parser, result_type, initial_repair=False,
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
        active_seed = seed + repair_index
        try:
            raw = generator.generate_with_delta(
                active_messages,
                delta=action.delta.detach(),
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
        except AnalogyOutputError as error:
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
            active_messages = (
                messages[0],
                Message(
                    "user",
                    f"The prior output was invalid: {error}\nPrior output:\n{raw}\n"
                    "Return only a corrected JSON object matching the requested schema.",
                ),
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
