from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from analogy.config import load_config
from analogy.inference import model_provider
from analogy.solver.model_policy import TacticRanker, TacticRankingError
from analogy.inference.prompts import configure_prompt_catalog


ROOT = Path(__file__).parents[1]


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
    ranker = TacticRanker(BrokenProvider(), top_k=3, seed=7)

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
    ranker = TacticRanker(
        Provider(), top_k=1, seed=7, trace_sink=events.append
    )
    ranker.rank(
        SimpleNamespace(id="state", context=("intro h",), expression="P"),
        SimpleNamespace(expression="P"),
    )

    assert events[0]["kind"] == "ordinary_model_call"
    assert events[0]["state_id"] == "state"
    assert events[0]["messages"][1]["content"].startswith("Overall target:")
    assert events[0]["responses"] == [
        {
            "seed": 7,
            "raw_text": '[{"tactic":"exact h","score":1}]',
            "status": "valid",
        }
    ]
    assert events[0]["normalized_candidates"] == ["exact h"]


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
