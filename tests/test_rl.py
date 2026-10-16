import pytest
import torch

from analogy.records import Embedding
from analogy.rescue import PolicyActionRecord, RescueRollout, RescueTermination
from analogy.rl import (
    PolicyAction, StoryDiversitySample, TrainingSample, centered_advantages,
    reinforce_step, story_diversity,
)


class _Policy(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.weight = torch.nn.Parameter(torch.tensor(0.0))


class _CountingSGD(torch.optim.SGD):
    def __init__(self, params):
        super().__init__(params, lr=0.1)
        self.steps = 0

    def step(self, closure=None):
        self.steps += 1
        return super().step(closure)


def _sample(policy, state_id, reward, coefficient):
    rollout = RescueRollout(
        f"rollout-{state_id}-{coefficient}", state_id,
        PolicyActionRecord(0, 0.0, 0.0, 0.0), (), (), RescueTermination.EXHAUSTED,
    )
    action = PolicyAction(
        torch.zeros(1), policy.weight * coefficient,
        policy.weight * 0.0 + 1.0,
    )
    return TrainingSample(rollout, action, reward)


def test_rewards_are_centered_separately_and_each_group_steps_once():
    policy = _Policy()
    optimizer = _CountingSGD(policy.parameters())
    first = tuple(_sample(policy, "problem-a:state", reward, coefficient)
                  for reward, coefficient in zip((1.0, 0.0, -1.0), (1.0, 0.0, -1.0)))
    assert centered_advantages(first).tolist() == pytest.approx([1.0, 0.0, -1.0])
    reinforce_step(policy, optimizer, first, 0.0, 10.0)

    second = tuple(_sample(policy, "problem-b:state", reward, coefficient)
                   for reward, coefficient in zip((0.4, 0.2), (1.0, -1.0)))
    assert centered_advantages(second).tolist() == pytest.approx([0.1, -0.1])
    reinforce_step(policy, optimizer, second, 0.0, 10.0)
    assert optimizer.steps == 2


def test_singleton_and_mixed_state_updates_are_rejected():
    policy = _Policy()
    optimizer = _CountingSGD(policy.parameters())
    sample = _sample(policy, "a", 1.0, 1.0)
    with pytest.raises(ValueError, match="at least two"):
        reinforce_step(policy, optimizer, (sample,), 0.0, 1.0)
    with pytest.raises(ValueError, match="one problem state"):
        reinforce_step(policy, optimizer, (sample, _sample(policy, "b", 0.0, -1.0)), 0.0, 1.0)
    assert optimizer.steps == 0


def test_story_diversity_normalizes_exact_text_and_computes_cosine_distance():
    samples = (
        StoryDiversitySample("state-a", "  Red   key ", "exact_local"),
        StoryDiversitySample("state-a", "red key", "exact_local"),
        StoryDiversitySample("state-a", "blue door", "split"),
        StoryDiversitySample("singleton", "one", "exact_local"),
    )
    vectors = {
        "red key": Embedding("fake", (1.0, 0.0)),
        "blue door": Embedding("fake", (0.0, 1.0)),
        "one": Embedding("fake", (1.0, 1.0)),
    }
    metrics = story_diversity(samples, vectors.__getitem__)
    assert metrics.states == 1
    assert metrics.exact_distinct_ratio == pytest.approx(2 / 3)
    assert metrics.mean_pairwise_distance == pytest.approx(2 / 3)
    assert metrics.operator_diversity_ratio == 1.0
