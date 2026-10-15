from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch

from analogy.model import ModelConfigurationError, QwenGenerator, QwenProofEncoder
from analogy.records import Embedding, SymbolicState
from analogy.rescue import (
    ProofRepresentation,
    ProofRescueContext,
    render_proof_embeddings,
)


def context() -> ProofRescueContext:
    return ProofRescueContext(
        state=SymbolicState(
            "s", "P Q : Prop\nh : P ∨ Q\n⊢ Q ∨ P", "lean4", ("intro h",)
        ),
        goal=SymbolicState("g", "Q ∨ P", "lean4"),
        accepted_tactics=("intro h",),
        failed_tactics=("exact h",),
    )


def test_proof_embedding_prompts_separate_language_and_structure() -> None:
    language, structure = render_proof_embeddings(context())

    assert "Accepted prefix:\nintro h" in language
    assert "Failed tactics:\nexact h" in language
    assert "Current Lean goals" in structure
    assert "Do not propose tactics" in structure
    assert language != structure


class TensorBatch(dict):
    def to(self, device):
        return TensorBatch({key: value.to(device) for key, value in self.items()})


class HiddenTokenizer:
    def __call__(self, texts, **kwargs):
        assert texts == ["proof text"]
        assert kwargs == {"return_tensors": "pt", "padding": True}
        return TensorBatch(
            input_ids=torch.tensor([[10, 11, 0]]),
            attention_mask=torch.tensor([[1, 1, 0]]),
        )


class HiddenModel:
    def __init__(self, layers):
        self.layers = layers
        self.config = SimpleNamespace(_commit_hash="revision-1")
        self.device = "cpu"
        self.parameter = torch.nn.Parameter(torch.ones(1))
        self.calls = []

    def parameters(self):
        return (self.parameter,)

    def eval(self):
        return self

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(hidden_states=self.layers)


def generator_for_vector(vector, *, wrong_rank: bool = False):
    selected = torch.tensor([vector], dtype=torch.float16) if wrong_rank else torch.tensor(
        [[[9.0, 9.0], vector, [100.0, 100.0]]], dtype=torch.float16
    )
    layers = (
        torch.zeros((1, 3, 2), dtype=torch.float16),
        selected,
        torch.ones((1, 3, 2), dtype=torch.float16),
    )
    model = HiddenModel(layers)
    return QwenGenerator(
        tokenizer=HiddenTokenizer(),
        model=model,
        torch_module=torch,
        device="cpu",
        max_new_tokens=8,
        timeout_seconds=1.0,
    ), model


def test_embed_text_selects_final_nonpadding_token_and_normalizes_float32() -> None:
    generator, model = generator_for_vector([3.0, 4.0])

    embedding = generator.embed_text("proof text", layer=1)

    assert embedding.values == pytest.approx((0.6, 0.8))
    assert embedding.model_id == "revision-1:layer=1:final-nonpadding"
    assert model.calls[0]["output_hidden_states"] is True
    assert model.calls[0]["use_cache"] is False
    assert model.parameter.requires_grad is False


def test_positive_and_equivalent_negative_layers_are_identical() -> None:
    generator, _ = generator_for_vector([3.0, 4.0])

    positive = generator.embed_text("proof text", layer=1)
    negative = generator.embed_text("proof text", layer=-2)

    assert positive == negative


def test_embedding_is_deterministic_in_eval_mode() -> None:
    generator, _ = generator_for_vector([3.0, 4.0])

    assert generator.embed_text("proof text", layer=1).values == generator.embed_text(
        "proof text", layer=1
    ).values


@pytest.mark.parametrize(
    "vector",
    [[0.0, 0.0], [float("nan"), 1.0], [float("inf"), 1.0]],
)
def test_embed_text_rejects_nonfinite_or_zero_vectors(vector) -> None:
    generator, _ = generator_for_vector(vector)

    with pytest.raises(ModelConfigurationError, match="finite and nonzero"):
        generator.embed_text("proof text", layer=1)


def test_embed_text_rejects_wrong_rank_hidden_state() -> None:
    generator, _ = generator_for_vector([3.0, 4.0], wrong_rank=True)

    with pytest.raises(ModelConfigurationError, match="rank"):
        generator.embed_text("proof text", layer=1)


@pytest.mark.parametrize("layer", [3, -4, True, 1.5])
def test_embed_text_rejects_invalid_layer(layer: object) -> None:
    generator, _ = generator_for_vector([3.0, 4.0])

    with pytest.raises(ModelConfigurationError, match="layer"):
        generator.embed_text("proof text", layer=layer)  # type: ignore[arg-type]


def test_proof_representation_requires_matching_dimensions_and_distinct_hashes() -> None:
    base = {
        "context": context(),
        "language_prompt_hash": "language",
        "structure_prompt_hash": "structure",
        "language_embedding": Embedding("model", (1.0, 0.0)),
        "structure_embedding": Embedding("model", (0.0, 1.0)),
    }
    assert ProofRepresentation(**base).dimension == 2
    with pytest.raises(ValueError, match="hashes"):
        ProofRepresentation(**{**base, "structure_prompt_hash": "language"})
    with pytest.raises(ValueError, match="dimensions"):
        ProofRepresentation(
            **{**base, "structure_embedding": Embedding("model", (1.0,))}
        )


class SpyEmbedder:
    def __init__(self):
        self.calls = []

    def embed_text(self, text, *, layer):
        self.calls.append((text, layer))
        values = (1.0, 0.0) if len(self.calls) == 1 else (0.0, 1.0)
        return Embedding(f"model:layer={layer}", values)


def test_proof_encoder_embeds_each_view_once_with_same_layer() -> None:
    embedder = SpyEmbedder()

    representation = QwenProofEncoder(embedder, layer=-1).encode(context())

    assert len(embedder.calls) == 2
    assert [layer for _, layer in embedder.calls] == [-1, -1]
    assert embedder.calls[0][0] != embedder.calls[1][0]
    assert representation.language_prompt_hash != representation.structure_prompt_hash
