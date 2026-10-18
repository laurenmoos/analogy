"""Persistent records for a solver invocation.

This module stores what happened during a run; it does not perform proof search.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..lean_gym.models import Problem
from ..model.prompts import configure_prompt_catalog
from ..utils.checkpoint import Checkpoint, record_status
from ..utils.records import (
    DiscardReason,
    ProblemResult,
    SearchStatus,
    SolveStatus,
    SolveSummary,
    TacticStatus,
)
from .model_policy import TacticRankingError
from .report import render_problem_trace, write_run_report


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
    """Mutable collection of records accumulated by one solve invocation."""

    config: AppConfig
    checkpoint: Checkpoint | None
    run_id: str
    output_directory: Path
    started: float
    results: list[ProblemResult] = field(default_factory=list)
    traces: list[str] = field(default_factory=list)


def start_run(config: AppConfig, checkpoint: Checkpoint | None = None) -> RunState:
    """Create storage for a run before any proof computation begins."""

    configure_prompt_catalog(config.prompts.path)
    run_id = uuid.uuid4().hex
    output_directory = config.run.output_dir / "solve" / run_id
    output_directory.mkdir(parents=True, exist_ok=False)
    return RunState(config, checkpoint, run_id, output_directory, time.monotonic())


def _problem_result(problem_id: str, search_result: Any) -> ProblemResult:
    ordinary = tuple(
        attempt
        for expansion in search_result.ordinary_expansions
        for attempt in expansion.attempts
    )
    fallback = tuple(
        attempt.result
        for rollout in search_result.rescue_rollouts
        for attempt in rollout.attempts
    )
    forced = tuple(event.result for event in search_result.forced_moves)
    all_attempts = ordinary + forced + fallback
    discarded = tuple(
        item
        for expansion in search_result.ordinary_expansions
        for item in expansion.discarded
    )
    status = (
        SolveStatus.COMPLETE
        if search_result.status is SearchStatus.COMPLETE
        else SolveStatus.BUDGET_EXHAUSTED
        if search_result.status is SearchStatus.BUDGET_EXHAUSTED
        else SolveStatus.SEMANTIC_FAILURE
        if search_result.status is SearchStatus.SEMANTIC_FAILURE
        else SolveStatus.EXHAUSTED
    )
    failure = None
    if status is SolveStatus.SEMANTIC_FAILURE:
        event = search_result.semantic_recoveries[-1]
        failure = (
            f"semantic check failed: {event.error}"
            if event.error is not None
            else "no propositionally valid rescue ancestor"
        )
    return ProblemResult(
        problem_id=problem_id,
        status=status,
        proof=search_result.proof,
        failure=failure,
        expansion_count=len(search_result.ordinary_expansions),
        verifier_call_count=len(all_attempts),
        invalid_tactic_count=sum(item.status is TacticStatus.ERROR for item in all_attempts),
        repeated_state_count=sum(item.reason is DiscardReason.REPEATED_STATE for item in discarded),
        no_progress_count=sum(item.reason is DiscardReason.NO_PROGRESS for item in discarded),
        stuck_state_count=sum(item.is_stuck for item in search_result.stuckness_decisions),
        analogy_fallback_count=len(fallback),
    )


def _failed_problem_result(
    problem_id: str, error: BaseException
) -> ProblemResult:
    status = (
        SolveStatus.MODEL_FAILURE
        if isinstance(error, TacticRankingError)
        else SolveStatus.VERIFIER_FAILURE
    )
    failure = str(error) if status is SolveStatus.MODEL_FAILURE else f"{type(error).__name__}: {error}"
    return ProblemResult(problem_id, status, None, failure, 0, 0, 0, 0, 0, 0, 0)


def _problem_route(outcome: ProblemResult, search_result: Any | None) -> str:
    if search_result is not None and search_result.rescue_rollouts:
        return "analogy-assisted"
    if getattr(outcome.status, "value", outcome.status) == SolveStatus.COMPLETE.value:
        return "ordinary"
    return "unsolved"


def _public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _public_payload(item)
            for key, item in value.items()
            if key != "seed"
        }
    if isinstance(value, (list, tuple)):
        return [_public_payload(item) for item in value]
    return value


def build_problem_record(
    problem: Problem,
    search: Any | None,
    model_events: list[dict[str, Any]],
    error: BaseException | None = None,
) -> ProblemRunRecord:
    """Convert a completed or failed search into durable run evidence."""

    search_result = None if search is None or error is not None else search.result
    outcome = (
        _failed_problem_result(problem.id, error)
        if error is not None
        else _problem_result(problem.id, search_result)
    )
    payload = _public_payload(
        {
            "problem": asdict(problem),
            "outcome": asdict(outcome),
            "search": None if search_result is None else asdict(search_result),
            "model_events": model_events,
        }
    )
    trace = render_problem_trace(
        payload, model_events, route=_problem_route(outcome, search_result)
    )
    return ProblemRunRecord(problem, outcome, payload, trace)


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def write_problem_record(run: RunState, record: ProblemRunRecord) -> None:
    """Persist one problem and add it to the in-memory run record."""

    _write_json(run.output_directory / f"{record.problem.id}.json", record.payload)
    if run.checkpoint is not None:
        record_status(run.checkpoint, record.problem.id, record.outcome.status.value)
    run.traces.append(record.trace)
    run.results.append(record.outcome)


def build_run_record(
    run: RunState, *, label: str, analogy_enabled: bool
) -> SolveRunRecord:
    summary = SolveSummary(label, tuple(run.results), time.monotonic() - run.started)
    return SolveRunRecord(
        run.run_id,
        analogy_enabled,
        summary,
        time.monotonic() - run.started,
        run.output_directory,
    )


def write_run_record(run_state: RunState, run: SolveRunRecord) -> None:
    """Persist final run metadata and its human-readable report."""

    config = run_state.config
    checkpoint = run_state.checkpoint
    _write_json(
        run.output_directory / "run.json",
        {
            "run_id": run.run_id,
            "analogy_enabled": run.analogy_enabled,
            "model": config.model.name,
            "top_k": config.search.top_k,
            "max_steps": config.search.max_steps,
            "max_analogy_model_calls_per_stuck_state": config.analogy.max_model_calls,
            "problem_index_signature": checkpoint.signature if checkpoint is not None else None,
            "problem_index_fingerprints": checkpoint.fingerprints if checkpoint is not None else None,
            "elapsed_seconds": run.elapsed_seconds,
            "summary": asdict(run.summary),
        },
    )
    write_run_report(run, run_state.traces)


__all__ = [
    "ProblemRunRecord",
    "RunState",
    "SolveRunRecord",
    "build_problem_record",
    "build_run_record",
    "start_run",
    "write_problem_record",
    "write_run_record",
]

