"""Shared frozen-model boundary and lazy backend selection."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Protocol

from .config import ModelConfig
from .model import (
    CompletionScore,
    ModelConfigurationError,
    QwenGenerator,
)
from .records import Embedding, Message


class ModelBackend(Protocol):
    hidden_size: int
    model_revision: str
    timeout_seconds: float

    def generate(self, messages: tuple[Message, ...], *, seed: int) -> str: ...

    def generate_with_delta(
        self,
        messages: tuple[Message, ...],
        *,
        delta: Any,
        layer: int,
        seed: int,
    ) -> str: ...

    def embed_text(self, text: str, *, layer: int) -> Embedding: ...

    def score_completions(
        self,
        messages: tuple[Message, ...],
        completions: tuple[str, ...],
    ) -> tuple[CompletionScore, ...]: ...


def load_model_backend(config: ModelConfig) -> ModelBackend:
    if config.backend == "transformers":
        return QwenGenerator.from_config(config)
    try:
        module = import_module("analogy.mlx_model")
    except ImportError as error:
        raise ModelConfigurationError(
            "the MLX extra is not installed; install analogy[mlx]"
        ) from error
    return module.MLXQwenGenerator.from_config(config)


__all__ = ["ModelBackend", "load_model_backend"]
