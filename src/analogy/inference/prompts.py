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

from .parser import normalize_lean4_tactic
from ..records import Message


class PromptError(ValueError):
    """A prompt catalog or render request is invalid."""


_VARIABLE = re.compile(r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}")
_NEGATION_GOAL = re.compile(r"(?m)^(?P<prefix>\s*⊢\s*)?¬(?P<body>[^\n]+)$")
_IDENTIFIER_TEXT = r"«[^»]+»|[^\s«»:]+"
_IDENTIFIER = re.compile(_IDENTIFIER_TEXT)
_DECLARATION = re.compile(
    rf"^\s*(?P<names>(?:{_IDENTIFIER_TEXT})(?:\s+(?:{_IDENTIFIER_TEXT}))*)"
    rf"\s*:(?!=)\s*(?P<type>.+?)\s*$"
)
_ACTIVE_CATALOG: PromptCatalog | None = None


def _strip_outer_parentheses(expression: str) -> str:
    result = expression.strip()
    while result.startswith("(") and result.endswith(")"):
        depth = 0
        closes_at_end = False
        for index, character in enumerate(result):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    closes_at_end = index == len(result) - 1
                    break
        if not closes_at_end:
            break
        result = result[1:-1].strip()
    return result


def _root_connective(expression: str) -> tuple[str, str, str] | None:
    """Split a pretty-printed proposition at its lowest-precedence root operator."""

    expression = _strip_outer_parentheses(expression)
    depth = 0
    found: dict[str, int] = {}
    for index, character in enumerate(expression):
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and character in "↔→∨∧":
            found.setdefault(character, index)
    for connective in ("↔", "→", "∨", "∧"):
        if connective in found:
            index = found[connective]
            return (
                connective,
                expression[:index].strip(),
                expression[index + 1 :].strip(),
            )
    return None


def _same_expression(left: str, right: str) -> bool:
    return " ".join(_strip_outer_parentheses(left).split()) == " ".join(
        _strip_outer_parentheses(right).split()
    )


def _fresh_identifier(base: str, occupied: set[str]) -> str:
    if base not in occupied:
        occupied.add(base)
        return base
    suffix = 2
    while f"{base}_{suffix}" in occupied:
        suffix += 1
    result = f"{base}_{suffix}"
    occupied.add(result)
    return result


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


def lean_tactic_card(proof_state: str) -> str:
    """Return a compact generic Lean syntax card without solution hints."""

    if not isinstance(proof_state, str):
        raise ValueError("Lean tactic card requires proof-state text")
    try:
        from ..solver.proof_inventory import proof_goals, proposition_root

        goal = proof_goals(focused_lean_goal(proof_state))[0]
    except (ValueError, IndexError):
        goal = None
    occupied = set() if goal is None else {name for name, _ in goal.declarations}

    def semantic_name(proposition: str, fallback: str) -> str:
        atoms = re.findall(r"[A-Za-z_][A-Za-z0-9_']*", proposition)
        ignored = {"Prop", "Type", "Sort", "forall", "True", "False"}
        suffix = "".join(atom for atom in atoms if atom not in ignored)[:18] or fallback
        return _fresh_identifier("h" + suffix, occupied)

    recipes: list[str] = []
    if goal is not None:
        for name, proposition in goal.declarations:
            root = proposition_root(proposition)
            if root.operator == "∨" and root.parts is not None:
                left, right = root.parts
                nested = proposition_root(right)
                if nested.operator == "∨" and nested.parts is not None:
                    names = (
                        semantic_name(left, "Left"),
                        semantic_name(nested.parts[0], "Middle"),
                        semantic_name(nested.parts[1], "Right"),
                    )
                    recipes.append(f"rcases {name} with {' | '.join(names)}")
                else:
                    recipes.append(
                        f"rcases {name} with {semantic_name(left, 'Left')} | "
                        f"{semantic_name(right, 'Right')}"
                    )
                break
        for name, proposition in goal.declarations:
            root = proposition_root(proposition)
            if root.operator == "∧" and root.parts is not None:
                recipes.append(
                    f"obtain ⟨{semantic_name(root.parts[0], 'Left')},"
                    f"{semantic_name(root.parts[1], 'Right')}⟩ := {name}"
                )
                break
        target = goal.target.strip()
        if target.startswith(("∀", "forall ")):
            match = re.match(r"(?:∀|forall)\s+([A-Za-z_][\w']*)", target)
            recipes.append(f"intro {match.group(1) if match else 'x'}")

    card = "Lean card: complete line; no tauto/itauto. "
    if recipes:
        card += "Destruct/introduce: " + "; ".join(recipes) + ". "
    card += "Build goals: Or.inl _, Or.inr _, ⟨_,_⟩. Use exact <hyp>, apply <hyp>, assumption, rw [<hyp>]."
    return card if len(card) <= 240 else card[:239] + "…"


def focused_lean_goal(proof_state: str) -> str:
    """Return only the first displayed goal and its local context."""

    if not isinstance(proof_state, str) or not proof_state.strip():
        raise ValueError("Lean proof state must be nonblank text")
    lines = proof_state.splitlines()
    case_indices = [index for index, line in enumerate(lines) if line.startswith("case ")]
    if not case_indices:
        return proof_state.strip()
    start = case_indices[0]
    end = case_indices[1] if len(case_indices) > 1 else len(lines)
    preamble = lines[:start]
    block = preamble + lines[start:end]
    return "\n".join(block).strip()


__all__ = [
    "PromptCatalog", "PromptError", "PromptTemplate",
    "configure_prompt_catalog", "focused_lean_goal", "lean_tactic_card", "prompt_catalog",
    "render_lean_goal_for_model",
]
