"""One proof-search runtime with an optional analogy fallback."""

from __future__ import annotations

import html
import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from ..config import AppConfig
from ..inference.model_provider import TextGenerator
from ..inference.prompts import configure_prompt_catalog
from ..lean_gym.models import Problem
from ..lean_gym.verifier import Verifier
from ..records import (
    DiscardReason,
    InferenceProblemResult,
    InferenceRun,
    InferenceStatus,
    SearchStatus,
    SymbolicState,
    TacticStatus,
)
from .diversity import FrozenEmbeddingDiversity
from .episode import EpisodeContext, run_episode
from .lean_adapter import LeanGymAdapter
from .model_policy import TacticRanker, TacticRankingError
from .rescue import AnalogyFallback
from .search import (
    AcceptNovelState,
    CanonicalStateFingerprinter,
    NeverCancelled,
    ObservableStucknessPolicy,
    WhitespaceTacticCanonicalizer,
)


@dataclass(frozen=True, slots=True)
class SolveRun:
    run_id: str
    analogy_enabled: bool
    inference: InferenceRun
    elapsed_seconds: float
    output_directory: Path


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _pre(value: Any) -> str:
    return f"<pre><code>{html.escape(str(value))}</code></pre>"


def _attempt_line(attempt: dict[str, Any], *, fallback: bool = False) -> str:
    result = attempt.get("result", attempt)
    candidate = (
        attempt.get("proposal", {}).get("candidate", {})
        if fallback
        else result.get("candidate", {})
    )
    source = candidate.get("source", "(unknown tactic)")
    discarded = attempt.get("discarded")
    if discarded:
        reason = _enum_value(discarded.get("reason", "discarded"))
        status = {
            "repeated_state": "repeated state",
            "no_progress": "no progress",
        }.get(reason, str(reason).replace("_", " "))
    else:
        raw_status = _enum_value(result.get("status", "unknown"))
        status = "progress" if raw_status == "accepted" else raw_status
    diagnostics = "; ".join(result.get("diagnostics") or ())
    suffix = f" — {diagnostics}" if diagnostics else ""
    lines = [f"- `{source}` → **{status}**{suffix}"]
    if result.get("state"):
        lines.extend(("", _pre(result["state"].get("expression", result["state"]))))
    if result.get("proof"):
        lines.extend(("", _pre(result["proof"])))
    return "\n".join(lines)


def render_problem_trace(
    detail: dict[str, Any],
    model_events: Sequence[dict[str, Any]],
    *,
    route: str,
) -> str:
    problem = detail["problem"]
    outcome = detail["outcome"]
    search = detail.get("search")
    status = _enum_value(outcome["status"])
    icon = "✅" if status == InferenceStatus.COMPLETE.value else "❌"
    binders = f" {problem.get('binders', '')}" if problem.get("binders") else ""
    lines = [
        "<details>",
        f"<summary>{icon} `{problem['id']}` — {status} — {route}</summary>",
        "",
        "### Problem",
        "",
        _pre(f"theorem current_problem{binders} : {problem['target']} := by"),
        "",
        f"Lean calls: **{outcome.get('verifier_call_count', 0)}** · "
        f"invalid tactics: **{outcome.get('invalid_tactic_count', 0)}** · "
        f"stuck states: **{outcome.get('stuck_state_count', 0)}** · "
        f"Analogy fallbacks: **{outcome.get('analogy_fallback_count', 0)}**",
    ]
    if outcome.get("failure"):
        lines.extend(("", f"**Failure:** {outcome['failure']}"))
    lines.extend(("", "### Model trace"))
    if not model_events:
        lines.extend(("", "No completed model call was recorded."))
    for index, event in enumerate(model_events, 1):
        kind = str(event.get("kind", "ordinary_model_call")).removesuffix("_model_call")
        lines.extend(("", f"#### Model call {index} — {kind}"))
        if event.get("state_id"):
            lines.extend(("", f"State: `{event['state_id']}`"))
        for message in event.get("messages", ()):
            lines.extend(("", f"**{str(message.get('role', 'message')).title()} prompt**", "", _pre(message.get("content", ""))))
        responses = event.get("responses") or ({"raw_text": event.get("response", ""), "seed": event.get("seed")},)
        for response in responses:
            seed = response.get("seed")
            label = f"Response (seed {seed})" if seed is not None else "Response"
            lines.extend(("", f"**{label}**", "", _pre(response.get("raw_text", ""))))
    lines.extend(("", "### Lean trace"))
    if search is None:
        lines.extend(("", "Search did not start."))
    else:
        for index, expansion in enumerate(search.get("ordinary_expansions", ()), 1):
            lines.extend(("", f"#### Lean expansion {index}"))
            for attempt in expansion.get("attempts", ()):
                lines.extend(("", _attempt_line(attempt)))
        for index, rollout in enumerate(search.get("rescue_rollouts", ()), 1):
            termination = _enum_value(rollout.get("termination", "unknown"))
            lines.extend(("", f"#### Analogy fallback {index} — {termination}"))
            if rollout.get("analogy_trace"):
                lines.extend(("", f"**Analogy:** {rollout['analogy_trace']}"))
            for attempt in rollout.get("attempts", ()):
                lines.extend(("", _attempt_line(attempt, fallback=True)))
    lines.extend(("", "### Final outcome", ""))
    lines.append(_pre(outcome["proof"]) if outcome.get("proof") else outcome.get("failure") or f"Stopped: {status}")
    lines.extend(("", "</details>"))
    return "\n".join(lines)


def _problem_result(problem_id: str, search_result: Any) -> InferenceProblemResult:
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
    all_attempts = ordinary + fallback
    discarded = tuple(
        item
        for expansion in search_result.ordinary_expansions
        for item in expansion.discarded
    )
    status = (
        InferenceStatus.COMPLETE
        if search_result.status is SearchStatus.COMPLETE
        else InferenceStatus.BUDGET_EXHAUSTED
        if search_result.status is SearchStatus.BUDGET_EXHAUSTED
        else InferenceStatus.EXHAUSTED
    )
    return InferenceProblemResult(
        problem_id=problem_id,
        status=status,
        proof=search_result.proof,
        failure=None,
        expansion_count=len(search_result.ordinary_expansions),
        verifier_call_count=len(all_attempts),
        invalid_tactic_count=sum(item.status is TacticStatus.ERROR for item in all_attempts),
        repeated_state_count=sum(item.reason is DiscardReason.REPEATED_STATE for item in discarded),
        no_progress_count=sum(item.reason is DiscardReason.NO_PROGRESS for item in discarded),
        stuck_state_count=sum(item.is_stuck for item in search_result.stuckness_decisions),
        analogy_fallback_count=len(fallback),
    )


def _failed_problem_result(problem_id: str, status: InferenceStatus, failure: str) -> InferenceProblemResult:
    return InferenceProblemResult(problem_id, status, None, failure, 0, 0, 0, 0, 0, 0, 0)


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _run_runtime(
    config: AppConfig,
    problems: Sequence[Problem],
    generator: TextGenerator,
    verifier: Verifier,
    *,
    run_directory: Path,
) -> tuple[InferenceRun, list[str], dict[str, dict[str, Any]]]:
    started = time.monotonic()
    details: dict[str, dict[str, Any]] = {}
    traces: list[str] = []
    results: list[InferenceProblemResult] = []
    for problem in problems:
        model_events: list[dict[str, Any]] = []
        search_result = None
        try:
            lean = LeanGymAdapter(problem, verifier)
            search_result = run_episode(
                EpisodeContext(
                    tactic_ranker=TacticRanker(
                        generator,
                        top_k=config.search.top_k,
                        seed=config.run.seed,
                        failed_tactics_for=lean.failed_tactics_for,
                        trace_sink=model_events.append,
                    ),
                    verifier=lean,
                    stuckness_policy=ObservableStucknessPolicy(),
                    state_fingerprinter=CanonicalStateFingerprinter(),
                    tactic_canonicalizer=WhitespaceTacticCanonicalizer(),
                    progress_policy=AcceptNovelState(),
                    cancellation=NeverCancelled(),
                    analogy_enabled=config.analogy.enabled,
                    analogy_fallback=AnalogyFallback(
                        generator,
                        max_model_calls=config.analogy.max_model_calls,
                        seed=config.run.seed,
                        trace_sink=model_events.append,
                    ),
                    ordinary_width=config.search.top_k,
                    max_expansions=config.search.max_steps,
                    state_diversity=FrozenEmbeddingDiversity(
                        generator, layer=config.model.hidden_layer, neighbors=3
                    ),
                ),
                lean.initial_state(),
                SymbolicState(f"{problem.id}:goal", problem.target, "lean4"),
            )
            outcome = _problem_result(problem.id, search_result)
        except TacticRankingError as error:
            outcome = _failed_problem_result(problem.id, InferenceStatus.MODEL_FAILURE, str(error))
        except Exception as error:
            outcome = _failed_problem_result(problem.id, InferenceStatus.VERIFIER_FAILURE, f"{type(error).__name__}: {error}")
        detail = {
            "problem": asdict(problem),
            "outcome": asdict(outcome),
            "search": None if search_result is None else asdict(search_result),
        }
        details[problem.id] = detail
        _write_json(run_directory / f"{problem.id}.json", detail)
        route = (
            "analogy-assisted"
            if outcome.analogy_fallback_count
            else "ordinary"
            if outcome.status is InferenceStatus.COMPLETE
            else "unsolved"
        )
        traces.append(render_problem_trace(detail, model_events, route=route))
        results.append(outcome)
    label = "analogy-on" if config.analogy.enabled else "analogy-off"
    return InferenceRun(label, tuple(results), time.monotonic() - started), traces, details


def solve_problems(
    config: AppConfig,
    problems: Sequence[Problem],
    generator: TextGenerator,
    verifier: Verifier,
) -> SolveRun:
    configure_prompt_catalog(config.prompts.path)
    started = time.monotonic()
    run_id = uuid.uuid4().hex
    run_directory = config.run.output_dir / "solve" / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    inference, traces, _details = _run_runtime(
        config, problems, generator, verifier, run_directory=run_directory
    )
    run = SolveRun(
        run_id,
        config.analogy.enabled,
        inference,
        time.monotonic() - started,
        run_directory,
    )
    _write_json(
        run_directory / "run.json",
        {
            "run_id": run_id,
            "analogy_enabled": config.analogy.enabled,
            "model": config.model.name,
            "seed": config.run.seed,
            "top_k": config.search.top_k,
            "max_steps": config.search.max_steps,
            "max_analogy_model_calls_per_stuck_state": config.analogy.max_model_calls,
            "elapsed_seconds": run.elapsed_seconds,
            "inference": asdict(inference),
        },
    )
    report = [
        "# Proof search run",
        "",
        f"- Analogy fallback: {'enabled' if config.analogy.enabled else 'disabled'}",
        f"- Solved: {inference.solved_count}/{inference.attempted_count}",
        f"- Analogy tactics submitted to Lean: {sum(item.analogy_fallback_count for item in inference.results)}",
        "",
        "## Detailed traces",
        "",
        *traces,
    ]
    (run_directory / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return run


__all__ = ["SolveRun", "render_problem_trace", "solve_problems"]
