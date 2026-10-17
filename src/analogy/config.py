"""Strict configuration for the single proof-search runtime."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when runtime configuration is missing, malformed, or unexpected."""


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


@dataclass(frozen=True)
class PromptConfig:
    path: Path


@dataclass(frozen=True)
class BenchmarkConfig:
    corpus: Path


@dataclass(frozen=True)
class SearchConfig:
    top_k: int
    max_steps: int
    stuck_after: int


@dataclass(frozen=True)
class AnalogyConfig:
    enabled: bool
    max_model_calls: int


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


_SCHEMA = {
    "prompts": frozenset({"path"}),
    "model": frozenset(
        {
            "backend", "name", "device", "hidden_layer", "max_new_tokens",
            "timeout_seconds", "do_sample", "temperature", "top_p",
        }
    ),
    "benchmark": frozenset({"corpus"}),
    "search": frozenset({"top_k", "max_steps", "stuck_after"}),
    "analogy": frozenset({"enabled", "max_model_calls"}),
    "run": frozenset({"output_dir", "seed"}),
}


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _validate_keys(value: dict[str, Any], expected: frozenset[str], path: str) -> None:
    for key in sorted(value.keys() - expected):
        raise ConfigError(f"{path}.{key} is unknown")
    for key in sorted(expected - value.keys()):
        raise ConfigError(f"{path}.{key} is required")


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} must be a non-empty string")
    return value


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path} must be an integer")
    return value


def _positive_integer(value: Any, path: str) -> int:
    result = _integer(value, path)
    if result <= 0:
        raise ConfigError(f"{path} must be positive")
    return result


def _number(value: Any, path: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path} must be a number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0.0):
        qualifier = "positive" if positive else "finite"
        raise ConfigError(f"{path} must be {qualifier}")
    return result


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{path} must be a boolean")
    return value


def _path(value: Any, path: str, base: Path) -> Path:
    raw = Path(_string(value, path)).expanduser()
    return (raw if raw.is_absolute() else base / raw).resolve()


def load_config(path: Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    try:
        root = _mapping(yaml.safe_load(config_path.read_text(encoding="utf-8")), "config")
    except OSError as error:
        raise ConfigError(f"config could not be read: {config_path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"config is not valid YAML: {error}") from error
    _validate_keys(root, frozenset(_SCHEMA), "config")
    sections: dict[str, dict[str, Any]] = {}
    for name, keys in _SCHEMA.items():
        section = _mapping(root[name], f"config.{name}")
        _validate_keys(section, keys, f"config.{name}")
        sections[name] = section
    base = config_path.parent
    model = sections["model"]
    backend = _string(model["backend"], "config.model.backend")
    if backend not in {"transformers", "mlx"}:
        raise ConfigError("config.model.backend must be 'transformers' or 'mlx'")
    top_p = _number(model["top_p"], "config.model.top_p", positive=True)
    if top_p > 1.0:
        raise ConfigError("config.model.top_p must be at most 1")
    return AppConfig(
        prompts=PromptConfig(_path(sections["prompts"]["path"], "config.prompts.path", base)),
        model=ModelConfig(
            backend=backend,
            name=_string(model["name"], "config.model.name"),
            device=_string(model["device"], "config.model.device"),
            hidden_layer=_integer(model["hidden_layer"], "config.model.hidden_layer"),
            max_new_tokens=_positive_integer(model["max_new_tokens"], "config.model.max_new_tokens"),
            timeout_seconds=_number(model["timeout_seconds"], "config.model.timeout_seconds", positive=True),
            do_sample=_boolean(model["do_sample"], "config.model.do_sample"),
            temperature=_number(model["temperature"], "config.model.temperature", positive=True),
            top_p=top_p,
        ),
        benchmark=BenchmarkConfig(_path(sections["benchmark"]["corpus"], "config.benchmark.corpus", base)),
        search=SearchConfig(
            top_k=_positive_integer(sections["search"]["top_k"], "config.search.top_k"),
            max_steps=_positive_integer(sections["search"]["max_steps"], "config.search.max_steps"),
            stuck_after=_positive_integer(sections["search"]["stuck_after"], "config.search.stuck_after"),
        ),
        analogy=AnalogyConfig(
            enabled=_boolean(sections["analogy"]["enabled"], "config.analogy.enabled"),
            max_model_calls=_positive_integer(sections["analogy"]["max_model_calls"], "config.analogy.max_model_calls"),
        ),
        run=RunConfig(
            output_dir=_path(sections["run"]["output_dir"], "config.run.output_dir", base),
            seed=_integer(sections["run"]["seed"], "config.run.seed"),
        ),
    )


__all__ = [
    "AnalogyConfig", "AppConfig", "BenchmarkConfig", "ConfigError",
    "ModelConfig", "PromptConfig", "RunConfig", "SearchConfig", "load_config",
]
