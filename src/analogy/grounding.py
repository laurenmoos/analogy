"""Deterministic grounding vocabulary shared by config and compilation."""

from __future__ import annotations

from enum import Enum


class GroundingOperator(str, Enum):
    EXACT_LOCAL = "exact_local"
    INTRODUCE = "introduce"
    APPLY_IMPLICATION = "apply_implication"
    IFF_FORWARD = "iff_forward"
    IFF_BACKWARD = "iff_backward"
    REWRITE_FORWARD = "rewrite_forward"
    REWRITE_BACKWARD = "rewrite_backward"
    SPLIT = "split"
    SELECT_LEFT = "select_left"
    SELECT_RIGHT = "select_right"
    CHOOSE_LEFT = "choose_left"
    CHOOSE_RIGHT = "choose_right"
    CASES = "cases"
    CONTRADICTION = "contradiction"
    COMPOSE = "compose"


__all__ = ["GroundingOperator"]
