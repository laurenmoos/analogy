from types import SimpleNamespace

from analogy.model.prompt_runner import GenerationStatus, PromptRunner
from analogy.utils.records import Message


class Provider:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def generate(self, messages, *, seed):
        self.calls.append((messages, seed))
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return output


def test_prompt_runner_repairs_invalid_output_and_records_both_attempts():
    provider = Provider(("bad", "42"))
    original = (Message("user", "answer"),)
    repaired = (Message("user", "repair"),)

    run = PromptRunner(provider).run(
        original,
        parser=lambda raw: int(raw),
        seed=4,
        max_attempts=2,
        repair_messages=lambda _messages, _raw, _error: repaired,
    )

    assert run.value == 42
    assert [item.status for item in run.attempts] == [
        GenerationStatus.INVALID,
        GenerationStatus.VALID,
    ]
    assert provider.calls == [(original, 4), (repaired, 5)]


def test_prompt_runner_stops_when_repair_repeats_the_same_invalid_output():
    provider = Provider(("bad", "bad", "unused"))
    original = (Message("user", "answer"),)
    repaired = (Message("user", "repair"),)

    run = PromptRunner(provider).run(
        original,
        parser=int,
        seed=4,
        max_attempts=3,
        repair_messages=lambda _messages, _raw, _error: repaired,
    )

    assert run.value is None
    assert [item.raw_text for item in run.attempts] == ["bad", "bad"]
    assert provider.calls == [(original, 4), (repaired, 5)]


def test_prompt_runner_records_runtime_failure_without_retrying():
    provider = Provider((TimeoutError("slow"), "unused"))

    run = PromptRunner(provider).run(
        (Message("user", "answer"),), parser=int, seed=2, max_attempts=2,
    )

    assert run.value is None
    assert run.attempts[0].status is GenerationStatus.RUNTIME_ERROR
    assert run.attempts[0].error_kind == "TimeoutError"
    assert len(provider.calls) == 1


def test_prompt_runner_honors_cancellation_before_generation():
    provider = Provider(("42",))

    run = PromptRunner(provider).run(
        (Message("user", "answer"),), parser=int, seed=2,
        cancellation=SimpleNamespace(is_cancelled=lambda: True),
    )

    assert run.cancelled is True
    assert run.attempts == ()
    assert provider.calls == []
