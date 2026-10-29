"""Shared validation for initialized and recovered mental models."""

from __future__ import annotations

import re
from dataclasses import replace
from ...model.parser import candidate_rejection_reason, normalize_lean4_tactic
from .domains import canonical_analogy_domain
from .state import MentalModel, MentalModelCorrespondence, StoryModel


class MentalModelValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "validation") -> None:
        super().__init__(message)
        self.code = code


_FORBIDDEN_DOMAIN_WORDS = re.compile(
    r"\b(?:lean|proofs?|tactics?|logic(?:al)?|mathematics?|propositions?|conjunctions?|disjunctions?|implications?)\b",
    re.IGNORECASE,
)
_PROPISH = re.compile(r"\bp\d+\b", re.IGNORECASE)
def validate_mental_model(
    model: MentalModel,
    proof_state: str | None = None,
    *,
    required_domain: str | None = None,
    abandoned_domains: tuple[str, ...] = (),
    proof_transitions: tuple[str, ...] | None = None,
) -> MentalModel:
    canonical = canonical_analogy_domain(model.domain)
    if canonical is None:
        raise MentalModelValidationError(
            f"domain {model.domain!r} is not one canonical domain from the card",
            code="domain",
        )
    required_canonical = (
        canonical_analogy_domain(required_domain)
        if required_domain is not None else None
    )
    if required_domain is not None and canonical != required_canonical:
        raise MentalModelValidationError(
            f"domain must be the harness-assigned domain {required_domain!r}",
            code="domain",
        )
    if model.abandoned_domains:
        raise MentalModelValidationError(
            "abandoned_domains is harness-owned; the model must return an empty list",
            code="domain_ledger",
        )
    if proof_transitions is not None and len(model.correspondences) != len(
        proof_transitions
    ):
        raise MentalModelValidationError(
            f"mental model has {len(model.correspondences)} correspondences but "
            f"the trajectory has {len(proof_transitions)} proof transitions",
            code="count",
        )
    for item in model.correspondences:
        for value in (item.domain_before, item.domain_transition, item.domain_after):
            forbidden = _FORBIDDEN_DOMAIN_WORDS.search(value)
            if forbidden is not None:
                raise MentalModelValidationError(
                    f"domain field contains forbidden word {forbidden.group(0)!r}",
                    code="forbidden_vocabulary",
                )
            propish = _PROPISH.search(value)
            if propish is not None:
                raise MentalModelValidationError(
                    f"domain field contains proof identifier {propish.group(0)!r}",
                    code="forbidden_vocabulary",
                )
        if proof_state and item.proof_transition is not None:
            tactic = normalize_lean4_tactic(item.proof_transition, proof_state)
            reason = candidate_rejection_reason(tactic, proof_state)
            if reason is not None:
                raise MentalModelValidationError(
                    f"tactic {tactic!r} was filtered: {reason}",
                    code="tactic",
                )
    for previous, current in zip(
        model.correspondences, model.correspondences[1:]
    ):
        if previous.domain_after != current.domain_before:
            raise MentalModelValidationError(
                "mental-model correspondences must form one continuous everyday sequence",
                code="continuity",
            )
    correspondences = model.correspondences
    if proof_transitions is not None:
        correspondences = tuple(
            replace(item, proof_transition=tactic)
            for item, tactic in zip(model.correspondences, proof_transitions)
        )
    return replace(
        model,
        domain=canonical if required_domain is not None else model.domain,
        correspondences=correspondences,
        abandoned_domains=tuple(abandoned_domains),
    )


def validate_story_model(
    story: StoryModel,
    *,
    required_domain: str,
    proof_transitions: tuple[str, ...],
    abandoned_domains: tuple[str, ...] = (),
) -> MentalModel:
    """Validate identifier-blind story text, then attach harness-owned tactics."""

    model = MentalModel(
        story.domain,
        tuple(
            MentalModelCorrespondence(
                item.domain_before,
                item.domain_transition,
                item.domain_after,
            )
            for item in story.correspondences
        ),
        story.abandoned_domains,
    )
    return validate_mental_model(
        model,
        required_domain=required_domain,
        abandoned_domains=abandoned_domains,
        proof_transitions=proof_transitions,
    )


__all__ = [
    "MentalModelValidationError",
    "validate_mental_model",
    "validate_story_model",
]
