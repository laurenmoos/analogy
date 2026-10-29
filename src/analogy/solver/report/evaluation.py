"""Combined evidence and validation for paired evaluation trials."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    evaluation_id: str
    phase: str
    status: str
    output_directory: Path
    trial_count: int
    expected_trial_count: int

    @property
    def complete(self) -> bool:
        return self.status == "complete"


def write_evaluation_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Atomically persist the resumable combined manifest."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_evaluation_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"evaluation manifest could not be read: {path}: {error}") from error
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("trials"), list)
    ):
        raise ValueError(f"evaluation manifest has an invalid schema: {path}")
    return value


def finalize_evaluation(path: Path) -> EvaluationRecord:
    """Validate paired artifacts, aggregate metrics, and render the report."""

    manifest = load_evaluation_manifest(path)
    errors: list[str] = []
    expected = int(manifest["expected_trial_count"])
    terminal = [
        trial for trial in manifest["trials"] if trial.get("status") == "terminal"
    ]
    failed = [trial for trial in manifest["trials"] if trial.get("status") == "failed"]
    if len(terminal) != expected:
        errors.append(
            f"trial inventory incomplete: expected {expected} terminal trials, got {len(terminal)}"
        )
    if failed:
        errors.extend(
            f"trial failed: {item.get('condition')}/{item.get('problem_id')}: {item.get('error')}"
            for item in failed
        )

    evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for trial in terminal:
        condition = trial.get("condition")
        problem_id = trial.get("problem_id")
        try:
            evidence[(condition, problem_id)] = _read_trial_evidence(trial)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            errors.append(f"trial artifact invalid: {condition}/{problem_id}: {error}")

    for problem_id in manifest["problem_ids"]:
        condition_evidence = {
            condition: evidence.get((condition, problem_id))
            for condition in manifest["conditions"]
        }
        if any(value is None for value in condition_evidence.values()):
            continue
        try:
            prefixes = {
                condition: _first_stuck_prefix_sha256(value["problem"])
                for condition, value in condition_evidence.items()
            }
        except ValueError as error:
            errors.append(f"first-stuck prefix evidence missing for {problem_id}: {error}")
        else:
            if len(set(prefixes.values())) != 1:
                errors.append(
                    f"first-stuck prefix mismatch for {problem_id}: {prefixes}"
                )
        try:
            _first_selection_evidence(
                condition_evidence["ranked_analogy"]["problem"]
            )
        except ValueError as error:
            errors.append(f"candidate evidence missing for {problem_id}: {error}")

    metrics = {
        condition: _condition_metrics(
            tuple(
                value
                for (trial_condition, _), value in evidence.items()
                if trial_condition == condition
            ),
            condition,
        )
        for condition in manifest["conditions"]
    }
    manifest["metrics"] = metrics
    manifest["validation"] = {"errors": errors}
    manifest["status"] = "complete" if not errors else "incomplete"
    write_evaluation_manifest(path, manifest)
    report_path = path.parent / "report.md"
    report_path.write_text(_render_report(manifest), encoding="utf-8")
    return _record(path.parent, manifest)


def _read_trial_evidence(trial: dict[str, Any]) -> dict[str, Any]:
    run_directory = Path(trial["run_directory"])
    problem_id = trial["problem_id"]
    problem = json.loads(
        (run_directory / f"{problem_id}.json").read_text(encoding="utf-8")
    )
    run = json.loads((run_directory / "run.json").read_text(encoding="utf-8"))
    if not isinstance(problem, dict) or not isinstance(run, dict):
        raise ValueError("trial evidence must be JSON objects")
    return {"problem": problem, "run": run}


def _first_stuck_prefix_sha256(problem: dict[str, Any]) -> str:
    search = problem.get("search")
    if not isinstance(search, dict):
        raise ValueError("missing search evidence")
    decisions = search.get("stuckness_decisions", [])
    first = next(
        (
            item
            for item in decisions
            if isinstance(item, dict) and item.get("is_stuck") is True
        ),
        None,
    )
    if first is None:
        raise ValueError("missing first stuck decision")
    stuck_id = first.get("state_id")
    prefix = []
    for expansion in search.get("ordinary_expansions", []):
        prefix.append(expansion)
        node = expansion.get("node", {}) if isinstance(expansion, dict) else {}
        state = node.get("state", {}) if isinstance(node, dict) else {}
        if isinstance(state, dict) and state.get("id") == stuck_id:
            break
    payload = {"stuck_decision": first, "ordinary_expansions": prefix}
    return _sha256_json(payload)


def _first_candidate_set(problem: dict[str, Any]) -> tuple[tuple[int, str], ...]:
    events = problem.get("model_events", [])
    candidates = [
        item
        for item in events
        if isinstance(item, dict)
        and item.get("kind") == "analogy_story_candidate_cache"
        and item.get("phase") == "initialization"
    ]
    if not candidates:
        raise ValueError("missing analogy candidate hashes")
    first_state = candidates[0].get("state_id")
    selected = [item for item in candidates if item.get("state_id") == first_state]
    return tuple(
        (int(item["generation_index"]), str(item["candidate_sha256"]))
        for item in selected
    )


def _first_selection_evidence(problem: dict[str, Any]) -> dict[str, Any]:
    candidates = _first_candidate_set(problem)
    if len(candidates) < 2:
        raise ValueError("fewer than two initial analogy candidates")
    selections = [
        item
        for item in problem.get("model_events", [])
        if isinstance(item, dict)
        and item.get("kind") == "analogy_initial_selection"
    ]
    if len(selections) != 1:
        raise ValueError("expected exactly one initial analogy selection")
    return selections[0]


def _condition_metrics(
    evidence: tuple[dict[str, Any], ...], condition: str
) -> dict[str, Any]:
    solved = rescued = submissions = winning = verifier_calls = model_calls = 0
    counterfactuals = counterfactual_accepts = 0
    for value in evidence:
        problem = value["problem"]
        outcome = problem.get("outcome", {})
        solved += outcome.get("status") == "complete"
        verifier_calls += int(outcome.get("verifier_call_count", 0))
        if condition == "direct_deliberation":
            submissions += int(outcome.get("deliberation_fallback_count", 0))
            winning += int(outcome.get("deliberation_winning_path_count", 0))
        else:
            submissions += int(outcome.get("analogy_fallback_count", 0))
            winning += int(outcome.get("analogy_winning_path_count", 0))
        search = problem.get("search") or {}
        rescued += any(
            rollout.get("termination") in {"complete", "progress"}
            and any(
                attempt.get("result", {}).get("status") in {"complete", "accepted"}
                for attempt in rollout.get("attempts", [])
            )
            for rollout in search.get("rescue_rollouts", [])
        )
        events = problem.get("model_events", [])
        counterfactual_events = [
            event
            for event in events
            if isinstance(event, dict)
            and event.get("kind") == "analogy_initial_counterfactual"
        ]
        counterfactuals += len(counterfactual_events)
        counterfactual_accepts += sum(
            event.get("accepted") is True for event in counterfactual_events
        )
        model_calls += sum(
            isinstance(event, dict)
            and (
                event.get("kind") == "model_call"
                or str(event.get("kind", "")).endswith("_call")
            )
            for event in events
        )
        model_calls -= sum(
            isinstance(event, dict)
            and event.get("kind") == "analogy_story_candidate_cache"
            and event.get("cache_hit") is True
            for event in events
        )
    return {
        "trials": len(evidence),
        "solved": solved,
        "solve_rate": solved / len(evidence) if evidence else None,
        "rescued": rescued,
        "intervention_submissions": submissions,
        "intervention_winning_path": winning,
        "verifier_calls": verifier_calls + counterfactuals,
        "realized_model_calls": model_calls,
        "counterfactual_tactics": counterfactuals,
        "counterfactual_tactics_accepted": counterfactual_accepts,
    }


def _render_report(manifest: dict[str, Any]) -> str:
    lines = [
        f"# Evaluation {manifest['evaluation_id']}",
        "",
        f"Status: **{manifest['status']}**",
        "",
        f"Trials: {len(manifest['trials'])}/{manifest['expected_trial_count']}",
        "",
        "## Metrics",
        "",
    ]
    for condition in manifest["conditions"]:
        metric = manifest.get("metrics", {}).get(condition, {})
        lines.append(
            f"- {condition}: solved {metric.get('solved', 0)}/{metric.get('trials', 0)}, "
            f"rescued {metric.get('rescued', 0)}, submissions "
            f"{metric.get('intervention_submissions', 0)}, model calls "
            f"{metric.get('realized_model_calls', 0)}"
        )
    lines.extend(("", "## Validation", ""))
    errors = manifest.get("validation", {}).get("errors", [])
    lines.extend([f"- {item}" for item in errors] or ["- All pairing checks passed."])
    return "\n".join(lines) + "\n"


def _record(output_directory: Path, manifest: dict[str, Any]) -> EvaluationRecord:
    return EvaluationRecord(
        manifest["evaluation_id"],
        manifest["phase"],
        manifest["status"],
        output_directory,
        len(manifest["trials"]),
        int(manifest["expected_trial_count"]),
    )


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "EvaluationRecord",
    "finalize_evaluation",
    "load_evaluation_manifest",
    "write_evaluation_manifest",
]
