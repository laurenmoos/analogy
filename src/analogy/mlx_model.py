"""Optional MLX backend for frozen Qwen generation on Apple silicon."""

from __future__ import annotations

import math
import time
from typing import Any, Callable

from .config import ModelConfig
from .model import ModelConfigurationError
from .records import Message


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
        config: ModelConfig,
    ) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.mx = mx
        self._generate_fn = generate_fn
        self._make_sampler = make_sampler
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
