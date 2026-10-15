import math
from dataclasses import replace

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


def observation_context():
    return ProofRescueContext(
        SymbolicState("s", "h : P\n⊢ P", "lean4", ("intro h",)),
        SymbolicState("g", "P → P", "lean4"), ("intro h",), ("exact missing",),
    )


class RecordingEncoder:
    def __init__(self):
        self.texts = []

    def embed_text(self, text, *, layer):
        self.texts.append(text)
        return Embedding("fake", (1.0, 2.0, 3.0, 4.0), "revision", layer, 7)


def test_first_observation_uses_zero_optional_channels():
    from analogy.proof_views import build_policy_representation

    encoder = RecordingEncoder()
    representation = build_policy_representation(
        observation_context(), ProofStructure(1, '"atom"', ()), encoder, layer=-1,
        previous_state=None, latest_transfer=None,
        scalar_features=(0.0, 1.0, 1.0, 0.0),
    )
    assert representation.transition_embedding.values == (0.0,) * 4
    assert representation.analogy_embedding.values == (0.0,) * 4
    assert representation.scalar_features == (0.0, 1.0, 1.0, 0.0)
    assert encoder.texts[0] == "Fixed theorem target:\nP → P"
    assert "h : P\n⊢ P" in encoder.texts[1]
    assert "target_shape" in encoder.texts[1]
    assert len(encoder.texts) == 2
    assert representation.transition_embedding.model_revision == "revision"
    assert representation.transition_embedding.layer == -1
    assert representation.transition_embedding.text_hash


def test_policy_representation_embeds_verified_transition_and_transfer():
    from analogy.analogy_transfer import ProofReasoningTransfer
    from analogy.proof_views import (
        build_policy_representation, render_observed_transition,
        render_transfer_observation,
    )

    transfer = ProofReasoningTransfer(
        "t1", "a1", "P", ("matching premise",),
        ("Use the available premise.",), "No remaining goals.", True,
    )
    encoder = RecordingEncoder()
    representation = build_policy_representation(
        observation_context(), ProofStructure(1, '"atom"', ()), encoder, -1,
        previous_state="⊢ P → P", last_tactic="intro h", latest_transfer=transfer,
        scalar_features=(0.5, 0.75, 0.25, 0.0),
    )
    assert encoder.texts[2] == render_observed_transition("⊢ P → P", "intro h", "h : P\n⊢ P")
    assert "intro h" in encoder.texts[2]
    assert encoder.texts[3] == render_transfer_observation(None, None, transfer)
    assert "Use the available premise." in encoder.texts[3]
    assert representation.transition_embedding.values == (1.0, 2.0, 3.0, 4.0)
    assert representation.analogy_embedding.values == (1.0, 2.0, 3.0, 4.0)


def test_representation_rejects_mismatched_optional_channels():
    from analogy.proof_views import build_policy_representation

    representation = build_policy_representation(
        observation_context(), ProofStructure(1, '"atom"', ()), RecordingEncoder(), -1,
    )
    for name in ("transition_embedding", "analogy_embedding"):
        with pytest.raises(ValueError, match="dimension"):
            replace(representation, **{name: Embedding("fake", (1.0,))})


def test_readable_observation_preserves_trajectory_and_diagnostics():
    from analogy.analogy_transfer import AnalogyObservation

    observation = AnalogyObservation(
        fixed_target="P → P", current_state="h : P\n⊢ P", previous_state="⊢ P → P",
        last_tactic="intro h", accepted_tactics=("intro h",),
        rejected_tactics=(("exact missing", ("unknown identifier",)),),
        latest_source=None, latest_alignment=None, latest_transfer=None,
        predicted_transition="⊢ P", actual_transition="h : P\n⊢ P",
        surprise=0.2, surprise_delta=-0.1, repeated_state=False, iteration_index=1,
        remaining_model_calls=6, remaining_lean_calls=3,
    )
    assert observation.fixed_target == "P → P"
    assert observation.rejected_tactics == (("exact missing", ("unknown identifier",)),)


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
