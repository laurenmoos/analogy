from __future__ import annotations

import pytest
import torch

from analogy.records import (
    CandidateOrigin,
    Embedding,
    SearchNode,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)
from analogy.rescue import (
    PolicyActionRecord,
    ProofRepresentation,
    ProofRescueContext,
    RescueAttempt,
    RescueProposal,
    RescueRollout,
    RescueTermination,
)
from analogy.rl import (
    DirectRescuePolicy,
    PolicyAction,
    RewardWeights,
    TrainingSample,
    reinforce_step,
    verifier_reward,
    with_reward,
)


def representation(
    language=(1.0, 0.0), structure=(0.0, 1.0)
) -> ProofRepresentation:
    context = ProofRescueContext(
        SymbolicState("s", "⊢ P", "lean4"),
        SymbolicState("g", "P", "lean4"),
        (),
        (),
    )
    return ProofRepresentation(
        context,
        "language-hash",
        "structure-hash",
        Embedding("model", language),
        Embedding("model", structure),
    )


def test_policy_sample_is_seeded_bounded_and_differentiable() -> None:
    policy = DirectRescuePolicy(
        embedding_dimension=2,
        hidden_width=4,
        max_delta_norm=0.5,
        initial_log_scale=-2.0,
    )

    first = policy.sample(representation(), torch.Generator().manual_seed(9))
    second = policy.sample(representation(), torch.Generator().manual_seed(9))

    assert torch.equal(first.delta, second.delta)
    assert torch.linalg.vector_norm(first.delta) <= 0.5 + 1e-6
    assert first.log_probability.requires_grad
    assert first.entropy.requires_grad
    assert first.delta.dtype is torch.float32


def test_initial_policy_does_not_saturate_high_dimensional_actions() -> None:
    dimension = 4096
    policy = DirectRescuePolicy(dimension, 8, 2.0, -2.0)
    view = tuple(1.0 if index == 0 else 0.0 for index in range(dimension))
    proof = representation(view, view)

    actions = [
        policy.sample(proof, torch.Generator().manual_seed(seed))
        for seed in range(128)
    ]
    saturated = sum(
        float(action.delta.detach().norm()) >= 0.99 * policy.max_delta_norm
        for action in actions
    )

    assert saturated / len(actions) < 0.10


def test_action_radius_matches_delta_norm_and_retains_gradient() -> None:
    dimension = 8
    policy = DirectRescuePolicy(dimension, 4, 2.0, -2.0)
    view = tuple(1.0 if index == 0 else 0.0 for index in range(dimension))

    action = policy.sample(
        representation(view, view), torch.Generator().manual_seed(1)
    )

    assert float(action.delta.detach().norm()) == pytest.approx(
        float(action.radius.detach())
    )
    assert action.radius.ndim == 0
    assert action.raw_direction_norm.ndim == 0
    assert action.log_probability.requires_grad


@pytest.mark.parametrize(
    "kwargs",
    [
        {"embedding_dimension": True},
        {"embedding_dimension": 0},
        {"hidden_width": 0},
        {"max_delta_norm": 0.0},
        {"max_delta_norm": float("inf")},
        {"initial_log_scale": float("nan")},
    ],
)
def test_policy_rejects_invalid_construction(kwargs) -> None:
    values = {
        "embedding_dimension": 2,
        "hidden_width": 4,
        "max_delta_norm": 0.5,
        "initial_log_scale": -2.0,
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        DirectRescuePolicy(**values)


def test_policy_rejects_representation_with_wrong_dimension() -> None:
    policy = DirectRescuePolicy(3, 4, 0.5, -2.0)

    with pytest.raises(ValueError, match="dimension"):
        policy.sample(representation(), torch.Generator().manual_seed(1))


def test_policy_rejects_nonfinite_distribution_parameters() -> None:
    policy = DirectRescuePolicy(2, 4, 0.5, -2.0)
    with torch.no_grad():
        policy.mean.bias.fill_(float("nan"))

    with pytest.raises(ValueError, match="finite"):
        policy.sample(representation(), torch.Generator().manual_seed(1))


def test_policy_action_rejects_batch_or_nonfinite_tensors() -> None:
    with pytest.raises(ValueError, match="one-dimensional"):
        PolicyAction(torch.zeros((1, 2)), torch.tensor(0.0), torch.tensor(0.0))
    with pytest.raises(ValueError, match="finite"):
        PolicyAction(
            torch.tensor([float("nan")]), torch.tensor(0.0), torch.tensor(0.0)
        )


def test_policy_uses_float32_even_for_half_precision_parameters() -> None:
    policy = DirectRescuePolicy(2, 4, 0.5, -2.0).half()

    action = policy.sample(representation(), torch.Generator().manual_seed(3))

    assert action.delta.dtype is torch.float32
    assert torch.isfinite(action.log_probability)


def test_policy_rejects_generator_on_wrong_device_when_available() -> None:
    if not torch.cuda.is_available():
        pytest.skip("requires a second Torch generator device")
    policy = DirectRescuePolicy(2, 4, 0.5, -2.0).cuda()

    with pytest.raises(ValueError, match="generator device"):
        policy.sample(representation(), torch.Generator(device="cpu"))


def rollout(termination, *, tactic="simp", predicted="smaller goal", delta_norm=0.0):
    selected = TacticCandidate(tactic, 1.0, CandidateOrigin.ANALOGICAL)
    proposal = RescueProposal(selected, predicted)
    attempts = ()
    proposals = ()
    if termination is RescueTermination.COMPLETE:
        result = TacticResult(selected, TacticStatus.COMPLETE, proof="by simp")
        attempts = (RescueAttempt(proposal, result),)
        proposals = (proposal,)
    elif termination is RescueTermination.PROGRESS:
        result = TacticResult(
            selected, TacticStatus.ACCEPTED, SymbolicState("next", "⊢ Q", "lean4")
        )
        attempts = (RescueAttempt(proposal, result),)
        proposals = (proposal,)
    elif termination is RescueTermination.REJECTED:
        result = TacticResult(selected, TacticStatus.ERROR)
        attempts = (RescueAttempt(proposal, result),)
        proposals = (proposal,)
    return RescueRollout(
        "r",
        "s",
        PolicyActionRecord(1, delta_norm, 0.0, 0.0),
        proposals,
        attempts,
        termination,
    )


def weights():
    return RewardWeights(completion=1.0, progress=0.1, rejection=-0.1)


@pytest.mark.parametrize(
    ("termination", "expected"),
    [
        (RescueTermination.COMPLETE, 1.0),
        (RescueTermination.PROGRESS, 0.1),
        (RescueTermination.REJECTED, -0.1),
        (RescueTermination.EXHAUSTED, 0.0),
        (RescueTermination.BUDGET_EXHAUSTED, 0.0),
    ],
)
def test_verifier_reward_is_determined_only_by_lean_outcome(termination, expected):
    assert verifier_reward(rollout(termination), weights()).total == expected


def test_verifier_reward_ignores_text_action_size_and_domain_words() -> None:
    first = rollout(
        RescueTermination.REJECTED,
        tactic="simp",
        predicted="small goal",
        delta_norm=0.0,
    )
    second = rollout(
        RescueTermination.REJECTED,
        tactic="imagine a planetary orbit then exact h",
        predicted="a long poetic physics prediction " * 20,
        delta_norm=99.0,
    )

    assert verifier_reward(first, weights()) == verifier_reward(second, weights())


def test_verifier_reward_revalidates_mutated_terminal_classification() -> None:
    invalid = rollout(RescueTermination.REJECTED)
    object.__setattr__(invalid, "termination", RescueTermination.COMPLETE)

    with pytest.raises(ValueError, match="contradicts"):
        verifier_reward(invalid, weights())


class OneParameterPolicy(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.theta = torch.nn.Parameter(torch.tensor(0.0))

    def action(self, *, scale=1.0, entropy=None):
        entropy_value = self.theta * 0.0 if entropy is None else entropy
        log_probability = self.theta if scale == 1.0 else self.theta * scale
        return PolicyAction(
            torch.zeros(1), log_probability, entropy_value
        )


def training_sample(policy, reward, *, scale=1.0, entropy=None):
    return TrainingSample(
        rollout(RescueTermination.EXHAUSTED),
        policy.action(scale=scale, entropy=entropy),
        reward,
    )


@pytest.mark.parametrize(("reward", "direction"), [(1.0, 1), (-1.0, -1)])
def test_reinforce_single_sample_moves_log_probability_with_reward(reward, direction):
    policy = OneParameterPolicy()
    action = policy.action()
    sample = TrainingSample(rollout(RescueTermination.EXHAUSTED), action, reward)
    optimizer = torch.optim.SGD(policy.parameters(), lr=0.1)

    before = float(action.log_probability.detach())
    reinforce_step(policy, optimizer, (sample,), 0.0, 1.0)

    after = float(action.log_probability.detach())
    assert (after - before) * direction > 0


def test_zero_advantage_and_entropy_leave_parameters_unchanged() -> None:
    policy = OneParameterPolicy()
    optimizer = torch.optim.SGD(policy.parameters(), lr=0.1)

    reinforce_step(
        policy, optimizer, (training_sample(policy, 0.0),), 0.0, 1.0
    )

    assert policy.theta.item() == 0.0


def test_reinforce_does_not_create_gradients_for_external_generator() -> None:
    policy = OneParameterPolicy()
    frozen_generator = torch.nn.Linear(1, 1)
    for parameter in frozen_generator.parameters():
        parameter.requires_grad_(False)

    reinforce_step(
        policy,
        torch.optim.SGD(policy.parameters(), lr=0.1),
        (training_sample(policy, 1.0),),
        0.0,
        1.0,
    )

    assert all(parameter.grad is None for parameter in frozen_generator.parameters())


def test_reinforce_rejects_empty_detached_and_nonfinite_inputs() -> None:
    policy = OneParameterPolicy()
    optimizer = torch.optim.SGD(policy.parameters(), lr=0.1)
    with pytest.raises(ValueError, match="non-empty"):
        reinforce_step(policy, optimizer, (), 0.0, 1.0)
    detached = PolicyAction(torch.zeros(1), torch.tensor(0.0), torch.tensor(0.0))
    with pytest.raises(ValueError, match="gradient"):
        reinforce_step(
            policy,
            optimizer,
            (TrainingSample(rollout(RescueTermination.EXHAUSTED), detached, 1.0),),
            0.0,
            1.0,
        )
    with pytest.raises(ValueError, match="reward"):
        TrainingSample(rollout(RescueTermination.EXHAUSTED), policy.action(), float("nan"))


def test_reinforce_rejects_nonfinite_gradients_before_step() -> None:
    policy = OneParameterPolicy()
    optimizer = torch.optim.SGD(policy.parameters(), lr=0.1)
    policy.theta.register_hook(lambda gradient: gradient * float("nan"))

    with pytest.raises(ValueError, match="gradients"):
        reinforce_step(
            policy, optimizer, (training_sample(policy, 1.0),), 0.0, 1.0
        )

    assert policy.theta.item() == 0.0


def test_reinforce_clips_gradient_before_optimizer_step() -> None:
    policy = OneParameterPolicy()
    optimizer = torch.optim.SGD(policy.parameters(), lr=1.0)

    metrics = reinforce_step(
        policy,
        optimizer,
        (training_sample(policy, 1.0, scale=100.0),),
        0.0,
        0.1,
    )

    assert policy.theta.item() == pytest.approx(0.1, abs=1e-5)
    assert metrics.gradient_norm == pytest.approx(100.0)


def test_with_reward_returns_new_scalar_rollout_without_graph() -> None:
    original = rollout(RescueTermination.PROGRESS)

    rewarded = with_reward(original, 0.1)

    assert rewarded.reward == 0.1
    assert original.reward is None
