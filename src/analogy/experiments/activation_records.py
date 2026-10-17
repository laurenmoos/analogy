"""Strict configuration and serializable records for activation analogy runs."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
from enum import Enum
from typing import Any

import yaml

from ..config import AppConfig, ConfigError, _boolean, _finite_number, _integer, _mapping, _path, _positive_integer, _positive_number, _string, _validate_keys, load_config
from ..records import SymbolicState


class ActivationCondition(str, Enum):
    NEUTRAL = "neutral"
    PROMPTED = "prompted_analogy"
    SELECTED = "activation_selected"
    SHUFFLED = "shuffled_analogy"


@dataclass(frozen=True, slots=True)
class ActivationEvalConfig:
    base_config_path: Path
    base_config: AppConfig
    problem_ids: tuple[str, ...]
    seed_start: int
    seed_count: int
    domain_cues: tuple[str, ...]
    analogy_candidates: int
    tactics_per_slate: int
    tactic_union_limit: int
    activation_layer_count: int
    score_weights: tuple[float, float, float]
    variance_floor: float
    persist_full_activations: bool
    output_namespace: str
    sampling_temperature: float = 0.8
    sampling_top_p: float = 0.95

    def __post_init__(self):
        if _finite(self.sampling_temperature, "sampling_temperature") <= 0:
            raise ValueError("sampling_temperature must be positive")
        if not 0 < _finite(self.sampling_top_p, "sampling_top_p") <= 1:
            raise ValueError("sampling_top_p must be in (0, 1]")

    def generation_config(self, app: AppConfig) -> AppConfig:
        return replace(app, model=replace(app.model, do_sample=True,
                       temperature=self.sampling_temperature, top_p=self.sampling_top_p))

    @property
    def seeds(self) -> tuple[int, ...]:
        return tuple(range(self.seed_start, self.seed_start + self.seed_count))


@dataclass(frozen=True, slots=True)
class AnchorRecord:
    problem_id: str
    seed: int
    theorem: str
    current_state: SymbolicState
    predecessor_state: SymbolicState | None
    accepted_prefix: tuple[str, ...]
    failed_tactics: tuple[str, ...]
    diagnostics: tuple[str, ...]
    state_hash: str
    anchor_hash: str
    no_stuck_anchor: bool

    def __post_init__(self) -> None:
        _text(self.problem_id, "problem_id")
        _nonnegative_int(self.seed, "seed")
        _text(self.theorem, "theorem")
        if not isinstance(self.current_state, SymbolicState):
            raise ValueError("current_state must be a SymbolicState")
        if self.predecessor_state is not None and not isinstance(self.predecessor_state, SymbolicState):
            raise ValueError("predecessor_state must be a SymbolicState or None")
        for field in ("accepted_prefix", "failed_tactics", "diagnostics"):
            object.__setattr__(self, field, _text_tuple(getattr(self, field), field))
        _text(self.state_hash, "state_hash")
        _text(self.anchor_hash, "anchor_hash")
        if not isinstance(self.no_stuck_anchor, bool):
            raise ValueError("no_stuck_anchor must be a boolean")


@dataclass(frozen=True, slots=True)
class AnalogyCandidateRecord:
    candidate_id: str
    source_domain: str
    analogy: str
    role_mapping: tuple[tuple[str, str], ...]
    limitation: str
    tactics: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in ("candidate_id", "source_domain", "analogy", "limitation"):
            _text(getattr(self, field), field)
        if isinstance(self.role_mapping, (str, bytes)) or any(isinstance(pair, (str, bytes)) for pair in self.role_mapping):
            raise ValueError("role_mapping must contain non-empty string pairs")
        normalized = tuple(tuple(pair) for pair in self.role_mapping)
        if any(len(pair) != 2 or not all(isinstance(item, str) and item.strip() for item in pair) for pair in normalized):
            raise ValueError("role_mapping must contain non-empty string pairs")
        object.__setattr__(self, "role_mapping", normalized)
        object.__setattr__(self, "tactics", _text_tuple(self.tactics, "tactics"))


@dataclass(frozen=True, slots=True)
class CandidateMeasurement:
    candidate_id: str
    reasoning_displacement: float | None
    causal_effect: float | None
    previous_state_value: float | None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.candidate_id, "candidate_id")
        for field in ("reasoning_displacement", "causal_effect", "previous_state_value"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _finite(value, field))
        object.__setattr__(self, "diagnostics", _text_tuple(self.diagnostics, "diagnostics"))


@dataclass(frozen=True, slots=True)
class ConditionOutcome:
    condition: ActivationCondition
    generated_tactics: tuple[str, ...]
    verifier_results: tuple[str, ...]
    successor_state_hashes: tuple[str, ...]
    completed: bool
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.condition, ActivationCondition):
            raise ValueError("condition must be an ActivationCondition")
        for field in ("generated_tactics", "verifier_results", "successor_state_hashes", "diagnostics"):
            object.__setattr__(self, field, _text_tuple(getattr(self, field), field))
        if not isinstance(self.completed, bool):
            raise ValueError("completed must be a boolean")


@dataclass(frozen=True, slots=True)
class ActivationTrialRecord:
    problem_id: str
    seed: int
    anchor: AnchorRecord
    candidates: tuple[AnalogyCandidateRecord, ...]
    measurements: tuple[CandidateMeasurement, ...]
    outcomes: tuple[ConditionOutcome, ...]

    def __post_init__(self) -> None:
        _text(self.problem_id, "problem_id")
        _nonnegative_int(self.seed, "seed")
        if not isinstance(self.anchor, AnchorRecord):
            raise ValueError("anchor must be an AnchorRecord")
        for field, expected in (("candidates", AnalogyCandidateRecord), ("measurements", CandidateMeasurement), ("outcomes", ConditionOutcome)):
            values = tuple(getattr(self, field))
            if any(not isinstance(value, expected) for value in values):
                raise ValueError(f"{field} must contain {expected.__name__} records")
            object.__setattr__(self, field, values)


def load_activation_eval_config(path: Path) -> ActivationEvalConfig:
    """Load the strict standalone activation evaluation profile."""
    config_path = Path(path).expanduser().resolve()
    try:
        root = _mapping(yaml.safe_load(config_path.read_text(encoding="utf-8")), "config")
    except OSError as error:
        raise ConfigError(f"activation config could not be read: {config_path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"activation config is not valid YAML: {error}") from error
    keys = frozenset({"schema_version", "base_config", "problem_ids", "seed_start", "seed_count", "domain_cues", "analogy_candidates", "tactics_per_slate", "tactic_union_limit", "activation_layer_count", "score_weights", "variance_floor", "persist_full_activations", "output_namespace"})
    decoding = _mapping(root.pop("decoding", {"temperature": 0.8, "top_p": 0.95}), "config.decoding")
    _validate_keys(decoding, frozenset({"temperature", "top_p"}), "config.decoding")
    temperature = _positive_number(decoding["temperature"], "config.decoding.temperature")
    top_p = _positive_number(decoding["top_p"], "config.decoding.top_p")
    if top_p > 1:
        raise ConfigError("config.decoding.top_p must not exceed one")
    _validate_keys(root, keys, "config")
    if _string(root["schema_version"], "config.schema_version") != "activation-analogy-v1":
        raise ConfigError("config.schema_version must be activation-analogy-v1")
    base_config_path = _path(root["base_config"], "config.base_config", config_path.parent)
    base_config = load_config(base_config_path)
    problem_ids = _string_list(root["problem_ids"], "config.problem_ids")
    if len(set(problem_ids)) != len(problem_ids):
        raise ConfigError("config.problem_ids must not contain duplicates")
    domain_cues = _string_list(root["domain_cues"], "config.domain_cues")
    if len(set(domain_cues)) != len(domain_cues):
        raise ConfigError("config.domain_cues must not contain duplicates")
    weights_raw = root["score_weights"]
    if not isinstance(weights_raw, list) or len(weights_raw) != 3:
        raise ConfigError("config.score_weights must be a list of three finite numbers")
    weights = tuple(_finite_number(item, f"config.score_weights[{index}]") for index, item in enumerate(weights_raw))
    tactics_per_slate = _positive_integer(root["tactics_per_slate"], "config.tactics_per_slate")
    union_limit = _positive_integer(root["tactic_union_limit"], "config.tactic_union_limit")
    if union_limit < tactics_per_slate:
        raise ConfigError("config.tactic_union_limit must be at least tactics_per_slate")
    return ActivationEvalConfig(
        base_config_path=base_config_path, base_config=base_config,
        problem_ids=problem_ids, seed_start=_nonnegative_int(root["seed_start"], "config.seed_start"),
        seed_count=_positive_integer(root["seed_count"], "config.seed_count"), domain_cues=domain_cues,
        analogy_candidates=_positive_integer(root["analogy_candidates"], "config.analogy_candidates"),
        tactics_per_slate=tactics_per_slate, tactic_union_limit=union_limit,
        activation_layer_count=_positive_integer(root["activation_layer_count"], "config.activation_layer_count"),
        score_weights=weights, variance_floor=_positive_number(root["variance_floor"], "config.variance_floor"),
        persist_full_activations=_boolean(root["persist_full_activations"], "config.persist_full_activations"),
        output_namespace=_string(root["output_namespace"], "config.output_namespace"),
        sampling_temperature=temperature, sampling_top_p=top_p,
    )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _text_tuple(values: object, field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must be a sequence of strings")
    try:
        result = tuple(values)  # type: ignore[arg-type]
    except TypeError as error:
        raise ValueError(f"{field} must be a sequence of strings") from error
    return tuple(_text(value, field) for value in result)


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return float(value)


def _nonnegative_int(value: Any, field: str) -> int:
    number = _integer(value, field)
    if number < 0:
        raise ConfigError(f"{field} must be non-negative")
    return number


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{field} must be a non-empty list of strings")
    return tuple(_string(item, f"{field}[{index}]") for index, item in enumerate(value))


__all__ = ["ActivationCondition", "ActivationEvalConfig", "AnchorRecord", "AnalogyCandidateRecord", "CandidateMeasurement", "ConditionOutcome", "ActivationTrialRecord", "load_activation_eval_config"]
