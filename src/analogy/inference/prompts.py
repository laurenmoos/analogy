"""Strict, cached loading for model-facing prompt templates."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml

from ..records import Message


class PromptError(ValueError):
    """A prompt catalog or render request is invalid."""


_VARIABLE = re.compile(r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}")
_NEGATION_GOAL = re.compile(r"(?m)^(?P<prefix>\s*⊢\s*)?¬(?P<body>[^\n]+)$")
_ACTIVE_CATALOG: PromptCatalog | None = None


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    version: str
    system: str
    user: str


@dataclass(frozen=True, slots=True)
class PromptCatalog:
    templates: Mapping[str, PromptTemplate]
    content_hash: str

    @classmethod
    def load(cls, path: Path) -> "PromptCatalog":
        prompt_path = Path(path).expanduser().resolve()
        try:
            raw = prompt_path.read_bytes()
            payload = yaml.safe_load(raw)
        except (OSError, yaml.YAMLError) as error:
            raise PromptError(f"prompt catalog could not be read: {error}") from error
        if not isinstance(payload, dict) or set(payload) != {"prompts"}:
            raise PromptError("prompt catalog must contain only prompts")
        prompts = payload["prompts"]
        if not isinstance(prompts, dict) or not prompts:
            raise PromptError("prompts must be a nonempty mapping")
        templates: dict[str, PromptTemplate] = {}
        for name, value in prompts.items():
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(value, dict)
                or set(value) != {"version", "system", "user"}
                or any(
                    not isinstance(value[key], str) or not value[key].strip()
                    for key in ("version", "system", "user")
                )
            ):
                raise PromptError(f"invalid prompt template: {name!r}")
            templates[name] = PromptTemplate(**value)
        return cls(
            MappingProxyType(templates),
            hashlib.sha256(raw).hexdigest(),
        )

    def messages(self, name: str, **values: object) -> tuple[Message, ...]:
        try:
            template = self.templates[name]
        except KeyError as error:
            raise PromptError(f"unknown prompt: {name}") from error
        supplied = {key: str(value) for key, value in values.items()}
        required = set(_VARIABLE.findall(template.system + template.user))
        if required != set(supplied):
            raise PromptError(
                "prompt variables differ: "
                f"required={sorted(required)} supplied={sorted(supplied)}"
            )

        def render(text: str) -> str:
            return _VARIABLE.sub(lambda match: supplied[match.group(1)], text)

        return Message("system", render(template.system)), Message(
            "user", render(template.user)
        )

    def version(self, name: str) -> str:
        try:
            return self.templates[name].version
        except KeyError as error:
            raise PromptError(f"unknown prompt: {name}") from error


@lru_cache(maxsize=None)
def _load_prompt_catalog(path: Path) -> PromptCatalog:
    return PromptCatalog.load(path)


def configure_prompt_catalog(path: Path) -> PromptCatalog:
    global _ACTIVE_CATALOG
    resolved = Path(path).expanduser().resolve()
    _ACTIVE_CATALOG = _load_prompt_catalog(resolved)
    return _ACTIVE_CATALOG


def prompt_catalog() -> PromptCatalog:
    global _ACTIVE_CATALOG
    if _ACTIVE_CATALOG is None:
        _ACTIVE_CATALOG = configure_prompt_catalog(
            Path(__file__).parents[3] / "prompts.yaml"
        )
    return _ACTIVE_CATALOG


def render_lean_goal_for_model(text: str) -> str:
    """Expose Lean's `¬P = P → False` definition without changing the goal."""

    if not isinstance(text, str):
        raise ValueError("Lean goal rendering requires text")

    def expand(match: re.Match[str]) -> str:
        prefix = match.group("prefix") or ""
        body = match.group("body").strip()
        return f"{prefix}¬{body}  [definitionally: {body} → False]"

    return _NEGATION_GOAL.sub(expand, text)


__all__ = [
    "PromptCatalog", "PromptError", "PromptTemplate",
    "configure_prompt_catalog", "prompt_catalog", "render_lean_goal_for_model",
]
