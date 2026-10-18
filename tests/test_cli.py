from dataclasses import replace
from pathlib import Path

import pytest

from analogy.__main__ import build_parser, main, render_report, run_solve
from analogy.config import load_config
from analogy.utils.records import ProblemResult, SolveSummary, SolveStatus
from analogy.runtime import with_analogy_enabled
from analogy.solver import run as run_module
from analogy.solver.run import SolveRunRecord


ROOT = Path(__file__).parents[1]


def _result(problem_id, status, *, fallbacks=0):
    return ProblemResult(
        problem_id=problem_id,
        status=status,
        proof="exact h" if status is SolveStatus.COMPLETE else None,
        failure=None,
        expansion_count=1,
        verifier_call_count=1 + fallbacks,
        invalid_tactic_count=0,
        repeated_state_count=0,
        no_progress_count=0,
        stuck_state_count=fallbacks,
        analogy_fallback_count=fallbacks,
    )


def test_cli_has_one_solve_entry_point_and_boolean_override():
    parser = build_parser()

    default = parser.parse_args(["solve"])
    enabled = parser.parse_args(["solve", "--analogy"])
    disabled = parser.parse_args(["solve", "--no-analogy"])
    exhaustive = parser.parse_args(["solve", "--all-problems"])
    failures = parser.parse_args(["solve", "--previous-failures"])

    assert (default.command, default.handler) == ("solve", run_solve)
    assert default.config == Path("config.yaml")
    assert default.analogy_enabled is None
    assert enabled.analogy_enabled is True
    assert disabled.analogy_enabled is False
    assert default.problem_selection is None
    assert exhaustive.problem_selection == "exhaustive"
    assert failures.problem_selection == "previous_failures"


def test_retired_activation_command_is_rejected():
    with pytest.raises(SystemExit):
        main(["activation-eval"])


def test_boolean_override_changes_only_analogy_enabled():
    configured = load_config(ROOT / "config.yaml")

    disabled = with_analogy_enabled(configured, False)

    assert disabled.analogy.enabled is False
    assert disabled.analogy.max_model_calls == configured.analogy.max_model_calls
    assert replace(disabled, analogy=configured.analogy) == configured


def test_report_describes_one_runtime_run():
    summary = SolveSummary(
        "analogy-on",
        (
            _result("easy", SolveStatus.COMPLETE),
            _result("hard", SolveStatus.EXHAUSTED, fallbacks=1),
        ),
        1.5,
    )
    run = SolveRunRecord("run-1", True, summary, 1.5, Path("/tmp/run-1"))

    assert render_report(run) == (
        "COMPLETE easy: lean_calls=1, analogy_fallbacks=0\n"
        "EXHAUSTED hard: lean_calls=2, analogy_fallbacks=1\n"
        "\nSUMMARY: analogy=on, solved=1/2, analogy_fallbacks=1"
    )


def test_report_names_semantic_failure_distinctly():
    summary = SolveSummary(
        "analogy-on",
        (_result("false-root", SolveStatus.SEMANTIC_FAILURE),),
        0.2,
    )
    run = SolveRunRecord("run-2", True, summary, 0.2, Path("/tmp/run-2"))

    assert render_report(run).startswith(
        "SEMANTIC_FAILURE false-root: lean_calls=1, analogy_fallbacks=0"
    )


def test_solve_runs_shared_runtime_once(tmp_path, monkeypatch):
    configured = load_config(ROOT / "config.yaml")
    configured = replace(configured, run=replace(configured.run, output_dir=tmp_path))
    calls = []

    def fake_runtime(
        config, problems, generator, verifier, *, run_directory, checkpoint=None
    ):
        calls.append((config, problems, generator, verifier, run_directory, checkpoint))
        return SolveSummary("analogy-on", (), 0.0), [], {}

    monkeypatch.setattr(run_module, "_run_runtime", fake_runtime)
    run = run_module.solve_problems(configured, (), object(), object())

    assert len(calls) == 1
    assert calls[0][0] is configured
    assert calls[0][5] is None
    assert run.analogy_enabled is True
    assert run.summary.results == ()
