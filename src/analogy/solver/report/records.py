"""Structured evidence from one solver invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import AppConfig
from ...lean_gym.models import Problem
from ...utils.checkpoint import Checkpoint
from ...utils.records import ProblemResult, SolveSummary


@dataclass(frozen=True, slots=True)
class SolveRunRecord:
    """Stored metadata and results from one completed solve invocation."""

    run_id: str
    analogy_enabled: bool
    summary: SolveSummary
    elapsed_seconds: float
    output_directory: Path


@dataclass(frozen=True, slots=True)
class ProblemRunRecord:
    problem: Problem
    outcome: ProblemResult
    payload: dict[str, Any]
    trace: str


@dataclass(slots=True)
class RunState:
    """Mutable collection of report records for one solve invocation."""

    config: AppConfig
    checkpoint: Checkpoint | None
    run_id: str
    output_directory: Path
    started: float
    model_revision: str | None = None
    analogy_model_revision: str | None = None
    results: list[ProblemResult] = field(default_factory=list)
    traces: list[str] = field(default_factory=list)


__all__ = ["ProblemRunRecord", "RunState", "SolveRunRecord"]
