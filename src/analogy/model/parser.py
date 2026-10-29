"""Strict parsing and normalization for model-generated Lean tactics."""

from __future__ import annotations

import re

class ModelOutputError(ValueError):
    """Raised when generated text is not a valid tactic sequence."""


class IncompleteCandidatePool(ModelOutputError):
    """Raised when every otherwise valid candidate lacks required operands."""


class FilteredCandidatePool(ModelOutputError):
    """Raised when candidate safety filters remove the entire generated pool."""


_INCOMPLETE_TACTIC_HEADS = frozenset(
    {"exact", "apply", "refine", "rw", "cases", "rcases", "have", "show", "rename_i"}
)
_IDENTIFIER_TEXT = r"«[^»]+»|[^\s«»:]+"
_IDENTIFIER = re.compile(_IDENTIFIER_TEXT)
_DECLARATION = re.compile(
    rf"^\s*(?P<names>(?:{_IDENTIFIER_TEXT})(?:\s+(?:{_IDENTIFIER_TEXT}))*)"
    rf"\s*:(?!=)\s*(?P<type>.+?)\s*$"
)
_SIMPLE_IDENTIFIER = re.compile(r"[A-Za-z_][\w'✝]*")
_LEAN3_CASES = re.compile(
    r"^cases\s+(?P<subject>«[^»]+»|[A-Za-z_][\w'✝]*)\s+with\s+"
    r"(?P<left>[A-Za-z_][\w'✝]*)\s+(?P<right>[A-Za-z_][\w'✝]*)$"
)
_RCases_PAIR = re.compile(
    r"^rcases\s+(?P<subject>«[^»]+»|[A-Za-z_][\w'✝]*)\s+with\s+"
    r"⟨\s*(?P<left>[A-Za-z_][\w'✝]*)\s*,\s*"
    r"(?P<right>[A-Za-z_][\w'✝]*)\s*⟩(?P<suffix>.*)$"
)
_RCASES_SUM = re.compile(
    r"^rcases\s+(?P<subject>«[^»]+»|[A-Za-z_][\w'✝]*)\s+with\s+"
    r"\(?\s*(?P<left>[A-Za-z_][\w'✝]*)\s*\|\s*"
    r"(?P<right>[A-Za-z_][\w'✝]*)\s*\)?$"
)
_LEGACY_NAMES = (
    (re.compile(r"\band\.intro\b"), "And.intro"),
    (re.compile(r"\band\.left\b"), "And.left"),
    (re.compile(r"\band\.right\b"), "And.right"),
    (re.compile(r"\biff\.intro\b"), "Iff.intro"),
    (re.compile(r"\biff\.left\b"), "Iff.mp"),
    (re.compile(r"\biff\.right\b"), "Iff.mpr"),
    (re.compile(r"\bor\.introL\b"), "Or.inl"),
    (re.compile(r"\bor\.introR\b"), "Or.inr"),
    (re.compile(r"\bnot\.intro\b"), "Not.intro"),
)
_BARE_RW = re.compile(r"^rw\s+(?P<hyp>«[^»]+»|[A-Za-z_][\w'✝]*)$")
_ALLOWED_TACTIC_HEADS = frozenset(
    {
        "exact", "apply", "refine", "rw", "cases", "rcases", "obtain",
        "have", "show", "rename_i", "intro", "intros", "rintro", "left", "right",
        "constructor", "assumption", "contradiction", "exfalso", "rfl",
        "simp", "simpa", "aesop", "omega", "decide", "norm_num", "trivial",
    }
)
_BANNED_TACTIC_TOKEN = re.compile(
    r"(?<![\w'.])(?:sorry|admit|tauto|itauto|modus_tollens)(?![\w'.])"
)
_NESTED_TACTIC_BLOCK = re.compile(r"(?<![\w'.])by(?![\w'.])")
_SHAPE_SENSITIVE_HEADS = frozenset({"left", "right", "constructor", "intro", "intros"})


def _strip_outer_parentheses(expression: str) -> str:
    result = expression.strip()
    while result.startswith("(") and result.endswith(")"):
        depth = 0
        for index, character in enumerate(result):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    if index != len(result) - 1:
                        return result
                    result = result[1:-1].strip()
                    break
        else:
            break
    return result


def _root_connective(expression: str) -> tuple[str, str, str] | None:
    expression = _strip_outer_parentheses(expression)
    depth = 0
    found: dict[str, int] = {}
    for index, character in enumerate(expression):
        if character in "([{":
            depth += 1
        elif character in ")]}" and depth:
            depth -= 1
        elif depth == 0 and character in "↔→∨∧":
            found.setdefault(character, index)
    for connective in ("↔", "→", "∨", "∧"):
        if connective in found:
            index = found[connective]
            return connective, expression[:index].strip(), expression[index + 1:].strip()
    return None


def _first_goal_declarations(proof_state: str) -> tuple[list[tuple[str, str]], set[str]]:
    declarations: list[tuple[str, str]] = []
    occupied: set[str] = set()
    for line in proof_state.splitlines():
        if "⊢" in line:
            break
        match = _DECLARATION.fullmatch(line)
        if match is None:
            continue
        names = _IDENTIFIER.findall(match["names"])
        occupied.update(names)
        declarations.extend((name, match["type"].strip()) for name in names)
    return declarations, occupied


def _semantic_suffix(expression: str) -> str | None:
    expression = _strip_outer_parentheses(expression)
    if _SIMPLE_IDENTIFIER.fullmatch(expression):
        return expression
    root = _root_connective(expression)
    if root is None:
        return None
    left = _semantic_suffix(root[1])
    right = _semantic_suffix(root[2])
    return None if left is None or right is None else left + right


def _fresh_semantic_name(expression: str, fallback: str, occupied: set[str]) -> str:
    base = "h" + (_semantic_suffix(expression) or fallback)
    candidate = base
    suffix = 2
    while candidate in occupied:
        candidate = f"{base}_{suffix}"
        suffix += 1
    occupied.add(candidate)
    return candidate


_SAFE_CONTINUATION_HEADS = frozenset(
    {
        "exact", "apply", "assumption", "left", "right", "constructor",
        "contradiction", "rfl", "simp", "simpa", "aesop", "tauto", "omega",
        "decide", "norm_num",
    }
)
_BINDING_CONTINUATION_TOKEN = re.compile(
    r"(?<![\w'✝.])(?:by|fun|have|intro|let|match|obtain|rcases|cases|rename_i|rintro|suffices)(?![\w'✝.])|λ"
)


def _suffix_is_safe_for_renaming(suffix: str) -> bool:
    if not suffix.strip():
        return True
    if _BINDING_CONTINUATION_TOKEN.search(suffix):
        return False
    segments = [item.strip() for item in re.split(r"\s*(?:<;>|;)\s*", suffix) if item.strip()]
    return bool(segments) and all(
        segment.split(maxsplit=1)[0] in _SAFE_CONTINUATION_HEADS
        for segment in segments
    )


def _replace_identifiers(text: str, replacements: dict[str, str]) -> str:
    active = {old: new for old, new in replacements.items() if old != new}
    if not active:
        return text
    alternatives = "|".join(
        re.escape(item) for item in sorted(active, key=len, reverse=True)
    )
    pattern = re.compile(rf"(?<![\w'✝.])(?:{alternatives})(?![\w'✝])")
    return pattern.sub(lambda match: active[match.group(0)], text)


def _replace_continuation_identifiers(
    suffix: str,
    replacements: dict[str, str],
) -> str:
    parts = re.split(r"(\s*(?:<;>|;)\s*)", suffix)
    for index in range(0, len(parts), 2):
        segment = parts[index]
        match = re.fullmatch(r"(?P<leading>\s*)(?P<head>\S+)(?P<arguments>.*)", segment)
        if match is None:
            continue
        parts[index] = (
            match["leading"]
            + match["head"]
            + _replace_identifiers(match["arguments"], replacements)
        )
    return "".join(parts)


def _semantic_destructuring(tactic: str, proof_state: str) -> str:
    declarations, occupied = _first_goal_declarations(proof_state)
    proposition_for = dict(declarations)
    pair = _RCases_PAIR.fullmatch(tactic)
    legacy = _LEAN3_CASES.fullmatch(tactic)
    summation = _RCASES_SUM.fullmatch(tactic)
    subject = (
        pair["subject"] if pair is not None
        else legacy["subject"] if legacy is not None
        else summation["subject"] if summation is not None
        else None
    )
    proposition = proposition_for.get(subject or "")
    root = None if proposition is None else _root_connective(proposition)
    if root is None:
        return tactic
    connective, left_type, right_type = root
    left_name = _fresh_semantic_name(left_type, "Left", occupied)
    right_name = _fresh_semantic_name(right_type, "Right", occupied)
    if pair is not None and connective == "∧":
        suffix = pair["suffix"]
        if pair["left"] == pair["right"] or not _suffix_is_safe_for_renaming(suffix):
            return tactic
        suffix = _replace_continuation_identifiers(
            suffix,
            {pair["left"]: left_name, pair["right"]: right_name},
        )
        return f"rcases {subject} with ⟨{left_name},{right_name}⟩{suffix}"
    if legacy is not None:
        if connective == "∧":
            return f"rcases {subject} with ⟨{left_name},{right_name}⟩"
        if connective == "∨":
            return f"rcases {subject} with {left_name} | {right_name}"
    if summation is not None and connective == "∨":
        return f"rcases {subject} with {left_name} | {right_name}"
    return tactic


def normalize_lean4_tactic(text: str, proof_state: str | None = None) -> str:
    canonical = " ".join(text.split())
    if canonical.startswith("by "):
        canonical = canonical[3:]
    for pattern, replacement in _LEGACY_NAMES:
        canonical = pattern.sub(replacement, canonical)
    if canonical == "split":
        canonical = "constructor"
    bare_rw = _BARE_RW.fullmatch(canonical)
    if bare_rw is not None:
        canonical = f"rw [{bare_rw['hyp']}]"
    if proof_state is not None:
        canonical = _semantic_destructuring(canonical, proof_state)
    return canonical


def _first_goal_target(proof_state: str) -> str | None:
    lines = proof_state.splitlines()
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("⊢"):
            continue
        target = [line.lstrip()[1:].strip()]
        for continuation in lines[index + 1 :]:
            if continuation.startswith("case ") or continuation.lstrip().startswith("⊢"):
                break
            if continuation.strip():
                target.append(continuation.strip())
        return " ".join(target).strip()
    return None


def candidate_rejection_reason(
    text: str,
    proof_state: str | None,
    *,
    excluded_tactics: set[str] | frozenset[str] = frozenset(),
) -> str | None:
    """Return why a normalized one-line tactic must not be sent to Lean."""

    tactic = normalize_lean4_tactic(text, proof_state)
    if _BANNED_TACTIC_TOKEN.search(tactic):
        return "banned or unknown tactic"
    if _NESTED_TACTIC_BLOCK.search(tactic):
        return "unsupported nested tactic block"
    segments = [item.strip() for item in re.split(r"\s*(?:<;>|;)\s*", tactic)]
    if not segments or any(not item for item in segments):
        return "unsupported tactic syntax"
    heads = [segment.split(maxsplit=1)[0] for segment in segments]
    if any(head not in _ALLOWED_TACTIC_HEADS for head in heads):
        return "banned or unknown tactic"
    if any(segment in _INCOMPLETE_TACTIC_HEADS for segment in segments):
        return "incomplete tactic"
    if any(head in _SHAPE_SENSITIVE_HEADS for head in heads[1:]):
        return "shape-sensitive continuation requires a separate Lean call"
    head = heads[0]
    if tactic in excluded_tactics:
        return "already tried at this state"
    if proof_state is None:
        return None
    target = _first_goal_target(proof_state)
    if target is None:
        return None
    declarations, _ = _first_goal_declarations(proof_state)
    declaration_types = dict(declarations)
    if head in {"exact", "apply", "cases", "rcases"}:
        parts = segments[0].split(maxsplit=2)
        if len(parts) >= 2 and _SIMPLE_IDENTIFIER.fullmatch(parts[1]):
            operand = parts[1]
            if declaration_types.get(operand) == "Prop":
                return "proposition name is not evidence"
            if operand not in declaration_types:
                return "identifier is not visible in the proof state"
    root = _root_connective(target)
    connective = None if root is None else root[0]
    if head in {"left", "right"} and connective != "∨":
        return "requires a disjunction goal"
    if head == "constructor" and connective not in {"∧", "↔", "∨"}:
        return "requires a conjunction, equivalence, or disjunction goal"
    universal = _strip_outer_parentheses(target).startswith(("∀", "forall "))
    if head in {"intro", "intros"} and connective != "→" and not _strip_outer_parentheses(target).startswith("¬") and not universal:
        return "requires an implication, negation, or universal goal"
    return None


def tactic_is_incomplete(text: str) -> bool:
    return normalize_lean4_tactic(text) in _INCOMPLETE_TACTIC_HEADS


__all__ = [
    "FilteredCandidatePool", "IncompleteCandidatePool", "ModelOutputError",
    "candidate_rejection_reason", "normalize_lean4_tactic", "tactic_is_incomplete",
]
