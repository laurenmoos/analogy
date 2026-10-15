"""Optional MLX backend for frozen Qwen generation on Apple silicon."""

from __future__ import annotations

import math
import hashlib
import time
from typing import Any, Callable

from .config import ModelConfig
from .model import CompletionScore, ModelConfigurationError
from .records import Embedding, Message


class _DeltaLayer:
    def __init__(self, inner: Any, delta: Any, mx: Any) -> None:
        self.inner = inner
        self.delta = delta
        self.mx = mx
        self.applied = False

    def __call__(self, hidden: Any, mask: Any = None, cache: Any = None) -> Any:
        output = self.inner(hidden, mask, cache)
        if not self.applied:
            output = self.mx.concatenate(
                [output[:, :-1, :], output[:, -1:, :] + self.delta], axis=1
            )
            self.applied = True
        return output


class MLXQwenGenerator:
    """Qwen backend with an MLX-native, temporary decoder-layer intervention."""

    def __init__(
        self,
        *,
        model: Any,
        tokenizer: Any,
        mx: Any,
        generate_fn: Callable[..., str],
        make_sampler: Callable[..., Any],
        create_attention_mask: Callable[[Any, Any], Any],
        config: ModelConfig,
    ) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.mx = mx
        self._generate_fn = generate_fn
        self._make_sampler = make_sampler
        self._create_attention_mask = create_attention_mask
        self.max_new_tokens = config.max_new_tokens
        self.timeout_seconds = config.timeout_seconds
        self.do_sample = config.do_sample
        self.temperature = config.temperature
        self.top_p = config.top_p
        self.model_revision = config.name
        hidden_size = getattr(getattr(model, "args", None), "hidden_size", None)
        if hidden_size is None:
            raise ModelConfigurationError("MLX model does not expose hidden size")
        self.hidden_size = int(hidden_size)
        self._layers()

    @classmethod
    def from_config(cls, config: ModelConfig) -> "MLXQwenGenerator":
        try:
            import mlx.core as mx
            from mlx_lm import generate, load
            from mlx_lm.models.base import create_attention_mask
            from mlx_lm.sample_utils import make_sampler
        except ImportError as error:
            raise ModelConfigurationError(
                "the MLX extra is not installed; install analogy[mlx]"
            ) from error
        model, tokenizer = load(config.name)
        return cls(
            model=model,
            tokenizer=tokenizer,
            mx=mx,
            generate_fn=generate,
            make_sampler=make_sampler,
            create_attention_mask=create_attention_mask,
            config=config,
        )

    def generate(self, messages: tuple[Message, ...], *, seed: int) -> str:
        return self._run_generation(messages, seed=seed)

    def generate_with_delta(
        self,
        messages: tuple[Message, ...],
        *,
        delta: Any,
        layer: int,
        seed: int,
    ) -> str:
        values = self._delta_values(delta)
        layers = self._layers()
        resolved = _resolve_layer(layer, len(layers))
        mlx_delta = self.mx.array(values)
        original = layers[resolved]
        layers[resolved] = _DeltaLayer(original, mlx_delta, self.mx)
        try:
            return self._run_generation(messages, seed=seed)
        finally:
            layers[resolved] = original

    def embed_text(self, text: str, *, layer: int) -> Embedding:
        if not isinstance(text, str) or not text.strip():
            raise ModelConfigurationError("embedding text must be nonblank")
        token_ids = tuple(
            int(item)
            for item in self.tokenizer.encode(text, add_special_tokens=False)
        )
        if not token_ids:
            raise ModelConfigurationError("embedding tokenization must be nonempty")
        layers = self._layers()
        resolved = _resolve_layer(layer, len(layers))
        inputs = self.mx.array([token_ids])
        core = self.model.model
        embed_tokens = getattr(core, "embed_tokens", None)
        if embed_tokens is None:
            raise ModelConfigurationError("MLX model does not expose token embeddings")
        hidden = embed_tokens(inputs)
        mask = self._create_attention_mask(hidden, None)
        for decoder_layer in layers[: resolved + 1]:
            hidden = decoder_layer(hidden, mask, None)
        vector = hidden[0, -1]
        vector = (
            vector.astype(self.mx.float32)
            if hasattr(vector, "astype")
            else vector.float()
        )
        norm = self.mx.linalg.norm(vector)
        self.mx.eval(vector, norm)
        norm_value = float(norm.item())
        if not math.isfinite(norm_value) or norm_value <= 0.0:
            raise ModelConfigurationError("embedding must be finite and nonzero")
        normalized = vector / norm
        self.mx.eval(normalized)
        values = tuple(float(item) for item in normalized.tolist())
        if not values or not all(math.isfinite(item) for item in values):
            raise ModelConfigurationError("embedding must be finite and nonzero")
        return Embedding(
            f"{self.model_revision}:layer={resolved}:final-nonpadding",
            values,
            model_revision=self.model_revision,
            layer=resolved,
            token_index=len(token_ids) - 1,
            text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )

    def score_completions(
        self,
        messages: tuple[Message, ...],
        completions: tuple[str, ...],
    ) -> tuple[CompletionScore, ...]:
        if not completions or any(not item.strip() for item in completions):
            raise ModelConfigurationError("scoring completions must be nonempty")
        prompt = self.tokenizer.apply_chat_template(
            [{"role": item.role, "content": item.content} for item in messages],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        prompt_ids = tuple(
            int(item)
            for item in self.tokenizer.encode(prompt, add_special_tokens=False)
        )
        if not prompt_ids:
            raise ModelConfigurationError("scoring prompt tokenization must be nonempty")
        encoded: list[tuple[str, tuple[int, ...]]] = []
        for completion in completions:
            completion_ids = tuple(
                int(item)
                for item in self.tokenizer.encode(
                    completion, add_special_tokens=False
                )
            )
            if not completion_ids:
                raise ModelConfigurationError(
                    "completion tokenization must be nonempty"
                )
            encoded.append((completion, completion_ids))
        pad_id = getattr(self.tokenizer, "pad_token_id", None)
        if pad_id is None:
            pad_id = getattr(self.tokenizer, "eos_token_id", None)
        if pad_id is None:
            pad_id = 0
        width = max(len(prompt_ids) + len(ids) for _, ids in encoded)
        rows = []
        for _, completion_ids in encoded:
            sequence = prompt_ids + completion_ids
            rows.append(sequence + (int(pad_id),) * (width - len(sequence)))
        started = time.monotonic()
        logits = self.model(self.mx.array(rows))
        logits = (
            logits.astype(self.mx.float32)
            if hasattr(logits, "astype")
            else logits.float()
        )
        log_probabilities = self.mx.log_softmax(logits, axis=-1)
        self.mx.eval(log_probabilities)
        synchronize = getattr(self.mx, "synchronize", None)
        if synchronize is not None:
            synchronize()
        if time.monotonic() - started >= self.timeout_seconds:
            raise TimeoutError(
                f"completion scoring exceeded {self.timeout_seconds:g} seconds"
            )
        scores = []
        for row, (completion, completion_ids) in enumerate(encoded):
            token_scores = tuple(
                float(
                    log_probabilities[
                        row, len(prompt_ids) + offset - 1, token_id
                    ].item()
                )
                for offset, token_id in enumerate(completion_ids)
            )
            total = sum(token_scores)
            scores.append(
                CompletionScore(
                    completion,
                    total,
                    total / len(token_scores),
                    len(token_scores),
                )
            )
        return tuple(scores)

    def _run_generation(
        self, messages: tuple[Message, ...], *, seed: int
    ) -> str:
        prompt = self.tokenizer.apply_chat_template(
            [{"role": item.role, "content": item.content} for item in messages],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        self.mx.random.seed(seed)
        sampler = (
            self._make_sampler(temp=0.0)
            if not self.do_sample
            else self._make_sampler(temp=self.temperature, top_p=self.top_p)
        )
        started = time.monotonic()
        output = self._generate_fn(
            self.model,
            self.tokenizer,
            prompt=prompt,
            max_tokens=self.max_new_tokens,
            sampler=sampler,
            verbose=False,
        )
        synchronize = getattr(self.mx, "synchronize", None)
        if synchronize is not None:
            synchronize()
        elapsed = time.monotonic() - started
        if elapsed >= self.timeout_seconds:
            raise TimeoutError(
                f"generation exceeded {self.timeout_seconds:g} seconds"
            )
        if not isinstance(output, str):
            raise ModelConfigurationError("MLX generation must return text")
        return output

    def _layers(self) -> list[Any]:
        layers = getattr(getattr(self.model, "model", None), "layers", None)
        if not isinstance(layers, list) or not layers:
            raise ModelConfigurationError(
                "MLX model does not expose mutable decoder layers"
            )
        return layers

    def _delta_values(self, delta: Any) -> tuple[float, ...]:
        ndim = getattr(delta, "ndim", None)
        if ndim != 1:
            raise ModelConfigurationError(
                "latent delta must be a one-dimensional tensor"
            )
        detached = delta.detach() if hasattr(delta, "detach") else delta
        if hasattr(detached, "cpu"):
            detached = detached.cpu()
        raw = detached.tolist() if hasattr(detached, "tolist") else list(detached)
        values = tuple(float(item) for item in raw)
        if len(values) != self.hidden_size:
            raise ModelConfigurationError(
                "latent delta dimension must match model hidden size"
            )
        if not all(math.isfinite(item) for item in values):
            raise ModelConfigurationError("latent delta must be finite")
        return values


def _resolve_layer(layer: int, count: int) -> int:
    if isinstance(layer, bool) or not isinstance(layer, int):
        raise ModelConfigurationError("hidden layer must be an integer")
    resolved = layer if layer >= 0 else count + layer
    if resolved < 0 or resolved >= count:
        raise ModelConfigurationError(
            f"hidden layer {layer} is out of range for {count} decoder layers"
        )
    return resolved


__all__ = ["MLXQwenGenerator"]
