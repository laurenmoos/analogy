from __future__ import annotations

import importlib.util
from pathlib import Path

from analogy.config import load_config
from analogy.lean_gym.corpus import load_problems


ROOT = Path(__file__).parents[1]


def _runner_module():
    path = ROOT / ".artifacts/experiment-configs/hardest3/run_comparison.py"
    spec = importlib.util.spec_from_file_location("hardest3_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hardest_config_samples_at_temperature_point_seven():
    config = load_config(ROOT / ".artifacts/experiment-configs/hardest3/config.yaml")

    assert config.model.do_sample is True
    assert config.model.temperature == 0.7


def test_harder_problem_manifest_is_fixed_and_corpus_sourced():
    problems = load_problems(
        ROOT / ".artifacts/experiment-configs/hardest3/problems.json"
    )

    assert tuple(item.id for item in problems) == ("or_intro_05", "not_intro_05", "or_intro_06")
    assert all(item.source_path.startswith("Game/Levels/") for item in problems)
    assert all(not item.source_path.startswith("Synthetic/") for item in problems)


def test_stuck_subset_metrics_use_only_paired_baseline_stuck_rows():
    runs = []
    runs.extend(
        (
                {
                    "arm": "no_analogy",
                    "results": [
                        {
                            "problem_id": "p",
                            "stuck_state_count": 1,
                            "status": "exhausted",
                        }
                    ],
                },
                {
                    "arm": "analogy",
                    "results": [
                        {
                            "problem_id": "p",
                            "stuck_state_count": 1,
                            "status": "complete",
                        }
                    ],
                },
        )
    )

    metrics = _runner_module().baseline_stuck_subset(runs, ("p",))

    assert metrics["problem_count"] == 1
    assert metrics["problem_ids"] == ["p"]
    assert metrics["no_analogy_solved"] == 0
    assert metrics["analogy_solved"] == 1


def test_runner_does_not_resume_or_select_prior_attempts():
    source = (ROOT / ".artifacts/experiment-configs/hardest3/run_comparison.py").read_text()

    assert "resume" not in source.casefold()
    assert "range(5)" not in source
