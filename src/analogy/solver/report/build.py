"""Build structured report records from completed solver searches."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from ...lean_gym.models import Problem
from ...utils.records import (
    DiscardReason,
    ProblemResult,
    SearchStatus,
    SolveStatus,
    SolveSummary,
    TacticStatus,
)
from ..search.tactic_generator import TacticGenerationError
from .records import ProblemRunRecord, RunState, SolveRunRecord
from .render import render_problem_trace


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
        if isinstance(error, TacticGenerationError)
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


__all__ = [
    "build_problem_record",
    "build_run_record",
]

