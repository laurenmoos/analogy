"""Shared execution boundary for generated, parsed prompt responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from ..utils.records import Message
from ..solver.rescue import GenerationAttempt, GenerationStatus


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class PromptRun(Generic[T]):
    value: T | None
    attempts: tuple[GenerationAttempt, ...]
    error: Exception | None = None
    cancelled: bool = False


class PromptRunner:
    def __init__(self, provider: Any) -> None:
        self.provider = provider

    def run(
        self,
        messages: tuple[Message, ...],
        *,
        parser: Callable[[str], T],
        seed: int,
        max_attempts: int = 1,
        repair_messages: Callable[
            [tuple[Message, ...], str, Exception], tuple[Message, ...]
        ] | None = None,
        generate: Callable[[tuple[Message, ...], int], str] | None = None,
        cancellation: Any = None,
        initial_repair: bool = False,
        schema_errors: tuple[type[Exception], ...] = (ValueError,),
        repair_errors: tuple[type[Exception], ...] | None = None,
    ) -> PromptRun[T]:
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts <= 0:
            raise ValueError("max_attempts must be a positive integer")
        active_messages = messages
        attempts: list[GenerationAttempt] = []
        invoke = generate or (
            lambda active, active_seed: self.provider.generate(active, seed=active_seed)
        )
        previous_invalid_raw: str | None = None
        for index in range(max_attempts):
            if cancellation is not None and cancellation.is_cancelled():
                return PromptRun(None, tuple(attempts), cancelled=True)
            active_seed = seed + index
            repair = initial_repair or bool(index)
            try:
                raw = invoke(active_messages, active_seed)
            except Exception as error:
                attempts.append(GenerationAttempt(
                    active_seed, "", GenerationStatus.RUNTIME_ERROR,
                    repair=repair, error_kind=type(error).__name__,
                    diagnostic=str(error) or type(error).__name__,
                ))
                return PromptRun(None, tuple(attempts), error)
            try:
                value = parser(raw)
            except schema_errors as error:
                attempts.append(GenerationAttempt(
                    active_seed, raw, GenerationStatus.INVALID,
                    repair=repair, error_kind="schema", diagnostic=str(error),
                ))
                if previous_invalid_raw == raw:
                    return PromptRun(None, tuple(attempts), error)
                previous_invalid_raw = raw
                retryable = repair_errors or schema_errors
                if (
                    not isinstance(error, retryable)
                    or repair_messages is None
                    or index + 1 >= max_attempts
                ):
                    return PromptRun(None, tuple(attempts), error)
                active_messages = repair_messages(messages, raw, error)
                continue
            attempts.append(GenerationAttempt(
                active_seed, raw, GenerationStatus.VALID, repair=repair,
            ))
            return PromptRun(value, tuple(attempts))
        raise AssertionError("positive prompt attempt loop did not return")


__all__ = ["PromptRun", "PromptRunner"]
