from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from analogy.config import load_config
from analogy.model import model_provider
from analogy.solver.model_policy import TacticRanker, TacticRankingError
from analogy.solver.analogy.state import AnalogyScratchpad, AnalogyState
from analogy.model.prompts import configure_prompt_catalog


ROOT = Path(__file__).parents[1]


def _ranker(generator, *, top_k, seed, trace_sink=None, analogy_scratchpad=None):
    return TacticRanker(
        generator,
        top_k=top_k,
        seed=seed,
        failed_tactics_for=lambda _state_id: (),
        trace_sink=trace_sink or (lambda _event: None),
        analogy_scratchpad=analogy_scratchpad,
    )


@pytest.fixture(autouse=True)
def clear_model_provider_cache():
    model_provider.get_model_provider.cache_clear()
    yield
    model_provider.get_model_provider.cache_clear()


def test_tactic_ranker_wraps_provider_failures_in_its_loop_exception():
    class BrokenProvider:
        def generate(self, _messages, *, seed):
            raise RuntimeError(f"generation failed at seed {seed}")

    configure_prompt_catalog(ROOT / "prompts.yaml")
    ranker = _ranker(BrokenProvider(), top_k=3, seed=7)

    with pytest.raises(TacticRankingError, match="generation failed at seed 7"):
        ranker.rank(
            SimpleNamespace(id="state", context=(), expression="P"),
            SimpleNamespace(expression="P"),
        )


def test_tactic_ranker_records_the_prompt_and_raw_model_response():
    events = []

    class Provider:
        def generate(self, _messages, *, seed):
            assert seed == 7
            return '[{"tactic":"exact h","score":1}]'

    configure_prompt_catalog(ROOT / "prompts.yaml")
    ranker = _ranker(
        Provider(), top_k=1, seed=7, trace_sink=events.append
    )
    ranker.rank(
        SimpleNamespace(id="state", context=("intro h",), expression="P"),
        SimpleNamespace(expression="P"),
    )

    assert events[0]["kind"] == "ordinary_model_call"
    assert events[0]["state_id"] == "state"
    assert events[0]["messages"][1]["content"].startswith("Overall target:")
    assert "Lean card:" in events[0]["messages"][1]["content"]
    response = events[0]["responses"][0]
    assert response["seed"] == 7
    assert response["raw_text"] == '[{"tactic":"exact h","score":1}]'
    assert response["status"] == "valid"
    assert response["repair"] is False
    assert response["error_kind"] is None
    assert response["diagnostic"] is None
    assert response["messages"] == events[0]["messages"]
    assert events[0]["normalized_candidates"] == ["exact h"]


def test_tactic_ranker_repairs_an_all_incomplete_pool_once_and_traces_both_generations():
    calls = []
    events = []

    class Provider:
        def generate(self, messages, *, seed):
            calls.append((messages, seed))
            if len(calls) == 1:
                return '[{"tactic":"exact","score":2},{"tactic":"rw","score":1}]'
            assert seed == 8
            assert "exact h2" in messages[-1].content
            assert "rw [h1]" in messages[-1].content
            return '[{"tactic":"exact h2","score":1}]'

    configure_prompt_catalog(ROOT / "prompts.yaml")
    ranker = _ranker(
        Provider(), top_k=2, seed=7, trace_sink=events.append
    )

    candidates = ranker.rank(
        SimpleNamespace(id="state", context=(), expression="h2 : P\n⊢ P"),
        SimpleNamespace(expression="P"),
    )

    assert [item.source for item in candidates] == ["exact h2"]
    assert [item[1] for item in calls] == [7, 8]
    assert [item["status"] for item in events[0]["responses"]] == [
        "invalid", "valid",
    ]
    assert events[0]["responses"][0]["diagnostic"].startswith(
        "all generated tactics were incomplete"
    )
    assert events[0]["discarded_candidates"] == [
        "exact: incomplete tactic", "rw: incomplete tactic",
    ]


def test_tactic_ranker_requests_replacements_for_filtered_and_state_tried_candidates():
    calls = []

    class Provider:
        def generate(self, messages, *, seed):
            calls.append((messages, seed))
            if len(calls) == 1:
                return '[{"tactic":"left","score":2},{"tactic":"exact h","score":1}]'
            assert "already tried" in messages[-1].content
            assert "requires a disjunction goal" in messages[-1].content
            return '[{"tactic":"assumption","score":1}]'

    configure_prompt_catalog(ROOT / "prompts.yaml")
    ranker = _ranker(Provider(), top_k=2, seed=7)

    candidates = ranker.rank(
        SimpleNamespace(id="state", context=(), expression="h : P\n⊢ P"),
        SimpleNamespace(expression="P"),
        excluded_tactics={"exact h"},
    )

    assert [item.source for item in candidates] == ["assumption"]
    assert [seed for _, seed in calls] == [7, 8]


def test_tactic_ranker_prompt_contains_only_the_focused_goal():
    seen = []

    class Provider:
        def generate(self, messages, *, seed):
            seen.append(messages[-1].content)
            return '[{"tactic":"exact h","score":1}]'

    configure_prompt_catalog(ROOT / "prompts.yaml")
    _ranker(Provider(), top_k=1, seed=1).rank(
        SimpleNamespace(
            id="state",
            context=(),
            expression="case one\nP Q : Prop\nh : P\n⊢ P\n\ncase two\nbad : Q\n⊢ Q",
        ),
        SimpleNamespace(expression="P"),
    )

    assert "h : P" in seen[0]
    assert "bad : Q" not in seen[0]


def test_enabled_ranker_initializes_scratchpad_and_strips_block_before_candidate_parser():
    scratchpad = AnalogyScratchpad()

    class Provider:
        def generate(self, messages, *, seed):
            assert seed == 7
            assert len(messages) == 2
            assert "create one" in messages[-1].content
            return """<analogy>
domain: locksmithing
mapping:
- P -> locked door
steps:
1. Open the matching lock.
abandoned domains:
- (none)
</analogy>
[{"tactic":"exact h","score":1}]"""

    configure_prompt_catalog(ROOT / "prompts.yaml")
    candidates = _ranker(
        Provider(), top_k=1, seed=7, analogy_scratchpad=scratchpad
    ).rank(
        SimpleNamespace(id="state", context=(), expression="h : P\n⊢ P"),
        SimpleNamespace(expression="P"),
    )

    assert [item.source for item in candidates] == ["exact h"]
    assert scratchpad.current is not None
    assert scratchpad.current.domain == "locksmithing"
    assert scratchpad.history == ('[{"tactic":"exact h","score":1}]',)


def test_enabled_ranker_injects_current_block_and_rejects_ordinary_mutation():
    current = AnalogyState("locksmithing", ("P -> lock",), ("Open it.",))
    scratchpad = AnalogyScratchpad(current)
    calls = []

    class Provider:
        def generate(self, messages, *, seed):
            calls.append(messages)
            assert messages[0].role == "system"
            assert messages[1].role == "system"
            assert messages[1].content == current.render()
            assert messages[2].role == "user"
            if len(calls) == 1:
                return """<analogy>
domain: shipping
mapping:
- P -> parcel
steps:
1. Deliver it.
abandoned domains:
- (none)
</analogy>
[{"tactic":"exact h","score":1}]"""
            return current.render() + '\n[{"tactic":"exact h","score":1}]'

    configure_prompt_catalog(ROOT / "prompts.yaml")
    ranker = _ranker(
        Provider(), top_k=1, seed=7, analogy_scratchpad=scratchpad
    )

    with pytest.raises(TacticRankingError, match="must remain unchanged"):
        ranker.rank(
            SimpleNamespace(id="state", context=(), expression="h : P\n⊢ P"),
            SimpleNamespace(expression="P"),
        )

    assert scratchpad.current == current


def test_disabled_ranker_keeps_existing_two_message_contract():
    seen = []

    class Provider:
        def generate(self, messages, *, seed):
            seen.append(messages)
            return '[{"tactic":"exact h","score":1}]'

    configure_prompt_catalog(ROOT / "prompts.yaml")
    _ranker(Provider(), top_k=1, seed=7).rank(
        SimpleNamespace(id="state", context=(), expression="h : P\n⊢ P"),
        SimpleNamespace(expression="P"),
    )

    assert len(seen[0]) == 2


def test_pending_rebuild_uses_plain_ordinary_tactics_without_reinitializing():
    scratchpad = AnalogyScratchpad(
        AnalogyState("locksmithing", ("P -> lock",), ("Open it.",))
    )
    scratchpad.abandon_current()

    class Provider:
        def generate(self, messages, *, seed):
            assert len(messages) == 2
            assert "<analogy>" not in "\n".join(item.content for item in messages)
            return '[{"tactic":"exact h","score":1}]'

    configure_prompt_catalog(ROOT / "prompts.yaml")
    candidates = _ranker(
        Provider(), top_k=1, seed=7, analogy_scratchpad=scratchpad
    ).rank(
        SimpleNamespace(id="state", context=(), expression="h : P\n⊢ P"),
        SimpleNamespace(expression="P"),
    )

    assert [item.source for item in candidates] == ["exact h"]
    assert scratchpad.current is None
    assert scratchpad.pending_rebuild is True


def test_enabled_partial_candidate_repair_refreshes_current_block_and_contract():
    scratchpad = AnalogyScratchpad()
    state = AnalogyState("locksmithing", ("P -> lock",), ("Open it.",))
    calls = []

    class Provider:
        def generate(self, messages, *, seed):
            calls.append(messages)
            if len(calls) == 1:
                return state.render() + (
                    '\n[{"tactic":"exact h","score":2},'
                    '{"tactic":"left","score":1}]'
                )
            assert messages[1].content == state.render()
            assert "<analogy> block" in messages[-1].content
            return state.render() + '\n[{"tactic":"assumption","score":1}]'

    configure_prompt_catalog(ROOT / "prompts.yaml")
    candidates = _ranker(
        Provider(), top_k=2, seed=7, analogy_scratchpad=scratchpad
    ).rank(
        SimpleNamespace(id="state", context=(), expression="h : P\n⊢ P"),
        SimpleNamespace(expression="P"),
    )

    assert [item.source for item in candidates] == ["exact h", "assumption"]


def test_tactic_ranker_does_not_retry_unrelated_malformed_json():
    calls = []

    class Provider:
        def generate(self, _messages, *, seed):
            calls.append(seed)
            return "not json"

    configure_prompt_catalog(ROOT / "prompts.yaml")
    ranker = _ranker(Provider(), top_k=2, seed=7)

    with pytest.raises(TacticRankingError, match="valid JSON"):
        ranker.rank(
            SimpleNamespace(id="state", context=(), expression="P"),
            SimpleNamespace(expression="P"),
        )

    assert calls == [7]


def test_equal_model_configs_reuse_one_loaded_provider(monkeypatch):
    config = load_config(ROOT / "config.yaml").model
    loaded = []

    def create(candidate):
        provider = object()
        loaded.append((candidate, provider))
        return provider

    monkeypatch.setattr(model_provider, "_create_model_provider", create)
    first = model_provider.get_model_provider(config)
    second = model_provider.get_model_provider(replace(config))

    assert first is second
    assert loaded == [(config, first)]


def test_changed_model_config_does_not_reuse_previous_provider(monkeypatch):
    config = load_config(ROOT / "config.yaml").model
    loaded = []

    def create(candidate):
        provider = object()
        loaded.append((candidate, provider))
        return provider

    monkeypatch.setattr(model_provider, "_create_model_provider", create)
    first = model_provider.get_model_provider(config)
    changed = replace(config, max_new_tokens=config.max_new_tokens + 1)
    second = model_provider.get_model_provider(changed)

    assert first is not second
    assert loaded == [(config, first), (changed, second)]
