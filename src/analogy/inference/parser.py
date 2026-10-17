"""Strict parsing and normalization for model-generated Lean tactics."""

from __future__ import annotations

import json
import math
import re

from ..records import CandidateOrigin, TacticCandidate


class ModelOutputError(ValueError):
    """Raised when generated text is not a valid tactic sequence."""


_LEAN3_CASES = re.compile(
    r"^cases\s+(?P<subject>«[^»]+»|[A-Za-z_][\w'✝]*)\s+with\s+"
    r"(?P<left>[A-Za-z_][\w'✝]*)\s+(?P<right>[A-Za-z_][\w'✝]*)$"
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
)


def normalize_lean4_tactic(text: str) -> str:
    canonical = " ".join(text.split())
    if canonical.startswith("by "):
        canonical = canonical[3:]
    for pattern, replacement in _LEGACY_NAMES:
        canonical = pattern.sub(replacement, canonical)
    match = _LEAN3_CASES.fullmatch(canonical)
    return canonical if match is None else f"cases {match['subject']}"


def parse_tactic_candidates(
    text: str,
    top_k: int,
) -> tuple[TacticCandidate, ...]:
    """Parse, deduplicate, truncate, and normalize ranked tactic weights."""

    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ModelOutputError("candidate output must be valid JSON") from error
    if not isinstance(value, list) or not value:
        raise ModelOutputError("candidate output must be a nonempty JSON array")

    order: list[str] = []
    weights: dict[str, float] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"tactic", "score"}:
            raise ModelOutputError(
                "every candidate must contain exactly tactic and score"
            )
        tactic = item["tactic"]
        if (
            not isinstance(tactic, str)
            or not tactic.strip()
            or "\n" in tactic
            or "\r" in tactic
            or "\x00" in tactic
        ):
            raise ModelOutputError("candidate tactics must be nonblank single lines")
        score = item["score"]
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
            or float(score) < 0.0
        ):
            raise ModelOutputError(
                "candidate scores must be finite nonnegative numbers"
            )
        canonical = normalize_lean4_tactic(tactic)
        if canonical not in weights:
            order.append(canonical)
            weights[canonical] = float(score)
        else:
            weights[canonical] = max(weights[canonical], float(score))

    selected = order[:top_k]
    total = sum(weights[tactic] for tactic in selected)
    if total <= 0.0:
        raise ModelOutputError("selected candidate scores must not all be zero")
    return tuple(
        TacticCandidate(
            tactic,
            weights[tactic] / total,
            CandidateOrigin.ORDINARY,
        )
        for tactic in selected
    )


__all__ = [
    "ModelOutputError",
    "normalize_lean4_tactic",
    "parse_tactic_candidates",
]
