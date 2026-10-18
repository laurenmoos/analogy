"""Semantic novelty for Lean proof states using the frozen base model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ...utils.records import Embedding, SymbolicState


@dataclass(frozen=True, slots=True)
class DiversityObservation:
    state_id: str
    novelty: float
    neighbor_state_ids: tuple[str, ...]
    neighbor_distances: tuple[float, ...]
    embedding_model: str


class FrozenEmbeddingDiversity:
    """Measure novelty as mean cosine distance to the k nearest seen states."""

    def __init__(self, generator: Any, *, layer: int, neighbors: int = 3) -> None:
        if isinstance(layer, bool) or not isinstance(layer, int):
            raise ValueError("layer must be an integer")
        if isinstance(neighbors, bool) or not isinstance(neighbors, int) or neighbors <= 0:
            raise ValueError("neighbors must be a positive integer")
        self._generator = generator
        self._layer = layer
        self._neighbors = neighbors
        self._archive: list[tuple[str, Embedding]] = []
        self._observations: dict[str, DiversityObservation] = {}

    def observe(self, state: SymbolicState) -> DiversityObservation:
        cached = self._observations.get(state.id)
        if cached is not None:
            return cached
        embedding = self._generator.embed_text(
            _render_state(state), layer=self._layer
        )
        ranked = sorted(
            (
                (_cosine_distance(embedding, prior), state_id)
                for state_id, prior in self._archive
            ),
            key=lambda item: (item[0], item[1]),
        )[: self._neighbors]
        distances = tuple(distance for distance, _ in ranked)
        observation = DiversityObservation(
            state_id=state.id,
            novelty=sum(distances) / len(distances) if distances else 1.0,
            neighbor_state_ids=tuple(state_id for _, state_id in ranked),
            neighbor_distances=distances,
            embedding_model=embedding.model_id,
        )
        self._archive.append((state.id, embedding))
        self._observations[state.id] = observation
        return observation


def _render_state(state: SymbolicState) -> str:
    accepted = "\n".join(state.context) if state.context else "(none)"
    return (
        "Lean 4 proof-search state\n"
        f"Current goals:\n{state.expression}\n"
        f"Accepted prefix:\n{accepted}"
    )


def _cosine_distance(left: Embedding, right: Embedding) -> float:
    if left.dimension != right.dimension:
        raise ValueError("state embeddings must have matching dimensions")
    dot = sum(a * b for a, b in zip(left.values, right.values))
    left_norm = math.sqrt(sum(value * value for value in left.values))
    right_norm = math.sqrt(sum(value * value for value in right.values))
    if left_norm <= 0.0 or right_norm <= 0.0:
        raise ValueError("state embeddings must be nonzero")
    return max(0.0, min(2.0, 1.0 - dot / (left_norm * right_norm)))


__all__ = ["DiversityObservation", "FrozenEmbeddingDiversity"]
