"""Persistent, externally stored analogy scratchpad state."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum

from ...model.prompts import prompt_catalog


_BLOCK = re.compile(r"<analogy>\s*\n(?P<body>.*?)\n\s*</analogy>", re.DOTALL)
_STEP = re.compile(r"(?P<number>[1-9][0-9]*)\.\s+(?P<text>.+)")


class AnalogyStateError(ValueError):
    """Raised when an analogy block or lifecycle transition is invalid."""


class AnalogyUpdateAction(str, Enum):
    REVISE_STEPS = "revise_steps"
    CHANGE_DOMAIN = "change_domain"


def _clean_line(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalogyStateError(f"{field} must be nonblank text")
    normalized = value.strip()
    if "\n" in normalized or "\r" in normalized or "<analogy>" in normalized:
        raise AnalogyStateError(f"{field} must fit on one line")
    return normalized


def _clean_lines(values: object, field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise AnalogyStateError(f"{field} must be a sequence of lines")
    try:
        normalized = tuple(_clean_line(value, field) for value in values)  # type: ignore[arg-type]
    except TypeError as error:
        raise AnalogyStateError(f"{field} must be a sequence of lines") from error
    if not normalized:
        raise AnalogyStateError(f"{field} must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AnalogyState:
    domain: str
    mapping: tuple[str, ...]
    steps: tuple[str, ...]
    abandoned_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", _clean_line(self.domain, "domain"))
        object.__setattr__(self, "mapping", _clean_lines(self.mapping, "mapping"))
        object.__setattr__(self, "steps", _clean_lines(self.steps, "steps"))
        abandoned = tuple(
            dict.fromkeys(_clean_line(value, "abandoned domain") for value in self.abandoned_domains)
        )
        if self.domain in abandoned:
            raise AnalogyStateError("active domain must not be an abandoned domain")
        object.__setattr__(self, "abandoned_domains", abandoned)

    def render(self) -> str:
        mapping = "\n".join(f"- {item}" for item in self.mapping)
        steps = "\n".join(f"{index}. {item}" for index, item in enumerate(self.steps, 1))
        abandoned = "\n".join(f"- {item}" for item in self.abandoned_domains) or "- (none)"
        return (
            "<analogy>\n"
            f"domain: {self.domain}\n"
            "mapping:\n"
            f"{mapping}\n"
            "steps:\n"
            f"{steps}\n"
            "abandoned domains:\n"
            f"{abandoned}\n"
            "</analogy>"
        )


def extract_analogy_block(text: str) -> tuple[AnalogyState, str]:
    if not isinstance(text, str):
        raise AnalogyStateError("analogy output must be text")
    matches = tuple(_BLOCK.finditer(text))
    if len(matches) != 1:
        raise AnalogyStateError("output must contain exactly one analogy block")
    match = matches[0]
    state = _parse_body(match.group("body"))
    remainder = text[: match.start()] + text[match.end() :]
    return state, remainder.strip("\n")


def parse_analogy_block(text: str) -> AnalogyState:
    state, remainder = extract_analogy_block(text)
    if remainder.strip():
        raise AnalogyStateError("analogy block input must not contain other text")
    return state


def remove_analogy_blocks(text: str) -> str:
    """Remove complete analogy blocks before text enters prompt history."""

    if not isinstance(text, str):
        raise AnalogyStateError("analogy output must be text")
    return _BLOCK.sub("", text).strip()


def _parse_body(body: str) -> AnalogyState:
    lines = body.splitlines()
    if not lines or not lines[0].startswith("domain:"):
        raise AnalogyStateError("analogy block requires domain")
    domain = lines[0].partition(":")[2].strip()
    try:
        mapping_index = lines.index("mapping:")
        steps_index = lines.index("steps:")
        abandoned_index = lines.index("abandoned domains:")
    except ValueError as error:
        missing = next(
            name
            for name in ("mapping", "steps", "abandoned domains")
            if f"{name}:" not in lines
        )
        raise AnalogyStateError(f"analogy block requires {missing}") from error
    if not (mapping_index == 1 < steps_index < abandoned_index):
        raise AnalogyStateError("analogy sections must be ordered domain, mapping, steps, abandoned domains")
    mapping_lines = lines[mapping_index + 1 : steps_index]
    if not mapping_lines or any(not line.startswith("- ") for line in mapping_lines):
        raise AnalogyStateError("mapping must be a nonempty bullet list")
    mapping = tuple(line[2:].strip() for line in mapping_lines)
    step_lines = lines[steps_index + 1 : abandoned_index]
    steps: list[str] = []
    for expected, line in enumerate(step_lines, 1):
        matched = _STEP.fullmatch(line)
        if matched is None or int(matched.group("number")) != expected:
            raise AnalogyStateError("steps must be consecutively numbered from 1")
        steps.append(matched.group("text").strip())
    if not steps:
        raise AnalogyStateError("steps must be a nonempty numbered list")
    abandoned_lines = lines[abandoned_index + 1 :]
    if not abandoned_lines or any(not line.startswith("- ") for line in abandoned_lines):
        raise AnalogyStateError("abandoned domains must be a bullet list")
    abandoned = tuple(
        line[2:].strip()
        for line in abandoned_lines
        if line[2:].strip() != "(none)"
    )
    return AnalogyState(domain, mapping, tuple(steps), abandoned)


class AnalogyScratchpad:
    """Own one analogy and block-free bounded history for a proof episode."""

    def __init__(
        self,
        initial: AnalogyState | None = None,
        *,
        abandoned_limit: int = 8,
        history_limit: int = 12,
    ) -> None:
        if abandoned_limit <= 0 or history_limit <= 0:
            raise ValueError("scratchpad limits must be positive")
        self._current = initial
        self._abandoned = list(initial.abandoned_domains if initial else ())
        self._abandoned_limit = abandoned_limit
        self._history_limit = history_limit
        self._history: list[str] = []
        self._pending_rebuild = False

    @property
    def current(self) -> AnalogyState | None:
        return self._current

    @property
    def abandoned_domains(self) -> tuple[str, ...]:
        return tuple(self._abandoned)

    @property
    def history(self) -> tuple[str, ...]:
        return tuple(self._history)

    @property
    def pending_rebuild(self) -> bool:
        return self._pending_rebuild

    def context_block(self) -> str:
        if self._current is not None:
            return self._current.render()
        abandoned = (
            "\n".join(f"- {item}" for item in self._abandoned) or "- (none)"
        )
        return prompt_catalog().text(
            "analogy_pending_context",
            abandoned_domains=abandoned,
        )

    def initialize(self, state: AnalogyState) -> None:
        if self._current is not None:
            raise AnalogyStateError("active analogy is already initialized")
        if self._pending_rebuild:
            raise AnalogyStateError(
                "initialization is forbidden while a pending rebuild exists"
            )
        if state.domain in self._abandoned:
            raise AnalogyStateError("new domain must not reuse an abandoned domain")
        self._current = replace(state, abandoned_domains=tuple(self._abandoned))

    def validate_transition(
        self, action: AnalogyUpdateAction, state: AnalogyState
    ) -> None:
        if not isinstance(action, AnalogyUpdateAction):
            raise AnalogyStateError("unknown analogy update action")
        if action is AnalogyUpdateAction.REVISE_STEPS:
            if self._current is None:
                raise AnalogyStateError("step revision requires an active analogy")
            if self._pending_rebuild:
                raise AnalogyStateError("step revision cannot satisfy a pending rebuild")
            if (state.domain, state.mapping) != (
                self._current.domain,
                self._current.mapping,
            ):
                raise AnalogyStateError("step revision must preserve domain and mapping")
            return
        if self._current is not None and state.domain == self._current.domain:
            raise AnalogyStateError(
                "change_domain must choose a domain different from the current domain"
            )
        if state.domain in self._abandoned:
            raise AnalogyStateError("new domain must not reuse an abandoned domain")

    def apply(self, action: AnalogyUpdateAction, state: AnalogyState) -> None:
        self.validate_transition(action, state)
        if action is AnalogyUpdateAction.REVISE_STEPS:
            self._current = replace(
                state,
                abandoned_domains=tuple(self._abandoned),
            )
            return
        prospective = list(self._abandoned)
        if self._current is not None:
            if self._current.domain not in prospective:
                prospective.append(self._current.domain)
        prospective = prospective[-self._abandoned_limit :]
        replacement = replace(state, abandoned_domains=tuple(prospective))
        self._abandoned = prospective
        self._current = replacement
        self._pending_rebuild = False

    def abandon_current(self) -> None:
        if self._current is not None:
            self._remember_abandoned(self._current.domain)
            self._current = None
        self._pending_rebuild = True

    def record_history(self, text: str) -> None:
        cleaned = remove_analogy_blocks(text)
        if "<analogy>" in cleaned or "</analogy>" in cleaned:
            raise AnalogyStateError("history must not contain analogy block text")
        if not cleaned:
            return
        self._history.append(cleaned)
        del self._history[: -self._history_limit]

    def _remember_abandoned(self, domain: str) -> None:
        if domain not in self._abandoned:
            self._abandoned.append(domain)
        del self._abandoned[: -self._abandoned_limit]


__all__ = [
    "AnalogyScratchpad",
    "AnalogyState",
    "AnalogyStateError",
    "AnalogyUpdateAction",
    "extract_analogy_block",
    "parse_analogy_block",
    "remove_analogy_blocks",
]
