"""Relational mental-model scoring with entropic Gromov–Wasserstein."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any

from ...utils.records import Embedding
from .state import MentalModel


class StructuralScoringError(ValueError):
    """Raised when a structural graph or embedding cannot be scored."""


@dataclass(frozen=True, slots=True)
class StructuralElement:
    id: str
    role: str
    text: str
    position: float

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (self.id, self.role, self.text)):
            raise StructuralScoringError("structural elements require nonblank text")
        if isinstance(self.position, bool) or not isinstance(self.position, (int, float)) or not math.isfinite(float(self.position)):
            raise StructuralScoringError("structural element position must be finite")


@dataclass(frozen=True, slots=True)
class StructuralGraph:
    elements: tuple[StructuralElement, ...]
    edges: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        elements = tuple(self.elements)
        edges = tuple(tuple(edge) for edge in self.edges)
        if not elements:
            raise StructuralScoringError("structural graph must not be empty")
        ids = tuple(item.id for item in elements)
        if len(set(ids)) != len(ids):
            raise StructuralScoringError("structural element ids must be unique")
        known = set(ids)
        if any(len(edge) != 2 or edge[0] not in known or edge[1] not in known for edge in edges):
            raise StructuralScoringError("structural graph edges must reference known elements")
        object.__setattr__(self, "elements", elements)
        object.__setattr__(self, "edges", edges)


@dataclass(frozen=True, slots=True)
class ProofBelief:
    graph: StructuralGraph


@dataclass(frozen=True, slots=True)
class MentalModelFit:
    distance: float
    coupling: tuple[tuple[float, ...], ...]
    mental_model_graph: StructuralGraph
    proof_belief_graph: StructuralGraph


def build_proof_belief(search: Any) -> ProofBelief:
    """Build the accepted proof-state/tactic graph known to search."""

    nodes = list(search.nodes_by_id.values())
    insertion = {node.state.id: index for index, node in enumerate(nodes)}
    nodes.sort(key=lambda node: (node.depth, insertion[node.state.id]))
    elements: list[StructuralElement] = []
    edges: list[tuple[str, str]] = []
    position = 0.0
    for node in nodes:
        state_id = f"proof-state:{node.state.id}"
        if node.parent_state_id is not None and node.tactic is not None:
            tactic_id = f"proof-tactic:{node.state.id}"
            elements.append(
                StructuralElement(tactic_id, "proof-tactic", node.tactic, position)
            )
            position += 1.0
            edges.append((f"proof-state:{node.parent_state_id}", tactic_id))
            edges.append((tactic_id, state_id))
        elements.append(
            StructuralElement(state_id, "proof-state", node.state.expression, position)
        )
        position += 1.0
    return ProofBelief(StructuralGraph(tuple(elements), tuple(edges)))


def score_mental_model_revision(
    model: MentalModel,
    belief: ProofBelief,
    embedder: Any,
    *,
    layer: int,
) -> MentalModelFit:
    """Score one candidate revision by relational fit; lower is better."""

    mental_graph = _mental_model_graph(model)
    mental_embeddings = _embed_graph(
        mental_graph, embedder, layer=layer, domain=model.domain
    )
    proof_embeddings = _embed_graph(
        belief.graph, embedder, layer=layer, domain=None
    )
    all_embeddings = mental_embeddings + proof_embeddings
    dimensions = {item.dimension for item in all_embeddings}
    if len(dimensions) != 1:
        raise StructuralScoringError("embedding dimensions must agree")
    if any(_norm(item.values) <= 0.0 for item in all_embeddings):
        raise StructuralScoringError("embedding vectors must be nonzero")
    mental_distances = _relational_distances(mental_graph, mental_embeddings)
    proof_distances = _relational_distances(belief.graph, proof_embeddings)
    distance, coupling = _entropic_gromov_wasserstein(
        mental_distances, proof_distances
    )
    return MentalModelFit(
        distance,
        coupling,
        mental_graph,
        belief.graph,
    )


def _mental_model_graph(model: MentalModel) -> StructuralGraph:
    elements: list[StructuralElement] = []
    by_state: dict[str, str] = {}
    edges: list[tuple[str, str]] = []

    def state_element(text: str, position: float) -> str:
        normalized = " ".join(text.split()).casefold()
        existing = by_state.get(normalized)
        if existing is not None:
            return existing
        identifier = f"domain-state:{len(by_state)}"
        by_state[normalized] = identifier
        elements.append(StructuralElement(identifier, "domain-state", text, position))
        return identifier

    for index, correspondence in enumerate(model.correspondences):
        base = float(index * 2)
        before = state_element(correspondence.domain_before, base)
        transition = f"domain-transition:{index}"
        elements.append(
            StructuralElement(
                transition,
                "domain-transition",
                correspondence.domain_transition,
                base + 1.0,
            )
        )
        after = state_element(correspondence.domain_after, base + 2.0)
        edges.extend(((before, transition), (transition, after)))
    elements.sort(key=lambda item: (item.position, item.id))
    return StructuralGraph(tuple(elements), tuple(edges))


def _embed_graph(
    graph: StructuralGraph,
    embedder: Any,
    *,
    layer: int,
    domain: str | None,
) -> tuple[Embedding, ...]:
    values: list[Embedding] = []
    for element in graph.elements:
        prefix = f"domain={domain}\n" if domain is not None else ""
        text = f"{prefix}role={element.role}\n{element.text}"
        try:
            embedding = embedder.embed_text(text, layer=layer)
        except Exception as error:
            raise StructuralScoringError(f"embedding failed: {error}") from error
        if not isinstance(embedding, Embedding):
            raise StructuralScoringError("embedding provider must return Embedding")
        values.append(embedding)
    return tuple(values)


def _relational_distances(
    graph: StructuralGraph,
    embeddings: tuple[Embedding, ...],
) -> tuple[tuple[float, ...], ...]:
    graph_distances = _graph_distances(graph)
    positions = [item.position for item in graph.elements]
    span = max(positions) - min(positions) if len(positions) > 1 else 0.0
    result: list[tuple[float, ...]] = []
    for left, left_embedding in enumerate(embeddings):
        row = []
        for right, right_embedding in enumerate(embeddings):
            semantic = _cosine_distance(left_embedding, right_embedding)
            order = abs(positions[left] - positions[right]) / span if span else 0.0
            row.append((semantic + graph_distances[left][right] + order) / 3.0)
        result.append(tuple(row))
    return tuple(result)


def _graph_distances(graph: StructuralGraph) -> tuple[tuple[float, ...], ...]:
    ids = [item.id for item in graph.elements]
    indices = {identifier: index for index, identifier in enumerate(ids)}
    neighbors = [set() for _ in ids]
    for source, target in graph.edges:
        left, right = indices[source], indices[target]
        neighbors[left].add(right)
        neighbors[right].add(left)
    raw: list[list[float]] = []
    maximum = 0.0
    for origin in range(len(ids)):
        distances = [math.inf] * len(ids)
        distances[origin] = 0.0
        queue = deque([origin])
        while queue:
            current = queue.popleft()
            for neighbor in neighbors[current]:
                if math.isinf(distances[neighbor]):
                    distances[neighbor] = distances[current] + 1.0
                    maximum = max(maximum, distances[neighbor])
                    queue.append(neighbor)
        raw.append(distances)
    disconnected = maximum + 1.0 if maximum else 1.0
    scale = max(maximum, disconnected, 1.0)
    return tuple(
        tuple((value if math.isfinite(value) else disconnected) / scale for value in row)
        for row in raw
    )


def _cosine_distance(left: Embedding, right: Embedding) -> float:
    if left.dimension != right.dimension:
        raise StructuralScoringError("embedding dimensions must agree")
    left_norm = _norm(left.values)
    right_norm = _norm(right.values)
    if left_norm <= 0.0 or right_norm <= 0.0:
        raise StructuralScoringError("embedding vectors must be nonzero")
    similarity = sum(a * b for a, b in zip(left.values, right.values)) / (
        left_norm * right_norm
    )
    return max(0.0, min(2.0, 1.0 - similarity)) / 2.0


def _norm(values: tuple[float, ...]) -> float:
    return math.sqrt(sum(value * value for value in values))


def _entropic_gromov_wasserstein(
    left: tuple[tuple[float, ...], ...],
    right: tuple[tuple[float, ...], ...],
    *,
    epsilon: float = 0.01,
    outer_iterations: int = 40,
    sinkhorn_iterations: int = 80,
) -> tuple[float, tuple[tuple[float, ...], ...]]:
    n, m = len(left), len(right)
    source = [1.0 / n] * n
    target = [1.0 / m] * m
    coupling = [[1.0 / (n * m) for _ in range(m)] for _ in range(n)]
    for _ in range(outer_iterations):
        loss = [
            [
                sum(
                    (left[i][k] - right[j][l]) ** 2 * coupling[k][l]
                    for k in range(n)
                    for l in range(m)
                )
                for j in range(m)
            ]
            for i in range(n)
        ]
        minimum = min(min(row) for row in loss)
        kernel = [
            [max(math.exp(-(value - minimum) / epsilon), 1e-300) for value in row]
            for row in loss
        ]
        u = [1.0] * n
        v = [1.0] * m
        for _ in range(sinkhorn_iterations):
            u = [
                source[i] / max(sum(kernel[i][j] * v[j] for j in range(m)), 1e-300)
                for i in range(n)
            ]
            v = [
                target[j] / max(sum(kernel[i][j] * u[i] for i in range(n)), 1e-300)
                for j in range(m)
            ]
        updated = [[u[i] * kernel[i][j] * v[j] for j in range(m)] for i in range(n)]
        change = sum(abs(updated[i][j] - coupling[i][j]) for i in range(n) for j in range(m))
        coupling = updated
        if change < 1e-10:
            break
    objective = sum(
        (left[i][k] - right[j][l]) ** 2
        * coupling[i][j]
        * coupling[k][l]
        for i in range(n)
        for j in range(m)
        for k in range(n)
        for l in range(m)
    )
    return math.sqrt(max(objective, 0.0)), tuple(tuple(row) for row in coupling)


__all__ = [
    "MentalModelFit",
    "ProofBelief",
    "StructuralElement",
    "StructuralGraph",
    "StructuralScoringError",
    "build_proof_belief",
    "score_mental_model_revision",
]
