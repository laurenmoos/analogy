import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from analogy.config import load_config
from analogy.lean_gym.models import Problem
from analogy.problem_index import ProblemIndex


ROOT = Path(__file__).parents[1]


def _problem(problem_id: str) -> Problem:
    return Problem(problem_id, problem_id, "", "P", f"{problem_id}.lean", problem_id, "test", 1)


def _config(tmp_path: Path):
    configured = load_config(ROOT / "config.yaml")
    return replace(configured, run=replace(configured.run, output_dir=tmp_path / "runs"))


def test_previous_failures_selects_failed_and_unseen_problems(tmp_path):
    config = _config(tmp_path)
    problems = (_problem("solved"), _problem("failed"), _problem("new"))
    index = ProblemIndex.load(config, problems)
    index.record("solved", "complete")
    index.record("failed", "exhausted")

    reloaded = ProblemIndex.load(config, problems)

    assert [problem.id for problem in reloaded.select("previous_failures")] == [
        "failed",
        "new",
    ]


def test_startup_constructs_a_discovery_manifest_before_any_result(tmp_path):
    config = _config(tmp_path)

    index = ProblemIndex.load(config, (_problem("a"), _problem("b")))
    payload = json.loads(index.path.read_text(encoding="utf-8"))

    assert payload["experiments"][index.signature] == {
        "problem_ids": ["a", "b"],
        "problem_fingerprints": index.fingerprints,
        "statuses": {},
    }


def test_exhaustive_selection_ignores_cached_successes(tmp_path):
    config = _config(tmp_path)
    problems = (_problem("solved"), _problem("failed"))
    index = ProblemIndex.load(config, problems)
    index.record("solved", "complete")

    assert [problem.id for problem in index.select("exhaustive")] == [
        "solved",
        "failed",
    ]


def test_incompatible_experiment_settings_do_not_hide_problems(tmp_path):
    config = _config(tmp_path)
    problems = (_problem("p"),)
    ProblemIndex.load(config, problems).record("p", "complete")
    changed = replace(config, search=replace(config.search, max_steps=config.search.max_steps + 1))

    assert [problem.id for problem in ProblemIndex.load(changed, problems).select("previous_failures")] == ["p"]


def test_adding_a_problem_keeps_unchanged_successes_and_selects_the_new_problem(tmp_path):
    config = _config(tmp_path)
    original = _problem("existing")
    ProblemIndex.load(config, (original,)).record("existing", "complete")

    reloaded = ProblemIndex.load(config, (original, _problem("new")))

    assert [problem.id for problem in reloaded.select("previous_failures")] == ["new"]


def test_changing_a_problem_makes_its_old_success_unresolved(tmp_path):
    config = _config(tmp_path)
    original = _problem("p")
    ProblemIndex.load(config, (original,)).record("p", "complete")
    changed = replace(original, target="Q")

    assert [
        problem.id
        for problem in ProblemIndex.load(config, (changed,)).select("previous_failures")
    ] == ["p"]


def test_changed_prompts_do_not_reuse_solved_status(tmp_path):
    prompt_path = tmp_path / "prompts.yaml"
    prompt_path.write_text("version: one\n", encoding="utf-8")
    config = replace(_config(tmp_path), prompts=replace(_config(tmp_path).prompts, path=prompt_path))
    problems = (_problem("p"),)
    ProblemIndex.load(config, problems).record("p", "complete")
    prompt_path.write_text("version: two\n", encoding="utf-8")

    assert [problem.id for problem in ProblemIndex.load(config, problems).select("previous_failures")] == ["p"]


@pytest.mark.parametrize("result_key", ["summary", "inference"])
def test_missing_index_is_reconstructed_from_compatible_run_artifacts(
    tmp_path, result_key
):
    config = _config(tmp_path)
    problems = (_problem("solved"), _problem("failed"))
    index = ProblemIndex.load(config, problems)
    index.path.unlink()
    run_directory = config.run.output_dir / "solve" / "old-run"
    run_directory.mkdir(parents=True)
    (run_directory / "run.json").write_text(
        json.dumps(
            {
                "problem_index_signature": index.signature,
                "problem_index_fingerprints": index.fingerprints,
                result_key: {
                    "results": [
                        {"problem_id": "solved", "status": "complete"},
                        {"problem_id": "failed", "status": "exhausted"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    rebuilt = ProblemIndex.load(config, problems)

    assert [problem.id for problem in rebuilt.select("previous_failures")] == ["failed"]


def test_malformed_index_and_run_payloads_are_ignored(tmp_path):
    config = _config(tmp_path)
    problems = (_problem("p"),)
    index = ProblemIndex.load(config, problems)
    index.path.write_bytes(b"\xff\xfe")
    run_directory = config.run.output_dir / "solve" / "malformed"
    run_directory.mkdir(parents=True)
    (run_directory / "run.json").write_text("[]", encoding="utf-8")

    rebuilt = ProblemIndex.load(config, problems)

    assert [problem.id for problem in rebuilt.select("previous_failures")] == ["p"]


def test_reconstruction_uses_latest_result_not_directory_name(tmp_path):
    config = _config(tmp_path)
    problems = (_problem("p"),)
    index = ProblemIndex.load(config, problems)
    index.path.unlink()
    root = config.run.output_dir / "solve"
    for directory, status, timestamp in (
        ("z-older", "complete", 1_000),
        ("a-newer", "exhausted", 2_000),
    ):
        run_directory = root / directory
        run_directory.mkdir(parents=True)
        run_path = run_directory / "run.json"
        run_path.write_text(
            json.dumps(
                {
                    "problem_index_signature": index.signature,
                        "problem_index_fingerprints": index.fingerprints,
                    "inference": {"results": [{"problem_id": "p", "status": status}]},
                }
            ),
            encoding="utf-8",
        )
        os.utime(run_path, (timestamp, timestamp))

    rebuilt = ProblemIndex.load(config, problems)

    assert [problem.id for problem in rebuilt.select("previous_failures")] == ["p"]

