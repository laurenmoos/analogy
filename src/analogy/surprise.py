"""Pure stopping policy for the surprise-driven analogy loop."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class StopReason(str, Enum):
    CONTINUE = "continue"
    COMPLETE = "complete"
    NO_SUCCESSOR = "no_successor"
    SURPRISE_CONVERGED = "surprise_converged"
    IMPROVEMENT_STALLED = "improvement_stalled"
    MODEL_STOP = "model_stop"
    MAX_ITERATIONS = "max_iterations"
    MODEL_BUDGET = "model_budget"
    LEAN_BUDGET = "lean_budget"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SurpriseConfig:
    min_iterations: int
    max_iterations: int
    surprise_threshold: float
    surprise_patience: int
    min_surprise_improvement: float
    improvement_patience: int

    def __post_init__(self) -> None:
        integers = (
            self.min_iterations,
            self.max_iterations,
            self.surprise_patience,
            self.improvement_patience,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in integers):
            raise ValueError("surprise controller counts must be positive integers")
        if self.min_iterations > self.max_iterations:
            raise ValueError("min_iterations must not exceed max_iterations")
        for value, name in (
            (self.surprise_threshold, "surprise_threshold"),
            (self.min_surprise_improvement, "min_surprise_improvement"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if not 0.0 <= self.surprise_threshold <= 2.0:
            raise ValueError("surprise_threshold must be in [0, 2]")
        if self.min_surprise_improvement < 0.0:
            raise ValueError("min_surprise_improvement must be non-negative")


@dataclass(frozen=True, slots=True)
class SurpriseState:
    values: tuple[float, ...] = ()
    low_surprise_streak: int = 0
    low_improvement_streak: int = 0


@dataclass(frozen=True, slots=True)
class SurpriseObservation:
    iteration_index: int
    surprise: float | None
    has_successor: bool
    completed: bool
    cancelled: bool
    model_stop: bool
    remaining_model_calls: int
    remaining_lean_calls: int


@dataclass(frozen=True, slots=True)
class SurpriseDecision:
    continue_loop: bool
    reason: StopReason
    state: SurpriseState


class SurpriseController:
    def __init__(self, config: SurpriseConfig) -> None:
        if not isinstance(config, SurpriseConfig):
            raise ValueError("config must be a SurpriseConfig")
        self.config = config

    def decide(
        self, state: SurpriseState, observation: SurpriseObservation
    ) -> SurpriseDecision:
        if observation.cancelled:
            return self._stop(StopReason.CANCELLED, state)
        if observation.completed:
            return self._stop(StopReason.COMPLETE, state)
        if observation.remaining_model_calls <= 0:
            return self._stop(StopReason.MODEL_BUDGET, state)
        if observation.remaining_lean_calls <= 0:
            return self._stop(StopReason.LEAN_BUDGET, state)
        if not observation.has_successor or observation.surprise is None:
            return self._stop(StopReason.NO_SUCCESSOR, state)

        surprise = float(observation.surprise)
        if not math.isfinite(surprise) or not 0.0 <= surprise <= 2.0:
            raise ValueError("surprise must be finite and in [0, 2]")
        previous = state.values[-1] if state.values else None
        low_surprise = state.low_surprise_streak + 1 if surprise <= self.config.surprise_threshold else 0
        improvement = None if previous is None else previous - surprise
        low_improvement = (
            state.low_improvement_streak + 1
            if improvement is not None
            and improvement < self.config.min_surprise_improvement
            else 0
        )
        updated = SurpriseState(
            state.values + (surprise,), low_surprise, low_improvement
        )
        completed_iterations = observation.iteration_index + 1
        if completed_iterations >= self.config.max_iterations:
            return self._stop(StopReason.MAX_ITERATIONS, updated)
        if completed_iterations >= self.config.min_iterations:
            if low_surprise >= self.config.surprise_patience:
                return self._stop(StopReason.SURPRISE_CONVERGED, updated)
            if low_improvement >= self.config.improvement_patience:
                return self._stop(StopReason.IMPROVEMENT_STALLED, updated)
            if observation.model_stop:
                return self._stop(StopReason.MODEL_STOP, updated)
        return SurpriseDecision(True, StopReason.CONTINUE, updated)

    @staticmethod
    def _stop(reason: StopReason, state: SurpriseState) -> SurpriseDecision:
        return SurpriseDecision(False, reason, state)


__all__ = [
    "StopReason",
    "SurpriseConfig",
    "SurpriseController",
    "SurpriseDecision",
    "SurpriseObservation",
    "SurpriseState",
]
