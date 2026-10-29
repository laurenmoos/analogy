import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from analogy.main import load_config
from analogy.solver.analogy.state import MentalModel, MentalModelCorrespondence
from analogy.solver.analogy.structural_scoring import (
    FROZEN_EMBEDDING_LAYER,
    FROZEN_EMBEDDING_MODEL,
    FROZEN_EMBEDDING_REVISION,
    FROZEN_MATRIX_SIZE,
    EmbeddingCalibration,
    ProofBelief,
    ProofTrajectory,
    StructuralElement,
    StructuralGraph,
    StructuralScoringError,
    build_cosine_step_distance_matrix,
    build_proof_belief,
    abstract_proof_transition,
    proof_trajectory,
    compute_state_change_vectors,
    fit_embedding_calibration,
    load_embedding_calibration,
    load_frozen_embedding_calibration,
    ordered_mental_model_state_texts,
    ordered_proof_state_texts,
    remove_embedding_anisotropy,
    resize_step_distance_matrix,
    score_mental_model_revision,
    validate_calibration_embeddings,
    upper_triangle_correlation_distance,
)
from analogy.utils.records import Embedding, SearchNode, SymbolicState


ROOT = Path(__file__).parents[1]


def _model(order=(0, 1, 2)):
    transitions = (
        MentalModelCorrespondence("shore", "sail", "island"),
        MentalModelCorrespondence("island", "read map", "cave"),
        MentalModelCorrespondence("cave", "open chest", "treasure"),
    )
    return MentalModel("treasure hunt", tuple(transitions[index] for index in order))


def _belief():
    elements = (
        StructuralElement("p0", "proof-state", "shore", 0.0),
        StructuralElement("t1", "proof-tactic", "sail", 1.0),
        StructuralElement("p1", "proof-state", "island", 2.0),
        StructuralElement("t2", "proof-tactic", "read map", 3.0),
        StructuralElement("p2", "proof-state", "cave", 4.0),
        StructuralElement("t3", "proof-tactic", "open chest", 5.0),
        StructuralElement("p3", "proof-state", "treasure", 6.0),
    )
    return ProofBelief(
        StructuralGraph(
            elements,
            tuple((elements[index].id, elements[index + 1].id) for index in range(6)),
        )
    )


class _MeanSemanticEmbedder:
    def __init__(self):
        self.inputs = []
        self.values = {
            "shore": (1.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            "island": (0.5, 0.5, 0.0, 0.0, 0.0, 0.0),
            "cave": (0.0, 0.5, 0.5, 0.0, 0.0, 0.0),
            "treasure": (0.0, 0.0, 0.5, 0.5, 0.0, 0.0),
        }

    def embed_mean_text(self, text, *, layer):
        self.inputs.append((text, layer))
        value = next(vector for label, vector in self.values.items() if text.endswith(label))
        return Embedding("semantic-mean", value, layer=layer)


def _calibration():
    return EmbeddingCalibration(
        mean=(0.0,) * 6,
        principal_components=(
            (0.0, 0.0, 0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
        ),
    )


def test_build_proof_belief_contains_accepted_states_and_connecting_tactics():
    root = SearchNode(SymbolicState("root", "P → P", "lean4"), 1.0)
    child = SearchNode(
        SymbolicState("child", "h : P\n⊢ P", "lean4"),
        0.8,
        parent_state_id="root",
        tactic="intro h",
        depth=1,
    )
    search = SimpleNamespace(nodes_by_id={"root": root, "child": child})

    belief = build_proof_belief(search)

    assert [(item.role, item.text) for item in belief.graph.elements] == [
        ("proof-state", "P → P"),
        ("proof-tactic", "intro h"),
        ("proof-state", "h : P\n⊢ P"),
    ]


def test_build_proof_belief_follows_only_the_active_rescue_ancestry():
    root = SearchNode(SymbolicState("root", "root state", "lean4"), 1.0)
    left = SearchNode(
        SymbolicState("left", "left sibling", "lean4"),
        0.8,
        parent_state_id="root",
        tactic="left tactic",
        depth=1,
    )
    right = SearchNode(
        SymbolicState("right", "right state", "lean4"),
        0.7,
        parent_state_id="root",
        tactic="right tactic",
        depth=1,
    )
    active = SearchNode(
        SymbolicState("active", "active state", "lean4"),
        0.6,
        parent_state_id="right",
        tactic="active tactic",
        depth=2,
    )
    search = SimpleNamespace(
        nodes_by_id={node.state.id: node for node in (root, left, right, active)},
        rescue_node=active,
    )

    belief = build_proof_belief(search)

    assert [(item.role, item.text) for item in belief.graph.elements] == [
        ("proof-state", "root state"),
        ("proof-tactic", "right tactic"),
        ("proof-state", "right state"),
        ("proof-tactic", "active tactic"),
        ("proof-state", "active state"),
    ]


def test_proof_trajectory_extracts_owned_states_and_tactics_in_order():
    assert proof_trajectory(_belief()) == ProofTrajectory(
        ("shore", "island", "cave", "treasure"),
        ("sail", "read map", "open chest"),
    )


@pytest.mark.parametrize(
    "tactic,abstract",
    [
        ("intro h", "take on an assumption"),
        ("obtain ⟨hA, hB⟩ := h", "break a combined fact into its parts"),
        ("cases h", "break a combined fact into its parts"),
        ("apply hRule", "use a rule to reduce the goal"),
        ("exact h", "finish using something already in hand"),
        ("constructor", "split the task into required parts"),
        ("left", "commit to the left available choice"),
        ("right", "commit to the right available choice"),
        ("rw [h]", "rewrite the situation using a known relationship"),
        ("uncommon h9", "make the next proof move"),
    ],
)
def test_tactic_abstraction_is_operand_free_and_total(tactic, abstract):
    assert abstract_proof_transition(tactic) == abstract


def test_ordered_state_texts_exclude_transition_labels_but_keep_runtime_prefixes():
    assert ordered_mental_model_state_texts(_model()) == (
        "domain=treasure hunt\nrole=domain-state\nshore",
        "domain=treasure hunt\nrole=domain-state\nisland",
        "domain=treasure hunt\nrole=domain-state\ncave",
        "domain=treasure hunt\nrole=domain-state\ntreasure",
    )
    assert ordered_proof_state_texts(_belief()) == (
        "role=proof-state\nshore",
        "role=proof-state\nisland",
        "role=proof-state\ncave",
        "role=proof-state\ntreasure",
    )


def test_mental_model_states_must_form_one_continuous_sequence():
    model = MentalModel(
        "journey",
        (
            MentalModelCorrespondence("station", "board train", "city"),
            MentalModelCorrespondence("airport", "board plane", "island"),
        ),
    )

    with pytest.raises(StructuralScoringError, match="continuous"):
        ordered_mental_model_state_texts(model)


def test_remove_embedding_anisotropy_subtracts_mean_and_top_two_components():
    calibration = EmbeddingCalibration(
        mean=(1.0, 1.0, 1.0),
        principal_components=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    )
    cleaned = remove_embedding_anisotropy(((4.0, 5.0, 7.0),), calibration)
    np.testing.assert_allclose(cleaned, ((0.0, 0.0, 6.0),))


def test_transition_and_matrix_helpers_preserve_order_and_fixed_size():
    states = ((1.0, 1.0), (3.0, 2.0), (2.0, 5.0))
    changes = compute_state_change_vectors(states)
    assert changes == ((2.0, 1.0), (-1.0, 3.0))
    distances = build_cosine_step_distance_matrix(changes)
    assert distances[0][0] == pytest.approx(0.0)
    assert distances[0][1] == pytest.approx(distances[1][0])
    resized = resize_step_distance_matrix(distances, size=3)
    assert len(resized) == 3 and all(len(row) == 3 for row in resized)


def test_upper_triangle_correlation_is_zero_for_identical_structure():
    matrix = ((0.0, 0.2, 0.9), (0.2, 0.0, 0.5), (0.9, 0.5, 0.0))
    assert upper_triangle_correlation_distance(matrix, matrix) == pytest.approx(0.0)


def test_runtime_score_uses_frozen_mean_layer_and_prefers_matching_order():
    embedder = _MeanSemanticEmbedder()
    different_structure = MentalModel(
        "treasure hunt",
        (
            MentalModelCorrespondence("shore", "enter cave", "cave"),
            MentalModelCorrespondence("cave", "reach island", "island"),
            MentalModelCorrespondence("island", "open chest", "treasure"),
        ),
    )

    matching = score_mental_model_revision(_model(), _belief(), embedder, calibration=_calibration())
    shuffled = score_mental_model_revision(
        different_structure, _belief(), embedder, calibration=_calibration()
    )

    assert matching.distance < shuffled.distance
    assert matching.coupling == shuffled.coupling
    assert math.isfinite(matching.distance)
    assert all(layer == FROZEN_EMBEDDING_LAYER for _, layer in embedder.inputs)
    assert len(matching.coupling) == FROZEN_MATRIX_SIZE
    assert sum(sum(row) for row in matching.coupling) == pytest.approx(1.0)


def test_runtime_score_without_calibration_skips_frozen_calibration(monkeypatch):
    monkeypatch.setattr(
        "analogy.solver.analogy.structural_scoring.load_frozen_embedding_calibration",
        lambda: pytest.fail("disabled calibration must not be loaded"),
    )

    fit = score_mental_model_revision(
        _model(),
        _belief(),
        _MeanSemanticEmbedder(),
    )

    assert math.isfinite(fit.distance)


def test_runtime_score_is_neutral_until_the_proof_has_a_transition():
    belief = ProofBelief(
        StructuralGraph((StructuralElement("only", "proof-state", "shore", 0.0),), ())
    )
    fit = score_mental_model_revision(
        _model(), belief, _MeanSemanticEmbedder(), calibration=_calibration()
    )
    assert fit.distance == pytest.approx(0.5)
    assert fit.discriminative is False


def test_two_transition_score_is_marked_non_discriminating_instead_of_perfect():
    model = MentalModel(
        "treasure hunt",
        (
            MentalModelCorrespondence("shore", "sail", "island"),
            MentalModelCorrespondence("island", "read map", "cave"),
        ),
    )
    belief = ProofBelief(
        StructuralGraph(
            (
                StructuralElement("p0", "proof-state", "shore", 0.0),
                StructuralElement("t1", "proof-tactic", "sail", 1.0),
                StructuralElement("p1", "proof-state", "island", 2.0),
                StructuralElement("t2", "proof-tactic", "read map", 3.0),
                StructuralElement("p2", "proof-state", "cave", 4.0),
            ),
            (("p0", "t1"), ("t1", "p1"), ("p1", "t2"), ("t2", "p2")),
        )
    )

    fit = score_mental_model_revision(
        model, belief, _MeanSemanticEmbedder(), calibration=_calibration()
    )

    assert fit.distance == pytest.approx(0.5)
    assert fit.discriminative is False


def test_root_state_score_still_rejects_wrong_calibration_revision():
    belief = ProofBelief(
        StructuralGraph((StructuralElement("only", "proof-state", "shore", 0.0),), ())
    )
    calibration = EmbeddingCalibration(
        mean=(0.0,) * 6,
        principal_components=(
            (0.0, 0.0, 0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
        ),
        model_id="expected/model",
        model_revision="expected/model@revision",
    )

    with pytest.raises(StructuralScoringError, match="calibration model"):
        score_mental_model_revision(
            _model(), belief, _MeanSemanticEmbedder(), calibration=calibration
        )


def test_frozen_calibration_matches_runtime_embedding_dimension():
    calibration = EmbeddingCalibration(
        mean=(0.0, 0.0),
        principal_components=((1.0, 0.0), (0.0, 1.0)),
    )
    with pytest.raises(StructuralScoringError, match="calibration dimension"):
        remove_embedding_anisotropy(((1.0, 2.0, 3.0),), calibration)


def test_packaged_calibration_is_bound_to_the_frozen_embedding_model():
    calibration = load_frozen_embedding_calibration()

    assert calibration.model_id == FROZEN_EMBEDDING_MODEL
    assert calibration.model_revision == FROZEN_EMBEDDING_REVISION
    assert len(calibration.mean) == 5120
    assert len(calibration.principal_components) == 2


def test_release_config_and_packaged_calibration_use_the_same_14b_model():
    config = load_config(ROOT / "config.yaml")
    calibration = load_embedding_calibration(
        config.analogy.calibration_path,
        expected_model_id=config.model.name,
    )

    assert config.model.name == "mlx-community/Qwen3-14B-4bit"
    assert config.analogy.model_name == config.model.name
    assert calibration.model_id == config.model.name
    assert calibration.model_revision == FROZEN_EMBEDDING_REVISION


def test_generic_calibration_loader_accepts_matching_frozen_metadata(tmp_path):
    path = tmp_path / "calibration.npz"
    np.savez_compressed(
        path,
        mean=np.asarray([1.0, 2.0, 3.0]),
        principal_components=np.asarray(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        ),
        score_method=np.asarray(["ordered-transition-resized-correlation-v1"]),
        model_id=np.asarray(["example/model"]),
        model_revision=np.asarray(["example/model@frozen"]),
        pooling=np.asarray(["mean"]),
        source_text_count=np.asarray([838]),
        embedding_layer=np.asarray([12]),
        matrix_size=np.asarray([24]),
    )

    calibration = load_embedding_calibration(
        path,
        expected_model_id="example/model",
    )

    assert calibration.mean == (1.0, 2.0, 3.0)
    assert calibration.model_revision == "example/model@frozen"


def test_generic_calibration_loader_rejects_wrong_model(tmp_path):
    path = tmp_path / "calibration.npz"
    np.savez_compressed(
        path,
        mean=np.asarray([1.0, 2.0, 3.0]),
        principal_components=np.asarray(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        ),
        score_method=np.asarray(["ordered-transition-resized-correlation-v1"]),
        model_id=np.asarray(["wrong/model"]),
        model_revision=np.asarray(["wrong/model@frozen"]),
        pooling=np.asarray(["mean"]),
        source_text_count=np.asarray([838]),
        embedding_layer=np.asarray([12]),
        matrix_size=np.asarray([24]),
    )

    with pytest.raises(StructuralScoringError, match="model id"):
        load_embedding_calibration(path, expected_model_id="expected/model")


def test_fit_embedding_calibration_freezes_mean_and_two_orthogonal_components():
    calibration = fit_embedding_calibration(
        (
            (0.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (0.0, 4.0, 0.0),
            (2.0, 4.0, 1.0),
        ),
        model_id="example/model",
        model_revision="example/model@revision",
    )

    np.testing.assert_allclose(calibration.mean, (1.0, 2.0, 0.25))
    components = np.asarray(calibration.principal_components)
    np.testing.assert_allclose(components @ components.T, np.eye(2), atol=1e-12)
    assert calibration.model_id == "example/model"


def test_calibration_inputs_require_one_exact_resolved_revision():
    valid = Embedding(
        "embedding-a", (1.0, 0.0), model_revision="model@abc", layer=12
    )
    assert validate_calibration_embeddings((valid,))[1] == "model@abc"

    with pytest.raises(StructuralScoringError, match="resolved immutable revision"):
        validate_calibration_embeddings(
            (
                Embedding(
                    "embedding-a",
                    (1.0, 0.0),
                    model_revision="unresolved:model",
                    layer=12,
                ),
            )
        )
    with pytest.raises(StructuralScoringError, match="same model revision"):
        validate_calibration_embeddings(
            (
                valid,
                Embedding(
                    "embedding-b",
                    (0.0, 1.0),
                    model_revision="model@different",
                    layer=12,
                ),
            )
        )


def test_runtime_score_rejects_embeddings_from_a_different_model():
    calibration = EmbeddingCalibration(
        mean=(0.0,) * 6,
        principal_components=(
            (0.0, 0.0, 0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
        ),
        model_id="example/other-model",
        model_revision="example/other-model@expected-revision",
    )

    with pytest.raises(StructuralScoringError, match="calibration model"):
        score_mental_model_revision(
            _model(), _belief(), _MeanSemanticEmbedder(), calibration=calibration
        )
