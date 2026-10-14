from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "standalone_analogy_recovery.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("standalone_analogy_recovery", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_masked_problem_payload_has_no_reference_channel() -> None:
    module = load_script()
    problem = module.Problem("secret", "(P : Prop)", "P → P")

    payload = module.masked_problem_payload(problem)

    rendered = json.dumps(payload, sort_keys=True)
    assert rendered == '{"binders": "(P : Prop)", "problem_id": "secret", "target": "P \\u2192 P"}'
    assert "intro hidden_name" not in rendered


def test_poetry_pairs_only_adjacent_lines_from_same_work(tmp_path: Path) -> None:
    module = load_script()
    path = tmp_path / "poetry.ndjson.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in (
            {"gid": "a", "s": "first"},
            {"gid": "a", "s": "second"},
            {"gid": "b", "s": "third"},
        ):
            handle.write(json.dumps(row) + "\n")

    transitions = tuple(module.iter_poetry_transitions(path))

    assert [(item.before, item.after) for item in transitions] == [("first", "second")]


def test_balanced_sampling_is_deterministic_and_deduplicated() -> None:
    module = load_script()
    poetry = tuple(
        module.Transition(f"p{i}", "poetry", "a", f"b{i}", {})
        for i in range(4)
    )
    physics = tuple(
        module.Transition(f"f{i}", "physics", "a", f"b{i}", {})
        for i in range(4)
    )

    first = module.sample_transitions(poetry, physics, per_domain=2, seed=7)
    second = module.sample_transitions(poetry, physics, per_domain=2, seed=7)

    assert first == second
    assert [item.domain for item in first].count("poetry") == 2
    assert [item.domain for item in first].count("physics") == 2
    assert len({item.id for item in first}) == 4


def test_prompts_cannot_receive_reference_proof() -> None:
    module = load_script()
    problem = module.Problem("identity", "(P : Prop)", "P → P")

    prompt = module.candidate_prompt(problem, "⊢ P → P", (), (), 4)

    rendered = "\n".join(message.content for message in prompt)
    assert "P → P" in rendered
    assert "reference" not in rendered.casefold()
    assert "exact hidden" not in rendered


@pytest.mark.parametrize(
    "text",
    ["{}", "[]", '["intro h", "intro h"]', '["bad\\nline"]'],
)
def test_candidate_parser_rejects_malformed_or_duplicate_tactics(text: str) -> None:
    module = load_script()
    with pytest.raises(module.ModelOutputError):
        module.parse_candidates(text, top_k=4)


def test_ranking_parser_appends_omitted_indices_in_original_order() -> None:
    module = load_script()
    assert module.parse_ranking("[2, 0]", candidate_count=4) == (2, 0, 1, 3)


class FakeBatch(dict):
    def __init__(self, torch_module) -> None:
        super().__init__(
            input_ids=torch_module.tensor([[10, 11, 0]]),
            attention_mask=torch_module.tensor([[1, 1, 0]]),
        )

    def to(self, device: str):
        return self


class FakeTokenizer:
    def __init__(self, torch_module) -> None:
        self.torch = torch_module

    def apply_chat_template(self, messages, **kwargs):
        return "rendered chat"

    def __call__(self, texts, **kwargs):
        return FakeBatch(self.torch)

    def decode(self, tokens, **kwargs):
        return '["intro h"]'


class FakeModel:
    def __init__(self, torch_module, final_vector=(3.0, 4.0)) -> None:
        self.torch = torch_module
        self.device = "cpu"
        self.config = SimpleNamespace(_commit_hash="revision-1", num_hidden_layers=2)
        self.parameter = torch_module.nn.Parameter(torch_module.tensor([1.0]))
        self.final_vector = final_vector
        self.eval_called = False

    def parameters(self):
        return (self.parameter,)

    def eval(self):
        self.eval_called = True
        return self

    def __call__(self, **kwargs):
        torch = self.torch
        final = torch.tensor(
            [[[9.0, 9.0], list(self.final_vector), [8.0, 8.0]]],
            dtype=torch.float16,
        )
        return SimpleNamespace(
            hidden_states=(
                torch.zeros((1, 3, 2)),
                torch.ones((1, 3, 2)),
                final,
            )
        )

    def generate(self, **kwargs):
        return self.torch.tensor([[10, 11, 0, 99]])


def make_qwen(module, final_vector=(3.0, 4.0)):
    import torch

    return module.QwenModel(
        tokenizer=FakeTokenizer(torch),
        model=FakeModel(torch, final_vector),
        torch_module=torch,
        device="cpu",
        max_new_tokens=16,
        timeout_seconds=5.0,
    )


def test_embedding_uses_final_nonpadding_token_and_normalizes_float32() -> None:
    module = load_script()
    model = make_qwen(module)

    embedding = model.embed("structural need", layer=-1)

    assert embedding.values == pytest.approx((0.6, 0.8))
    assert embedding.layer == 2
    assert embedding.token_index == 1
    assert embedding.dimension == 2
    assert embedding.model_revision == "revision-1"
    assert embedding.text_hash == module.sha256_text("structural need")
    assert model._model.parameter.requires_grad is False
    assert model._model.eval_called is True


@pytest.mark.parametrize("layer", [-4, 3])
def test_embedding_rejects_out_of_range_layers(layer: int) -> None:
    module = load_script()
    with pytest.raises(module.ModelConfigurationError, match="layer"):
        make_qwen(module).embed("need", layer=layer)


def test_embedding_rejects_zero_vector() -> None:
    module = load_script()
    with pytest.raises(module.ModelOutputError, match="zero|finite"):
        make_qwen(module, final_vector=(0.0, 0.0)).embed("need", layer=-1)


def indexed_fixture(module):
    items = []
    for identifier, vector in (("x", (1.0, 0.0)), ("y", (0.8, 0.6)), ("z", (0.0, 1.0))):
        transition = module.Transition(identifier, "poetry", "before", f"after {identifier}", {})
        embedding = module.Embedding(vector, 0, 0, "rev", module.sha256_text(identifier))
        items.append(module.IndexedTransition(transition, f"change {identifier}", embedding, f"key-{identifier}"))
    return tuple(items)


def test_cosine_and_random_retrieval_use_same_eligible_index() -> None:
    module = load_script()
    index = indexed_fixture(module)

    nearest = module.cosine_retrieve((1.0, 0.0), index, count=2)
    random_hits = module.random_retrieve(index, count=2, seed=11)

    assert [hit.item.transition.id for hit in nearest] == ["x", "y"]
    assert len(random_hits) == len(nearest) == 2
    assert {hit.item.transition.id for hit in random_hits} <= {
        item.transition.id for item in index
    }
    assert all(hit.similarity is None for hit in random_hits)


def test_cache_key_changes_with_prompt_model_layer_or_source() -> None:
    module = load_script()
    transition = module.Transition("x", "poetry", "before", "after", {})
    changed = module.Transition("y", "poetry", "before", "different", {})
    base = module.transformation_cache_key(transition, "rev-a", -1, "transformation-v1")

    assert base != module.transformation_cache_key(transition, "rev-b", -1, "transformation-v1")
    assert base != module.transformation_cache_key(transition, "rev-a", -2, "transformation-v1")
    assert base != module.transformation_cache_key(transition, "rev-a", -1, "transformation-v2")
    assert base != module.transformation_cache_key(changed, "rev-a", -1, "transformation-v1")


def test_build_index_rejects_non_normalized_cached_vector(tmp_path: Path) -> None:
    module = load_script()
    model = SpyModel()
    transition = module.Transition("x", "poetry", "before", "after", {})
    key = module.transformation_cache_key(
        transition,
        model.model_revision,
        -1,
        module.TRANSFORMATION_PROMPT_VERSION,
    )
    cache = tmp_path / f"{key}.json"
    cache.write_text(
        json.dumps(
            {
                "cache_key": key,
                "transition_id": "x",
                "transformation": "one element becomes available",
                "embedding": {
                    "values": [3.0, 4.0],
                    "layer": -1,
                    "token_index": 0,
                    "model_revision": model.model_revision,
                    "text_hash": module.sha256_text("one element becomes available"),
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.ExperimentError, match="normalized"):
        module.build_index((transition,), model, tmp_path, -1)


class SpyModel:
    model_revision = "spy-revision"

    def __init__(self) -> None:
        self.generation_calls = []
        self.embedding_calls = []

    def generate(self, messages, *, seed: int):
        self.generation_calls.append((messages, seed))
        system = messages[0].content
        if "candidate-v1" in system:
            return '["intro h", "exact h"]'
        if "proof-need-v1" in system:
            return "make a hidden element available"
        if "transformation-v1" in system:
            return '{"transformation":"one element becomes available"}'
        return "[0]"

    def embed(self, text: str, layer: int):
        self.embedding_calls.append((text, layer))
        module = load_script()
        return module.Embedding((1.0, 0.0), layer, 0, self.model_revision, module.sha256_text(text))


def test_no_retrieval_never_calls_embedding_or_retriever() -> None:
    module = load_script()
    model = SpyModel()
    problem = module.Problem("identity", "(P : Prop)", "P → P")

    decision = module.rank_for_condition(
        module.Condition.NONE,
        problem,
        "goal",
        ("exact h",),
        None,
        (),
        model,
        2,
        0,
    )

    assert decision.indices == (0,)
    assert decision.hits == ()
    assert model.embedding_calls == []


def test_candidate_pool_store_shares_pool_by_state_fingerprint() -> None:
    module = load_script()
    model = SpyModel()
    store = module.CandidatePoolStore(model)
    problem = module.Problem("identity", "(P : Prop)", "P → P")

    first = store.get(problem, "⊢ P → P", (), (), seed=3, top_k=4)
    second = store.get(problem, "⊢ P → P", (), (), seed=3, top_k=4)
    other = store.get(problem, "h : P\n⊢ P", ("intro h",), (), seed=3, top_k=4)

    assert first == second == ("intro h", "exact h")
    assert other == ("intro h", "exact h")
    candidate_calls = [call for call in model.generation_calls if "candidate-v1" in call[0][0].content]
    assert len(candidate_calls) == 2
