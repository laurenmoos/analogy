import pytest

from analogy.records import Embedding, SymbolicState
from analogy.solver.diversity import FrozenEmbeddingDiversity


class _Encoder:
    model_revision = "frozen-test-model"

    def __init__(self):
        self.texts = []

    def embed_text(self, text, *, layer):
        self.texts.append((text, layer))
        expression = text.split("Current goals:\n", 1)[1].split("\n", 1)[0]
        values = {
            "A": (1.0, 0.0),
            "B": (0.0, 1.0),
            "A again": (1.0, 0.0),
        }[expression]
        return Embedding("test", values, model_revision=self.model_revision, layer=layer)


def _state(identifier, expression, context=()):
    return SymbolicState(identifier, expression, "lean4", context)


def test_diversity_is_mean_cosine_distance_to_nearest_seen_states():
    encoder = _Encoder()
    diversity = FrozenEmbeddingDiversity(encoder, layer=-1, neighbors=2)

    first = diversity.observe(_state("a", "A", ("intro h",)))
    second = diversity.observe(_state("b", "B"))
    third = diversity.observe(_state("a2", "A again"))

    assert first.novelty == 1.0
    assert first.neighbor_state_ids == ()
    assert second.novelty == pytest.approx(1.0)
    assert second.neighbor_state_ids == ("a",)
    assert third.novelty == pytest.approx(0.5)
    assert third.neighbor_state_ids == ("a", "b")
    assert "Accepted prefix:\nintro h" in encoder.texts[0][0]
    assert all(layer == -1 for _, layer in encoder.texts)


def test_diversity_observations_are_cached_by_exact_state_id():
    encoder = _Encoder()
    diversity = FrozenEmbeddingDiversity(encoder, layer=-1, neighbors=3)
    state = _state("a", "A")

    assert diversity.observe(state) == diversity.observe(state)
    assert len(encoder.texts) == 1

