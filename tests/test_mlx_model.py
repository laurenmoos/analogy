from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from analogy.config import load_config
from analogy.model import ModelConfigurationError
from analogy.records import Message


ROOT = Path(__file__).parents[1]


class FakeMX:
    def __init__(self) -> None:
        self.seeds = []
        self.random = SimpleNamespace(seed=self.seeds.append)

    @staticmethod
    def array(value):
        if isinstance(value, torch.Tensor):
            value = value.detach().cpu().tolist()
        return torch.tensor(value, dtype=torch.float32)

    float32 = torch.float32
    linalg = SimpleNamespace(norm=torch.linalg.vector_norm)

    @staticmethod
    def concatenate(values, axis):
        return torch.cat(values, dim=axis)

    @staticmethod
    def eval(*values):
        return values[-1]

    @staticmethod
    def synchronize():
        return None


class IdentityLayer:
    def __call__(self, hidden, mask=None, cache=None):
        return hidden


class RecordingLayer:
    def __init__(self) -> None:
        self.values = []

    def __call__(self, hidden, mask=None, cache=None):
        self.values.append(hidden.detach().clone())
        return hidden


class FakeModel:
    def __init__(self) -> None:
        self.recorder = RecordingLayer()
        self.model = SimpleNamespace(layers=[IdentityLayer(), self.recorder])
        self.args = SimpleNamespace(hidden_size=2)


class FixedFinalLayer:
    def __init__(self, values=(3.0, 4.0)) -> None:
        self.values = values

    def __call__(self, hidden, mask=None, cache=None):
        changed = hidden.clone()
        changed[:, -1, :] = torch.tensor(self.values)
        return changed


class EmbeddingCore:
    def __init__(self, values=(3.0, 4.0)) -> None:
        self.layers = [IdentityLayer(), FixedFinalLayer(values)]
        self.norm = IdentityLayer()

    @staticmethod
    def embed_tokens(inputs):
        return torch.ones((inputs.shape[0], inputs.shape[1], 2))


class EmbeddingModel:
    def __init__(self, values=(3.0, 4.0)) -> None:
        self.model = EmbeddingCore(values)
        self.args = SimpleNamespace(hidden_size=2)


class ScoringModel:
    def __init__(self) -> None:
        self.model = SimpleNamespace(layers=[IdentityLayer()])
        self.args = SimpleNamespace(hidden_size=2)
        self.calls = []

    def __call__(self, inputs):
        self.calls.append(inputs.detach().clone())
        batch, width = inputs.shape
        logits = torch.zeros((batch, width, 10))
        logits[0, 1, 3] = 5.0
        logits[1, 1, 4] = 1.0
        logits[1, 2, 5] = 1.0
        logits[1, 3, 6] = 1.0
        logits[0, -1, 0] = 20.0
        return logits


class FakeTokenizer:
    def __init__(self) -> None:
        self.calls = []

    def apply_chat_template(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return "rendered prompt"

    @staticmethod
    def encode(text, add_special_tokens=False):
        return {
            "proof view": [7, 8],
            "rendered prompt": [1, 2],
            "weak": [3],
            "strong": [4, 5, 6],
        }[text]


class Runtime:
    def __init__(self, model=None) -> None:
        self.mx = FakeMX()
        self.model = model or FakeModel()
        self.tokenizer = FakeTokenizer()
        self.generate_error = None
        self.generate_calls = []
        self.sampler_calls = []

    def generate(self, model, tokenizer, **kwargs):
        self.generate_calls.append(kwargs)
        if self.generate_error is not None:
            raise self.generate_error
        prefill = torch.zeros((1, 2, 2))
        decode = torch.zeros((1, 1, 2))
        for layer in model.model.layers:
            prefill = layer(prefill, None, None)
        for layer in model.model.layers:
            decode = layer(decode, None, object())
        return "decoded answer"

    def make_sampler(self, **kwargs):
        self.sampler_calls.append(kwargs)
        return ("sampler", kwargs)

    def generator(self, *, do_sample=False):
        from analogy.mlx_model import MLXQwenGenerator

        config = replace(
            load_config(ROOT / "config.yaml").model,
            backend="mlx",
            name="mlx-community/Qwen3-8B-4bit",
            max_new_tokens=96,
            timeout_seconds=15.0,
            do_sample=do_sample,
        )
        return MLXQwenGenerator(
            model=self.model,
            tokenizer=self.tokenizer,
            mx=self.mx,
            generate_fn=self.generate,
            make_sampler=self.make_sampler,
            log_softmax=lambda value, axis: torch.log_softmax(value, dim=axis),
            config=config,
            create_attention_mask=lambda hidden, cache: "causal-mask",
        )


def messages():
    return (Message("system", "system"), Message("user", "prove P"))


def test_generation_renders_nonthinking_chat_and_uses_deterministic_sampler():
    runtime = Runtime()
    generator = runtime.generator()

    output = generator.generate(messages(), seed=7)

    assert output == "decoded answer"
    assert runtime.mx.seeds == [7]
    assert runtime.sampler_calls == [{"temp": 0.0}]
    assert runtime.generate_calls == [
        {
            "prompt": "rendered prompt",
            "max_tokens": 96,
            "sampler": ("sampler", {"temp": 0.0}),
            "verbose": False,
        }
    ]
    assert runtime.tokenizer.calls == [
        (
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "prove P"},
            ],
            {
                "tokenize": False,
                "add_generation_prompt": True,
                "enable_thinking": False,
            },
        )
    ]


def test_sampled_generation_passes_temperature_and_top_p():
    runtime = Runtime()
    generator = runtime.generator(do_sample=True)

    generator.generate(messages(), seed=3)

    assert runtime.sampler_calls == [{"temp": 0.3, "top_p": 0.9}]


def test_delta_is_added_once_to_final_prefill_token_and_layer_is_restored():
    runtime = Runtime()
    generator = runtime.generator()
    original = generator.model.model.layers[0]

    generator.generate_with_delta(
        messages(), delta=torch.tensor([1.0, 2.0]), layer=0, seed=7
    )

    prefill, decode = runtime.model.recorder.values
    assert prefill.tolist() == [[[0.0, 0.0], [1.0, 2.0]]]
    assert decode.tolist() == [[[0.0, 0.0]]]
    assert generator.model.model.layers[0] is original


def test_layer_is_restored_when_generation_raises():
    runtime = Runtime()
    generator = runtime.generator()
    runtime.generate_error = RuntimeError("boom")
    original = generator.model.model.layers[-1]

    with pytest.raises(RuntimeError, match="boom"):
        generator.generate_with_delta(
            messages(), delta=torch.tensor([1.0, 2.0]), layer=-1, seed=7
        )

    assert generator.model.model.layers[-1] is original


def test_steered_call_does_not_affect_later_unsteered_call():
    runtime = Runtime()
    generator = runtime.generator()

    generator.generate_with_delta(
        messages(), delta=torch.tensor([1.0, 2.0]), layer=0, seed=7
    )
    generator.generate(messages(), seed=8)

    later_prefill = runtime.model.recorder.values[2]
    assert later_prefill.tolist() == [[[0.0, 0.0], [0.0, 0.0]]]


@pytest.mark.parametrize(
    ("delta", "layer", "message"),
    (
        (torch.zeros((1, 2)), 0, "one-dimensional"),
        (torch.zeros(3), 0, "hidden size"),
        (torch.tensor([float("nan"), 0.0]), 0, "finite"),
        (torch.zeros(2), 2, "out of range"),
        (torch.zeros(2), -3, "out of range"),
    ),
)
def test_delta_generation_rejects_invalid_action(delta, layer, message):
    generator = Runtime().generator()

    with pytest.raises(ModelConfigurationError, match=message):
        generator.generate_with_delta(
            messages(), delta=delta, layer=layer, seed=0
        )


def test_embed_text_returns_normalized_final_token_with_provenance():
    generator = Runtime(EmbeddingModel()).generator()

    embedding = generator.embed_text("proof view", layer=-1)

    assert embedding.values == pytest.approx((0.6, 0.8))
    assert embedding.model_revision == "mlx-community/Qwen3-8B-4bit"
    assert embedding.layer == 1
    assert embedding.token_index == 1
    assert len(embedding.text_hash) == 64


def test_embed_text_rejects_blank_input():
    generator = Runtime(EmbeddingModel()).generator()

    with pytest.raises(ModelConfigurationError, match="nonblank"):
        generator.embed_text("  ", layer=0)


@pytest.mark.parametrize("values", ((0.0, 0.0), (float("nan"), 1.0)))
def test_embed_text_rejects_zero_or_nonfinite_vectors(values):
    generator = Runtime(EmbeddingModel(values)).generator()

    with pytest.raises(ModelConfigurationError, match="finite and nonzero"):
        generator.embed_text("proof view", layer=-1)


def test_score_completions_is_one_forward_and_ignores_prompt_and_padding():
    model = ScoringModel()
    generator = Runtime(model).generator()

    scores = generator.score_completions(messages(), ("weak", "strong"))

    assert len(model.calls) == 1
    assert model.calls[0].tolist() == [[1, 2, 3, 0, 0], [1, 2, 4, 5, 6]]
    assert [item.text for item in scores] == ["weak", "strong"]
    assert scores[0].mean_log_likelihood > scores[1].mean_log_likelihood
    assert [item.token_count for item in scores] == [1, 3]


def test_score_completions_does_not_require_log_softmax_on_mlx_core():
    generator = Runtime(ScoringModel()).generator()

    scores = generator.score_completions(messages(), ("weak", "strong"))

    assert len(scores) == 2
