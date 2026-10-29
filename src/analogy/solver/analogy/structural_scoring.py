"""Frozen ordered-transition score for working mental models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np

from ...utils.records import Embedding
from .state import MentalModel


FROZEN_EMBEDDING_LAYER = 12
FROZEN_MATRIX_SIZE = 24
FROZEN_PRINCIPAL_COMPONENTS = 2
FROZEN_SOURCE_TEXT_COUNT = 838
FROZEN_EMBEDDING_MODEL = "mlx-community/Qwen3-14B-4bit"
FROZEN_EMBEDDING_REVISION = (
    "mlx-community/Qwen3-14B-4bit@a4d9b2df59d2c150bef02fcbe0d91046b7ca33a4"
)
FROZEN_EMBEDDING_POOLING = "mean"
FROZEN_SCORE_METHOD = "ordered-transition-resized-correlation-v1"
_CALIBRATION_RESOURCE = "score_calibration.npz"


class StructuralScoringError(ValueError):
    """Raised when ordered transition structure cannot be scored."""


class UnscorableMentalModelError(StructuralScoringError):
    """Raised when one candidate mental model has no scoreable structure."""


@dataclass(frozen=True, slots=True)
class StructuralElement:
    id: str
    role: str
    text: str
    position: float

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.id, self.role, self.text)
        ):
            raise StructuralScoringError("structural elements require nonblank text")
        if (
            isinstance(self.position, bool)
            or not isinstance(self.position, (int, float))
            or not math.isfinite(float(self.position))
        ):
            raise StructuralScoringError(
                "structural element position must be finite"
            )


@dataclass(frozen=True, slots=True)
class StructuralGraph:
    elements: tuple[StructuralElement, ...]
    edges: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        elements = tuple(self.elements)
        edges = tuple(tuple(edge) for edge in self.edges)
        if not elements:
            raise StructuralScoringError("structural graph must not be empty")
        identifiers = tuple(item.id for item in elements)
        if len(set(identifiers)) != len(identifiers):
            raise StructuralScoringError("structural element ids must be unique")
        known = set(identifiers)
        if any(
            len(edge) != 2 or edge[0] not in known or edge[1] not in known
            for edge in edges
        ):
            raise StructuralScoringError(
                "structural graph edges must reference known elements"
            )
        object.__setattr__(self, "elements", elements)
        object.__setattr__(self, "edges", edges)


@dataclass(frozen=True, slots=True)
class ProofBelief:
    graph: StructuralGraph


@dataclass(frozen=True, slots=True)
class ProofTrajectory:
    states: tuple[str, ...]
    tactics: tuple[str, ...]

    def __post_init__(self) -> None:
        states = tuple(self.states)
        tactics = tuple(self.tactics)
        if len(states) != len(tactics) + 1:
            raise StructuralScoringError(
                "proof trajectory must contain one more state than tactic"
            )
        object.__setattr__(self, "states", states)
        object.__setattr__(self, "tactics", tactics)


def proof_trajectory(belief: ProofBelief) -> ProofTrajectory:
    return ProofTrajectory(
        tuple(
            item.text for item in belief.graph.elements if item.role == "proof-state"
        ),
        tuple(
            item.text for item in belief.graph.elements if item.role == "proof-tactic"
        ),
    )


def abstract_proof_transition(tactic: str) -> str:
    """Describe an accepted tactic without exposing any Lean operand."""

    normalized = " ".join(tactic.split())
    head = normalized.split(maxsplit=1)[0].casefold() if normalized else ""
    if head in {"intro", "intros", "rintro"}:
        return "take on an assumption"
    if head in {"obtain", "cases", "rcases"}:
        return "break a combined fact into its parts"
    if head == "apply":
        return "use a rule to reduce the goal"
    if head in {"exact", "assumption"}:
        return "finish using something already in hand"
    if head == "constructor":
        return "split the task into required parts"
    if head == "left":
        return "commit to the left available choice"
    if head == "right":
        return "commit to the right available choice"
    if head in {"rw", "rewrite"}:
        return "rewrite the situation using a known relationship"
    if head in {"simp", "simpa"}:
        return "simplify the situation using known relationships"
    if "false.elim" in normalized.casefold() or head == "contradiction":
        return "finish because the starting conditions conflict"
    return "make the next proof move"


@dataclass(frozen=True, slots=True)
class EmbeddingCalibration:
    """Frozen anisotropy correction fitted on the 838-text experiment corpus."""

    mean: tuple[float, ...]
    principal_components: tuple[tuple[float, ...], ...]
    model_id: str | None = None
    model_revision: str | None = None

    def __post_init__(self) -> None:
        mean = tuple(float(value) for value in self.mean)
        components = tuple(
            tuple(float(value) for value in component)
            for component in self.principal_components
        )
        if not mean or not all(math.isfinite(value) for value in mean):
            raise StructuralScoringError("calibration mean must be finite and nonempty")
        if len(components) != FROZEN_PRINCIPAL_COMPONENTS:
            raise StructuralScoringError("calibration must contain exactly two principal components")
        if any(
            len(component) != len(mean)
            or not all(math.isfinite(value) for value in component)
            for component in components
        ):
            raise StructuralScoringError("calibration dimensions must agree")
        if self.model_id is not None and (
            not isinstance(self.model_id, str) or not self.model_id.strip()
        ):
            raise StructuralScoringError("calibration model id must be nonblank")
        if self.model_revision is not None and (
            not isinstance(self.model_revision, str)
            or not self.model_revision.strip()
        ):
            raise StructuralScoringError(
                "calibration model revision must be nonblank"
            )
        object.__setattr__(self, "mean", mean)
        object.__setattr__(self, "principal_components", components)


@dataclass(frozen=True, slots=True)
class MentalModelFit:
    distance: float
    coupling: tuple[tuple[float, ...], ...]
    mental_model_graph: StructuralGraph
    proof_belief_graph: StructuralGraph
    discriminative: bool = True


def build_proof_belief(search: Any) -> ProofBelief:
    """Build the linear root-to-active-state trace used by the frozen score."""

    rescue_node = getattr(search, "rescue_node", None)
    if rescue_node is not None:
        nodes = _active_ancestry(rescue_node, search.nodes_by_id)
    else:
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


def _active_ancestry(active_node: Any, nodes_by_id: dict[str, Any]) -> list[Any]:
    """Follow parent links from the active rescue node back to the root."""

    reversed_path = []
    seen = set()
    node = active_node
    while node is not None:
        state_id = node.state.id
        if state_id in seen:
            raise StructuralScoringError("proof ancestry contains a cycle")
        seen.add(state_id)
        reversed_path.append(node)
        parent_id = node.parent_state_id
        if parent_id is None:
            break
        try:
            node = nodes_by_id[parent_id]
        except KeyError as error:
            raise StructuralScoringError(
                f"proof ancestry is missing parent state {parent_id!r}"
            ) from error
    return list(reversed(reversed_path))


def ordered_mental_model_state_texts(model: MentalModel) -> tuple[str, ...]:
    """Return the story states in correspondence order, excluding action labels."""

    for previous, current in zip(
        model.correspondences, model.correspondences[1:]
    ):
        if _normalized_state(previous.domain_after) != _normalized_state(
            current.domain_before
        ):
            raise UnscorableMentalModelError(
                "mental-model correspondences must form one continuous state sequence"
            )
    states = [model.correspondences[0].domain_before]
    states.extend(item.domain_after for item in model.correspondences)
    return tuple(
        f"domain={model.domain}\nrole=domain-state\n{text}" for text in states
    )


def _normalized_state(text: str) -> str:
    return " ".join(text.split()).casefold()


def ordered_proof_state_texts(belief: ProofBelief) -> tuple[str, ...]:
    """Return proof states in the order recorded by the proof belief."""

    return tuple(
        f"role=proof-state\n{item.text}"
        for item in belief.graph.elements
        if item.role == "proof-state"
    )


def embed_mean_pooled_states(
    texts: tuple[str, ...],
    embedder: Any,
    *,
    expected_model_revision: str | None = None,
) -> tuple[tuple[float, ...], ...]:
    """Embed every ordered state with layer-12 mean-token pooling."""

    values = []
    for text in texts:
        try:
            embedding = embedder.embed_mean_text(
                text, layer=FROZEN_EMBEDDING_LAYER
            )
        except Exception as error:
            raise StructuralScoringError(f"embedding failed: {error}") from error
        if not isinstance(embedding, Embedding):
            raise StructuralScoringError("embedding provider must return Embedding")
        if expected_model_revision is not None and (
            embedding.model_revision != expected_model_revision
        ):
            raise StructuralScoringError(
                "embedding provider does not match the frozen calibration model "
                f"revision {expected_model_revision!r}"
            )
        values.append(embedding.values)
    dimensions = {len(value) for value in values}
    if len(dimensions) != 1:
        raise StructuralScoringError("embedding dimensions must agree")
    return tuple(values)


@lru_cache(maxsize=1)
def load_frozen_embedding_calibration() -> EmbeddingCalibration:
    """Load the frozen Qwen layer-12 mean-token calibration shipped with the solver."""

    resource = resources.files(__package__).joinpath(_CALIBRATION_RESOURCE)
    with resources.as_file(resource) as path:
        return load_embedding_calibration(
            path,
            expected_model_id=FROZEN_EMBEDDING_MODEL,
            expected_model_revision=FROZEN_EMBEDDING_REVISION,
        )


def load_embedding_calibration(
    path: Path,
    *,
    expected_model_id: str | None = None,
    expected_model_revision: str | None = None,
) -> EmbeddingCalibration:
    """Load one immutable calibration and validate its scoring contract."""

    try:
        with np.load(Path(path)) as payload:
            metadata = {
                "score_method": str(payload["score_method"].reshape(-1)[0]),
                "model_id": str(payload["model_id"].reshape(-1)[0]),
                "model_revision": str(
                    payload["model_revision"].reshape(-1)[0]
                ),
                "pooling": str(payload["pooling"].reshape(-1)[0]),
                "source_text_count": int(payload["source_text_count"].reshape(-1)[0]),
                "embedding_layer": int(payload["embedding_layer"].reshape(-1)[0]),
                "matrix_size": int(payload["matrix_size"].reshape(-1)[0]),
            }
            frozen_contract = {
                "score_method": FROZEN_SCORE_METHOD,
                "pooling": FROZEN_EMBEDDING_POOLING,
                "source_text_count": FROZEN_SOURCE_TEXT_COUNT,
                "embedding_layer": FROZEN_EMBEDDING_LAYER,
                "matrix_size": FROZEN_MATRIX_SIZE,
            }
            actual_contract = {
                key: metadata[key] for key in frozen_contract
            }
            if actual_contract != frozen_contract:
                raise StructuralScoringError(
                    f"frozen score calibration metadata mismatch: {metadata!r}"
                )
            if (
                expected_model_id is not None
                and metadata["model_id"] != expected_model_id
            ):
                raise StructuralScoringError(
                    "frozen score calibration model id mismatch: "
                    f"expected {expected_model_id!r}, got {metadata['model_id']!r}"
                )
            if (
                expected_model_revision is not None
                and metadata["model_revision"] != expected_model_revision
            ):
                raise StructuralScoringError(
                    "frozen score calibration model revision mismatch: "
                    f"expected {expected_model_revision!r}, "
                    f"got {metadata['model_revision']!r}"
                )
            mean = tuple(float(value) for value in payload["mean"])
            components = tuple(
                tuple(float(value) for value in row)
                for row in payload["principal_components"]
            )
    except Exception as error:
        raise StructuralScoringError(
            f"could not load frozen score calibration: {error}"
        ) from error
    return EmbeddingCalibration(
        mean,
        components,
        model_id=metadata["model_id"],
        model_revision=metadata["model_revision"],
    )


def fit_embedding_calibration(
    embeddings: tuple[tuple[float, ...], ...],
    *,
    model_id: str,
    model_revision: str,
) -> EmbeddingCalibration:
    """Fit frozen mean and top-two principal components from a fixed corpus."""

    vectors = np.asarray(embeddings, dtype=np.float64)
    if vectors.ndim != 2 or vectors.shape[0] < 3 or vectors.shape[1] < 2:
        raise StructuralScoringError(
            "calibration fitting requires at least three equal-sized embeddings"
        )
    if not np.all(np.isfinite(vectors)):
        raise StructuralScoringError("calibration embeddings must be finite")
    mean = vectors.mean(axis=0)
    centered = vectors - mean
    _, _, right_vectors = np.linalg.svd(centered, full_matrices=False)
    return EmbeddingCalibration(
        tuple(float(value) for value in mean),
        tuple(
            tuple(float(value) for value in component)
            for component in right_vectors[:FROZEN_PRINCIPAL_COMPONENTS]
        ),
        model_id=model_id,
        model_revision=model_revision,
    )


def validate_calibration_embeddings(
    records: tuple[Embedding, ...],
) -> tuple[tuple[tuple[float, ...], ...], str]:
    """Require one exact immutable model revision across a calibration corpus."""

    if not records or any(not isinstance(record, Embedding) for record in records):
        raise StructuralScoringError(
            "calibration requires nonempty embedding records"
        )
    revisions = {record.model_revision for record in records}
    if len(revisions) != 1:
        raise StructuralScoringError(
            "calibration embeddings must use the same model revision"
        )
    revision = next(iter(revisions))
    if revision is None or revision.startswith("unresolved:"):
        raise StructuralScoringError(
            "calibration requires a resolved immutable revision"
        )
    dimensions = {len(record.values) for record in records}
    if len(dimensions) != 1:
        raise StructuralScoringError(
            "calibration embedding dimensions must agree"
        )
    return tuple(record.values for record in records), revision


def remove_embedding_anisotropy(
    embeddings: tuple[tuple[float, ...], ...],
    calibration: EmbeddingCalibration,
) -> tuple[tuple[float, ...], ...]:
    """Subtract the fitted mean and remove the fitted top two PCs."""

    vectors = np.asarray(embeddings, dtype=np.float64)
    mean = np.asarray(calibration.mean, dtype=np.float64)
    components = np.asarray(calibration.principal_components, dtype=np.float64)
    if vectors.ndim != 2 or vectors.shape[1] != mean.shape[0]:
        raise StructuralScoringError("calibration dimension does not match embeddings")
    centered = vectors - mean
    cleaned = centered - (centered @ components.T) @ components
    return tuple(tuple(float(value) for value in row) for row in cleaned)


def compute_state_change_vectors(
    ordered_states: tuple[tuple[float, ...], ...],
) -> tuple[tuple[float, ...], ...]:
    """Represent each step by after-state minus before-state."""

    states = np.asarray(ordered_states, dtype=np.float64)
    if states.ndim != 2 or len(states) < 2:
        raise StructuralScoringError("score requires at least two ordered states")
    changes = states[1:] - states[:-1]
    return tuple(tuple(float(value) for value in row) for row in changes)


def build_cosine_step_distance_matrix(
    changes: tuple[tuple[float, ...], ...],
) -> tuple[tuple[float, ...], ...]:
    """Build and mean-normalize the ordered step-to-step cosine distances."""

    vectors = np.asarray(changes, dtype=np.float64)
    if vectors.ndim != 2 or not len(vectors):
        raise StructuralScoringError("score requires at least one state change")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(~np.isfinite(norms)) or np.any(norms <= 1e-12):
        raise StructuralScoringError("state-change vectors must be finite and nonzero")
    unit = vectors / norms
    distances = np.clip(1.0 - unit @ unit.T, 0.0, 2.0) / 2.0
    mean = float(distances.mean())
    if mean > 1e-12:
        distances = distances / mean
    return tuple(tuple(float(value) for value in row) for row in distances)


def resize_step_distance_matrix(
    matrix: tuple[tuple[float, ...], ...], *, size: int = FROZEN_MATRIX_SIZE
) -> tuple[tuple[float, ...], ...]:
    """Bilinearly resize an ordered square distance matrix to the frozen size."""

    values = np.asarray(matrix, dtype=np.float64)
    if values.ndim != 2 or values.shape[0] != values.shape[1] or not len(values):
        raise StructuralScoringError("step-distance matrix must be nonempty and square")
    if isinstance(size, bool) or not isinstance(size, int) or size < 2:
        raise StructuralScoringError("resized matrix size must be at least two")
    source = np.linspace(0.0, 1.0, values.shape[0])
    target = np.linspace(0.0, 1.0, size)
    rows = np.asarray([np.interp(target, source, row) for row in values])
    resized = np.asarray(
        [np.interp(target, source, column) for column in rows.T]
    ).T
    resized = (resized + resized.T) / 2.0
    return tuple(tuple(float(value) for value in row) for row in resized)


def upper_triangle_correlation_distance(
    left: tuple[tuple[float, ...], ...],
    right: tuple[tuple[float, ...], ...],
) -> float:
    """Compare two same-sized structures by upper-triangle Pearson distance."""

    left_values = np.asarray(left, dtype=np.float64)
    right_values = np.asarray(right, dtype=np.float64)
    if left_values.shape != right_values.shape or left_values.ndim != 2:
        raise StructuralScoringError("correlation requires equal square matrices")
    if left_values.shape[0] != left_values.shape[1] or left_values.shape[0] < 2:
        raise StructuralScoringError("correlation requires equal square matrices")
    indices = np.triu_indices(left_values.shape[0], k=1)
    left_upper = left_values[indices]
    right_upper = right_values[indices]
    left_centered = left_upper - left_upper.mean()
    right_centered = right_upper - right_upper.mean()
    denominator = float(
        np.linalg.norm(left_centered) * np.linalg.norm(right_centered)
    )
    if denominator <= 1e-12:
        return float(np.sqrt(np.mean((left_centered - right_centered) ** 2)))
    correlation = float(np.dot(left_centered, right_centered) / denominator)
    return float(np.clip(1.0 - correlation, 0.0, 2.0) / 2.0)


def fixed_order_alignment(size: int = FROZEN_MATRIX_SIZE) -> tuple[tuple[float, ...], ...]:
    """Describe the fixed index-to-index alignment used after matrix resizing."""

    return tuple(
        tuple(1.0 / size if row == column else 0.0 for column in range(size))
        for row in range(size)
    )


def build_resized_transition_structure(
    ordered_states: tuple[tuple[float, ...], ...],
) -> tuple[tuple[float, ...], ...]:
    """Turn ordered state embeddings into the frozen 24×24 change structure."""

    changes = compute_state_change_vectors(ordered_states)
    distances = build_cosine_step_distance_matrix(changes)
    return resize_step_distance_matrix(distances)


def score_mental_model_revision(
    model: MentalModel,
    belief: ProofBelief,
    embedder: Any,
    *,
    calibration: EmbeddingCalibration | None = None,
) -> MentalModelFit:
    """Score ordered transition fit with the frozen 24×24 correlation method."""

    mental_texts = ordered_mental_model_state_texts(model)
    proof_texts = ordered_proof_state_texts(belief)
    calibration = calibration or load_frozen_embedding_calibration()
    # One transition has no relationship to compare. Two transitions always
    # reduce to the same normalized 2x2 distance matrix. Neither case can
    # discriminate a useful analogy from an irrelevant one.
    if len(proof_texts) < 4:
        probe = embed_mean_pooled_states(
            (mental_texts[0],),
            embedder,
            expected_model_revision=calibration.model_revision,
        )
        remove_embedding_anisotropy(probe, calibration)
        return MentalModelFit(
            0.5,
            fixed_order_alignment(),
            _mental_model_graph(model),
            belief.graph,
            discriminative=False,
        )
    mental_states = remove_embedding_anisotropy(
        embed_mean_pooled_states(
            mental_texts,
            embedder,
            expected_model_revision=calibration.model_revision,
        ),
        calibration,
    )
    proof_states = remove_embedding_anisotropy(
        embed_mean_pooled_states(
            proof_texts,
            embedder,
            expected_model_revision=calibration.model_revision,
        ),
        calibration,
    )
    try:
        mental_resized = build_resized_transition_structure(mental_states)
    except StructuralScoringError as error:
        raise UnscorableMentalModelError(str(error)) from error
    proof_resized = build_resized_transition_structure(proof_states)
    distance = upper_triangle_correlation_distance(
        mental_resized, proof_resized
    )
    return MentalModelFit(
        distance,
        fixed_order_alignment(),
        _mental_model_graph(model),
        belief.graph,
    )


def _mental_model_graph(model: MentalModel) -> StructuralGraph:
    elements: list[StructuralElement] = []
    edges: list[tuple[str, str]] = []
    first_id = "domain-state:0"
    elements.append(
        StructuralElement(
            first_id,
            "domain-state",
            model.correspondences[0].domain_before,
            0.0,
        )
    )
    for index, correspondence in enumerate(model.correspondences):
        transition_id = f"domain-transition:{index}"
        after_id = f"domain-state:{index + 1}"
        elements.append(
            StructuralElement(
                transition_id,
                "domain-transition",
                correspondence.domain_transition,
                float(index * 2 + 1),
            )
        )
        elements.append(
            StructuralElement(
                after_id,
                "domain-state",
                correspondence.domain_after,
                float(index * 2 + 2),
            )
        )
        edges.extend(
            ((f"domain-state:{index}", transition_id), (transition_id, after_id))
        )
    return StructuralGraph(tuple(elements), tuple(edges))


__all__ = [
    "FROZEN_EMBEDDING_LAYER",
    "FROZEN_EMBEDDING_MODEL",
    "FROZEN_EMBEDDING_POOLING",
    "FROZEN_EMBEDDING_REVISION",
    "FROZEN_MATRIX_SIZE",
    "FROZEN_SCORE_METHOD",
    "FROZEN_SOURCE_TEXT_COUNT",
    "EmbeddingCalibration",
    "MentalModelFit",
    "ProofBelief",
    "StructuralElement",
    "StructuralGraph",
    "StructuralScoringError",
    "UnscorableMentalModelError",
    "build_cosine_step_distance_matrix",
    "build_proof_belief",
    "build_resized_transition_structure",
    "compute_state_change_vectors",
    "embed_mean_pooled_states",
    "fixed_order_alignment",
    "fit_embedding_calibration",
    "load_embedding_calibration",
    "load_frozen_embedding_calibration",
    "ordered_mental_model_state_texts",
    "ordered_proof_state_texts",
    "remove_embedding_anisotropy",
    "resize_step_distance_matrix",
    "score_mental_model_revision",
    "upper_triangle_correlation_distance",
    "validate_calibration_embeddings",
]
