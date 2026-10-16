"""Exact, state-bound declarations available to grounded proof reasoning."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

from .proof_views import ProofStructure
from .records import SymbolicState
from .replay import proof_state_fingerprint


class InventoryError(ValueError):
    pass


class InventoryKind(str, Enum):
    LOCAL = "local"
    ACTIVE_SUBGOAL = "active_subgoal"
    TARGET_COMPONENT = "target_component"
    CONSTRUCTOR = "constructor"


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    inventory_id: str
    kind: InventoryKind
    identifier: str | None
    proposition: str
    structural_shape: str


@dataclass(frozen=True, slots=True)
class LocalProofInventory:
    state_fingerprint: str
    fixed_target: str
    active_subgoal: str
    locals: tuple[InventoryEntry, ...]
    target_entries: tuple[InventoryEntry, ...]

    def resolve(self, inventory_id: str) -> InventoryEntry:
        matches = tuple(item for item in self.locals + self.target_entries
                        if item.inventory_id == inventory_id)
        if len(matches) != 1:
            raise InventoryError(f"unknown inventory id {inventory_id!r}")
        return matches[0]


_OPAQUE = '{"children":[],"root":"opaque"}'
_IDENTIFIER_TEXT = r"«[^»]+»|[^\s«»:]+"
_IDENTIFIER = re.compile(_IDENTIFIER_TEXT)
_DECLARATION = re.compile(
    rf"(?P<names>(?:{_IDENTIFIER_TEXT})(?:\s+(?:{_IDENTIFIER_TEXT}))*) :(?![=]) ?(?P<proposition>.*)"
)
_WORD = re.compile(r"[\w']+")
_SORT = re.compile(r"(?:Prop|Type(?:\s+\S+)?|Sort\s+\S+)")


def _declarations_and_target(expression: str) -> tuple[list[tuple[str, str]], str]:
    lines = expression.splitlines()
    try:
        goal_line = next(index for index, line in enumerate(lines) if line.lstrip().startswith("⊢"))
    except StopIteration as error:
        raise InventoryError("proof state has no active subgoal") from error
    declarations: list[tuple[str, str]] = []
    pending_names: list[str] = []
    pending_type: list[str] = []

    def flush() -> None:
        if pending_names:
            proposition = "\n".join(pending_type).strip()
            definition = _split_component(proposition, ":=")
            if definition is not None:
                proposition = definition[0]
            if not proposition.strip():
                raise InventoryError("local declaration has no proposition")
            declarations.extend((name, proposition) for name in pending_names)

    for line in lines[:goal_line]:
        if not line.strip() or line.startswith("case "):
            continue
        # A type or initializer can continue at column zero (notably let bodies).
        # Only a new printed declaration header ends the preceding declaration.
        declaration = _DECLARATION.fullmatch(line) if not line[:1].isspace() else None
        if declaration is None:
            if not pending_names:
                raise InventoryError(f"cannot parse local declaration {line!r}")
            pending_type.append(line)
            continue
        flush()
        pending_names = _IDENTIFIER.findall(declaration["names"])
        pending_type = [declaration["proposition"]]
    flush()
    target_lines = [lines[goal_line].lstrip()[1:].lstrip()]
    for line in lines[goal_line + 1:]:
        if not line.strip() or line.startswith("case ") or line.lstrip().startswith("⊢"):
            break
        target_lines.append(line)
    target = "\n".join(target_lines)
    if not target.strip():
        raise InventoryError("active subgoal must be nonblank")
    return declarations, target


def _split_component(expression: str, operator: str) -> tuple[str, str] | None:
    """Split syntax outside literals, quoted names, nested terms, and let binders.

    Incomplete literals, comments, and mismatched delimiters return no split.
    Delimiter-looking characters in a printed literal never become proof syntax.
    """
    text = expression.strip()
    pairs = {"(": ")", "[": "]", "{": "}", "⟨": "⟩"}
    stack: list[str] = []
    index = 0
    split_at: int | None = None
    pending_lets = 0
    enclosing_parentheses = text.startswith("(")
    while index < len(text):
        char = text[index]
        is_prime = char == "'" and index > 0 and (text[index - 1].isalnum() or text[index - 1] in "_'✝")
        if char == '"' or (char == "'" and not is_prime):
            quote = char
            index += 1
            while index < len(text) and text[index] != quote:
                index += 2 if text[index] == "\\" else 1
            if index >= len(text):
                return None
            index += 1
            continue
        if char == "«":
            end = text.find("»", index + 1)
            if end < 0:
                return None
            index = end + 1
            continue
        if text.startswith(("/-", "--"), index):
            return None
        if char in pairs:
            stack.append(pairs[char])
        elif char in pairs.values():
            if not stack or char != stack[-1]:
                return None
            stack.pop()
            if not stack and index != len(text) - 1:
                enclosing_parentheses = False
        elif not stack:
            word = _WORD.match(text, index)
            if word is not None:
                if word[0] == "let":
                    pending_lets += 1
                index = word.end()
                continue
            if text.startswith(":=", index) and pending_lets:
                pending_lets -= 1
                index += 2
                continue
            if split_at is None and text.startswith(operator, index):
                split_at = index
        index += 1
    if stack:
        return None
    if split_at is not None:
        left, right = text[:split_at].strip(), text[split_at + len(operator):].strip()
        return (left, right) if left and right else None
    if enclosing_parentheses:
        return _split_component(text[1:-1], operator)
    return None


def _target_entries(target: str, structure: ProofStructure) -> tuple[InventoryEntry, ...]:
    entries = [InventoryEntry("active_subgoal", InventoryKind.ACTIVE_SUBGOAL, None, target, structure.target_shape)]
    shape = json.loads(structure.target_shape)
    if not isinstance(shape, dict):
        raise InventoryError("target structural shape must be an object")
    root = shape.get("root")
    operator = {"and": "∧", "or": "∨", "iff": "↔", "implication": "→"}.get(root)
    components = _split_component(target, operator) if operator is not None else None
    children = shape.get("children", [])
    if components is not None and len(children) == 2:
        entries.extend(
            InventoryEntry(f"target:{index}", InventoryKind.TARGET_COMPONENT, None, component,
                           json.dumps(children[index], sort_keys=True, separators=(",", ":")))
            for index, component in enumerate(components)
        )
    constructors = {"and": ("And.intro",), "or": ("Or.inl", "Or.inr"), "iff": ("Iff.intro",)}.get(root, ())
    entries.extend(
        InventoryEntry(f"constructor:{index}", InventoryKind.CONSTRUCTOR, identifier, target, structure.target_shape)
        for index, identifier in enumerate(constructors)
    )
    return tuple(entries)


def build_local_inventory(
    state: SymbolicState, structure: ProofStructure, *, fixed_target: str,
) -> LocalProofInventory:
    """Preserve the active goal's exact names/types without invoking Lean.

    Lean's current shape extractor only includes proposition hypotheses. Known
    sort declarations are excluded when aligning those shapes. If that does not
    yield an unambiguous alignment, retain opaque metadata rather than attaching
    another declaration's verified shape to a local identifier.
    """
    if not isinstance(fixed_target, str) or not fixed_target.strip():
        raise InventoryError("fixed theorem target must be nonblank")
    declarations, target = _declarations_and_target(state.expression)
    proposition_indices = [index for index, (_, proposition) in enumerate(declarations)
                           if not _SORT.fullmatch(proposition)]
    shapes: dict[int, str] = {}
    if len(proposition_indices) == len(structure.hypothesis_shapes):
        shapes = dict(zip(proposition_indices, structure.hypothesis_shapes))
    locals_ = tuple(
        InventoryEntry(f"local:{index}", InventoryKind.LOCAL, identifier, proposition, shapes.get(index, _OPAQUE))
        for index, (identifier, proposition) in enumerate(declarations)
    )
    return LocalProofInventory(
        proof_state_fingerprint(state.expression), fixed_target, target, locals_,
        _target_entries(target, structure),
    )


__all__ = ["InventoryEntry", "InventoryError", "InventoryKind", "LocalProofInventory", "build_local_inventory"]
