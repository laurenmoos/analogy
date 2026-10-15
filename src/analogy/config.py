"""Strict loading for the repository's single YAML configuration."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when configuration is missing, malformed, or unexpected."""


@dataclass(frozen=True)
class ExperimentConfig:
    stage: str
    minimum_solved: int


@dataclass(frozen=True)
class ModelConfig:
    name: str
    device: str
    hidden_layer: int
    max_new_tokens: int
    timeout_seconds: float
    do_sample: bool
    temperature: float
    top_p: float


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
    samples: int


@dataclass(frozen=True)
class RLConfig:
    enabled: bool
    algorithm: str
    rollouts_per_state: int
    tactics_per_rollout: int
    hidden_width: int
    max_delta_norm: float
    initial_log_scale: float
    learning_rate: float
    entropy_weight: float
    max_gradient_norm: float
    completion_reward: float
    progress_reward: float
    rejection_reward: float
    max_rescue_verifier_calls: int
    epochs: int
    checkpoint_interval: int
    checkpoint: Path | None
    train_connectives: tuple[str, ...]
    validation_connectives: tuple[str, ...]
    test_connectives: tuple[str, ...]


@dataclass(frozen=True)
class RunConfig:
    output_dir: Path
    seed: int


@dataclass(frozen=True)
class DataConfig:
    root: Path


@dataclass(frozen=True)
class AppConfig:
    experiment: ExperimentConfig
    model: ModelConfig
    benchmark: BenchmarkConfig
    search: SearchConfig
    analogy: AnalogyConfig
    rl: RLConfig
    run: RunConfig
    data: DataConfig


_SCHEMA: dict[str, frozenset[str]] = {
    "experiment": frozenset({"stage", "minimum_solved"}),
    "model": frozenset(
        {
            "name",
            "device",
            "hidden_layer",
            "max_new_tokens",
            "timeout_seconds",
            "do_sample",
            "temperature",
            "top_p",
        }
    ),
    "benchmark": frozenset({"corpus"}),
    "search": frozenset({"top_k", "max_steps", "stuck_after"}),
    "analogy": frozenset({"enabled", "samples"}),
    "rl": frozenset(
        {
            "enabled",
            "algorithm",
            "rollouts_per_state",
            "tactics_per_rollout",
            "hidden_width",
            "max_delta_norm",
            "initial_log_scale",
            "learning_rate",
            "entropy_weight",
            "max_gradient_norm",
            "completion_reward",
            "progress_reward",
            "rejection_reward",
            "max_rescue_verifier_calls",
            "epochs",
            "checkpoint_interval",
            "checkpoint",
            "train_connectives",
            "validation_connectives",
            "test_connectives",
        }
    ),
    "run": frozenset({"output_dir", "seed"}),
    "data": frozenset({"root"}),
}


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _validate_keys(mapping: dict[str, Any], expected: frozenset[str], path: str) -> None:
    for key in sorted(mapping.keys() - expected):
        raise ConfigError(f"{path}.{key} is unknown")
    for key in sorted(expected - mapping.keys()):
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
    integer = _integer(value, path)
    if integer <= 0:
        raise ConfigError(f"{path} must be positive")
    return integer


def _positive_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path} must be a number")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ConfigError(f"{path} must be positive")
    return number


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigError(f"{path} must be a finite number")
    return number


def _nonnegative_number(value: Any, path: str) -> float:
    number = _finite_number(value, path)
    if number < 0.0:
        raise ConfigError(f"{path} must be non-negative")
    return number


def _probability(value: Any, path: str) -> float:
    number = _positive_number(value, path)
    if number > 1.0:
        raise ConfigError(f"{path} must be at most 1")
    return number


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{path} must be a boolean")
    return value


def _path(value: Any, path: str, base: Path) -> Path:
    raw = Path(_string(value, path)).expanduser()
    return (raw if raw.is_absolute() else base / raw).resolve()


def _optional_path(value: Any, path: str, base: Path) -> Path | None:
    if value is None:
        return None
    return _path(value, path, base)


def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{path} must be a non-empty list of strings")
    normalized = tuple(
        _string(item, f"{path}[{index}]") for index, item in enumerate(value)
    )
    if len(set(normalized)) != len(normalized):
        raise ConfigError(f"{path} must not contain duplicates")
    return normalized


def load_config(path: Path) -> AppConfig:
    """Load and validate a complete configuration from ``path``."""

    config_path = Path(path).expanduser().resolve()
    try:
        parsed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"config could not be read: {config_path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"config is not valid YAML: {error}") from error

    root = _mapping(parsed, "config")
    expected_sections = frozenset(_SCHEMA)
    _validate_keys(root, expected_sections, "config")
    sections: dict[str, dict[str, Any]] = {}
    for name, keys in _SCHEMA.items():
        section = _mapping(root[name], f"config.{name}")
        _validate_keys(section, keys, f"config.{name}")
        sections[name] = section

    experiment = sections["experiment"]
    model = sections["model"]
    benchmark = sections["benchmark"]
    search = sections["search"]
    analogy = sections["analogy"]
    rl = sections["rl"]
    run = sections["run"]
    data = sections["data"]
    base = config_path.parent

    completion_reward = _finite_number(
        rl["completion_reward"], "config.rl.completion_reward"
    )
    progress_reward = _finite_number(
        rl["progress_reward"], "config.rl.progress_reward"
    )
    rejection_reward = _finite_number(
        rl["rejection_reward"], "config.rl.rejection_reward"
    )
    if not completion_reward > progress_reward >= 0.0 > rejection_reward:
        raise ConfigError(
            "rl reward ordering must satisfy completion > progress >= 0 > rejection"
        )
    train_connectives = _string_tuple(
        rl["train_connectives"], "config.rl.train_connectives"
    )
    validation_connectives = _string_tuple(
        rl["validation_connectives"], "config.rl.validation_connectives"
    )
    test_connectives = _string_tuple(
        rl["test_connectives"], "config.rl.test_connectives"
    )
    connective_sets = tuple(
        map(set, (train_connectives, validation_connectives, test_connectives))
    )
    if any(
        connective_sets[left] & connective_sets[right]
        for left, right in ((0, 1), (0, 2), (1, 2))
    ):
        raise ConfigError("rl connective splits must be pairwise disjoint")

    return AppConfig(
        experiment=ExperimentConfig(
            stage=_string(experiment["stage"], "config.experiment.stage"),
            minimum_solved=_positive_integer(
                experiment["minimum_solved"],
                "config.experiment.minimum_solved",
            ),
        ),
        model=ModelConfig(
            name=_string(model["name"], "config.model.name"),
            device=_string(model["device"], "config.model.device"),
            hidden_layer=_integer(model["hidden_layer"], "config.model.hidden_layer"),
            max_new_tokens=_positive_integer(
                model["max_new_tokens"], "config.model.max_new_tokens"
            ),
            timeout_seconds=_positive_number(
                model["timeout_seconds"], "config.model.timeout_seconds"
            ),
            do_sample=_boolean(model["do_sample"], "config.model.do_sample"),
            temperature=_positive_number(
                model["temperature"], "config.model.temperature"
            ),
            top_p=_probability(model["top_p"], "config.model.top_p"),
        ),
        benchmark=BenchmarkConfig(
            corpus=_path(benchmark["corpus"], "config.benchmark.corpus", base)
        ),
        search=SearchConfig(
            top_k=_positive_integer(search["top_k"], "config.search.top_k"),
            max_steps=_positive_integer(search["max_steps"], "config.search.max_steps"),
            stuck_after=_positive_integer(search["stuck_after"], "config.search.stuck_after"),
        ),
        analogy=AnalogyConfig(
            enabled=_boolean(analogy["enabled"], "config.analogy.enabled"),
            samples=_positive_integer(analogy["samples"], "config.analogy.samples"),
        ),
        rl=RLConfig(
            enabled=_boolean(rl["enabled"], "config.rl.enabled"),
            algorithm=_string(rl["algorithm"], "config.rl.algorithm"),
            rollouts_per_state=_positive_integer(
                rl["rollouts_per_state"], "config.rl.rollouts_per_state"
            ),
            tactics_per_rollout=_positive_integer(
                rl["tactics_per_rollout"], "config.rl.tactics_per_rollout"
            ),
            hidden_width=_positive_integer(
                rl["hidden_width"], "config.rl.hidden_width"
            ),
            max_delta_norm=_positive_number(
                rl["max_delta_norm"], "config.rl.max_delta_norm"
            ),
            initial_log_scale=_finite_number(
                rl["initial_log_scale"], "config.rl.initial_log_scale"
            ),
            learning_rate=_positive_number(
                rl["learning_rate"], "config.rl.learning_rate"
            ),
            entropy_weight=_nonnegative_number(
                rl["entropy_weight"], "config.rl.entropy_weight"
            ),
            max_gradient_norm=_positive_number(
                rl["max_gradient_norm"], "config.rl.max_gradient_norm"
            ),
            completion_reward=completion_reward,
            progress_reward=progress_reward,
            rejection_reward=rejection_reward,
            max_rescue_verifier_calls=_positive_integer(
                rl["max_rescue_verifier_calls"],
                "config.rl.max_rescue_verifier_calls",
            ),
            epochs=_positive_integer(rl["epochs"], "config.rl.epochs"),
            checkpoint_interval=_positive_integer(
                rl["checkpoint_interval"], "config.rl.checkpoint_interval"
            ),
            checkpoint=_optional_path(
                rl["checkpoint"], "config.rl.checkpoint", base
            ),
            train_connectives=train_connectives,
            validation_connectives=validation_connectives,
            test_connectives=test_connectives,
        ),
        run=RunConfig(
            output_dir=_path(run["output_dir"], "config.run.output_dir", base),
            seed=_integer(run["seed"], "config.run.seed"),
        ),
        data=DataConfig(root=_path(data["root"], "config.data.root", base)),
    )


__all__ = [
    "AnalogyConfig",
    "AppConfig",
    "BenchmarkConfig",
    "ConfigError",
    "DataConfig",
    "ExperimentConfig",
    "ModelConfig",
    "RLConfig",
    "RunConfig",
    "SearchConfig",
    "load_config",
]
