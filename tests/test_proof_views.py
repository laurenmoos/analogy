import math

import pytest

from analogy.proof_views import (
    ProofStructure,
    ProofViewError,
    compare_transitions,
    render_language_view,
    render_structure_view,
    render_transition,
)
from analogy.records import Embedding, SymbolicState
from analogy.rescue import ProofRescueContext


class MappingEncoder:
    def __init__(self, values):
        self.values = values

    def embed_text(self, text, *, layer):
        del layer
        return Embedding("fake", self.values[text])


def test_transition_comparison_measures_moves_not_raw_successors():
    predicted = render_transition("before", "predicted")
    actual = render_transition("before", "actual")
    encoder = MappingEncoder({predicted: (1.0, 0.0), actual: (0.8, 0.6)})

    result = compare_transitions("before", "predicted", "actual", encoder, -1)

    assert result.surprise == pytest.approx(0.2)
    assert result.predicted.rendering == predicted


@pytest.mark.parametrize(
    ("predicted", "actual"),
    [((1.0, math.nan), (1.0, 0.0)), ((1.0, 0.0), (1.0,))],
)
def test_transition_comparison_rejects_nonfinite_or_dimension_mismatch(
    predicted, actual
):
    encoder = MappingEncoder(
        {
            render_transition("before", "predicted"): predicted,
            render_transition("before", "actual"): actual,
        }
    )

    with pytest.raises(ProofViewError):
        compare_transitions("before", "predicted", "actual", encoder, -1)


def test_language_and_structure_views_are_separate_and_readable():
    context = ProofRescueContext(
        SymbolicState("s", "h : P\n⊢ P", "lean4", ("intro h",)),
        SymbolicState("g", "P", "lean4"),
        ("intro h",),
        ("exact missing",),
    )
    structure = ProofStructure(1, '{"root":"atom"}', ('{"root":"target"}',))

    language = render_language_view(context)
    structural = render_structure_view(structure)

    assert "exact missing" in language
    assert "target_shape" in structural
    assert language != structural
