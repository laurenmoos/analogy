"""Trainable policy primitives for generic proof-state rescue."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Sequence

import torch

from .records import TacticStatus
from .rescue import ProofRepresentation, RescueRollout, RescueTermination


@dataclass(frozen=True, slots=True)
class PolicyAction:
    delta: torch.Tensor
    log_probability: torch.Tensor
    entropy: torch.Tensor

    def __post_init__(self) -> None:
        if not isinstance(self.delta, torch.Tensor) or self.delta.ndim != 1:
            raise ValueError("delta must be a one-dimensional Torch tensor")
        if not isinstance(self.log_probability, torch.Tensor) or self.log_probability.ndim != 0:
            raise ValueError("log_probability must be a scalar Torch tensor")
        if not isinstance(self.entropy, torch.Tensor) or self.entropy.ndim != 0:
            raise ValueError("entropy must be a scalar Torch tensor")
        if not torch.isfinite(self.delta).all():
            raise ValueError("delta must be finite")
        if not torch.isfinite(self.log_probability):
            raise ValueError("log_probability must be finite")
        if not torch.isfinite(self.entropy):
            raise ValueError("entropy must be finite")


class DirectRescuePolicy(torch.nn.Module):
    def __init__(
        self,
        embedding_dimension: int,
        hidden_width: int,
        max_delta_norm: float,
        initial_log_scale: float,
    ) -> None:
        super().__init__()
        if (
            isinstance(embedding_dimension, bool)
            or not isinstance(embedding_dimension, int)
            or embedding_dimension <= 0
        ):
            raise ValueError("embedding_dimension must be a positive integer")
        if (
            isinstance(hidden_width, bool)
            or not isinstance(hidden_width, int)
            or hidden_width <= 0
        ):
            raise ValueError("hidden_width must be a positive integer")
        if (
            isinstance(max_delta_norm, bool)
            or not isinstance(max_delta_norm, (int, float))
            or not math.isfinite(float(max_delta_norm))
            or float(max_delta_norm) <= 0.0
        ):
            raise ValueError("max_delta_norm must be finite and positive")
        if (
            isinstance(initial_log_scale, bool)
            or not isinstance(initial_log_scale, (int, float))
            or not math.isfinite(float(initial_log_scale))
        ):
            raise ValueError("initial_log_scale must be finite")
        self.embedding_dimension = embedding_dimension
        self.body = torch.nn.Sequential(
            torch.nn.Linear(embedding_dimension * 2, hidden_width),
            torch.nn.Tanh(),
        )
        self.mean = torch.nn.Linear(hidden_width, embedding_dimension)
        self.log_scale = torch.nn.Linear(hidden_width, embedding_dimension)
        torch.nn.init.constant_(self.log_scale.bias, float(initial_log_scale))
        self.max_delta_norm = float(max_delta_norm)

    def sample(
        self, representation: ProofRepresentation, generator: torch.Generator
    ) -> PolicyAction:
        if not isinstance(representation, ProofRepresentation):
            raise ValueError("representation must be a ProofRepresentation")
        if representation.dimension != self.embedding_dimension:
            raise ValueError("proof representation dimension does not match policy")
        if not isinstance(generator, torch.Generator):
            raise ValueError("generator must be a torch.Generator")
        parameter = next(self.parameters())
        if torch.device(generator.device) != parameter.device:
            raise ValueError("generator device must match policy device")
        inputs = torch.tensor(
            representation.language_embedding.values
            + representation.structure_embedding.values,
            dtype=parameter.dtype,
            device=parameter.device,
        )
        hidden = self.body(inputs)
        mean = self.mean(hidden).float()
        log_scale = self.log_scale(hidden).float().clamp(-6.0, 2.0)
        if not torch.isfinite(mean).all() or not torch.isfinite(log_scale).all():
            raise ValueError("policy distribution parameters must be finite")
        noise = torch.randn(
            mean.shape,
            generator=generator,
            device=mean.device,
            dtype=torch.float32,
        )
        raw = mean + log_scale.exp() * noise
        if not torch.isfinite(raw).all():
            raise ValueError("sampled policy action must be finite")
        delta = _clip_vector_norm(raw, self.max_delta_norm)
        distribution = torch.distributions.Normal(mean, log_scale.exp())
        return PolicyAction(
            delta,
            distribution.log_prob(raw.detach()).sum(),
            distribution.entropy().sum(),
        )


def _clip_vector_norm(vector: torch.Tensor, maximum: float) -> torch.Tensor:
    norm = torch.linalg.vector_norm(vector)
    scale = torch.clamp(
        torch.tensor(maximum, device=vector.device, dtype=vector.dtype)
        / norm.clamp_min(torch.finfo(vector.dtype).tiny),
        max=1.0,
    )
    return vector * scale


@dataclass(frozen=True, slots=True)
class RewardWeights:
    completion: float
    progress: float
    rejection: float

    def __post_init__(self) -> None:
        values = (self.completion, self.progress, self.rejection)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in values
        ):
            raise ValueError("reward weights must be finite numbers")
        object.__setattr__(self, "completion", float(self.completion))
        object.__setattr__(self, "progress", float(self.progress))
        object.__setattr__(self, "rejection", float(self.rejection))
        if not self.completion > self.progress >= 0.0 > self.rejection:
            raise ValueError(
                "reward weights require completion > progress >= 0 > rejection"
            )


@dataclass(frozen=True, slots=True)
class RewardBreakdown:
    completion: float = 0.0
    progress: float = 0.0
    rejection: float = 0.0

    def __post_init__(self) -> None:
        for name in ("completion", "progress", "rejection"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"{name} reward must be finite")
            object.__setattr__(self, name, float(value))

    @property
    def total(self) -> float:
        return self.completion + self.progress + self.rejection


def verifier_reward(
    rollout: RescueRollout, weights: RewardWeights
) -> RewardBreakdown:
    if not isinstance(rollout, RescueRollout):
        raise ValueError("rollout must be a RescueRollout")
    if not isinstance(weights, RewardWeights):
        raise ValueError("weights must be RewardWeights")
    _validate_rollout_termination(rollout)
    if rollout.termination is RescueTermination.COMPLETE:
        return RewardBreakdown(completion=weights.completion)
    if rollout.termination is RescueTermination.PROGRESS:
        return RewardBreakdown(progress=weights.progress)
    if rollout.termination is RescueTermination.REJECTED:
        return RewardBreakdown(rejection=weights.rejection)
    return RewardBreakdown()


def _validate_rollout_termination(rollout: RescueRollout) -> None:
    has_complete = any(
        item.result.status is TacticStatus.COMPLETE for item in rollout.attempts
    )
    has_progress = any(
        item.result.status is TacticStatus.ACCEPTED and item.discarded is None
        for item in rollout.attempts
    )
    all_rejected = bool(rollout.attempts) and all(
        item.result.status is TacticStatus.ERROR for item in rollout.attempts
    )
    valid = {
        RescueTermination.COMPLETE: has_complete,
        RescueTermination.PROGRESS: has_progress and not has_complete,
        RescueTermination.REJECTED: all_rejected,
    }
    expected = valid.get(rollout.termination)
    if expected is False:
        raise ValueError("rollout termination contradicts its attempts")
    if rollout.termination not in valid and (has_complete or has_progress):
        raise ValueError("rollout termination contradicts its attempts")


@dataclass(frozen=True, slots=True)
class TrainingSample:
    rollout: RescueRollout
    action: PolicyAction
    reward: float

    def __post_init__(self) -> None:
        if not isinstance(self.rollout, RescueRollout):
            raise ValueError("rollout must be a RescueRollout")
        if not isinstance(self.action, PolicyAction):
            raise ValueError("action must be a PolicyAction")
        if (
            isinstance(self.reward, bool)
            or not isinstance(self.reward, (int, float))
            or not math.isfinite(float(self.reward))
        ):
            raise ValueError("reward must be finite")
        object.__setattr__(self, "reward", float(self.reward))


@dataclass(frozen=True, slots=True)
class TrainingMetrics:
    loss: float
    mean_reward: float
    gradient_norm: float

    def __post_init__(self) -> None:
        for name in ("loss", "mean_reward", "gradient_norm"):
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError(f"{name} must be finite")


def reinforce_step(
    policy: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    samples: Sequence[TrainingSample],
    entropy_weight: float,
    max_gradient_norm: float,
) -> TrainingMetrics:
    normalized = tuple(samples)
    if not normalized:
        raise ValueError("training samples must be non-empty")
    if not all(isinstance(sample, TrainingSample) for sample in normalized):
        raise ValueError("samples must contain only TrainingSample values")
    if (
        isinstance(entropy_weight, bool)
        or not isinstance(entropy_weight, (int, float))
        or not math.isfinite(float(entropy_weight))
        or entropy_weight < 0.0
    ):
        raise ValueError("entropy_weight must be finite and non-negative")
    if (
        isinstance(max_gradient_norm, bool)
        or not isinstance(max_gradient_norm, (int, float))
        or not math.isfinite(float(max_gradient_norm))
        or max_gradient_norm <= 0.0
    ):
        raise ValueError("max_gradient_norm must be finite and positive")
    if any(not sample.action.log_probability.requires_grad for sample in normalized):
        raise ValueError("action log probabilities must retain a gradient graph")
    rewards = torch.tensor(
        [sample.reward for sample in normalized], dtype=torch.float32
    )
    advantages = rewards - rewards.mean() if len(normalized) > 1 else rewards
    log_probabilities = torch.stack(
        [sample.action.log_probability for sample in normalized]
    )
    entropies = torch.stack([sample.action.entropy for sample in normalized])
    loss = -(
        advantages.to(log_probabilities.device).detach() * log_probabilities
    ).mean() - float(entropy_weight) * entropies.mean()
    if not bool(torch.isfinite(loss).item()):
        raise ValueError("training loss must be finite")
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    parameters = tuple(policy.parameters())
    gradients = tuple(
        parameter.grad for parameter in parameters if parameter.grad is not None
    )
    if not gradients:
        optimizer.zero_grad(set_to_none=True)
        raise ValueError("training loss must produce policy gradients")
    if any(not bool(torch.isfinite(gradient).all().item()) for gradient in gradients):
        optimizer.zero_grad(set_to_none=True)
        raise ValueError("policy gradients must be finite")
    gradient_norm = torch.nn.utils.clip_grad_norm_(
        parameters, float(max_gradient_norm)
    )
    if not bool(torch.isfinite(gradient_norm).item()):
        optimizer.zero_grad(set_to_none=True)
        raise ValueError("policy gradients must be finite")
    optimizer.step()
    return TrainingMetrics(
        float(loss.detach().cpu().item()),
        float(rewards.mean().item()),
        float(gradient_norm.detach().cpu().item()),
    )


def with_reward(rollout: RescueRollout, reward: float) -> RescueRollout:
    return replace(rollout, reward=reward)


__all__ = [
    "DirectRescuePolicy",
    "PolicyAction",
    "RewardBreakdown",
    "RewardWeights",
    "TrainingMetrics",
    "TrainingSample",
    "reinforce_step",
    "verifier_reward",
    "with_reward",
]
