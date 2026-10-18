from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from analogy.config import load_config
from analogy.model.prompts import configure_prompt_catalog
from analogy.solver.analogy.harness import (
    AnalogyHarnessInitializationError,
    AnalogyScoreError,
    EqualAnalogyScorer,
    activate_analogy,
    initialize_analogy_harness,
    score_analogies,
    select_analogy,
    update_analogies,
)
from analogy.solver.analogy.state import AnalogyState, AnalogyUpdateAction
from analogy.solver.proof.semantic import (
    ExhaustiveSemanticChecker,
    SemanticRecoveryEvent,
    SemanticResult,
)
from analogy.solver.search import (
    AcceptNovelState,
    CanonicalStateFingerprinter,
    EpisodeContext,
    NeverCancelled,
    ObservableStucknessPolicy,
    WhitespaceTacticCanonicalizer,
    start_search,
)
from analogy.utils.records import SymbolicState


ROOT = Path(__file__).parents[1]


def _analogy(domain: str) -> str:
    return f'''<mental_model>
{{
  "domain": "{domain}",
  "correspondences": [
    {{
      "domain_before": "resource is available",
      "domain_transition": "use the resource",
      "domain_after": "goal is reached",
      "proof_before": null,
      "proof_transition": null,
      "proof_after": null
    }}
  ],
  "abandoned_domains": []
}}
</mental_model>'''


class _Proof:
    def failed_tactics_for(self, _state_id):
        return ()


def _states():
    return (
        SymbolicState("initial", "h : P\n⊢ P", "lean4"),
        SymbolicState("goal", "⊢ P", "lean4"),
    )


def _harness(generator, *, count=2, scorer=None, events=None):
    configure_prompt_catalog(ROOT / "prompts.yaml")
    config = load_config(ROOT / "config.yaml")
    config = replace(config, analogy=replace(config.analogy, max_model_calls=count))
    initial, goal = _states()
    return initialize_analogy_harness(
        config,
        generator,
        _Proof(),
        [] if events is None else events,
        initial,
        goal,
        scorer=scorer,
    )


def test_initialization_builds_isolated_tracks_with_distinct_prompts_and_seeds():
    calls = []

    class Generator:
        def generate(self, messages, *, seed):
            calls.append((messages, seed))
            return _analogy(("lock", "journey", "recipe")[len(calls) - 1])

    events = []
    harness = _harness(Generator(), count=3, events=events)
    config = load_config(ROOT / "config.yaml")

    assert [track.id for track in harness.tracks] == ["analogy-1", "analogy-2", "analogy-3"]
    assert [track.scratchpad.current.domain for track in harness.tracks] == ["lock", "journey", "recipe"]
    assert [seed for _, seed in calls] == [config.run.seed, config.run.seed + 1, config.run.seed + 2]
    assert "- (none)" in calls[0][0][1].content
    assert "- lock" in calls[1][0][1].content
    assert "- lock\n- journey" in calls[2][0][1].content
    assert "Current focused Lean proof state:\nh : P\n⊢ P" in calls[0][0][1].content
    assert "Everyday-domain card:" in calls[0][0][1].content
    assert "Lean card:" in calls[0][0][1].content
    assert len({id(track.scratchpad) for track in harness.tracks}) == 3
    assert len({id(track.model_policy) for track in harness.tracks}) == 3
    assert len({id(track.fallback) for track in harness.tracks}) == 3
    assert all(track.model_policy.analogy_scratchpad is track.scratchpad for track in harness.tracks)
    assert all(track.fallback.scratchpad is track.scratchpad for track in harness.tracks)
    assert [event["status"] for event in events] == ["valid", "valid", "valid"]


def test_initialization_skips_invalid_candidates_but_requires_one_valid_track():
    class Generator:
        def __init__(self, outputs):
            self.outputs = iter(outputs)

        def generate(self, _messages, *, seed):
            return next(self.outputs)

    events = []
    harness = _harness(
        Generator(("not an analogy", _analogy("bridge"))), events=events
    )

    assert [track.id for track in harness.tracks] == ["analogy-2"]
    assert [event["status"] for event in events] == ["invalid", "valid"]

    with pytest.raises(AnalogyHarnessInitializationError, match="no valid analogies"):
        _harness(Generator(("bad", _analogy("bridge") + "\nextra")))


def test_equal_scores_preserve_cohort_order_and_select_first_track():
    class Generator:
        calls = 0

        def generate(self, _messages, *, seed):
            self.calls += 1
            return _analogy(("lock", "bridge")[self.calls - 1])

    harness = _harness(Generator(), scorer=EqualAnalogyScorer())
    scored = score_analogies(None, harness)

    assert [item.score for item in scored] == [0.0, 0.0]
    assert select_analogy(scored) is harness.tracks[0]


def test_custom_scorer_can_select_a_later_track():
    class Generator:
        calls = 0

        def generate(self, _messages, *, seed):
            self.calls += 1
            return _analogy(("lock", "bridge")[self.calls - 1])

    class Scorer:
        def score(self, track, search):
            assert search is None
            return {"analogy-1": 1.0, "analogy-2": 4.0}[track.id]

    harness = _harness(Generator(), scorer=Scorer())

    assert select_analogy(score_analogies(None, harness)) is harness.tracks[1]


@pytest.mark.parametrize("bad_score", [True, float("nan"), float("inf"), float("-inf"), "1"])
def test_scoring_rejects_nonfinite_or_nonreal_values(bad_score):
    class Generator:
        def generate(self, _messages, *, seed):
            return _analogy("lock")

    scorer = SimpleNamespace(score=lambda _track, _search: bad_score)
    harness = _harness(Generator(), count=1, scorer=scorer)

    with pytest.raises(AnalogyScoreError, match="finite real"):
        score_analogies(None, harness)


def _search_for(harness, result):
    selected = harness.tracks[0]
    context = EpisodeContext(
        tactic_ranker=selected.model_policy,
        stuckness_policy=ObservableStucknessPolicy(),
        state_fingerprinter=CanonicalStateFingerprinter(),
        tactic_canonicalizer=WhitespaceTacticCanonicalizer(),
        progress_policy=AcceptNovelState(),
        cancellation=NeverCancelled(),
        analogy_fallback=selected.fallback,
        ordinary_width=1,
        max_expansions=4,
        state_diversity=None,
        semantic_checker=ExhaustiveSemanticChecker(),
    )
    initial, goal = _states()
    search = start_search(context, initial, goal)
    search.recovery = SemanticRecoveryEvent("semantic:1", "initial", result)
    return search


def test_non_countermodel_update_preserves_every_track():
    class Generator:
        calls = 0

        def generate(self, _messages, *, seed):
            self.calls += 1
            return _analogy(("lock", "bridge")[self.calls - 1])

    harness = _harness(Generator())
    search = _search_for(harness, SemanticResult.VALID)

    updated = update_analogies(search, harness)

    assert updated is harness
    assert all(track.scratchpad.current is not None for track in harness.tracks)


def test_countermodel_update_marks_tracks_without_mutating_models():
    class Generator:
        calls = 0

        def generate(self, _messages, *, seed):
            self.calls += 1
            return _analogy(("lock", "bridge")[self.calls - 1])

    harness = _harness(Generator())
    search = _search_for(harness, SemanticResult.COUNTERMODEL)

    updated = update_analogies(search, harness)

    assert [track.scratchpad.abandoned_domains for track in updated.tracks] == [(), ()]
    assert [track.scratchpad.current.domain for track in updated.tracks] == [
        "lock",
        "bridge",
    ]
    assert all(not track.scratchpad.pending_rebuild for track in updated.tracks)

    updated.tracks[0].scratchpad.apply(
        AnalogyUpdateAction.CHANGE_DOMAIN,
        AnalogyState("recipe", ("goal is a dish",), ("finish it",)),
    )
    repeated = update_analogies(search, updated)
    assert repeated.tracks[0].scratchpad.current.domain == "recipe"


def test_activate_analogy_replaces_only_policy_and_fallback():
    class Generator:
        calls = 0

        def generate(self, _messages, *, seed):
            self.calls += 1
            return _analogy(("lock", "bridge")[self.calls - 1])

    harness = _harness(Generator())
    search = _search_for(harness, SemanticResult.VALID)
    before = search.context
    frontier = search.frontier
    visited = search.visited
    recovery = search.recovery

    activate_analogy(search, harness.tracks[1])

    assert search.context.tactic_ranker is harness.tracks[1].model_policy
    assert search.context.analogy_fallback is harness.tracks[1].fallback
    for field in before.__dataclass_fields__:
        if field not in {"tactic_ranker", "analogy_fallback"}:
            assert getattr(search.context, field) is getattr(before, field)
    assert search.frontier is frontier
    assert search.visited is visited
    assert search.recovery is recovery


def test_activate_analogy_rejects_mismatched_track_ownership():
    class Generator:
        calls = 0

        def generate(self, _messages, *, seed):
            self.calls += 1
            return _analogy(("lock", "bridge")[self.calls - 1])

    harness = _harness(Generator())
    search = _search_for(harness, SemanticResult.VALID)
    first, second = harness.tracks
    mismatched = SimpleNamespace(
        id="bad",
        scratchpad=first.scratchpad,
        model_policy=first.model_policy,
        fallback=second.fallback,
    )

    with pytest.raises(ValueError, match="same scratchpad"):
        activate_analogy(search, mismatched)
