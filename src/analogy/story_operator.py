"""One-call story-mediated selection of a configured grounding operator."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields
from typing import Any

from .analogy_transfer import NeutralProofNeed, TransferOutputError
from .grounding import GroundingOperator
from .proof_inventory import LocalProofInventory, render_model_inventory
from .prompts import PromptCatalog
from .rescue import GenerationAttempt, GenerationStatus


_LEAN_LEAKAGE = re.compile(
    r"\b(?:Lean|tactic|intro|intros|rintro|exact|apply|rcases|constructor|simp|"
    r"simpa|aesop|assumption|rfl|rw|rewrite|refine|specialize|unfold|omega|tauto|"
    r"sorry|admit)\b|[⊢∀∃∧∨¬↔→]|:=",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class StoryBinding:
    role: str
    inventory_id: str

    def __post_init__(self) -> None:
        for name in ("role", "inventory_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise TransferOutputError(f"{name} must be nonblank text")
            object.__setattr__(self, name, " ".join(value.split()))


@dataclass(frozen=True, slots=True)
class StoryOperatorDecision:
    story: str
    operator: GroundingOperator
    bindings: tuple[StoryBinding, ...]


@dataclass(frozen=True, slots=True)
class GeneratedStoryOperatorDecision:
    decision: StoryOperatorDecision
    attempts: tuple[GenerationAttempt, ...]


class StoryOperatorGenerationFailure(RuntimeError):
    def __init__(self, message: str, attempts: tuple[GenerationAttempt, ...]) -> None:
        super().__init__(message)
        self.attempts = attempts


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TransferOutputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_object(value: object, record_type: type) -> dict[str, object]:
    expected = {field.name for field in fields(record_type)}
    if not isinstance(value, dict) or set(value) != expected:
        raise TransferOutputError(f"{record_type.__name__} has missing or unknown keys")
    return value


def parse_story_operator_decision(
    raw: str, inventory: LocalProofInventory, enabled: tuple[GroundingOperator, ...],
) -> StoryOperatorDecision:
    if not enabled or len(set(enabled)) != len(enabled) or any(
        not isinstance(item, GroundingOperator) for item in enabled
    ):
        raise ValueError("enabled operators must be unique GroundingOperator values")
    try:
        payload = json.loads(raw, object_pairs_hook=_pairs,
                             parse_constant=lambda value: (_ for _ in ()).throw(
                                 TransferOutputError(f"invalid JSON constant: {value}")))
    except (TypeError, ValueError) as error:
        raise TransferOutputError(f"StoryOperatorDecision must be one strict JSON object: {error}") from error
    payload = _strict_object(payload, StoryOperatorDecision)
    story = payload["story"]
    if not isinstance(story, str) or not story.strip() or len(story) > 600 or _LEAN_LEAKAGE.search(story):
        raise TransferOutputError("story must be short everyday prose without Lean or tactic text")
    try:
        operator = GroundingOperator(payload["operator"])
    except (TypeError, ValueError) as error:
        raise TransferOutputError("operator must be a real grounding operator") from error
    if operator not in enabled:
        raise TransferOutputError("operator is disabled")
    raw_bindings = payload["bindings"]
    if not isinstance(raw_bindings, list) or len(raw_bindings) > 16:
        raise TransferOutputError("bindings must be a bounded JSON array")
    bindings = tuple(StoryBinding(**_strict_object(item, StoryBinding)) for item in raw_bindings)
    roles = tuple(item.role for item in bindings)
    ids = tuple(item.inventory_id for item in bindings)
    if len(set(roles)) != len(roles):
        raise TransferOutputError("binding role labels must be unique")
    if len(set(ids)) != len(ids):
        raise TransferOutputError("binding inventory ids must be unique")
    for inventory_id in ids:
        try:
            inventory.resolve(inventory_id)
        except ValueError as error:
            raise TransferOutputError(f"unknown inventory id {inventory_id!r}") from error
    return StoryOperatorDecision(" ".join(story.split()), operator, bindings)


class StoryOperatorGenerator:
    def __init__(
        self, generator: Any, prompts: PromptCatalog,
        enabled: tuple[GroundingOperator, ...], *, hidden_layer: int = -1,
        base_seed: int = 0,
    ) -> None:
        if not enabled or len(set(enabled)) != len(enabled):
            raise ValueError("enabled operators must be nonempty and unique")
        self.generator = generator
        self.prompts = prompts
        self.enabled = enabled
        self.hidden_layer = hidden_layer
        self.base_seed = base_seed

    def generate(
        self, description: NeutralProofNeed, inventory: LocalProofInventory,
        action: Any, *, analogy_index: int, cancellation: Any = None,
    ) -> GeneratedStoryOperatorDecision:
        if cancellation is not None and cancellation.is_cancelled():
            raise StoryOperatorGenerationFailure("generation cancelled", ())
        seed = self.base_seed + analogy_index
        messages = self.prompts.messages(
            "story_operator",
            need_json=json.dumps(asdict(description), ensure_ascii=False, sort_keys=True),
            inventory_json=render_model_inventory(inventory),
            operators_json=json.dumps([item.value for item in self.enabled]),
        )
        try:
            raw = self.generator.generate_with_delta(
                messages, delta=action.delta.detach(), layer=self.hidden_layer, seed=seed,
            )
        except Exception as error:
            attempt = GenerationAttempt(
                seed, "", GenerationStatus.RUNTIME_ERROR, error_kind=type(error).__name__,
                diagnostic=str(error) or type(error).__name__,
            )
            raise StoryOperatorGenerationFailure("story generation failed", (attempt,)) from error
        try:
            decision = parse_story_operator_decision(raw, inventory, self.enabled)
        except TransferOutputError as error:
            attempt = GenerationAttempt(
                seed, raw, GenerationStatus.INVALID, error_kind="schema", diagnostic=str(error),
            )
            raise StoryOperatorGenerationFailure("story output was invalid", (attempt,)) from error
        attempt = GenerationAttempt(seed, raw, GenerationStatus.VALID)
        return GeneratedStoryOperatorDecision(decision, (attempt,))


__all__ = [
    "GeneratedStoryOperatorDecision", "StoryBinding", "StoryOperatorDecision",
    "StoryOperatorGenerationFailure", "StoryOperatorGenerator",
    "parse_story_operator_decision",
]
