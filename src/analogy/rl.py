"""Trainable policy primitives for generic proof-state rescue."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable, Sequence

import torch

from .records import Embedding, TacticStatus
from .rescue import ProofRepresentation, RescueRollout, RescueTermination


@dataclass(frozen=True, slots=True)
class PolicyAction:
    delta: torch.Tensor
    log_probability: torch.Tensor
    entropy: torch.Tensor
    radius: torch.Tensor | None = None
    raw_direction_norm: torch.Tensor | None = None

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
        radius = self.radius
        if radius is None:
            radius = torch.linalg.vector_norm(self.delta)
            object.__setattr__(self, "radius", radius)
        raw_direction_norm = self.raw_direction_norm
        if raw_direction_norm is None:
            raw_direction_norm = torch.linalg.vector_norm(self.delta)
            object.__setattr__(self, "raw_direction_norm", raw_direction_norm)
        for value, name in (
            (radius, "radius"),
            (raw_direction_norm, "raw_direction_norm"),
        ):
            if not isinstance(value, torch.Tensor) or value.ndim != 0:
                raise ValueError(f"{name} must be a scalar Torch tensor")
            if not torch.isfinite(value):
                raise ValueError(f"{name} must be finite")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")


class DirectRescuePolicy(torch.nn.Module):
    schema_version = 3

    def __init__(
        self,
        embedding_dimension: int,
        hidden_width: int,
        max_delta_norm: float,
        initial_log_scale: float,
        *,
        scalar_feature_count: int = 4,
    ) -> None:
        super().__init__()
        if (isinstance(scalar_feature_count, bool)
                or not isinstance(scalar_feature_count, int) or scalar_feature_count < 0):
            raise ValueError("scalar_feature_count must be a non-negative integer")
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
        self.scalar_feature_count = scalar_feature_count
        self.body = torch.nn.Sequential(
            torch.nn.Linear(embedding_dimension * 4 + scalar_feature_count, hidden_width),
            torch.nn.Tanh(),
        )
        self.mean = torch.nn.Linear(hidden_width, embedding_dimension)
        self.log_scale = torch.nn.Linear(hidden_width, embedding_dimension)
        torch.nn.init.constant_(self.log_scale.bias, float(initial_log_scale))
        self.radius_mean = torch.nn.Linear(hidden_width, 1)
        self.radius_log_scale = torch.nn.Linear(hidden_width, 1)
        torch.nn.init.zeros_(self.radius_mean.weight)
        torch.nn.init.constant_(self.radius_mean.bias, math.log(0.1 / 0.9))
        torch.nn.init.zeros_(self.radius_log_scale.weight)
        torch.nn.init.constant_(
            self.radius_log_scale.bias, float(initial_log_scale)
        )
        self.max_delta_norm = float(max_delta_norm)

    def sample(
        self, representation: ProofRepresentation, generator: torch.Generator
    ) -> PolicyAction:
        if not isinstance(representation, ProofRepresentation):
            raise ValueError("representation must be a ProofRepresentation")
        if representation.dimension != self.embedding_dimension:
            raise ValueError("proof representation dimension does not match policy")
        if len(representation.scalar_features) != self.scalar_feature_count:
            raise ValueError("proof representation scalar feature count does not match policy")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in representation.scalar_features):
            raise ValueError("proof representation scalar features must be finite numbers")
        if not isinstance(generator, torch.Generator):
            raise ValueError("generator must be a torch.Generator")
        parameter = next(self.parameters())
        if torch.device(generator.device) != parameter.device:
            raise ValueError("generator device must match policy device")
        inputs = torch.tensor(
            representation.language_embedding.values
            + representation.structure_embedding.values
            + representation.transition_embedding.values
            + representation.analogy_embedding.values
            + representation.scalar_features,
            dtype=parameter.dtype,
            device=parameter.device,
        )
        hidden = self.body(inputs)
        mean = self.mean(hidden).float()
        log_scale = self.log_scale(hidden).float().clamp(-6.0, 2.0)
        radius_mean = self.radius_mean(hidden).float().squeeze(-1)
        radius_log_scale = self.radius_log_scale(hidden).float().squeeze(-1).clamp(-6.0, 2.0)
        if (
            not torch.isfinite(mean).all()
            or not torch.isfinite(log_scale).all()
            or not torch.isfinite(radius_mean)
            or not torch.isfinite(radius_log_scale)
        ):
            raise ValueError("policy distribution parameters must be finite")
        noise = torch.randn(
            mean.shape,
            generator=generator,
            device=mean.device,
            dtype=torch.float32,
        )
        direction_scale = log_scale.exp() / math.sqrt(self.embedding_dimension)
        raw_direction = mean + direction_scale * noise
        radius_noise = torch.randn(
            (), generator=generator, device=mean.device, dtype=torch.float32
        )
        raw_radius = radius_mean + radius_log_scale.exp() * radius_noise
        if not torch.isfinite(raw_direction).all() or not torch.isfinite(raw_radius):
            raise ValueError("sampled policy action must be finite")
        raw_direction_norm = torch.linalg.vector_norm(raw_direction)
        direction = raw_direction / raw_direction_norm.clamp_min(
            torch.finfo(raw_direction.dtype).tiny
        )
        radius = self.max_delta_norm * torch.sigmoid(raw_radius)
        delta = direction * radius
        direction_distribution = torch.distributions.Normal(mean, direction_scale)
        radius_distribution = torch.distributions.Normal(
            radius_mean, radius_log_scale.exp()
        )
        return PolicyAction(
            delta,
            direction_distribution.log_prob(raw_direction.detach()).sum()
            + radius_distribution.log_prob(raw_radius.detach()),
            direction_distribution.entropy().sum()
            + radius_distribution.entropy(),
            radius,
            raw_direction_norm,
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
    invalid_output: float = -0.05
    empty_output: float = -0.02
    hop_cost: float = 0.0

    def __post_init__(self) -> None:
        values = (
            self.completion,
            self.progress,
            self.rejection,
            self.invalid_output,
            self.empty_output,
            self.hop_cost,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in values
        ):
            raise ValueError("reward weights must be finite numbers")
        object.__setattr__(self, "completion", float(self.completion))
        object.__setattr__(self, "progress", float(self.progress))
        for name in (
            "completion",
            "progress",
            "rejection",
            "invalid_output",
            "empty_output",
            "hop_cost",
        ):
            object.__setattr__(self, name, float(getattr(self, name)))
        if not (
            self.completion > self.progress >= 0.0
            and self.rejection < 0.0
            and self.invalid_output < 0.0
            and self.empty_output < 0.0
            and 0.0 <= self.hop_cost < self.completion
        ):
            raise ValueError(
                "reward weights require ordered positive rewards, negative failure "
                "rewards, and a non-negative hop cost below completion"
            )


@dataclass(frozen=True, slots=True)
class RewardBreakdown:
    completion: float = 0.0
    progress: float = 0.0
    rejection: float = 0.0
    invalid_output: float = 0.0
    empty_output: float = 0.0
    hop_cost: float = 0.0
    surprise: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "completion",
            "progress",
            "rejection",
            "invalid_output",
            "empty_output",
            "hop_cost",
            "surprise",
        ):
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
        return (
            self.completion
            + self.progress
            + self.rejection
            + self.invalid_output
            + self.empty_output
            + self.surprise
            - self.hop_cost
        )


def verifier_reward(
    rollout: RescueRollout,
    weights: RewardWeights,
    *,
    surprise_enabled: bool = False,
) -> RewardBreakdown:
    if not isinstance(rollout, RescueRollout):
        raise ValueError("rollout must be a RescueRollout")
    if not isinstance(weights, RewardWeights):
        raise ValueError("weights must be RewardWeights")
    if not isinstance(surprise_enabled, bool):
        raise ValueError("surprise_enabled must be a bool")
    _validate_rollout_termination(rollout)
    if rollout.termination is RescueTermination.COMPLETE:
        return RewardBreakdown(completion=weights.completion)
    if rollout.termination is RescueTermination.PROGRESS:
        surprise = 0.0
        if surprise_enabled:
            distances = tuple(
                item.prediction_distance
                for item in rollout.attempts
                if item.result.status is TacticStatus.ACCEPTED
                and item.prediction_distance is not None
            )
            if distances:
                surprise = min(weights.progress, max(distances) * weights.progress)
        return RewardBreakdown(progress=weights.progress, surprise=surprise)
    if rollout.termination is RescueTermination.REJECTED:
        return RewardBreakdown(rejection=weights.rejection)
    if rollout.termination is RescueTermination.INVALID_OUTPUT:
        return RewardBreakdown(invalid_output=weights.invalid_output)
    if rollout.termination is RescueTermination.EXHAUSTED:
        return RewardBreakdown(empty_output=weights.empty_output)
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
    state_id: str | None = None

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
        state_id = self.rollout.state_id if self.state_id is None else self.state_id
        if not isinstance(state_id, str) or not state_id:
            raise ValueError("state_id must be nonblank")
        object.__setattr__(self, "state_id", state_id)


@dataclass(frozen=True, slots=True)
class TrainingMetrics:
    loss: float
    mean_reward: float
    gradient_norm: float

    def __post_init__(self) -> None:
        for name in ("loss", "mean_reward", "gradient_norm"):
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError(f"{name} must be finite")


def centered_advantages(samples: Sequence[TrainingSample]) -> torch.Tensor:
    normalized = tuple(samples)
    if len(normalized) < 2:
        raise ValueError("same-state policy updates require at least two attempts")
    if not all(isinstance(sample, TrainingSample) for sample in normalized):
        raise ValueError("samples must contain only TrainingSample values")
    if len({sample.state_id for sample in normalized}) != 1:
        raise ValueError("samples must belong to one problem state")
    rewards = torch.tensor([sample.reward for sample in normalized], dtype=torch.float32)
    return rewards - rewards.mean()


def reinforce_step(
    policy: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    samples: Sequence[TrainingSample],
    entropy_weight: float,
    max_gradient_norm: float,
) -> TrainingMetrics:
    normalized = tuple(samples)
    advantages = centered_advantages(normalized)
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


@dataclass(frozen=True, slots=True)
class StoryDiversitySample:
    state_id: str
    story: str
    operator: str

    def __post_init__(self) -> None:
        for name in ("state_id", "story", "operator"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonblank text")


@dataclass(frozen=True, slots=True)
class StoryDiversityMetrics:
    states: int
    exact_distinct_ratio: float
    mean_pairwise_distance: float
    operator_diversity_ratio: float


def _normalized_story(value: str) -> str:
    return " ".join(value.casefold().split())


def story_diversity(
    samples: Sequence[StoryDiversitySample], embed: Callable[[str], Embedding],
) -> StoryDiversityMetrics:
    normalized = tuple(samples)
    if not all(isinstance(item, StoryDiversitySample) for item in normalized):
        raise ValueError("samples must contain StoryDiversitySample values")
    grouped: dict[str, list[StoryDiversitySample]] = {}
    for item in normalized:
        grouped.setdefault(item.state_id, []).append(item)
    eligible = tuple(group for group in grouped.values() if len(group) >= 2)
    if not eligible:
        return StoryDiversityMetrics(0, 0.0, 0.0, 0.0)
    texts = {_normalized_story(item.story) for group in eligible for item in group}
    embeddings = {text: embed(text) for text in sorted(texts)}
    if any(not isinstance(value, Embedding) for value in embeddings.values()):
        raise ValueError("embed must return Embedding values")
    exact_ratios: list[float] = []
    distances: list[float] = []
    diverse_operators = 0
    for group in eligible:
        stories = tuple(_normalized_story(item.story) for item in group)
        exact_ratios.append(len(set(stories)) / len(stories))
        diverse_operators += len({item.operator for item in group}) > 1
        for left in range(len(stories)):
            for right in range(left + 1, len(stories)):
                a = embeddings[stories[left]].values
                b = embeddings[stories[right]].values
                if len(a) != len(b):
                    raise ValueError("story embeddings must have equal dimensions")
                dot = sum(x * y for x, y in zip(a, b))
                norm_a = math.sqrt(sum(x * x for x in a))
                norm_b = math.sqrt(sum(y * y for y in b))
                cosine = dot / (norm_a * norm_b) if norm_a and norm_b else 0.0
                distances.append(1.0 - max(-1.0, min(1.0, cosine)))
    return StoryDiversityMetrics(
        len(eligible), sum(exact_ratios) / len(exact_ratios),
        sum(distances) / len(distances) if distances else 0.0,
        diverse_operators / len(eligible),
    )


def with_reward(rollout: RescueRollout, reward: float) -> RescueRollout:
    return replace(rollout, reward=reward)


def discounted_iteration_samples(
    trace: object,
    actions: Sequence[PolicyAction],
    rollout: RescueRollout,
    *,
    verified_reward: float,
    discount: float,
    iteration_cost: float,
) -> tuple[TrainingSample, ...]:
    """Propagate one verified outcome back through every analogy refinement."""
    from .analogy_loop import AnalogyLoopTrace
    from .analogy_transfer_loop import GeneratedAnalogyTrace

    if not isinstance(trace, (AnalogyLoopTrace, GeneratedAnalogyTrace)):
        raise ValueError("trace must be an AnalogyLoopTrace or GeneratedAnalogyTrace")
    normalized_actions = tuple(actions)
    sample_state_ids = tuple(rollout.state_id for _ in normalized_actions)
    action_count = (sum(iteration.action is not None for iteration in trace.iterations)
                    if isinstance(trace, GeneratedAnalogyTrace) else len(trace.iterations))
    if len(normalized_actions) != action_count:
        raise ValueError("trace iterations and live actions must have equal length")
    for value, name in (
        (verified_reward, "verified_reward"),
        (discount, "discount"),
        (iteration_cost, "iteration_cost"),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    if not 0.0 < float(discount) <= 1.0:
        raise ValueError("discount must be in (0, 1]")
    if float(iteration_cost) < 0.0:
        raise ValueError("iteration_cost must be non-negative")
    if isinstance(trace, GeneratedAnalogyTrace):
        action_iterations = tuple(iteration for iteration in trace.iterations if iteration.action is not None)
        eligible = tuple(
            (action, iteration) for action, iteration in zip(normalized_actions, action_iterations)
            if iteration.training_eligible and any(
                attempt.result.status is TacticStatus.COMPLETE
                or (attempt.result.status is TacticStatus.ACCEPTED and attempt.discarded is None)
                for attempt in iteration.attempts
            )
        )
        normalized_actions = tuple(action for action, _ in eligible)
        sample_state_ids = tuple(iteration.state_before.id for _, iteration in eligible)
    count = len(normalized_actions)
    return tuple(
        TrainingSample(
            rollout,
            action,
            float(discount) ** (count - index - 1) * float(verified_reward)
            - float(iteration_cost) * (count - index - 1),
            sample_state_ids[index],
        )
        for index, action in enumerate(normalized_actions)
    )


def intrinsic_surprise_reward(
    iteration: object, *, cap: float, enabled: bool = False
) -> float:
    """Return capped surprise only for a verified, accepted transition."""
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a bool")
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(float(cap)) or cap < 0.0:
        raise ValueError("cap must be finite and non-negative")
    if not enabled:
        return 0.0
    attempts = tuple(getattr(iteration, "attempts", ()))
    has_successor = any(
        isinstance(item, object)
        and getattr(getattr(item, "result", None), "status", None) is TacticStatus.ACCEPTED
        and getattr(item, "discarded", None) is None
        for item in attempts
    )
    surprise = getattr(iteration, "surprise", None)
    if not has_successor or surprise is None:
        return 0.0
    value = float(surprise)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("iteration surprise must be finite and non-negative")
    return min(float(cap), value)


__all__ = [
    "DirectRescuePolicy",
    "PolicyAction",
    "RewardBreakdown",
    "RewardWeights",
    "StoryDiversityMetrics",
    "StoryDiversitySample",
    "TrainingMetrics",
    "TrainingSample",
    "centered_advantages",
    "discounted_iteration_samples",
    "intrinsic_surprise_reward",
    "reinforce_step",
    "story_diversity",
    "verifier_reward",
    "with_reward",
]
