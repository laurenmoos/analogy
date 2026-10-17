"""Readable proof views and frozen-encoder transition comparisons."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from ..records import Embedding
from ..inference.prompts import prompt_catalog
from .rescue import ProofRepresentation, ProofRescueContext, zero_embedding_like

if TYPE_CHECKING:
    from ..transfer.domain import AnalogyAlignment, ProofReasoningTransfer, SourceAnalogy


class ProofViewError(ValueError):
    pass


_TEMP_PATH = re.compile(r"(?:/[^\s:]+/)?lean-gym-[^/\s:]+/[^\s:]+\.lean(?::\d+:\d+)?")


def _normalize_proof_state_text(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized = "\n".join(line.rstrip() for line in lines).strip("\n")
    return _TEMP_PATH.sub("<temp>", normalized)


@dataclass(frozen=True, slots=True)
class ProofStructure:
    goal_count: int
    target_shape: str
    hypothesis_shapes: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.goal_count, bool) or not isinstance(self.goal_count, int) or self.goal_count <= 0:
            raise ProofViewError("goal_count must be a positive integer")
        for value in (self.target_shape, *self.hypothesis_shapes):
            if not isinstance(value, str) or not value.strip():
                raise ProofViewError("proof shapes must be nonblank JSON text")
            try:
                json.loads(value)
            except json.JSONDecodeError as error:
                raise ProofViewError("proof shapes must be valid JSON") from error


@dataclass(frozen=True, slots=True)
class EmbeddingProvenance:
    model_revision: str
    layer: int | None
    token_index: int | None
    text_hash: str


@dataclass(frozen=True, slots=True)
class EmbeddedText:
    rendering: str
    embedding: Embedding
    provenance: EmbeddingProvenance


@dataclass(frozen=True, slots=True)
class ProofViews:
    language: EmbeddedText
    structure: EmbeddedText
    proof_structure: ProofStructure


@dataclass(frozen=True, slots=True)
class TransitionComparison:
    predicted: EmbeddedText
    actual: EmbeddedText
    surprise: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.surprise) or not 0.0 <= self.surprise <= 2.0:
            raise ProofViewError("surprise must be finite and in [0, 2]")


def render_language_view(context: ProofRescueContext) -> str:
    messages = prompt_catalog().messages(
        "proof_language_view",
        overall_target=context.goal.expression,
        current_state=context.state.expression,
        accepted=_lines(context.accepted_tactics),
        failed=_lines(context.failed_tactics),
    )
    return "\n".join(item.content for item in messages)


def render_structure_view(structure: ProofStructure) -> str:
    return json.dumps(
        {
            "goal_count": structure.goal_count,
            "target_shape": json.loads(structure.target_shape),
            "hypothesis_shapes": [json.loads(item) for item in structure.hypothesis_shapes],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def render_proof_relevance_view(context: ProofRescueContext, structure: ProofStructure) -> str:
    """Deterministic joint language/structure text for relevance embeddings."""
    return json.dumps(
        {"language": render_language_view(context), "structure": json.loads(render_structure_view(structure))},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def render_transition(before: str, after: str) -> str:
    messages = prompt_catalog().messages(
        "transition_embedding",
        before=_normalize_proof_state_text(before),
        after=_normalize_proof_state_text(after),
    )
    return "\n".join(item.content for item in messages)


def render_observed_transition(previous: str, tactic: str | None, current: str) -> str:
    messages = prompt_catalog().messages(
        "observed_transition_embedding",
        before=_normalize_proof_state_text(previous),
        tactic=tactic if tactic is not None else "(none)",
        after=_normalize_proof_state_text(current),
    )
    return "\n".join(item.content for item in messages)


def render_transfer_observation(
    source: SourceAnalogy | None,
    alignment: AnalogyAlignment | None,
    transfer: ProofReasoningTransfer | None,
) -> str:
    return json.dumps(
        {"source": asdict(source) if source is not None else None,
         "alignment": asdict(alignment) if alignment is not None else None,
         "transfer": asdict(transfer) if transfer is not None else None},
        ensure_ascii=False, sort_keys=True, indent=2,
    )


def build_policy_representation(
    context: ProofRescueContext,
    structure: ProofStructure,
    encoder: Any,
    layer: int,
    *,
    previous_state: str | None = None,
    last_tactic: str | None = None,
    verified_transition: str | None = None,
    latest_source: SourceAnalogy | None = None,
    latest_alignment: AnalogyAlignment | None = None,
    latest_transfer: ProofReasoningTransfer | None = None,
    scalar_features: tuple[float, ...] = (0.0, 1.0, 1.0, 0.0),
) -> ProofRepresentation:
    """Embed fixed target, current state, and optional verified trajectory."""
    target_text = "\n".join(item.content for item in prompt_catalog().messages(
        "policy_target_embedding", target=context.goal.expression))
    current_text = "\n".join(item.content for item in prompt_catalog().messages(
        "policy_current_embedding", language_view=render_language_view(context),
        structure=render_structure_view(structure)))
    language = _embedded(target_text, encoder.embed_text(target_text, layer=layer))
    structural = _embedded(current_text, encoder.embed_text(current_text, layer=layer))
    transition_text = verified_transition
    if transition_text is None and previous_state is not None:
        transition_text = render_observed_transition(previous_state, last_tactic, context.state.expression)
    transition = (
        encoder.embed_text(transition_text, layer=layer) if transition_text is not None
        else zero_embedding_like(language.embedding, rendering="(none)")
    )
    analogy = (
        encoder.embed_text(render_transfer_observation(latest_source, latest_alignment, latest_transfer), layer=layer)
        if any(item is not None for item in (latest_source, latest_alignment, latest_transfer))
        else zero_embedding_like(language.embedding, rendering="(none)")
    )
    return ProofRepresentation(
        context, language.provenance.text_hash, structural.provenance.text_hash,
        language.embedding, structural.embedding, transition, analogy, scalar_features,
    )


def compare_transitions(
    before: str, predicted: str, actual: str, encoder: Any, layer: int
) -> TransitionComparison:
    predicted_rendering = render_transition(before, predicted)
    actual_rendering = render_transition(before, actual)
    try:
        predicted_embedding = encoder.embed_text(predicted_rendering, layer=layer)
        actual_embedding = encoder.embed_text(actual_rendering, layer=layer)
        surprise = _cosine_distance(predicted_embedding, actual_embedding)
    except (TypeError, ValueError) as error:
        raise ProofViewError(f"could not compare proof transitions: {error}") from error
    return TransitionComparison(
        _embedded(predicted_rendering, predicted_embedding),
        _embedded(actual_rendering, actual_embedding),
        surprise,
    )


def build_proof_views(
    context: ProofRescueContext,
    structure: ProofStructure,
    encoder: Any,
    layer: int,
) -> ProofViews:
    language = render_language_view(context)
    structural = render_structure_view(structure)
    return ProofViews(
        _embedded(language, encoder.embed_text(language, layer=layer)),
        _embedded(structural, encoder.embed_text(structural, layer=layer)),
        structure,
    )


def _embedded(rendering: str, embedding: Embedding) -> EmbeddedText:
    return EmbeddedText(
        rendering,
        embedding,
        EmbeddingProvenance(
            embedding.model_revision or embedding.model_id,
            embedding.layer,
            embedding.token_index,
            embedding.text_hash or hashlib.sha256(rendering.encode("utf-8")).hexdigest(),
        ),
    )


def _cosine_distance(left: Embedding, right: Embedding) -> float:
    if left.dimension != right.dimension:
        raise ProofViewError("transition embeddings must have matching dimensions")
    if not all(math.isfinite(value) for value in (*left.values, *right.values)):
        raise ProofViewError("transition embeddings must be finite")
    left_norm = math.sqrt(sum(value * value for value in left.values))
    right_norm = math.sqrt(sum(value * value for value in right.values))
    if left_norm == 0.0 or right_norm == 0.0:
        raise ProofViewError("transition embeddings must be nonzero")
    cosine = sum(a * b for a, b in zip(left.values, right.values)) / (left_norm * right_norm)
    return max(0.0, min(2.0, 1.0 - cosine))


def _lines(values: tuple[str, ...]) -> str:
    return "\n".join(values) if values else "(none)"


__all__ = [
    "EmbeddedText", "EmbeddingProvenance", "ProofStructure", "ProofViewError",
    "ProofViews", "TransitionComparison", "build_proof_views",
    "compare_transitions", "render_language_view", "render_structure_view",
    "render_transition",
    "render_observed_transition", "render_transfer_observation",
    "build_policy_representation", "zero_embedding_like",
]
