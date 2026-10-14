from __future__ import annotations

from io import StringIO
from pathlib import Path

from analogy.lean_gym.cli import run_repl
from analogy.lean_gym.corpus import load_problems
from analogy.lean_gym.resources import CORPUS_ROOT, LEAN_PROJECT_ROOT
from analogy.lean_gym.traces import JsonlTraceWriter
from analogy.lean_gym.verifier import LeanSubprocessVerifier


def test_repl_supports_show_history_save_undo_reset_and_problem_listing(tmp_path: Path) -> None:
    problems = tuple(problem for problem in load_problems(CORPUS_ROOT / "problems.json") if problem.id == "imp_intro_02")
    save_path = tmp_path / "proof with spaces.lean"
    input_stream = StringIO(
        "imp_intro_02\n:show\nintro h\nexact missing\n:history\nexact h\n"
        f':save "{save_path}"\n:undo\n:reset\n:problems\n:quit\n'
    )
    output = StringIO()
    traces: list[Path] = []

    def trace_factory(problem):
        path = tmp_path / f"{problem.id}-{len(traces)}.jsonl"
        traces.append(path)
        return JsonlTraceWriter(path)

    status = run_repl(problems, LeanSubprocessVerifier(LEAN_PROJECT_ROOT), trace_factory, input_stream, output)

    rendered = output.getvalue()
    assert status == 0
    assert "Accepted" in rendered
    assert "Rejected" in rendered
    assert "Proof complete" in rendered
    assert "exact missing" in rendered
    assert "imp_intro_02" in rendered
    assert save_path.exists()
    assert len(traces) == 1


def test_repl_load_starts_a_new_trace_and_unknown_command_is_nonfatal(tmp_path: Path) -> None:
    problems = tuple(problem for problem in load_problems(CORPUS_ROOT / "problems.json") if problem.id in {"and_intro_01", "imp_intro_02"})
    input_stream = StringIO("and_intro_01\n:nope\n:load imp_intro_02\n:quit\n")
    output = StringIO()
    traces: list[Path] = []

    def trace_factory(problem):
        path = tmp_path / f"{len(traces)}.jsonl"
        traces.append(path)
        return JsonlTraceWriter(path)

    status = run_repl(problems, LeanSubprocessVerifier(LEAN_PROJECT_ROOT), trace_factory, input_stream, output)
    assert status == 0
    assert "Unknown command" in output.getvalue()
    assert len(traces) == 2
