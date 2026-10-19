from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from analogy.main import (
    build_parser,
    load_config,
    main,
    run_solve,
    with_analogy_enabled,
)
from analogy.utils.records import ProblemResult, SolveSummary, SolveStatus
from analogy.solver.report import SolveRunRecord
from analogy.solver.report.render import render_cli_report


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


def test_report_describes_one_session_run():
    summary = SolveSummary(
        "analogy-on",
        (
            _result("easy", SolveStatus.COMPLETE),
            _result("hard", SolveStatus.EXHAUSTED, fallbacks=1),
        ),
        1.5,
    )
    run = SolveRunRecord("run-1", True, summary, 1.5, Path("/tmp/run-1"))

    assert render_cli_report(run) == (
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

    assert render_cli_report(run).startswith(
        "SEMANTIC_FAILURE false-root: lean_calls=1, analogy_fallbacks=0"
    )


@pytest.mark.parametrize(
    ("analogy_enabled", "expected_label"),
    ((False, "vanilla"), (True, "analogy-assisted")),
)
def test_solve_selects_explicit_run_recipe(
    tmp_path, monkeypatch, analogy_enabled, expected_label
):
    configured = load_config(ROOT / "config.yaml")
    configured = replace(
        configured,
        analogy=replace(configured.analogy, enabled=analogy_enabled),
        run=replace(configured.run, output_dir=tmp_path),
    )

    calls = []
    session = SimpleNamespace(
        config=configured,
        problems=(object(),),
        model=object(),
        verifier=object(),
        checkpoint=None,
    )

    def recipe(label):
        def run(*args, **kwargs):
            calls.append(label)
            return SolveRunRecord(
                "run", analogy_enabled, SolveSummary(label, (), 0.0), 0.0, tmp_path
            )

        return run

    monkeypatch.setattr("analogy.main.get_session", lambda *args: session)
    monkeypatch.setattr("analogy.main.run_vanilla", recipe("vanilla"))
    monkeypatch.setattr(
        "analogy.main.run_analogy_assisted", recipe("analogy-assisted")
    )

    assert run_solve(SimpleNamespace(config=ROOT / "config.yaml", analogy_enabled=None, problem_selection=None)) == 0
    assert calls == [expected_label]
