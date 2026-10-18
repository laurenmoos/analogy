from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / ".artifacts/experiment-configs/hardest3/render_detailed_report.py"


def _module():
    spec = importlib.util.spec_from_file_location("hardest3_report", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _detail(status: str, *, analogy_tactics: int = 0):
    return {
        "outcome": {
            "status": status,
            "analogy_fallback_count": analogy_tactics,
        },
        "search": {"semantic_recoveries": []},
    }


def test_main_findings_are_derived_from_current_paired_outcomes():
    report = _module()
    details = {
        ("no_analogy", "or_intro_05"): _detail("exhausted"),
        ("analogy", "or_intro_05"): _detail("complete", analogy_tactics=2),
        ("no_analogy", "not_intro_05"): _detail("complete"),
        ("analogy", "not_intro_05"): _detail("complete"),
        ("no_analogy", "or_intro_06"): _detail("exhausted"),
        ("analogy", "or_intro_06"): _detail("complete", analogy_tactics=3),
    }

    findings = report._main_findings(details)

    assert findings[0] == (
        "- SAT-guided analogy solved **3/3** versus **1/3** without analogy."
    )
    assert "`or_intro_05`, `or_intro_06`" in findings[1]
    assert any("5 Lean tactics" in line for line in findings)


def test_main_findings_report_no_advantage_without_claiming_an_old_result():
    report = _module()
    details = {
        (arm, problem): _detail("complete" if problem == "not_intro_05" else "exhausted")
        for arm in ("no_analogy", "analogy")
        for problem in report.PROBLEM_ORDER
    }

    findings = report._main_findings(details)

    assert findings[0] == (
        "- SAT-guided analogy solved **1/3** versus **1/3** without analogy."
    )
    assert any("No problem was solved only by analogy" in line for line in findings)


def test_short_comparison_uses_current_run_ids_and_counts():
    report = _module()
    details = {}
    for arm in ("no_analogy", "analogy"):
        for problem in report.PROBLEM_ORDER:
            detail = _detail(
                "complete" if arm == "analogy" or problem == "not_intro_05" else "exhausted",
                analogy_tactics=1 if arm == "analogy" else 0,
            )
            detail["outcome"].update(expansion_count=2, verifier_call_count=7)
            details[(arm, problem)] = detail
    arms = {
        "no_analogy": {"arm": "no_analogy", "run_id": "new-control", "elapsed_seconds": 10.0},
        "analogy": {"arm": "analogy", "run_id": "new-treatment", "elapsed_seconds": 20.0},
    }
    comparison = {"top_k": 6, "max_steps": 16}

    rendered = report._comparison_markdown(comparison, arms, details)

    assert "`new-control`" in rendered
    assert "`new-treatment`" in rendered
    assert "solved **1/3**" in rendered
    assert "solved **3/3**" in rendered
    assert "38df9451" not in rendered


def test_trace_only_report_drops_comparison_prose_and_keeps_problem_sections():
    report = _module()
    full_lines = [
        "# Complete paired proof-search report",
        "",
        "## Main findings",
        "",
        "- A conclusion.",
        "",
        "# Problem: `first` — Example",
        "",
        "## No analogy",
        "trace A",
        "## SAT-guided analogy",
        "trace B",
    ]

    rendered = report._trace_only_markdown(full_lines)

    assert rendered.startswith("# Paired proof-search traces\n")
    assert "## How to read the traces" in rendered
    assert "## Problems" in rendered
    assert "- [`first` — Example](#problem-first--example)" in rendered
    assert "# Problem: `first` — Example" in rendered
    assert "## No analogy" in rendered
    assert "## SAT-guided analogy" in rendered
    assert "Main findings" not in rendered
    assert "A conclusion" not in rendered


def test_countermodel_groups_render_separate_outcome_tables():
    report = _module()
    details = {
        (arm, problem): _detail("exhausted")
        for arm in ("no_analogy", "analogy")
        for problem in report.PROBLEM_ORDER
    }
    details[("no_analogy", "or_intro_05")]["outcome"]["stuck_state_count"] = 1

    lines = report._group_outcome_table(
        "Observed countermodel or SAT prune", ["or_intro_05"], details
    )

    rendered = "\n".join(lines)
    assert "### Observed countermodel or SAT prune" in rendered
    assert "| `or_intro_05` | yes | exhausted | exhausted |" in rendered


def test_report_renderer_mentions_forced_move_ordering():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "Deterministic forced move" in source
    assert "successor was queued" in source
    assert "stuck-only analogy still fired" in source
    assert "analogy was disabled in this arm" in source


def test_attempt_heading_puts_status_before_full_diagnostics():
    report = _module()
    lines = []
    report._attempt(
        lines,
        {
            "candidate": {"source": "exact h2", "origin": "ordinary", "score": 1.0},
            "status": "complete",
            "diagnostics": ["declaration uses `sorry`"],
            "proof": "by\n  exact h2",
        },
        1,
    )

    assert lines[0] == "##### Attempt 1 — SOLVED — `exact h2`"
    assert "- Lean status: **SOLVED**" in lines
