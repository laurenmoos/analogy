"""Canonical everyday domains owned by the analogy harness."""

from __future__ import annotations

ANALOGY_DOMAINS = (
    "cooking or restaurant preparation",
    "travel or navigation",
    "building, maintenance, or construction",
    "board, card, or team game",
    "gardening",
    "package delivery or logistics",
    "household organization",
    "mechanical repair",
)

_ALIASES = {
    "cooking": ANALOGY_DOMAINS[0],
    "restaurant": ANALOGY_DOMAINS[0],
    "restaurant preparation": ANALOGY_DOMAINS[0],
    "travel": ANALOGY_DOMAINS[1],
    "navigation": ANALOGY_DOMAINS[1],
    "building": ANALOGY_DOMAINS[2],
    "maintenance": ANALOGY_DOMAINS[2],
    "construction": ANALOGY_DOMAINS[2],
    "board game": ANALOGY_DOMAINS[3],
    "card game": ANALOGY_DOMAINS[3],
    "team game": ANALOGY_DOMAINS[3],
    "package delivery": ANALOGY_DOMAINS[5],
    "logistics": ANALOGY_DOMAINS[5],
}


def canonical_analogy_domain(value: str) -> str | None:
    normalized = " ".join(value.casefold().split())
    for domain in ANALOGY_DOMAINS:
        if normalized == domain.casefold():
            return domain
    return _ALIASES.get(normalized)


def next_unused_domain(used: tuple[str, ...] | list[str]) -> str:
    canonical_used = {
        canonical for item in used
        if (canonical := canonical_analogy_domain(item)) is not None
    }
    for domain in ANALOGY_DOMAINS:
        if domain not in canonical_used:
            return domain
    raise ValueError("all analogy domains have been abandoned")


__all__ = ["ANALOGY_DOMAINS", "canonical_analogy_domain", "next_unused_domain"]

