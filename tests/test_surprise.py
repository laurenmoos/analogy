from dataclasses import replace

from analogy.surprise import (
    StopReason,
    SurpriseConfig,
    SurpriseController,
    SurpriseObservation,
    SurpriseState,
)


def config(**overrides):
    return replace(
        SurpriseConfig(
            min_iterations=1,
            max_iterations=6,
            surprise_threshold=0.2,
            surprise_patience=2,
            min_surprise_improvement=0.05,
            improvement_patience=2,
        ),
        **overrides,
    )


def observation(index, surprise, **overrides):
    return replace(
        SurpriseObservation(
            iteration_index=index,
            surprise=surprise,
            has_successor=True,
            completed=False,
            cancelled=False,
            model_stop=False,
            remaining_model_calls=4,
            remaining_lean_calls=4,
        ),
        **overrides,
    )


def test_low_surprise_stops_only_after_minimum_and_patience():
    controller = SurpriseController(config(min_iterations=2, surprise_patience=2))
    first = controller.decide(
        SurpriseState(), observation(0, 0.1, model_stop=True)
    )
    second = controller.decide(first.state, observation(1, 0.1))

    assert first.continue_loop
    assert second.reason is StopReason.SURPRISE_CONVERGED


def test_hard_budget_overrides_required_minimum():
    result = SurpriseController(config(min_iterations=3)).decide(
        SurpriseState(), observation(0, 0.9, remaining_model_calls=0)
    )

    assert result.reason is StopReason.MODEL_BUDGET


def test_small_improvement_stops_after_patience():
    controller = SurpriseController(
        config(min_surprise_improvement=0.05, improvement_patience=2)
    )
    first = controller.decide(SurpriseState(), observation(0, 0.8))
    second = controller.decide(first.state, observation(1, 0.78))
    third = controller.decide(second.state, observation(2, 0.77))

    assert third.reason is StopReason.IMPROVEMENT_STALLED


def test_terminal_conditions_precede_surprise_logic():
    controller = SurpriseController(config(min_iterations=5))

    assert controller.decide(
        SurpriseState(), observation(0, 0.1, cancelled=True)
    ).reason is StopReason.CANCELLED
    assert controller.decide(
        SurpriseState(), observation(0, 0.1, completed=True)
    ).reason is StopReason.COMPLETE
    assert controller.decide(
        SurpriseState(), observation(0, None, has_successor=False)
    ).reason is StopReason.NO_SUCCESSOR
