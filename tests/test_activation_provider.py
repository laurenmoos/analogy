"""Provider contracts using real tiny Torch blocks and deterministic token IDs."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
import math

import pytest

torch = pytest.importorskip("torch", reason="activation tensor tests require the model extra")

from analogy.config import load_config
from analogy.inference import model_provider as providers
from analogy.inference.mlx_model import MLXQwenGenerator, _DeltaLayer
from analogy.records import Message


MESSAGES = (Message("user", "question"),)


class Tokenizer:
    pad_token_id = 0

    def apply_chat_template(self, messages, **kwargs):
        assert kwargs == dict(tokenize=False, add_generation_prompt=True, enable_thinking=False)
        return "rendered prompt"

    def encode(self, text, *, add_special_tokens):
        assert add_special_tokens is False
        return {"rendered prompt": [1, 2, 3], "answer": [4, 5], "empty tokens": [], "": [], " ": [6]}[text]


class Block(torch.nn.Module):
    def __init__(self, amount):
        super().__init__()
        self.amount = amount
        self.inputs = []

    def forward(self, hidden, mask=None, cache=None):
        self.inputs.append(hidden.detach().clone())
        return hidden + self.amount


class TinyModel(torch.nn.Module):
    def __init__(self, *, mlx=False):
        super().__init__()
        self.config = SimpleNamespace(hidden_size=2, num_hidden_layers=6)
        self.args = SimpleNamespace(hidden_size=2)
        self.model = SimpleNamespace(
            layers=[Block(i) for i in range(1, 7)],
            embed_tokens=lambda ids: torch.stack((ids.float(), ids.float() * 2), dim=-1),
            norm=lambda hidden: hidden * 10,
        )
        self.mlx = mlx
        self.fail = False

    def forward(self, input_ids, **kwargs):
        hidden = self.model.embed_tokens(input_ids)
        states = [hidden]
        for block in self.model.layers:
            hidden = block(hidden)
            states.append(hidden)
        if self.fail:
            raise RuntimeError("decoder failed")
        hidden = self.model.norm(hidden)
        states[-1] = hidden
        logits = hidden[..., :1] * torch.arange(8) / 200
        if self.mlx:
            return logits
        return SimpleNamespace(logits=logits, hidden_states=tuple(states))


@pytest.fixture(params=["transformers", "mlx"])
def provider(request):
    model = TinyModel(mlx=request.param == "mlx")
    if request.param == "transformers":
        result = providers.QwenGenerator(
            tokenizer=Tokenizer(), model=model, torch_module=torch, device="cpu",
            max_new_tokens=8, timeout_seconds=60,
        )
    else:
        mx = SimpleNamespace(
            array=lambda values: torch.tensor(values),
            concatenate=lambda items, axis: torch.cat(items, dim=axis),
            eval=lambda *args: None,
        )
        config = replace(load_config(Path(__file__).parents[1] / "config.yaml").model, timeout_seconds=60)
        result = MLXQwenGenerator(
            model=model, tokenizer=Tokenizer(), mx=mx, generate_fn=None,
            make_sampler=None, log_softmax=lambda values, axis: torch.log_softmax(values, dim=axis),
            create_attention_mask=lambda hidden, cache: None, config=config,
        )
    return result, model, request.param


def test_resolves_unique_middle_to_late_decoder_blocks_ending_at_intervention(provider):
    engine, _, _ = provider
    assert engine.decoder_layer_count == 6
    assert engine.resolve_decoder_layers(3, through=-1) == (2, 3, 5)
    assert engine.resolve_decoder_layers(3, through=2) == (0, 1, 2)
    assert engine.resolve_decoder_layers(1, through=4) == (4,)


@pytest.mark.parametrize("count,through", [(0, 5), (True, 5), (4, 2), (2, 6), (2, -7), (2, True)])
def test_rejects_impossible_layer_resolution(provider, count, through):
    engine, _, _ = provider
    with pytest.raises(providers.ModelConfigurationError):
        engine.resolve_decoder_layers(count, through=through)


def test_count_includes_chat_prefill_and_separately_encoded_completion_without_forward(provider):
    engine, model, _ = provider
    assert engine.count_token_positions(MESSAGES, "answer") == 5
    assert model.model.layers[0].inputs == []


def test_trace_pools_only_completion_tokens_at_raw_decoder_outputs_and_charges_actual_passes(provider):
    engine, model, backend = provider
    trace = engine.capture_completion_trace(MESSAGES, "answer", layers=(0, 2, 5))
    assert [(item.layer, item.values) for item in trace.activations] == [
        (0, (5.5, 10.0)), (2, (10.5, 15.0)), (5, (25.5, 30.0)),
    ]
    assert trace.completion_token_count == trace.score.token_count == 2
    assert trace.score.text == "answer"
    assert trace.processed_token_positions == (10 if backend == "mlx" else 5)
    assert sum(row.shape[1] for row in model.model.layers[0].inputs) == trace.processed_token_positions
    # Causal logits for the two targets come from prompt index 2 and completion index 3.
    expected = sum((value * target / 20 - math.log(sum(math.exp(value * j / 20) for j in range(8))))
                   for value, target in [(24, 4), (25, 5)])
    assert trace.score.total_log_likelihood == pytest.approx(expected)
    assert trace.score.mean_log_likelihood == pytest.approx(expected / 2)


@pytest.mark.parametrize("layers", [(), (1, 1), (3, 1), (-1,), (6,), (True,)])
def test_capture_rejects_invalid_or_unordered_decoder_blocks(provider, layers):
    engine, model, _ = provider
    with pytest.raises(providers.ModelConfigurationError):
        engine.capture_completion_trace(MESSAGES, "answer", layers=layers)
    assert model.model.layers[0].inputs == []


@pytest.mark.parametrize("completion", ["", " ", "empty tokens"])
def test_empty_completions_fail_before_forward(provider, completion):
    engine, model, _ = provider
    for call in [
        lambda: engine.capture_completion_trace(MESSAGES, completion, layers=(1,)),
        lambda: engine.score_completion_with_delta(MESSAGES, completion, delta=(1., 2.), layer=0),
    ]:
        with pytest.raises(providers.ModelConfigurationError):
            call()
    assert model.model.layers[0].inputs == []


@pytest.mark.parametrize("completion,want", [("", 3), (" ", 4), ("empty tokens", 3)])
def test_generation_usage_counts_empty_or_malformed_output_without_scoring_it(provider, completion, want):
    engine, model, _ = provider
    assert engine.count_token_positions(MESSAGES, completion) == want
    assert model.model.layers[0].inputs == []


def test_causal_patch_targets_absolute_final_prompt_index_and_never_completion_input_positions(provider):
    engine, model, _ = provider
    baseline = engine.score_completions(MESSAGES, ("answer",))[0]
    before = model.model.layers[1].inputs[-1]
    patched = engine.score_completion_with_delta(MESSAGES, "answer", delta=(10., -3.), layer=0)
    after = model.model.layers[1].inputs[-1]
    assert torch.equal(after[:, :2], before[:, :2])
    assert torch.equal(after[:, 2], before[:, 2] + torch.tensor([10., -3.]))
    assert torch.equal(after[:, 3:], before[:, 3:])
    assert patched.total_log_likelihood != baseline.total_log_likelihood
    zero = engine.score_completion_with_delta(MESSAGES, "answer", delta=(0., 0.), layer=0)
    assert zero == baseline
    assert engine.score_completions(MESSAGES, ("answer",))[0] == baseline


@pytest.mark.parametrize("delta,layer", [((1.,), 0), ((float("nan"), 0.), 0), ((0., float("inf")), 0), ((1., 2.), 6), ((1., 2.), True)])
def test_invalid_patches_fail_before_forward(provider, delta, layer):
    engine, model, _ = provider
    with pytest.raises(providers.ModelConfigurationError):
        engine.score_completion_with_delta(MESSAGES, "answer", delta=delta, layer=layer)
    assert model.model.layers[0].inputs == []


def test_patch_is_removed_even_when_model_forward_raises(provider):
    engine, model, _ = provider
    original = model.model.layers[0]
    model.fail = True
    with pytest.raises(RuntimeError, match="decoder failed"):
        engine.score_completion_with_delta(MESSAGES, "answer", delta=(1., 2.), layer=0)
    assert model.model.layers[0] is original
    assert not original._forward_hooks
    model.fail = False
    assert engine.score_completions(MESSAGES, ("answer",))[0] == engine.score_completion_with_delta(
        MESSAGES, "answer", delta=(0., 0.), layer=0,
    )


def test_activation_records_reject_nonfinite_values_and_are_deeply_immutable():
    with pytest.raises(ValueError):
        providers.LayerActivation(0, (float("nan"),))
    activation = providers.LayerActivation(0, [1., 2.])
    assert activation.values == (1., 2.)
    with pytest.raises(FrozenInstanceError):
        activation.layer = 1
    score = providers.CompletionScore("answer", -2., -1., 2)
    trace = providers.CompletionActivationTrace(score, [activation], 2, 5)
    assert trace.activations == (activation,)
    with pytest.raises(ValueError):
        providers.CompletionActivationTrace(score, (activation,), 1, 5)


def test_mlx_cached_generation_wrapper_patches_boundary_once_across_chunks():
    mx = SimpleNamespace(concatenate=lambda parts, axis: torch.cat(parts, dim=axis))
    wrapped = _DeltaLayer(Block(1), torch.tensor([10., 20.]), mx, prompt_tokens=3)
    assert torch.equal(wrapped(torch.zeros(1, 2, 2)), torch.ones(1, 2, 2))
    assert torch.equal(wrapped(torch.zeros(1, 2, 2)), torch.tensor([[[11., 21.], [1., 1.]]]))
    assert torch.equal(wrapped(torch.zeros(1, 1, 2)), torch.ones(1, 1, 2))


@pytest.mark.parametrize("widths", [(5,), (2, 2, 1)])
def test_mlx_float32_patch_preserves_float16_residual_dtype_and_nonboundary_rows(widths):
    mx = SimpleNamespace(concatenate=lambda parts, axis: torch.cat(parts, dim=axis))
    wrapped = _DeltaLayer(Block(1), torch.tensor([10., 20.], dtype=torch.float32), mx, prompt_tokens=3)
    inputs = torch.arange(10, dtype=torch.float16).reshape(1, 5, 2)
    chunks = []
    start = 0
    for width in widths:
        chunk = wrapped(inputs[:, start:start + width])
        assert chunk.dtype == torch.float16
        chunks.append(chunk)
        start += width
    actual = torch.cat(chunks, dim=1)
    expected = inputs + 1
    assert torch.equal(actual[:, :2], expected[:, :2])
    assert torch.equal(actual[:, 2], expected[:, 2] + torch.tensor([10., 20.], dtype=torch.float16))
    assert torch.equal(actual[:, 3:], expected[:, 3:])


def test_float16_zero_patch_preserves_residual_dtype_and_exact_completion_score(provider):
    engine, model, _ = provider
    model.model.embed_tokens = lambda ids: torch.stack((ids.float(), ids.float() * 2), dim=-1).half() / 7
    baseline = engine.score_completions(MESSAGES, ("answer",))[0]
    patched = engine.score_completion_with_delta(MESSAGES, "answer", delta=(0., 0.), layer=0)
    assert patched == baseline
    assert model.model.layers[1].inputs[-1].dtype == torch.float16
