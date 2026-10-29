"""Persistent, externally stored analogy scratchpad state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class AnalogyStateError(ValueError):
    """Raised when an analogy block or lifecycle transition is invalid."""


class AnalogyUpdateAction(str, Enum):
    REVISE_STEPS = "revise_steps"
    REVISE_MODEL = "revise_model"
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


def _clean_optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise AnalogyStateError(f"{field} must be nonblank text or null")
    return value.strip()


@dataclass(frozen=True, slots=True)
class StoryCorrespondence:
    domain_before: str
    domain_transition: str
    domain_after: str

    def __post_init__(self) -> None:
        for field in ("domain_before", "domain_transition", "domain_after"):
            object.__setattr__(self, field, _clean_line(getattr(self, field), field))


@dataclass(frozen=True, slots=True)
class StoryModel:
    domain: str
    correspondences: tuple[StoryCorrespondence, ...]
    abandoned_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", _clean_line(self.domain, "domain"))
        correspondences = tuple(self.correspondences)
        if not correspondences:
            raise AnalogyStateError("correspondences must be nonempty")
        if any(not isinstance(item, StoryCorrespondence) for item in correspondences):
            raise AnalogyStateError("story correspondences have the wrong type")
        object.__setattr__(self, "correspondences", correspondences)
        abandoned = tuple(
            dict.fromkeys(
                _clean_line(value, "abandoned domain")
                for value in self.abandoned_domains
            )
        )
        object.__setattr__(self, "abandoned_domains", abandoned)


@dataclass(frozen=True, slots=True)
class MentalModelCorrespondence:
    domain_before: str
    domain_transition: str
    domain_after: str
    proof_transition: str | None = None

    def __post_init__(self) -> None:
        for field in ("domain_before", "domain_transition", "domain_after"):
            object.__setattr__(self, field, _clean_line(getattr(self, field), field))
        object.__setattr__(
            self,
            "proof_transition",
            _clean_optional_text(self.proof_transition, "proof_transition"),
        )


@dataclass(frozen=True, slots=True)
class MentalModel:
    domain: str
    correspondences: tuple[MentalModelCorrespondence, ...]
    abandoned_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", _clean_line(self.domain, "domain"))
        try:
            correspondences = tuple(self.correspondences)
        except TypeError as error:
            raise AnalogyStateError("correspondences must be a nonempty sequence") from error
        if not correspondences:
            raise AnalogyStateError("correspondences must be nonempty")
        if any(
            not isinstance(item, MentalModelCorrespondence)
            for item in correspondences
        ):
            raise AnalogyStateError(
                "correspondences must contain MentalModelCorrespondence values"
            )
        object.__setattr__(self, "correspondences", correspondences)
        abandoned = tuple(
            dict.fromkeys(
                _clean_line(value, "abandoned domain")
                for value in self.abandoned_domains
            )
        )
        if self.domain in abandoned:
            raise AnalogyStateError("active domain must not be an abandoned domain")
        object.__setattr__(self, "abandoned_domains", abandoned)


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

WorkingMentalModel = AnalogyState | MentalModel


class AnalogyScratchpad:
    """Own one analogy and block-free bounded history for a proof episode."""

    def __init__(
        self,
        initial: WorkingMentalModel | None = None,
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
        self._assigned_domain: str | None = None

    @property
    def current(self) -> WorkingMentalModel | None:
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

    @property
    def assigned_domain(self) -> str | None:
        return self._assigned_domain

    def initialize(self, state: WorkingMentalModel) -> None:
        if self._current is not None:
            raise AnalogyStateError("active analogy is already initialized")
        if self._pending_rebuild:
            raise AnalogyStateError(
                "initialization is forbidden while a pending rebuild exists"
            )
        if state.domain in self._abandoned:
            raise AnalogyStateError("new domain must not reuse an abandoned domain")
        if self._assigned_domain is not None and state.domain != self._assigned_domain:
            raise AnalogyStateError(
                f"change_domain must use harness-assigned domain {self._assigned_domain!r}"
            )
        self._current = replace(state, abandoned_domains=tuple(self._abandoned))

    def validate_transition(
        self, action: AnalogyUpdateAction, state: WorkingMentalModel
    ) -> None:
        if not isinstance(action, AnalogyUpdateAction):
            raise AnalogyStateError("unknown analogy update action")
        if action in (
            AnalogyUpdateAction.REVISE_STEPS,
            AnalogyUpdateAction.REVISE_MODEL,
        ):
            if self._current is None:
                raise AnalogyStateError("model revision requires an active analogy")
            if self._pending_rebuild:
                raise AnalogyStateError("model revision cannot satisfy a pending rebuild")
            if (
                action is AnalogyUpdateAction.REVISE_STEPS
                and isinstance(state, AnalogyState)
                and isinstance(self._current, AnalogyState)
                and (state.domain, state.mapping)
                != (self._current.domain, self._current.mapping)
            ):
                raise AnalogyStateError("step revision must preserve domain and mapping")
            if state.domain != self._current.domain:
                raise AnalogyStateError("model revision must preserve domain")
            return
        if self._current is not None and state.domain == self._current.domain:
            raise AnalogyStateError(
                "change_domain must choose a domain different from the current domain"
            )
        if state.domain in self._abandoned:
            raise AnalogyStateError("new domain must not reuse an abandoned domain")
        if self._assigned_domain is not None and state.domain != self._assigned_domain:
            raise AnalogyStateError(
                f"change_domain must use harness-assigned domain {self._assigned_domain!r}"
            )

    def apply(self, action: AnalogyUpdateAction, state: WorkingMentalModel) -> None:
        self.validate_transition(action, state)
        if action in (
            AnalogyUpdateAction.REVISE_STEPS,
            AnalogyUpdateAction.REVISE_MODEL,
        ):
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
        self._assigned_domain = None

    def abandon_current(self, assigned_domain: str | None = None) -> None:
        if self._current is not None:
            self._remember_abandoned(self._current.domain)
            self._current = None
        self._pending_rebuild = True
        self._assigned_domain = assigned_domain

    def record_history(self, text: str) -> None:
        if not text:
            return
        self._history.append(text)
        del self._history[: -self._history_limit]

    def _remember_abandoned(self, domain: str) -> None:
        if domain not in self._abandoned:
            self._abandoned.append(domain)
        del self._abandoned[: -self._abandoned_limit]


def initialize_analogy_scratchpad() -> AnalogyScratchpad:
    """Create the per-problem analogy state used by one assisted run."""

    return AnalogyScratchpad()


def update_analogy(search, scratchpad: AnalogyScratchpad) -> AnalogyScratchpad:
    """Prepare analogy state for the search's pending rescue context."""

    recovery = search.recovery
    if recovery is None:
        raise RuntimeError("search has no pending analogy rescue")
    if recovery.result.value == "countermodel":
        scratchpad.abandon_current()
    return scratchpad


__all__ = [
    "AnalogyScratchpad",
    "AnalogyState",
    "AnalogyStateError",
    "AnalogyUpdateAction",
    "MentalModel",
    "MentalModelCorrespondence",
    "WorkingMentalModel",
    "initialize_analogy_scratchpad",
    "update_analogy",
]
