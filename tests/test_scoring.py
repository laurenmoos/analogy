import json

import pytest

from analogy.solver.scoring import (
    FrozenEmbeddingDiversity,
    normalize_tactic_scores,
    parse_scored_tactic_candidates,
    search_priority,
)
from analogy.utils.records import (
    CandidateOrigin,
    Embedding,
    SearchNode,
    SymbolicState,
    TacticCandidate,
)


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
        return Embedding(
            "test", values, model_revision=self.model_revision, layer=layer
        )


def _state(identifier, expression, context=()):
    return SymbolicState(identifier, expression, "lean4", context)


def test_tactic_score_normalization_preserves_candidate_order() -> None:
    candidates = (
        TacticCandidate("assumption", 0.2, CandidateOrigin.ORDINARY),
        TacticCandidate("rfl", 0.6, CandidateOrigin.ORDINARY),
    )

    scored = normalize_tactic_scores(candidates)

    assert [item.source for item in scored] == ["assumption", "rfl"]
    assert [item.score for item in scored] == pytest.approx([0.25, 0.75])


def test_scored_tactic_parser_filters_before_normalizing_selected_weights() -> None:
    scored = parse_scored_tactic_candidates(
        json.dumps(
            [
                {"tactic": "exact", "score": 100.0},
                {"tactic": "exact h", "score": 3.0},
                {"tactic": "assumption", "score": 1.0},
            ]
        ),
        2,
    )

    assert [(item.source, item.score) for item in scored] == [
        ("exact h", 0.75),
        ("assumption", 0.25),
    ]


def test_diversity_is_mean_cosine_distance_to_nearest_seen_states() -> None:
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


def test_diversity_observations_are_cached_by_exact_state_id() -> None:
    encoder = _Encoder()
    diversity = FrozenEmbeddingDiversity(encoder, layer=-1, neighbors=3)
    state = _state("a", "A")

    assert diversity.observe(state) == diversity.observe(state)
    assert len(encoder.texts) == 1


def test_search_priority_keeps_step_score_before_novelty() -> None:
    node = SearchNode(_state("a", "A"), 0.8, depth=2)

    assert search_priority(node, novelty=0.3) == pytest.approx((-0.76, -0.3))
