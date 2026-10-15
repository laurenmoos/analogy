from __future__ import annotations

import pytest
import torch

from analogy.records import Embedding, SymbolicState
from analogy.rescue import ProofRepresentation, ProofRescueContext
from analogy.rl import DirectRescuePolicy, PolicyAction


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
