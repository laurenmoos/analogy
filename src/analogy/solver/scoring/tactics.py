"""Validation and normalization for model-assigned tactic scores."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence

from ...model.parser import (
    FilteredCandidatePool,
    IncompleteCandidatePool,
    ModelOutputError,
    candidate_rejection_reason,
    normalize_lean4_tactic,
)
from ...utils.records import CandidateOrigin, TacticCandidate


def normalize_tactic_scores(
    candidates: Sequence[TacticCandidate],
) -> tuple[TacticCandidate, ...]:
    """Normalize candidate weights without changing their order or origin."""

    values = tuple(candidates)
    total = sum(item.score for item in values)
    if total <= 0.0:
        raise ModelOutputError("selected candidate scores must not all be zero")
    return tuple(
        TacticCandidate(item.source, item.score / total, item.origin, item.model_rank)
        for item in values
    )


def parse_scored_tactic_candidates(
    text: str,
    top_k: int | None,
    *,
    discarded_candidates: list[str] | None = None,
    proof_state: str | None = None,
    excluded_tactics: set[str] | frozenset[str] = frozenset(),
) -> tuple[TacticCandidate, ...]:
    """Parse, filter, deduplicate, truncate, and normalize tactic weights."""

    if top_k is not None and (
        isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0
    ):
        raise ValueError("top_k must be a positive integer or None")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ModelOutputError("candidate output must be valid JSON") from error
    if not isinstance(value, list) or not value:
        raise ModelOutputError("candidate output must be a nonempty JSON array")

    order: list[str] = []
    weights: dict[str, float] = {}
    incomplete: list[str] = []
    filtered: list[str] = []
    for item in value:
        if not isinstance(item, dict) or set(item) not in (
            {"tactic"},
            {"tactic", "score"},
        ):
            raise ModelOutputError(
                "every candidate must contain tactic and may contain score"
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
        score = item.get("score", 1.0)
        if "score" in item:
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
                or float(score) < 0.0
            ):
                raise ModelOutputError(
                    "candidate scores must be finite nonnegative numbers"
                )
        canonical = normalize_lean4_tactic(tactic, proof_state)
        reason = candidate_rejection_reason(
            canonical,
            proof_state,
            excluded_tactics=excluded_tactics,
        )
        if reason == "incomplete tactic":
            incomplete.append(canonical)
            if discarded_candidates is not None:
                discarded_candidates.append(f"{canonical}: {reason}")
            continue
        if reason is not None:
            filtered.append(f"{canonical}: {reason}")
            if discarded_candidates is not None:
                discarded_candidates.append(f"{canonical}: {reason}")
            continue
        if canonical not in weights:
            order.append(canonical)
            weights[canonical] = float(score)
        else:
            if discarded_candidates is not None:
                discarded_candidates.append(f"{canonical}: duplicate proposal")

    if not order:
        if filtered:
            raise FilteredCandidatePool(
                "all generated tactics were filtered: " + ", ".join(filtered)
            )
        rejected = ", ".join(dict.fromkeys(incomplete))
        raise IncompleteCandidatePool(
            f"all generated tactics were incomplete: {rejected}"
        )
    selected = order if top_k is None else order[:top_k]
    neutral = 1.0 / len(selected)
    return tuple(
        TacticCandidate(
            tactic,
            neutral,
            CandidateOrigin.ORDINARY,
        )
        for tactic in selected
    )


__all__ = ["normalize_tactic_scores", "parse_scored_tactic_candidates"]
