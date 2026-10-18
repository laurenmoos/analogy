"""Single command-line composition root for proof search."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import ConfigError
from .model.model_provider import ModelConfigurationError
from .runtime import get_runtime
from .solver.run import run_analogy_assisted, run_vanilla
from .solver.run_records import SolveRunRecord


def render_report(run: SolveRunRecord) -> str:
    """Render one runtime invocation."""

    lines: list[str] = []
    for result in run.summary.results:
        lines.append(
            f"{result.status.value.upper()} {result.problem_id}: "
            f"lean_calls={result.verifier_call_count}, "
            f"analogy_fallbacks={result.analogy_fallback_count}"
        )
    fallback_count = sum(
        result.analogy_fallback_count for result in run.summary.results
    )
    lines.extend((
        "",
        f"SUMMARY: analogy={'on' if run.analogy_enabled else 'off'}, "
        f"solved={run.summary.solved_count}/{run.summary.attempted_count}, "
        f"analogy_fallbacks={fallback_count}",
    ))
    return "\n".join(lines)


def run_solve(args: argparse.Namespace) -> int:
    """Solve configured problems, using analogy retries only when Lean gets stuck."""

    runtime = get_runtime(
        args.config,
        args.analogy_enabled,
        args.problem_selection,
    )
    config = runtime.config

    print(f"MODEL: {config.model.name}")
    print(f"PROBLEMS: {len(runtime.problems)} from {config.benchmark.corpus}")
    print(f"SELECTION: {config.benchmark.selection}")
    print(
        f"SEARCH: top_k={config.search.top_k}, "
        f"max_steps={config.search.max_steps}"
    )
    print(
        f"ANALOGY: {'enabled' if config.analogy.enabled else 'disabled'}; "
        f"schema calls per stuck state={config.analogy.max_model_calls}"
    )
    print("DIVERSITY: frozen-model 3-nearest-neighbor cosine novelty")
    print("JUDGE: Lean\n")
    if not runtime.problems:
        print("No unresolved problems for this experiment configuration.")
        return 0
    recipe = run_analogy_assisted if config.analogy.enabled else run_vanilla
    run = recipe(
        config,
        runtime.problems,
        runtime.model,
        runtime.verifier,
        checkpoint=runtime.checkpoint,
    )
    print(render_report(run))
    print(f"\nDETAILS: {run.output_directory}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lean proof model runner")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser(
        "solve", help="solve every configured problem with analogy retries"
    )
    command.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="configuration file (default: config.yaml)",
    )
    analogy = command.add_mutually_exclusive_group()
    analogy.add_argument(
        "--analogy",
        dest="analogy_enabled",
        action="store_true",
        help="enable one-tactic analogy fallback after conventional stuckness",
    )
    analogy.add_argument(
        "--no-analogy",
        dest="analogy_enabled",
        action="store_false",
        help="disable analogy fallback",
    )
    command.set_defaults(analogy_enabled=None)
    selection = command.add_mutually_exclusive_group()
    selection.add_argument(
        "--all-problems",
        dest="problem_selection",
        action="store_const",
        const="exhaustive",
        help="run the complete configured corpus",
    )
    selection.add_argument(
        "--previous-failures",
        dest="problem_selection",
        action="store_const",
        const="previous_failures",
        help="run only unresolved or previously unsuccessful problems",
    )
    command.set_defaults(problem_selection=None)
    command.set_defaults(handler=run_solve)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (ConfigError, ModelConfigurationError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main", "render_report", "run_solve"]
