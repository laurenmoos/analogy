from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = Path(__file__).resolve().parent
COMPARISON = CONFIG_DIR / "comparison.json"
OUTPUT = CONFIG_DIR / "reasoning-steps.md"
SUMMARY_OUTPUT = CONFIG_DIR / "comparison.md"
TRACE_OUTPUT = CONFIG_DIR / "traces.md"
PROBLEM_ORDER = ("or_intro_05", "or_intro_06", "not_intro_05")
ARM_LABELS = {
    "no_analogy": "No analogy",
    "analogy": "SAT-guided analogy",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _code(text: Any, language: str = "text") -> list[str]:
    value = "" if text is None else str(text).rstrip()
    fence = "````" if "```" in value else "```"
    return [f"{fence}{language}", value, fence]


def _status(value: Any) -> str:
    return str(value).replace("_", " ")


def _display_status(value: Any) -> str:
    return {
        "complete": "SOLVED",
        "accepted": "OPEN",
        "error": "REJECTED",
        "exhausted": "UNSOLVED",
        "invalid_output": "INVALID OUTPUT",
        "rejected": "REJECTED",
        "progress": "OPEN",
    }.get(str(value), str(value).replace("_", " ").upper())


def _main_findings(
    details: dict[tuple[str, str], dict[str, Any]],
) -> list[str]:
    solved = {
        arm: [
            problem_id
            for problem_id in PROBLEM_ORDER
            if details[(arm, problem_id)]["outcome"]["status"] == "complete"
        ]
        for arm in ARM_LABELS
    }
    gained = [
        problem_id
        for problem_id in solved["analogy"]
        if problem_id not in solved["no_analogy"]
    ]
    lost = [
        problem_id
        for problem_id in solved["no_analogy"]
        if problem_id not in solved["analogy"]
    ]
    findings = [
        f"- SAT-guided analogy solved **{len(solved['analogy'])}/{len(PROBLEM_ORDER)}** "
        f"versus **{len(solved['no_analogy'])}/{len(PROBLEM_ORDER)}** without analogy."
    ]
    if gained:
        findings.append(
            "- Solved only by analogy: "
            + ", ".join(f"`{problem_id}`" for problem_id in gained)
            + "."
        )
    else:
        findings.append("- No problem was solved only by analogy in this run.")
    if lost:
        findings.append(
            "- Solved only without analogy: "
            + ", ".join(f"`{problem_id}`" for problem_id in lost)
            + "."
        )
    analogy_tactics = sum(
        details[("analogy", problem_id)]["outcome"].get(
            "analogy_fallback_count", 0
        )
        for problem_id in PROBLEM_ORDER
    )
    findings.append(
        f"- The analogy arm submitted **{analogy_tactics} Lean tactics** during stuck-only recovery."
    )
    return findings


def _countermodel_groups(details: dict[tuple[str, str], dict[str, Any]]) -> tuple[list[str], list[str]]:
    with_countermodel, without_countermodel = [], []
    for problem_id in PROBLEM_ORDER:
        observed = any(
            event.get("result") == "countermodel"
            for arm in ARM_LABELS
            for event in (details[(arm, problem_id)].get("search") or {}).get(
                "semantic_recoveries", ()
            )
        ) or any(
            (details[(arm, problem_id)].get("search") or {}).get("semantic_prunes", ())
            for arm in ARM_LABELS
        )
        (with_countermodel if observed else without_countermodel).append(problem_id)
    return with_countermodel, without_countermodel


def _group_outcome_table(
    title: str,
    problem_ids: list[str],
    details: dict[tuple[str, str], dict[str, Any]],
) -> list[str]:
    lines = [f"### {title}", ""]
    if not problem_ids:
        return lines + ["(none)", ""]
    lines.extend((
        "| Problem | Baseline stuck | No analogy | With analogy |",
        "|---|---:|---:|---:|",
    ))
    for problem_id in problem_ids:
        no = details[("no_analogy", problem_id)]["outcome"]
        yes = details[("analogy", problem_id)]["outcome"]
        lines.append(
            f"| `{problem_id}` | {'yes' if no.get('stuck_state_count', 0) else 'no'} | "
            f"{_status(no['status'])} | {_status(yes['status'])} |"
        )
    return lines + [""]


def _comparison_markdown(
    comparison: dict[str, Any],
    arms: dict[str, dict[str, Any]],
    details: dict[tuple[str, str], dict[str, Any]],
) -> str:
    lines = [
        "# Hardest-three SAT-guided analogy comparison",
        "",
        "- Problems: " + ", ".join(f"`{item}`" for item in PROBLEM_ORDER),
        f"- Shared ordinary search: `top_k={comparison['top_k']}`, `max_steps={comparison['max_steps']}`",
        "- Analogy correction ceiling: 3 total generations per stuck event",
        f"- No-analogy run: `{arms['no_analogy']['run_id']}`",
        f"- Analogy run: `{arms['analogy']['run_id']}`",
        "",
        "| Problem | No analogy | Expansions | Lean calls | With analogy | Expansions | Lean calls | Analogy submitted |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for problem_id in PROBLEM_ORDER:
        no = details[("no_analogy", problem_id)]["outcome"]
        yes = details[("analogy", problem_id)]["outcome"]
        lines.append(
            f"| `{problem_id}` | {_status(no['status'])} | {no['expansion_count']} | "
            f"{no['verifier_call_count']} | {_status(yes['status'])} | "
            f"{yes['expansion_count']} | {yes['verifier_call_count']} | "
            f"{yes.get('analogy_fallback_count', 0)} |"
        )
    lines.extend(("", *_main_findings(details), ""))
    no_solved = sum(
        details[("no_analogy", item)]["outcome"]["status"] == "complete"
        for item in PROBLEM_ORDER
    )
    yes_solved = sum(
        details[("analogy", item)]["outcome"]["status"] == "complete"
        for item in PROBLEM_ORDER
    )
    lines.extend(
        (
            f"The no-analogy arm solved **{no_solved}/{len(PROBLEM_ORDER)}** in "
            f"**{arms['no_analogy']['elapsed_seconds']:.2f}s**. The analogy arm solved "
            f"**{yes_solved}/{len(PROBLEM_ORDER)}** in "
            f"**{arms['analogy']['elapsed_seconds']:.2f}s**.",
            "Runtimes are descriptive because the sequential arms did not counterbalance model warm-up/order.",
            "",
        )
    )
    return "\n".join(lines)


def _trace_only_markdown(full_lines: list[str]) -> str:
    first_problem = next(
        index
        for index, line in enumerate(full_lines)
        if line.startswith("# Problem:")
    )
    problem_headers = [
        line for line in full_lines[first_problem:] if line.startswith("# Problem:")
    ]
    problem_links = []
    for header in problem_headers:
        match = re.fullmatch(r"# Problem: `(?P<id>[^`]+)` — (?P<title>.+)", header)
        if match is None:
            continue
        title_slug = re.sub(
            r"[^a-z0-9_-]+", "-", match["title"].lower()
        ).strip("-")
        problem_links.append(
            f"- [`{match['id']}` — {match['title']}](#problem-{match['id'].lower()}--{title_slug})"
        )
    lines = [
        "# Paired proof-search traces",
        "",
        "Observable harness traces for the same problems under no-analogy and SAT-guided analogy conditions.",
        "",
        "## How to read the traces",
        "",
        "- **Step** means one expanded Lean proof state.",
        "- **SOLVED** means the tactic closed every goal; **OPEN** means Lean accepted it and returned new goals; **REJECTED** means Lean refused it.",
        "- SAT checks and analogy retries appear only after the search is marked **STUCK**.",
        "- Full model prompts and responses are preserved in collapsible blocks at the end of each arm.",
        "",
        "## Problems",
        "",
        *problem_links,
        "",
        *full_lines[first_problem:],
    ]
    return "\n".join(lines).rstrip() + "\n"


def _countermodel(event: dict[str, Any]) -> str:
    return ", ".join(
        f"{atom}={'true' if value else 'false'}"
        for atom, value in event.get("countermodel", ())
    ) or "(none)"


def _attempt(lines: list[str], attempt: dict[str, Any], index: int) -> None:
    candidate = attempt["candidate"]
    source = candidate["source"]
    lines.extend(
        (
            f"##### Attempt {index} — {_display_status(attempt['status'])} — `{source}`",
            "",
            f"- Origin: `{candidate['origin']}`",
            f"- Score: `{candidate['score']:.8f}`",
            f"- Lean status: **{_display_status(attempt['status'])}**",
        )
    )
    diagnostics = attempt.get("diagnostics", ())
    if diagnostics:
        lines.extend(("", "Lean diagnostics:", "", *_code("\n\n".join(diagnostics))))
    state = attempt.get("state")
    if state is not None:
        lines.extend(("", f"Resulting state: `{state['id']}`", "", *_code(state["expression"], "lean")))
    if attempt.get("proof"):
        lines.extend(("", "Completed proof:", "", *_code(attempt["proof"], "lean")))
    lines.append("")


def _rescue_attempt(lines: list[str], attempt: dict[str, Any], index: int) -> None:
    proposal = attempt["proposal"]
    result = attempt["result"]
    lines.extend(
        (
            f"##### Analogy tactic {index} — {_display_status(result['status'])} — `{proposal['candidate']['source']}`",
            "",
            f"- Predicted state: `{proposal['predicted_state']}`",
            f"- Lean status: **{_display_status(result['status'])}**",
        )
    )
    if result.get("diagnostics"):
        lines.extend(("", "Lean diagnostics:", "", *_code("\n\n".join(result["diagnostics"]))))
    if result.get("state"):
        lines.extend(("", f"Resulting state: `{result['state']['id']}`", "", *_code(result["state"]["expression"], "lean")))
    if result.get("proof"):
        lines.extend(("", "Completed proof:", "", *_code(result["proof"], "lean")))
    lines.append("")


def _semantic_event(
    lines: list[str],
    event: dict[str, Any],
    rollouts: list[dict[str, Any]],
) -> None:
    result = event.get("result") or "error"
    lines.extend(
        (
            f"#### SAT check and recovery — **{_display_status(result)}**",
            "",
            f"- Trigger state: `{event['trigger_state_id']}`",
            f"- Rescue state: `{event.get('rescue_state_id') or '(none)'}`",
            f"- Countermodel: `{_countermodel(event)}`",
            "- Abandoned tactics: "
            + (", ".join(f"`{item}`" for item in event.get("abandoned_tactics", ())) or "(none)"),
        )
    )
    if event.get("error"):
        lines.append(f"- Semantic error: `{event['error']}`")
    lines.append("")
    associated = [
        rollout
        for rollout in rollouts
        if rollout.get("semantic_event_id") == event["event_id"]
    ]
    if not associated:
        lines.extend(("No analogy call in this arm.", ""))
        return
    for rollout_index, rollout in enumerate(associated, 1):
        lines.extend(
            (
                f"##### Analogy generation {rollout_index} — **{_display_status(rollout['termination'])}**",
                "",
                f"- Evaluated from state: `{rollout['state_id']}`",
            )
        )
        if rollout.get("analogy_trace"):
            lines.extend((f"- Analogy: {rollout['analogy_trace']}", ""))
        for generation_index, generation in enumerate(rollout.get("generations", ()), 1):
            retry = "yes" if generation.get("repair") else "no"
            prediction = generation.get("prediction_label")
            prediction_text = f"; prediction=`{_status(prediction)}`" if prediction else ""
            lines.extend(
                (
                    f"Generation {generation_index}: **{_status(generation['status'])}**; "
                    f"retry=`{retry}`{prediction_text}",
                    "",
                    *_code(generation.get("raw_text", ""), "json"),
                    "",
                )
            )
            if generation.get("diagnostic"):
                lines.append(
                    f"Failure: `{generation.get('error_kind')}` — {generation['diagnostic']}"
                )
                lines.append("")
        for attempt_index, attempt in enumerate(rollout.get("attempts", ()), 1):
            _rescue_attempt(lines, attempt, attempt_index)


def _model_transcript(lines: list[str], events: list[dict[str, Any]]) -> None:
    lines.extend(("### Raw model prompts and responses", ""))
    if not events:
        lines.extend(("No model calls were recorded.", ""))
        return
    for index, event in enumerate(events, 1):
        label = event.get("kind", "model_call").replace("_", " ")
        lines.extend(
            (
                "<details>",
                f"<summary>Model call {index}: {label} at {event.get('state_id', '(unknown)')}</summary>",
                "",
            )
        )
        for message in event.get("messages", ()):
            lines.extend(
                (
                    f"**{message['role'].title()} prompt**",
                    "",
                    *_code(message["content"]),
                    "",
                )
            )
        responses = event.get("responses")
        if responses is None:
            responses = (
                {
                    "raw_text": event.get("response", ""),
                    "status": None,
                    "repair": False,
                    "diagnostic": event.get("error"),
                },
            )
        for response_index, response in enumerate(responses, 1):
            status = response.get("status") or "recorded"
            retry = "yes" if response.get("repair") else "no"
            lines.extend(
                (
                    f"**Response {response_index}: {_status(status)}; retry=`{retry}`**",
                    "",
                    *_code(response.get("raw_text", ""), "json"),
                    "",
                )
            )
            if response.get("diagnostic"):
                lines.extend((f"Failure: {response['diagnostic']}", ""))
        lines.extend(("</details>", ""))


def _arm_section(
    lines: list[str], arm: dict[str, Any], detail: dict[str, Any]
) -> None:
    label = ARM_LABELS[arm["arm"]]
    outcome = detail["outcome"]
    search = detail["search"]
    lines.extend(
        (
            f"## {label}",
            "",
            f"- Run: `{arm['run_id']}`",
            f"- Outcome: **{_display_status(outcome['status'])}**",
            f"- Ordinary expansions: **{outcome['expansion_count']}**",
            f"- Lean calls: **{outcome['verifier_call_count']}**",
            f"- Invalid tactics: **{outcome['invalid_tactic_count']}**",
            f"- Repeated states: **{outcome['repeated_state_count']}**",
            f"- Stuck events: **{outcome['stuck_state_count']}**",
            f"- Analogy tactics submitted: **{outcome['analogy_fallback_count']}**",
            "",
            "### Reasoning trace",
            "",
        )
    )
    decisions = search.get("stuckness_decisions", ())
    decision_by_state = {item["state_id"]: item for item in decisions}
    semantic_by_trigger: dict[str, list[dict[str, Any]]] = {}
    for event in search.get("semantic_recoveries", ()):
        semantic_by_trigger.setdefault(event["trigger_state_id"], []).append(event)
    rollouts = list(search.get("rescue_rollouts", ()))
    forced_by_state: dict[str, list[dict[str, Any]]] = {}
    for event in search.get("forced_moves", ()):
        forced_by_state.setdefault(event["rescue_state_id"], []).append(event)
    for step, expansion in enumerate(search.get("ordinary_expansions", ()), 1):
        node = expansion["node"]
        state = node["state"]
        decision = decision_by_state.get(state["id"])
        incoming = node.get("tactic") or "initial state"
        step_label = "initial state" if not node.get("tactic") else f"after `{incoming}`"
        stuck = bool(decision and decision["is_stuck"])
        lines.extend(
            (
                f"#### Step {step} — {step_label}",
                "",
                f"- State ID: `{state['id']}`",
                f"- Parent: `{node.get('parent_state_id') or '(root)'}`",
                f"- Path score: `{node['score']:.8f}`",
                f"- Search decision: **{'STUCK — run SAT recovery' if stuck else 'continue'}**",
                "- Stuck reasons: "
                + (
                    ", ".join(f"`{item}`" for item in decision.get("reasons", ()))
                    if decision and decision.get("reasons")
                    else "(none)"
                ),
                f"- Successors added: **{len(expansion.get('successors', ())) }**",
                "",
                "Proof state:",
                "",
                *_code(state["expression"], "lean"),
                "",
            )
        )
        for attempt_index, attempt in enumerate(expansion.get("attempts", ()), 1):
            _attempt(lines, attempt, attempt_index)
        for skipped in expansion.get("skipped_candidates", ()):
            lines.extend(
                (
                    f"Skipped before Lean: `{skipped['candidate']['source']}` — "
                    f"**{_status(skipped['reason'])}**",
                    "",
                )
            )
        for discarded in expansion.get("discarded", ()):
            lines.extend(
                (
                    f"Discarded successor from `{discarded['result']['candidate']['source']}`: "
                    f"**{_status(discarded['reason'])}**",
                    "",
                )
            )
        for event in semantic_by_trigger.get(state["id"], ()):
            _semantic_event(lines, event, rollouts)
        for forced in forced_by_state.get(state["id"], ()):
            lines.extend(
                (
                    "#### Deterministic forced move",
                    "",
                    f"- Hypothesis: `{forced['hypothesis']}`",
                    f"- Tactic: `{forced['tactic']}`",
                    f"- Outcome: **{_status(forced['outcome'])}**",
                    "- Ordering: the successor was queued; "
                    + (
                        "stuck-only analogy still fired from this rescue state."
                        if arm["arm"] == "analogy"
                        else "analogy was disabled in this arm."
                    ),
                    "",
                )
            )
            _attempt(lines, forced["result"], 1)
    lines.extend(("### Trace result", ""))
    if outcome.get("proof"):
        lines.extend((*_code(outcome["proof"], "lean"), ""))
    else:
        lines.extend((f"**Unsolved:** {_status(outcome['status'])}.", ""))
    _model_transcript(lines, detail.get("model_events", ()))


def main() -> None:
    comparison = _load(COMPARISON)
    arms = {arm["arm"]: arm for arm in comparison["runs"]}
    details: dict[tuple[str, str], dict[str, Any]] = {}
    for arm_name, arm in arms.items():
        run_dir = Path(arm["output_directory"])
        for problem_id in PROBLEM_ORDER:
            details[(arm_name, problem_id)] = _load(run_dir / f"{problem_id}.json")

    lines = [
        "# Complete paired proof-search report",
        "",
        "This report covers the same three hardest examples under matched ordinary-search settings.",
        "It records observable model prompts/responses, tactics, Lean feedback, proof-state transitions,",
        "stuckness decisions, SAT assessments, countermodels, analogy retries, and final outcomes. It",
        "does not claim to expose private model chain-of-thought.",
        "",
        "## Experimental conditions",
        "",
        "- Model: `mlx-community/Qwen3-8B-4bit` on MLX/MPS",
        f"- Shared ordinary search: `top_k={comparison['top_k']}`, `max_steps={comparison['max_steps']}`",
        "- Analogy correction ceiling: 3 total generations per stuck event, including Lean-feedback retries",
        f"- No-analogy runtime: `{arms['no_analogy']['elapsed_seconds']:.2f}s`",
        f"- SAT-guided analogy runtime: `{arms['analogy']['elapsed_seconds']:.2f}s`",
        "- Runtime is descriptive: arms ran sequentially without counterbalancing model warm-up/order",
        "",
        "## Paired outcomes",
        "",
        "| Problem | No analogy | Expansions | Lean calls | SAT-guided analogy | Expansions | Lean calls |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for problem_id in PROBLEM_ORDER:
        no = details[("no_analogy", problem_id)]["outcome"]
        yes = details[("analogy", problem_id)]["outcome"]
        lines.append(
            f"| `{problem_id}` | {_status(no['status'])} | {no['expansion_count']} | "
            f"{no['verifier_call_count']} | {_status(yes['status'])} | "
            f"{yes['expansion_count']} | {yes['verifier_call_count']} |"
        )
    with_countermodel, without_countermodel = _countermodel_groups(details)
    lines.extend(("", "## Countermodel grouping", ""))
    lines.extend(_group_outcome_table("Observed countermodel or SAT prune", with_countermodel, details))
    lines.extend(_group_outcome_table("No observed countermodel", without_countermodel, details))
    lines.extend(("", "## Main findings", "", *_main_findings(details), ""))
    for problem_id in PROBLEM_ORDER:
        problem = details[("no_analogy", problem_id)]["problem"]
        binders = f" {problem['binders']}" if problem.get("binders") else ""
        lines.extend(
            (
                f"# Problem: `{problem_id}` — {problem['title']}",
                "",
                f"Source: `{problem['source_path']}` ({problem['source_level']})",
                "",
                "```lean",
                f"theorem current_problem{binders} : {problem['target']} := by",
                "```",
                "",
            )
        )
        _arm_section(lines, arms["no_analogy"], details[("no_analogy", problem_id)])
        _arm_section(lines, arms["analogy"], details[("analogy", problem_id)])

    OUTPUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    SUMMARY_OUTPUT.write_text(
        _comparison_markdown(comparison, arms, details).rstrip() + "\n",
        encoding="utf-8",
    )
    TRACE_OUTPUT.write_text(_trace_only_markdown(lines), encoding="utf-8")
    print(f"WROTE {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
    print(f"WROTE {SUMMARY_OUTPUT} ({SUMMARY_OUTPUT.stat().st_size} bytes)")
    print(f"WROTE {TRACE_OUTPUT} ({TRACE_OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
