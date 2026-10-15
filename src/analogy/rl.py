"""Trainable policy primitives for generic proof-state rescue."""

from __future__ import annotations

import math
from dataclasses import dataclass

import torch

from .rescue import ProofRepresentation


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


__all__ = ["DirectRescuePolicy", "PolicyAction"]
