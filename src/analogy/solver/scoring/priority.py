"""Frontier priority derived from tactic confidence and state novelty."""

from __future__ import annotations

from ...utils.records import SearchNode


def search_priority(node: SearchNode, novelty: float) -> tuple[float, float]:
    """Return the heap key prefix: step score first, novelty second."""

    return (-(node.score - 0.02 * node.depth), -novelty)


__all__ = ["search_priority"]
