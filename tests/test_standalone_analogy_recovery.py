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
