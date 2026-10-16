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
from .grounded_plan import (
    GroundedPlanError, GroundedTacticPlan, GroundingOperator, parse_grounded_tactic_plan,
)
from .proof_inventory import LocalProofInventory
from .prompts import prompt_catalog
from .records import Message, SymbolicState
from .rescue import GenerationAttempt

if TYPE_CHECKING:
    from .rl import PolicyAction


SOURCE_PROMPT_VERSION = "source-analogy-v2"
ALIGNMENT_PROMPT_VERSION = "analogy-alignment-v1"
TRANSFER_PROMPT_VERSION = "proof-reasoning-transfer-v1"
TACTIC_PROMPT_VERSION = "transferred-tactic-v1"
GROUNDING_PROMPT_VERSION = "grounding-v1"
GROUNDING_REPAIR_PROMPT_VERSION = "grounding-repair-v1"


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


@dataclass(frozen=True, slots=True)
class GeneratedGroundedPlan:
    plan: GroundedTacticPlan
    attempts: tuple[GenerationAttempt, ...]
    prompt_version: str = GROUNDING_PROMPT_VERSION


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


def render_source_prompt(observation: AnalogyObservation, need: ProofNeed) -> str:
    enums = {enum.__name__: [item.value for item in enum]
             for enum in (OperationClass, Arity, Permutation, InformationFlow)}
    return prompt_catalog().messages(
        "source_analogy", observation=_dump(observation), need=_dump(need),
        enums=json.dumps(enums),
    )[1].content


def render_alignment_prompt(
    observation: AnalogyObservation, need: ProofNeed, source: SourceAnalogy,
) -> str:
    return prompt_catalog().messages(
        "analogy_alignment", observation=_dump(observation), need=_dump(need),
        source=_dump(source),
    )[1].content


def render_transfer_prompt(
    observation: AnalogyObservation, need: ProofNeed, source: SourceAnalogy,
    alignment: AnalogyAlignment,
) -> str:
    return prompt_catalog().messages(
        "proof_transfer", observation=_dump(observation), need=_dump(need),
        source=_dump(source), alignment=_dump(alignment),
    )[1].content


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
    return prompt_catalog().messages(
        "transferred_tactic", state=state.expression,
        structure=render_structure_view(structure), source=_dump(source),
        alignment=_dump(alignment), transfer=_dump(transfer),
        candidate_count=candidate_count, transfer_id=json.dumps(transfer.transfer_id),
    )[1].content


def render_grounding_prompt(
    inventory: LocalProofInventory, transfer: ProofReasoningTransfer,
) -> str:
    if transfer.active_subgoal != " ".join(inventory.active_subgoal.split()):
        raise GroundedPlanError("transfer must describe the current active subgoal")
    return prompt_catalog().messages(
        "grounded_plan", inventory=_dump(inventory), transfer=_dump(transfer),
        operators=json.dumps([item.value for item in GroundingOperator]),
    )[1].content


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

    def _generate(self, prompt, prompt_name, *, delta, analogy_index, offset,
                  max_attempts, parser, result_type, cancellation=None):
        if (isinstance(analogy_index, bool) or not isinstance(analogy_index, int)
                or analogy_index < 0):
            raise ValueError("analogy_index must be a non-negative integer")
        return _generate_strict(
            generator=self.generator,
            messages=(Message("system", prompt_catalog().templates[prompt_name].system),
                      Message("user", prompt)),
            delta=delta, hidden_layer=self.hidden_layer,
            seed=self.base_seed + analogy_index * 100 + offset,
            max_repair_calls=self.max_repair_calls, max_attempts=max_attempts,
            parser=parser, result_type=result_type,
            schema_errors=(TransferOutputError,), preserve_repair_context=True,
            cancellation=cancellation,
        )


class SourceAnalogyGenerator(_TransferGenerator):
    def generate(
        self, observation: AnalogyObservation, need: ProofNeed, action: PolicyAction,
        *, analogy_index: int, max_attempts: int | None = None, cancellation=None,
    ) -> GeneratedSourceAnalogy:
        return self._generate(
            render_source_prompt(observation, need),
            "source_analogy",
            delta=action.delta, analogy_index=analogy_index, offset=0,
            max_attempts=max_attempts,
            parser=lambda raw: parse_source_analogy(
                raw, analogy_id=f"source-{analogy_index}", need=need),
            result_type=GeneratedSourceAnalogy, cancellation=cancellation,
        )


class AlignmentGenerator(_TransferGenerator):
    def generate(
        self, observation: AnalogyObservation, need: ProofNeed, source: SourceAnalogy,
        *, analogy_index: int, max_attempts: int | None = None, cancellation=None,
    ) -> GeneratedAlignment:
        return self._generate(
            render_alignment_prompt(observation, need, source),
            "analogy_alignment",
            delta=None, analogy_index=analogy_index, offset=10, max_attempts=max_attempts,
            parser=lambda raw: parse_alignment(
                raw, source=source, need=need, alignment_id=f"alignment-{analogy_index}"),
            result_type=GeneratedAlignment, cancellation=cancellation,
        )


class ProofTransferGenerator(_TransferGenerator):
    def generate(
        self, observation: AnalogyObservation, need: ProofNeed, source: SourceAnalogy,
        alignment: AnalogyAlignment, *, analogy_index: int, max_attempts: int | None = None, cancellation=None,
    ) -> GeneratedProofTransfer:
        _validate_chain(source, alignment)
        return self._generate(
            render_transfer_prompt(observation, need, source, alignment),
            "proof_transfer",
            delta=None, analogy_index=analogy_index, offset=20, max_attempts=max_attempts,
            parser=lambda raw: parse_proof_reasoning_transfer(
                raw, need=need, alignment=alignment, transfer_id=f"transfer-{analogy_index}"),
            result_type=GeneratedProofTransfer, cancellation=cancellation,
        )


class TransferredTacticGenerator(_TransferGenerator):
    def generate(
        self, state: SymbolicState, structure: ProofStructure, source: SourceAnalogy,
        alignment: AnalogyAlignment, transfer: ProofReasoningTransfer, *,
        candidate_count: int, max_attempts: int | None = None, analogy_index: int = 0, cancellation=None,
    ) -> GeneratedTransferredTacticPlan:
        prompt = render_transferred_tactic_prompt(
            state, structure, source, alignment, transfer, candidate_count=candidate_count)

        def parse(raw: str) -> TransferredTacticPlan:
            plan = parse_transferred_tactic_plan(raw, transfer)
            if len(plan.tactics) != candidate_count:
                raise TransferOutputError(f"tactics must contain exactly {candidate_count} candidates")
            return plan

        return self._generate(
            prompt, "transferred_tactic",
            delta=None, analogy_index=analogy_index, offset=30, max_attempts=max_attempts,
            parser=parse, result_type=GeneratedTransferredTacticPlan, cancellation=cancellation,
        )


class GroundedPlanGenerator(_TransferGenerator):
    def generate(
        self, inventory: LocalProofInventory, transfer: ProofReasoningTransfer, *,
        plan_id: str, analogy_index: int = 0, max_attempts: int | None = None, cancellation=None,
    ) -> GeneratedGroundedPlan:
        return self._generate(
            render_grounding_prompt(inventory, transfer),
            "grounded_plan",
            delta=None, analogy_index=analogy_index, offset=40, max_attempts=max_attempts,
            parser=lambda raw: parse_grounded_tactic_plan(raw, inventory, transfer, plan_id),
            result_type=GeneratedGroundedPlan, cancellation=cancellation,
        )

    def repair(
        self, inventory: LocalProofInventory, transfer: ProofReasoningTransfer, *,
        failed_plan: GroundedTacticPlan, compiled_tactics, failed_attempts,
        plan_id: str, analogy_index: int = 0, max_attempts: int | None = None, cancellation=None,
    ) -> GeneratedGroundedPlan:
        messages = prompt_catalog().messages(
            "grounded_plan_repair", inventory=_dump(inventory), transfer=_dump(transfer),
            failed_plan=_dump(failed_plan),
            compiled_tactics=json.dumps(
                [asdict(item) for item in compiled_tactics], ensure_ascii=False, sort_keys=True),
            failed_attempts=json.dumps(
                [{"tactic": item.proposal.candidate.source,
                  "diagnostics": item.result.diagnostics} for item in failed_attempts],
                ensure_ascii=False, sort_keys=True),
            operators=json.dumps([item.value for item in GroundingOperator]),
        )
        # The diagnostic path gets exactly one call, with no nested schema retry.
        return _generate_strict(
            generator=self.generator,
            messages=messages,
            delta=None, hidden_layer=self.hidden_layer,
            seed=self.base_seed + analogy_index * 100 + 50,
            max_repair_calls=0, max_attempts=max_attempts,
            parser=lambda raw: parse_grounded_tactic_plan(raw, inventory, transfer, plan_id),
            result_type=GeneratedGroundedPlan, initial_repair=True,
            schema_errors=(TransferOutputError,), cancellation=cancellation,
        )


__all__ = [
    "SOURCE_PROMPT_VERSION", "ALIGNMENT_PROMPT_VERSION", "TRANSFER_PROMPT_VERSION",
    "TACTIC_PROMPT_VERSION", "GeneratedSourceAnalogy", "GeneratedAlignment",
    "GeneratedProofTransfer", "TransferredTacticPlan", "GeneratedTransferredTacticPlan",
    "SourceAnalogyGenerator", "AlignmentGenerator", "ProofTransferGenerator",
    "TransferredTacticGenerator", "parse_transferred_tactic_plan", "render_source_prompt",
    "render_alignment_prompt", "render_transfer_prompt", "render_transferred_tactic_prompt",
    "GROUNDING_PROMPT_VERSION", "GeneratedGroundedPlan", "GroundedPlanGenerator",
    "render_grounding_prompt",
    "GROUNDING_REPAIR_PROMPT_VERSION",
]
