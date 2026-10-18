"""Lean proof-state syntax used by prompts and semantic checking."""

from __future__ import annotations

import re
from dataclasses import dataclass

class InventoryError(ValueError):
    pass


_IDENTIFIER_TEXT = r"«[^»]+»|[^\s«»:]+"
_IDENTIFIER = re.compile(_IDENTIFIER_TEXT)
_DECLARATION = re.compile(
    rf"(?P<names>(?:{_IDENTIFIER_TEXT})(?:\s+(?:{_IDENTIFIER_TEXT}))*) :(?![=]) ?(?P<proposition>.*)"
)
_WORD = re.compile(r"[\w']+")
_ROOT_WORD = re.compile(r"[\w']+")
_ROOT_BINDERS = {"forall", "exists", "fun", "λ", "let", "if", "match", "by", "do", "have", "show", "calc"}


@dataclass(frozen=True, slots=True)
class PropositionRoot:
    text: str
    operator: str | None = None
    parts: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ProofGoal:
    declarations: tuple[tuple[str, str], ...]
    target: str


def proposition_root(proposition: str) -> PropositionRoot:
    """Conservatively split only a supported outer Lean connective."""
    text = proposition.strip()
    pairs = {"(": ")", "[": "]", "{": "}", "⟨": "⟩"}
    for _ in range(64):
        opaque = PropositionRoot(text)
        stack: list[str] = []
        operators: list[tuple[str, int]] = []
        wrapped = text.startswith("(")
        index = 0
        while index < len(text):
            char = text[index]
            prime = char == "'" and index > 0 and (text[index - 1].isalnum() or text[index - 1] in "_'✝")
            if char == '"' or (char == "'" and not prime):
                quote = char
                index += 1
                while index < len(text) and text[index] != quote:
                    index += 2 if text[index] == "\\" else 1
                if index >= len(text):
                    return opaque
                index += 1
                continue
            if char == "«":
                end = text.find("»", index + 1)
                if end < 0:
                    return opaque
                index = end + 1
                continue
            if text.startswith(("/-", "--"), index):
                return opaque
            if char in pairs:
                stack.append(pairs[char])
            elif char in pairs.values():
                if not stack or stack.pop() != char:
                    return opaque
                if not stack and index != len(text) - 1:
                    wrapped = False
            elif not stack:
                if char in "∀∃":
                    return opaque
                word = _ROOT_WORD.match(text, index)
                if word:
                    if word[0] in _ROOT_BINDERS:
                        return opaque
                    index = word.end()
                    continue
                if char in "↔→∨∧=":
                    if char == "=" and ((index and text[index - 1] in "=:<>!")
                                         or (index + 1 < len(text) and text[index + 1] in "=>")):
                        return opaque
                    operators.append((char, index))
            index += 1
        if stack:
            return opaque
        if wrapped:
            text = text[1:-1].strip()
            continue
        for operator in ("↔", "→", "∨", "∧", "¬", "="):
            if operator == "¬":
                if text.startswith("¬") and text[1:].strip():
                    return PropositionRoot(text, "¬", (text[1:].strip(), "False"))
                continue
            positions = [position for symbol, position in operators if symbol == operator]
            if positions:
                if operator in ("↔", "=") and len(positions) != 1:
                    return opaque
                position = positions[0]
                left, right = text[:position].strip(), text[position + 1:].strip()
                return PropositionRoot(text, operator, (left, right)) if left and right else opaque
        return opaque
    return PropositionRoot(text)


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


def proof_goals(expression: str) -> tuple[ProofGoal, ...]:
    """Parse every displayed Lean goal without merging case-local contexts."""
    if not isinstance(expression, str) or not expression.strip():
        raise InventoryError("proof state must be nonblank text")
    lines = expression.splitlines()
    case_indices = [
        index for index, line in enumerate(lines) if line.startswith("case ")
    ]
    if not case_indices:
        if sum(line.lstrip().startswith("⊢") for line in lines) != 1:
            raise InventoryError("proof state must contain exactly one unnamed goal")
        declarations, target = _declarations_and_target(expression)
        return (ProofGoal(tuple(declarations), target),)

    preamble = lines[: case_indices[0]]
    goals: list[ProofGoal] = []
    for position, start in enumerate(case_indices):
        end = case_indices[position + 1] if position + 1 < len(case_indices) else len(lines)
        block = preamble + lines[start + 1 : end]
        if sum(line.lstrip().startswith("⊢") for line in block) != 1:
            raise InventoryError("each named case must contain exactly one goal")
        declarations, target = _declarations_and_target("\n".join(block))
        goals.append(ProofGoal(tuple(declarations), target))
    if not goals:
        raise InventoryError("proof state has no active goal")
    return tuple(goals)


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




__all__ = ["InventoryError", "ProofGoal", "PropositionRoot", "proof_goals", "proposition_root"]
