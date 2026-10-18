from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from analogy.problem_index import ProblemIndex
from analogy.runtime import get_runtime, with_analogy_enabled
from analogy.solver.inference import solve_problems


CONFIG = Path(__file__).with_name("config.yaml")
EXPECTED_PROBLEMS = ("or_intro_05", "not_intro_05", "or_intro_06")


def baseline_stuck_subset(runs, problem_ids):
    by_key = {(run["arm"], result["problem_id"]): result
              for run in runs for result in run["results"]}
    stuck = [problem_id for problem_id in problem_ids
             if by_key[("no_analogy", problem_id)]["stuck_state_count"] > 0]
    return {
        "problem_count": len(stuck),
        "problem_ids": stuck,
        "no_analogy_solved": sum(by_key[("no_analogy", item)]["status"] == "complete" for item in stuck),
        "analogy_solved": sum(by_key[("analogy", item)]["status"] == "complete" for item in stuck),
    }


def main() -> None:
    runtime = get_runtime(CONFIG, False, "exhaustive")
    problem_ids = tuple(problem.id for problem in runtime.problem_index.problems)
    if problem_ids != EXPECTED_PROBLEMS:
        raise ValueError(f"unexpected corpus manifest: {problem_ids}")
    if any(problem.source_path.startswith("Synthetic/") for problem in runtime.problem_index.problems):
        raise ValueError("experiment manifest must contain only corpus-sourced problems")
    runs = []
    for enabled in (False, True):
        arm = "analogy" if enabled else "no_analogy"
        config = with_analogy_enabled(runtime.config, enabled)
        problem_index = ProblemIndex.load(config, runtime.problem_index.problems)
        run = solve_problems(config, runtime.problem_index.problems, runtime.model,
                             runtime.verifier, problem_index=problem_index)
        runs.append({"arm": arm, "run_id": run.run_id,
                     "output_directory": str(run.output_directory),
                     "elapsed_seconds": run.elapsed_seconds,
                     "results": [asdict(result) for result in run.inference.results]})
        print(f"ARM {arm}: {run.inference.solved_count}/{run.inference.attempted_count} solved; "
              f"artifacts={run.output_directory}", flush=True)
    output = Path(__file__).with_name("comparison.json")
    output.write_text(json.dumps({"schema_version": 3, "problem_ids": list(problem_ids),
                                  "top_k": runtime.config.search.top_k,
                                  "max_steps": runtime.config.search.max_steps,
                                  "runs": runs,
                                  "baseline_stuck_subset": baseline_stuck_subset(runs, problem_ids)},
                                 ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(f"COMPARISON: {output}", flush=True)


if __name__ == "__main__":
    main()
