"""Process-wide construction of shared ML and Lean dependencies."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import AppConfig, load_config
from .inference.model_provider import get_model_provider
from .inference.prompts import PromptCatalog, configure_prompt_catalog
from .lean_gym.corpus import load_problems
from .lean_gym.models import Problem
from .lean_gym.resources import LEAN_PROJECT_ROOT
from .lean_gym.verifier import LeanSubprocessVerifier
from .problem_index import ProblemIndex


@dataclass(frozen=True, slots=True)
class Runtime:
    config: AppConfig
    prompts: PromptCatalog | None
    model: Any
    verifier: Any
    problems: tuple[Problem, ...]
    problem_index: ProblemIndex


def runtime_config(config: AppConfig) -> AppConfig:
    return config


def with_analogy_enabled(config: AppConfig, enabled: bool | None) -> AppConfig:
    """Return config with only the runtime analogy gate overridden."""

    if enabled is None:
        return config
    if not isinstance(enabled, bool):
        raise ValueError("analogy override must be a boolean or None")
    return replace(config, analogy=replace(config.analogy, enabled=enabled))


def with_problem_selection(config: AppConfig, selection: str | None) -> AppConfig:
    """Return config with a validated problem scheduling override."""

    if selection is None:
        return config
    if selection not in {"exhaustive", "previous_failures"}:
        raise ValueError(f"unknown problem selection mode {selection!r}")
    return replace(config, benchmark=replace(config.benchmark, selection=selection))


@lru_cache(maxsize=None)
def get_runtime(
    config_path: Path,
    analogy_enabled: bool | None = None,
    problem_selection: str | None = None,
) -> Runtime:
    """Build each immutable or expensive dependency once per configuration."""

    config = runtime_config(load_config(Path(config_path)))
    config = with_analogy_enabled(config, analogy_enabled)
    config = with_problem_selection(config, problem_selection)
    all_problems = tuple(load_problems(config.benchmark.corpus))
    problem_index = ProblemIndex.load(config, all_problems)
    problems = problem_index.select(config.benchmark.selection)
    if not problems:
        return Runtime(config, None, None, None, (), problem_index)
    prompts = configure_prompt_catalog(config.prompts.path)
    model = get_model_provider(config.model)
    verifier = LeanSubprocessVerifier(LEAN_PROJECT_ROOT)
    return Runtime(config, prompts, model, verifier, problems, problem_index)


__all__ = [
    "Runtime", "get_runtime", "runtime_config", "with_analogy_enabled",
    "with_problem_selection",
]
