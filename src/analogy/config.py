"""Immutable configuration records shared by the solver."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelConfig:
    backend: str
    name: str
    device: str
    hidden_layer: int
    max_new_tokens: int
    timeout_seconds: float
    do_sample: bool
    temperature: float
    top_p: float
    revision: str | None = None


@dataclass(frozen=True)
class PromptConfig:
    path: Path


@dataclass(frozen=True)
class BenchmarkConfig:
    corpus: Path
    selection: str


@dataclass(frozen=True)
class SearchConfig:
    top_k: int
    max_steps: int
    stuck_after: int
    samples_per_state: int = 3


@dataclass(frozen=True)
class AnalogyConfig:
    enabled: bool
    max_model_calls: int
    model_name: str | None = None
    calibration_path: Path | None = None


@dataclass(frozen=True)
class RunConfig:
    output_dir: Path
    seed: int


@dataclass(frozen=True)
class AppConfig:
    prompts: PromptConfig
    model: ModelConfig
    benchmark: BenchmarkConfig
    search: SearchConfig
    analogy: AnalogyConfig
    run: RunConfig


__all__ = [
    "AnalogyConfig",
    "AppConfig",
    "BenchmarkConfig",
    "ModelConfig",
    "PromptConfig",
    "RunConfig",
    "SearchConfig",
]
