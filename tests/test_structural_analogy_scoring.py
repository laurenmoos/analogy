import math
from types import SimpleNamespace

import pytest

from analogy.solver.analogy.state import MentalModel, MentalModelCorrespondence
from analogy.solver.analogy.structural_scoring import (
    ProofBelief,
    StructuralElement,
    StructuralGraph,
    StructuralScoringError,
    build_proof_belief,
    score_mental_model_revision,
)
from analogy.utils.records import Embedding, SearchNode, SymbolicState


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


class _SemanticEmbedder:
    def __init__(self):
        self.inputs = []
        self.values = {
            "shore": (1.0, 0.0, 0.0),
            "sail": (0.8, 0.2, 0.0),
            "island": (0.4, 0.6, 0.0),
            "read map": (0.0, 1.0, 0.0),
            "cave": (0.0, 0.6, 0.4),
            "open chest": (0.0, 0.2, 0.8),
            "treasure": (0.0, 0.0, 1.0),
        }

    def embed_text(self, text, *, layer):
        self.inputs.append((text, layer))
        value = next(vector for label, vector in self.values.items() if text.endswith(label))
        return Embedding("semantic", value, layer=layer)


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
    assert belief.graph.edges == (
        ("proof-state:root", "proof-tactic:child"),
        ("proof-tactic:child", "proof-state:child"),
    )


def test_structural_score_embeds_roles_and_prefers_matching_narrative_order():
    embedder = _SemanticEmbedder()

    matching = score_mental_model_revision(_model(), _belief(), embedder, layer=4)
    shuffled = score_mental_model_revision(
        _model((2, 0, 1)), _belief(), embedder, layer=4
    )
    repeated = score_mental_model_revision(_model(), _belief(), embedder, layer=4)

    assert matching.distance < shuffled.distance
    assert matching.distance == pytest.approx(repeated.distance)
    assert math.isfinite(matching.distance)
    assert all(layer == 4 for _, layer in embedder.inputs)
    assert any("role=domain-state" in text for text, _ in embedder.inputs)
    assert any("role=proof-tactic" in text for text, _ in embedder.inputs)
    assert sum(sum(row) for row in matching.coupling) == pytest.approx(1.0)


def test_structural_score_is_finite_for_singleton_disconnected_and_unequal_graphs():
    belief = ProofBelief(
        StructuralGraph(
            (StructuralElement("only", "proof-state", "shore", 0.0),), ()
        )
    )
    model = MentalModel(
        "treasure hunt",
        (
            MentalModelCorrespondence("shore", "sail", "island"),
            MentalModelCorrespondence("cave", "open chest", "treasure"),
        ),
    )

    fit = score_mental_model_revision(model, belief, _SemanticEmbedder(), layer=0)

    assert 0.0 <= fit.distance < float("inf")
    assert len(fit.coupling) == len(fit.mental_model_graph.elements)
    assert len(fit.coupling[0]) == 1


@pytest.mark.parametrize("vectors", [((0.0, 0.0),), ((1.0, 0.0), (1.0, 0.0, 0.0))])
def test_structural_score_rejects_zero_norm_or_dimension_mismatched_embeddings(vectors):
    class BadEmbedder:
        calls = 0

        def embed_text(self, text, *, layer):
            value = vectors[min(self.calls, len(vectors) - 1)]
            self.calls += 1
            return Embedding("bad", value)

    with pytest.raises(StructuralScoringError, match="embedding"):
        score_mental_model_revision(_model(), _belief(), BadEmbedder(), layer=0)
