"""Separately logged, strict generation stages for source-domain proof transfers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from .analogy_generation import _generate_strict
from .analogy_transfer import (
    AnalogyAlignment, AnalogyObservation, Arity, InformationFlow, OperationClass,
    Permutation, ProofNeed, ProofReasoningTransfer, SourceAnalogy, TransferOutputError,
    _parse_object, _text, _texts, parse_alignment, parse_proof_reasoning_transfer,
    parse_source_analogy,
)
from .proof_views import ProofStructure, render_structure_view
from .records import Message, SymbolicState
from .rescue import GenerationAttempt

if TYPE_CHECKING:
    from .rl import PolicyAction


SOURCE_PROMPT_VERSION = "source-analogy-v1"
ALIGNMENT_PROMPT_VERSION = "analogy-alignment-v1"
TRANSFER_PROMPT_VERSION = "proof-reasoning-transfer-v1"
TACTIC_PROMPT_VERSION = "transferred-tactic-v1"


@dataclass(frozen=True, slots=True)
class GeneratedSourceAnalogy:
    source: SourceAnalogy
    attempts: tuple[GenerationAttempt, ...]
    prompt_version: str = SOURCE_PROMPT_VERSION


@dataclass(frozen=True, slots=True)
class GeneratedAlignment:
    alignment: AnalogyAlignment
    attempts: tuple[GenerationAttempt, ...]
    prompt_version: str = ALIGNMENT_PROMPT_VERSION


@dataclass(frozen=True, slots=True)
class GeneratedProofTransfer:
    transfer: ProofReasoningTransfer
    attempts: tuple[GenerationAttempt, ...]
    prompt_version: str = TRANSFER_PROMPT_VERSION


@dataclass(frozen=True, slots=True)
class TransferredTacticPlan:
    predicted_state: str
    tactics: tuple[str, ...]
    motivating_transfer_id: str

    def __post_init__(self) -> None:
        for name in ("predicted_state", "motivating_transfer_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.tactics, (tuple, list)) or any(
            not isinstance(item, str) or "\n" in item or "\r" in item
            for item in self.tactics
        ):
            raise TransferOutputError("tactics must be nonblank single-line strings")
        object.__setattr__(self, "tactics", _texts(
            self.tactics, "tactics", minimum=1, unique=True))


@dataclass(frozen=True, slots=True)
class GeneratedTransferredTacticPlan:
    plan: TransferredTacticPlan
    attempts: tuple[GenerationAttempt, ...]
    prompt_version: str = TACTIC_PROMPT_VERSION


def parse_transferred_tactic_plan(
    raw: str, transfer: ProofReasoningTransfer,
) -> TransferredTacticPlan:
    payload = _parse_object(raw, TransferredTacticPlan)
    if not isinstance(payload["tactics"], list):
        raise TransferOutputError("tactics must be a JSON array")
    plan = TransferredTacticPlan(**payload)
    if plan.motivating_transfer_id != transfer.transfer_id:
        raise TransferOutputError("motivating_transfer_id must cite the supplied transfer")
    return plan


def _dump(record: Any) -> str:
    return json.dumps(asdict(record), ensure_ascii=False, sort_keys=True)


def _context(observation: AnalogyObservation, need: ProofNeed) -> str:
    return f"Trajectory observation: {_dump(observation)}\nProof need: {_dump(need)}\n"


def render_source_prompt(observation: AnalogyObservation, need: ProofNeed) -> str:
    enums = {enum.__name__: [item.value for item in enum]
             for enum in (OperationClass, Arity, Permutation, InformationFlow)}
    return (
        f"Prompt version: {SOURCE_PROMPT_VERSION}\n{_context(observation, need)}"
        "Generate a concrete source domain outside formal proof, with a distinct before/after "
        "transition that exhibits the needed transformation. Do not restate the proof. "
        "Use specific physical or everyday roles. Never copy proposition expressions, "
        "proof identifiers, Lean syntax, or tactic names into the source text. "
        "Generic logical vocabulary belongs only in the domain-neutral transformation. "
        "Return exactly one JSON object with domain, source_before, source_after, "
        "source_roles (at least two unique role names), relation_chain (two to four strings), "
        "transformation, and rationale. Do not emit analogy_id. "
        "transformation must contain exactly operation, input_arity, output_arity, "
        "permutation, information_flow (a nonempty unique array in enum order), "
        "focus_before, focus_after, and domain_neutral_summary. "
        f"Allowed enum values: {json.dumps(enums)}. Keep all prose concise."
    )


def render_alignment_prompt(
    observation: AnalogyObservation, need: ProofNeed, source: SourceAnalogy,
) -> str:
    return (
        f"Prompt version: {ALIGNMENT_PROMPT_VERSION}\n{_context(observation, need)}"
        f"Source analogy: {_dump(source)}\n"
        "Align the two distinct systems using only their declared roles. Return exactly "
        "one JSON object with mapping, invariant, source_operation, proof_operation, "
        "limitation, and confidence. mapping is an object of two to six source-role to "
        "proof-role pairs; every key must be a source_roles entry and every value a "
        "proof_roles entry. Cite active_subgoal and at least one relevant hypothesis or "
        "constructor role. Explain the shared relation, the corresponding operations, "
        "and a mismatch or limitation. confidence is a finite number in [0, 1] for "
        "diagnostics only. Do not emit alignment_id or source_analogy_id."
    )


def render_transfer_prompt(
    observation: AnalogyObservation, need: ProofNeed, source: SourceAnalogy,
    alignment: AnalogyAlignment,
) -> str:
    return (
        f"Prompt version: {TRANSFER_PROMPT_VERSION}\n{_context(observation, need)}"
        f"Source analogy: {_dump(source)}\nAlignment: {_dump(alignment)}\n"
        "Translate the aligned operation into tactic-free proof reasoning about the "
        "current active subgoal. Return exactly one JSON object with active_subgoal, "
        "premises (unique strings), reasoning_steps (nonempty ordered strings), "
        "predicted_next_subgoal, and completion_expected (boolean). Copy active_subgoal "
        "from the proof need exactly. Explain which premises are consumed and the "
        "expected next obligation or completion condition. Never emit tactics, Lean "
        "programs, transfer_id, or alignment_id."
    )


def _positive_integer(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _validate_chain(
    source: SourceAnalogy, alignment: AnalogyAlignment,
    transfer: ProofReasoningTransfer | None = None,
) -> None:
    if alignment.source_analogy_id != source.analogy_id:
        raise TransferOutputError("alignment must cite the supplied source analogy")
    if transfer is not None and transfer.alignment_id != alignment.alignment_id:
        raise TransferOutputError("transfer must cite the supplied alignment")


def render_transferred_tactic_prompt(
    state: SymbolicState, structure: ProofStructure, source: SourceAnalogy,
    alignment: AnalogyAlignment, transfer: ProofReasoningTransfer, *, candidate_count: int,
) -> str:
    _positive_integer(candidate_count, "candidate_count")
    _validate_chain(source, alignment, transfer)
    return (
        f"Prompt version: {TACTIC_PROMPT_VERSION}\nCurrent proof state:\n{state.expression}\n"
        f"Lean structural signature: {render_structure_view(structure)}\n"
        f"Source analogy: {_dump(source)}\nAlignment: {_dump(alignment)}\n"
        f"Transferred proof reasoning: {_dump(transfer)}\n"
        f"Generate exactly {candidate_count} distinct single-line Lean 4 tactics, ordered "
        "strongest first, implementing the transferred reasoning in the current state. "
        "Prefer short terms built from `intro`, `exact`, `apply`, `fun`, `.mp`, `.mpr`, "
        "and `rw` when appropriate. Do not use `assume`, `iff.elim`, semicolon-packed "
        "terms, or comma-separated tactic programs. Return exactly predicted_state, "
        "tactics (an array), and motivating_transfer_id as JSON. "
        f"motivating_transfer_id must equal {json.dumps(transfer.transfer_id)}."
    )


class _TransferGenerator:
    def __init__(
        self, generator: Any, *, hidden_layer: int = -1, base_seed: int = 0,
        max_repair_calls: int = 1,
    ) -> None:
        if (isinstance(max_repair_calls, bool) or not isinstance(max_repair_calls, int)
                or max_repair_calls < 0):
            raise ValueError("max_repair_calls must be a non-negative integer")
        if isinstance(base_seed, bool) or not isinstance(base_seed, int) or base_seed < 0:
            raise ValueError("base_seed must be a non-negative integer")
        self.generator = generator
        self.hidden_layer = hidden_layer
        self.base_seed = base_seed
        self.max_repair_calls = min(max_repair_calls, 1)

    def _generate(self, prompt, instruction, *, delta, analogy_index, offset,
                  max_attempts, parser, result_type):
        if (isinstance(analogy_index, bool) or not isinstance(analogy_index, int)
                or analogy_index < 0):
            raise ValueError("analogy_index must be a non-negative integer")
        return _generate_strict(
            generator=self.generator,
            messages=(Message("system", instruction), Message("user", prompt)),
            delta=delta, hidden_layer=self.hidden_layer,
            seed=self.base_seed + analogy_index * 100 + offset,
            max_repair_calls=self.max_repair_calls, max_attempts=max_attempts,
            parser=parser, result_type=result_type,
            schema_errors=(TransferOutputError,), preserve_repair_context=True,
        )


class SourceAnalogyGenerator(_TransferGenerator):
    def generate(
        self, observation: AnalogyObservation, need: ProofNeed, action: PolicyAction,
        *, analogy_index: int, max_attempts: int | None = None,
    ) -> GeneratedSourceAnalogy:
        return self._generate(
            render_source_prompt(observation, need),
            "Generate a concrete non-proof source situation as strict JSON.",
            delta=action.delta, analogy_index=analogy_index, offset=0,
            max_attempts=max_attempts,
            parser=lambda raw: parse_source_analogy(
                raw, analogy_id=f"source-{analogy_index}", need=need),
            result_type=GeneratedSourceAnalogy,
        )


class AlignmentGenerator(_TransferGenerator):
    def generate(
        self, observation: AnalogyObservation, need: ProofNeed, source: SourceAnalogy,
        *, analogy_index: int, max_attempts: int | None = None,
    ) -> GeneratedAlignment:
        return self._generate(
            render_alignment_prompt(observation, need, source),
            "Align declared source and proof roles as strict JSON.",
            delta=None, analogy_index=analogy_index, offset=10, max_attempts=max_attempts,
            parser=lambda raw: parse_alignment(
                raw, source=source, need=need, alignment_id=f"alignment-{analogy_index}"),
            result_type=GeneratedAlignment,
        )


class ProofTransferGenerator(_TransferGenerator):
    def generate(
        self, observation: AnalogyObservation, need: ProofNeed, source: SourceAnalogy,
        alignment: AnalogyAlignment, *, analogy_index: int, max_attempts: int | None = None,
    ) -> GeneratedProofTransfer:
        _validate_chain(source, alignment)
        return self._generate(
            render_transfer_prompt(observation, need, source, alignment),
            "Translate the alignment into tactic-free proof reasoning as strict JSON.",
            delta=None, analogy_index=analogy_index, offset=20, max_attempts=max_attempts,
            parser=lambda raw: parse_proof_reasoning_transfer(
                raw, need=need, alignment=alignment, transfer_id=f"transfer-{analogy_index}"),
            result_type=GeneratedProofTransfer,
        )


class TransferredTacticGenerator(_TransferGenerator):
    def generate(
        self, state: SymbolicState, structure: ProofStructure, source: SourceAnalogy,
        alignment: AnalogyAlignment, transfer: ProofReasoningTransfer, *,
        candidate_count: int, max_attempts: int | None = None, analogy_index: int = 0,
    ) -> GeneratedTransferredTacticPlan:
        prompt = render_transferred_tactic_prompt(
            state, structure, source, alignment, transfer, candidate_count=candidate_count)

        def parse(raw: str) -> TransferredTacticPlan:
            plan = parse_transferred_tactic_plan(raw, transfer)
            if len(plan.tactics) != candidate_count:
                raise TransferOutputError(f"tactics must contain exactly {candidate_count} candidates")
            return plan

        return self._generate(
            prompt, "Decode the cited proof reasoning transfer into Lean 4 tactics as strict JSON.",
            delta=None, analogy_index=analogy_index, offset=30, max_attempts=max_attempts,
            parser=parse, result_type=GeneratedTransferredTacticPlan,
        )


__all__ = [
    "SOURCE_PROMPT_VERSION", "ALIGNMENT_PROMPT_VERSION", "TRANSFER_PROMPT_VERSION",
    "TACTIC_PROMPT_VERSION", "GeneratedSourceAnalogy", "GeneratedAlignment",
    "GeneratedProofTransfer", "TransferredTacticPlan", "GeneratedTransferredTacticPlan",
    "SourceAnalogyGenerator", "AlignmentGenerator", "ProofTransferGenerator",
    "TransferredTacticGenerator", "parse_transferred_tactic_plan", "render_source_prompt",
    "render_alignment_prompt", "render_transfer_prompt", "render_transferred_tactic_prompt",
]
