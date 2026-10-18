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
from ..model.model_provider import TextGenerator
from ..model.prompts import configure_prompt_catalog
from ..lean_gym.models import Problem
from ..lean_gym.verifier import Verifier
from ..problem_index import ProblemIndex
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
from .analogy_state import AnalogyScratchpad
from .episode import EpisodeContext, run_episode
from .lean_adapter import LeanGymAdapter
from .model_policy import TacticRanker, TacticRankingError
from .rescue import AnalogyFallback
from .semantic import ExhaustiveSemanticChecker
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
            "semantic_refuted": "SAT-refuted successor",
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


def _append_rollout(
    lines: list[str], rollout: dict[str, Any], index: int
) -> None:
    termination = _enum_value(rollout.get("termination", "unknown"))
    lines.extend(("", f"#### Analogy fallback {index} — {termination}"))
    for generation in rollout.get("generations", ()):
        label = "Generation"
        if generation.get("status"):
            label += f" — {_enum_value(generation['status'])}"
        if generation.get("repair"):
            label += " — retry"
        if generation.get("prediction_label"):
            label += f" — {_enum_value(generation['prediction_label'])}"
        lines.extend(("", f"**{label}**"))
        diagnostic = generation.get("diagnostic")
        if diagnostic:
            kind = generation.get("error_kind")
            prefix = f"{kind}: " if kind else ""
            lines.extend(("", f"**Failure:** {prefix}{diagnostic}"))
    if rollout.get("analogy_trace"):
        lines.extend(("", f"**Analogy:** {rollout['analogy_trace']}"))
    for attempt in rollout.get("attempts", ()):
        lines.extend(("", _attempt_line(attempt, fallback=True)))


def _append_semantic_event(
    lines: list[str], event: dict[str, Any], index: int
) -> None:
    result = _enum_value(event.get("result")) if event.get("result") else "error"
    lines.extend(("", f"#### Semantic recovery {index} — {result}"))
    lines.extend(("", f"Trigger state: `{event.get('trigger_state_id', '(unknown)')}`"))
    countermodel = event.get("countermodel", ())
    if countermodel:
        assignments = ", ".join(
            f"{atom}={'true' if value else 'false'}" for atom, value in countermodel
        )
        lines.extend(("", f"Countermodel: `{assignments}`"))
    if event.get("rescue_state_id"):
        lines.extend(("", f"Rescue state: `{event['rescue_state_id']}`"))
    abandoned = event.get("abandoned_tactics", ())
    if abandoned:
        lines.extend(("", "Abandoned tactics: " + ", ".join(f"`{item}`" for item in abandoned)))
    if event.get("error"):
        lines.extend(("", f"Semantic check error: {event['error']}"))
    table = event.get("countermodel_table")
    if table and table.get("rows"):
        lines.extend(("", "**Countermodel dependency table**"))
        for row in table["rows"]:
            sides = ", ".join(
                f"{name}={side}" for name, side in row.get("hypothesis_sides", ())
            ) or "no disjunctive hypotheses"
            lines.append(f"- `{row.get('tactic', '(unknown)')}` → {sides}")


def _append_semantic_prune(lines: list[str], prune: dict[str, Any], index: int) -> None:
    assignments = ", ".join(
        f"{atom}={'true' if value else 'false'}"
        for atom, value in prune.get("countermodel", ())
    ) or "(none)"
    lines.extend(
        (
            "",
            f"#### SAT prune {index}",
            "",
            f"`{prune.get('tactic', '(unknown)')}` from "
            f"`{prune.get('parent_state_id', '(unknown)')}` produced "
            f"`{prune.get('successor_state_id', '(unknown)')}`.",
            "",
            f"Countermodel: `{assignments}`",
        )
    )


def _append_forced_move(lines: list[str], event: dict[str, Any], index: int) -> None:
    result = event.get("result", {})
    diagnostics = "; ".join(result.get("diagnostics") or ())
    suffix = f" — {diagnostics}" if diagnostics else ""
    lines.extend(
        (
            "",
            f"#### Forced move {index} — {event.get('outcome', 'unknown')}",
            "",
            f"`{event.get('tactic', '(unknown)')}` on disjunction "
            f"`{event.get('hypothesis', '(unknown)')}`{suffix}",
        )
    )


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
    rescue_rollouts = () if search is None else search.get("rescue_rollouts", ())
    analogy_generations = sum(
        len(rollout.get("generations", ())) for rollout in rescue_rollouts
    )
    analogy_submissions = sum(
        len(rollout.get("attempts", ())) for rollout in rescue_rollouts
    )
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
        f"Analogy generations: **{analogy_generations}** · "
        f"Analogy tactics submitted to Lean: **{analogy_submissions}**",
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
        discarded_candidates = event.get("discarded_candidates", ())
        if discarded_candidates:
            lines.extend(("", "**Pre-Lean filters**"))
            lines.extend(f"- {item}" for item in discarded_candidates)
        responses = event.get("responses") or ({"raw_text": event.get("response", "")},)
        for response in responses:
            response_messages = response.get("messages", event.get("messages", ()))
            for message in response_messages:
                lines.extend(("", f"**{str(message.get('role', 'message')).title()} prompt**", "", _pre(message.get("content", ""))))
            label = "Response"
            if response.get("status"):
                label += f" — {_enum_value(response['status'])}"
            if response.get("repair"):
                label += " — retry"
            lines.extend(("", f"**{label}**", "", _pre(response.get("raw_text", ""))))
            diagnostic = response.get("diagnostic")
            if diagnostic:
                kind = response.get("error_kind")
                prefix = f"{kind}: " if kind else ""
                lines.extend(("", f"**Failure:** {prefix}{diagnostic}"))
    lines.extend(("", "### Lean trace"))
    if search is None:
        lines.extend(("", "Search did not start."))
    else:
        for index, prune in enumerate(search.get("semantic_prunes", ()), 1):
            _append_semantic_prune(lines, prune, index)
        for index, event in enumerate(search.get("forced_moves", ()), 1):
            _append_forced_move(lines, event, index)
        for index, decision in enumerate(search.get("stuckness_decisions", ()), 1):
            label = "stuck" if decision.get("is_stuck") else "searchable"
            reasons = ", ".join(
                str(_enum_value(item)).replace("_", " ")
                for item in decision.get("reasons", ())
            ) or "none"
            lines.extend(
                (
                    "",
                    f"#### Stuck decision {index} — {label}",
                    "",
                    f"State: `{decision.get('state_id', '(unknown)')}` · reasons: {reasons}",
                )
            )
        for index, expansion in enumerate(search.get("ordinary_expansions", ()), 1):
            lines.extend(("", f"#### Lean expansion {index}"))
            discarded_by_source = {
                item.get("result", {}).get("candidate", {}).get("source"): item
                for item in expansion.get("discarded", ())
            }
            for attempt in expansion.get("attempts", ()):
                rendered_attempt = dict(attempt)
                source = attempt.get("candidate", {}).get("source")
                if source in discarded_by_source:
                    rendered_attempt["discarded"] = discarded_by_source[source]
                lines.extend(("", _attempt_line(rendered_attempt)))
            skipped = expansion.get("skipped_candidates", ())
            if skipped:
                lines.extend(("", "Skipped before Lean:"))
                for item in skipped:
                    source = item.get("candidate", {}).get("source", "(unknown)")
                    reason = str(item.get("reason", "unknown")).replace("_", " ")
                    lines.append(f"- `{source}` — {reason}")
        rollouts = tuple(search.get("rescue_rollouts", ()))
        emitted: set[int] = set()
        for event_index, event in enumerate(search.get("semantic_recoveries", ()), 1):
            _append_semantic_event(lines, event, event_index)
            for rollout_index, rollout in enumerate(rollouts, 1):
                if rollout.get("semantic_event_id") == event.get("event_id"):
                    _append_rollout(lines, rollout, rollout_index)
                    emitted.add(rollout_index)
        for rollout_index, rollout in enumerate(rollouts, 1):
            if rollout_index not in emitted:
                _append_rollout(lines, rollout, rollout_index)
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
    forced = tuple(event.result for event in search_result.forced_moves)
    all_attempts = ordinary + forced + fallback
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
        else InferenceStatus.SEMANTIC_FAILURE
        if search_result.status is SearchStatus.SEMANTIC_FAILURE
        else InferenceStatus.EXHAUSTED
    )
    failure = None
    if status is InferenceStatus.SEMANTIC_FAILURE:
        event = search_result.semantic_recoveries[-1]
        failure = (
            f"semantic check failed: {event.error}"
            if event.error is not None
            else "no propositionally valid rescue ancestor"
        )
    return InferenceProblemResult(
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


def _failed_problem_result(problem_id: str, status: InferenceStatus, failure: str) -> InferenceProblemResult:
    return InferenceProblemResult(problem_id, status, None, failure, 0, 0, 0, 0, 0, 0, 0)


def _problem_route(outcome: InferenceProblemResult, search_result: Any | None) -> str:
    if search_result is not None and search_result.rescue_rollouts:
        return "analogy-assisted"
    if _enum_value(outcome.status) == InferenceStatus.COMPLETE.value:
        return "ordinary"
    return "unsolved"


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _public_payload(value: Any) -> Any:
    """Remove internal random-generation controls from persisted experiment evidence."""
    if isinstance(value, dict):
        return {
            key: _public_payload(item)
            for key, item in value.items()
            if key != "seed"
        }
    if isinstance(value, (list, tuple)):
        return [_public_payload(item) for item in value]
    return value


def _run_runtime(
    config: AppConfig,
    problems: Sequence[Problem],
    generator: TextGenerator,
    verifier: Verifier,
    *,
    run_directory: Path,
    problem_index: ProblemIndex | None = None,
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
            analogy_scratchpad = (
                AnalogyScratchpad() if config.analogy.enabled else None
            )
            search_result = run_episode(
                EpisodeContext(
                    tactic_ranker=TacticRanker(
                        generator,
                        top_k=config.search.top_k,
                        seed=config.run.seed,
                        failed_tactics_for=lean.failed_tactics_for,
                        trace_sink=model_events.append,
                        analogy_scratchpad=analogy_scratchpad,
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
                        analogy_scratchpad=analogy_scratchpad,
                    ),
                    ordinary_width=config.search.top_k,
                    max_expansions=config.search.max_steps,
                    state_diversity=FrozenEmbeddingDiversity(
                        generator, layer=config.model.hidden_layer, neighbors=3
                    ),
                    semantic_checker=ExhaustiveSemanticChecker(),
                ),
                lean.initial_state(),
                SymbolicState(f"{problem.id}:goal", problem.target, "lean4"),
            )
            outcome = _problem_result(problem.id, search_result)
        except TacticRankingError as error:
            outcome = _failed_problem_result(problem.id, InferenceStatus.MODEL_FAILURE, str(error))
        except Exception as error:
            outcome = _failed_problem_result(problem.id, InferenceStatus.VERIFIER_FAILURE, f"{type(error).__name__}: {error}")
        detail = _public_payload({
            "problem": asdict(problem),
            "outcome": asdict(outcome),
            "search": None if search_result is None else asdict(search_result),
            "model_events": model_events,
        })
        details[problem.id] = detail
        _write_json(run_directory / f"{problem.id}.json", detail)
        if problem_index is not None:
            problem_index.record(problem.id, outcome.status.value)
        route = _problem_route(outcome, search_result)
        traces.append(render_problem_trace(detail, model_events, route=route))
        results.append(outcome)
    label = "analogy-on" if config.analogy.enabled else "analogy-off"
    return InferenceRun(label, tuple(results), time.monotonic() - started), traces, details


def solve_problems(
    config: AppConfig,
    problems: Sequence[Problem],
    generator: TextGenerator,
    verifier: Verifier,
    *,
    problem_index: ProblemIndex | None = None,
) -> SolveRun:
    configure_prompt_catalog(config.prompts.path)
    started = time.monotonic()
    run_id = uuid.uuid4().hex
    run_directory = config.run.output_dir / "solve" / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    inference, traces, _details = _run_runtime(
        config,
        problems,
        generator,
        verifier,
        run_directory=run_directory,
        problem_index=problem_index,
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
            "top_k": config.search.top_k,
            "max_steps": config.search.max_steps,
            "max_analogy_model_calls_per_stuck_state": config.analogy.max_model_calls,
            "problem_index_signature": (
                problem_index.signature if problem_index is not None else None
            ),
            "problem_index_fingerprints": (
                problem_index.fingerprints if problem_index is not None else None
            ),
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
