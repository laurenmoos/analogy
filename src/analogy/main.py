"""Command-line composition root for proof search."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .config import (
    AnalogyConfig,
    AppConfig,
    BenchmarkConfig,
    ModelConfig,
    PromptConfig,
    RunConfig,
    SearchConfig,
)
from .lean_gym.corpus import load_problems
from .lean_gym.models import Problem
from .lean_gym.resources import LEAN_PROJECT_ROOT
from .lean_gym.verifier import LeanSubprocessVerifier
from .model.model_provider import (
    ModelConfigurationError,
    get_model_provider,
)
from .model.prompts import PromptCatalog, configure_prompt_catalog
from .solver.report.render import render_cli_report
from .solver.run import (
    run_analogy_assisted,
    run_deliberation_assisted,
    run_vanilla,
)
from .utils.checkpoint import Checkpoint, load_checkpoint, select_problems


def load_config(path: Path) -> AppConfig:
    """Read the command-line session configuration from YAML."""

    config_path = Path(path).expanduser().resolve()
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"config could not be read: {config_path}: {error}") from error
    except yaml.YAMLError as error:
        raise ValueError(f"config is not valid YAML: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("config must be a mapping")

    base = config_path.parent

    def resolve(raw: str) -> Path:
        candidate = Path(raw).expanduser()
        return (candidate if candidate.is_absolute() else base / candidate).resolve()

    benchmark = {"selection": "previous_failures", **data["benchmark"]}
    run = {"seed": 0, **data["run"]}
    if benchmark["selection"] not in {"exhaustive", "previous_failures"}:
        raise ValueError(
            "config.benchmark.selection must be 'exhaustive' or "
            "'previous_failures'"
        )
    analogy = {"selection": "structural", **data["analogy"]}
    if not isinstance(analogy.get("enabled"), bool):
        raise ValueError("config.analogy.enabled must be a boolean")
    if analogy["selection"] not in {"structural", "random"}:
        raise ValueError(
            "config.analogy.selection must be 'structural' or 'random'"
        )
    data["search"] = {"samples_per_state": 3, **data["search"]}
    if (
        isinstance(data["search"]["samples_per_state"], bool)
        or not isinstance(data["search"]["samples_per_state"], int)
        or data["search"]["samples_per_state"] <= 0
    ):
        raise ValueError("config.search.samples_per_state must be a positive integer")

    return AppConfig(
        prompts=PromptConfig(
            path=resolve(data["prompts"]["path"])
        ),
        model=ModelConfig(**data["model"]),
        benchmark=BenchmarkConfig(
            corpus=resolve(benchmark.pop("corpus")),
            **benchmark,
        ),
        search=SearchConfig(**data["search"]),
        analogy=AnalogyConfig(
            **{
                **analogy,
                "calibration_path": (
                    resolve(analogy["calibration_path"])
                    if analogy.get("calibration_path") is not None
                    else None
                ),
            }
        ),
        run=RunConfig(
            output_dir=resolve(run.pop("output_dir")),
            **run,
        ),
    )


@dataclass(frozen=True, slots=True)
class Session:
    """Dependencies and selected problems for one command-line solve session."""

    config: AppConfig
    prompts: PromptCatalog | None
    model: Any
    analogy_model: Any
    verifier: Any
    problems: tuple[Problem, ...]
    checkpoint: Checkpoint


def with_analogy_enabled(
    config: AppConfig, enabled: bool | None
) -> AppConfig:
    """Return config with only the session's analogy gate overridden."""

    if enabled is None:
        return config
    if not isinstance(enabled, bool):
        raise ValueError("analogy override must be a boolean or None")
    return replace(config, analogy=replace(config.analogy, enabled=enabled))


def with_problem_selection(
    config: AppConfig, selection: str | None
) -> AppConfig:
    """Return config with a validated problem scheduling override."""

    if selection is None:
        return config
    if selection not in {"exhaustive", "previous_failures"}:
        raise ValueError(f"unknown problem selection mode {selection!r}")
    return replace(
        config, benchmark=replace(config.benchmark, selection=selection)
    )


def _condition_name(config: AppConfig, *, deliberation_enabled: bool) -> str:
    if deliberation_enabled:
        return "deliberation"
    if not config.analogy.enabled:
        return "ordinary"
    return (
        "ranked_analogy"
        if config.analogy.selection == "structural"
        else "random_analogy"
    )


@lru_cache(maxsize=None)
def get_session(
    config_path: Path,
    analogy_enabled: bool | None = None,
    problem_selection: str | None = None,
    deliberation_enabled: bool = False,
) -> Session:
    """Build each immutable or expensive dependency once per configuration."""

    config = load_config(Path(config_path))
    config = with_analogy_enabled(config, analogy_enabled)
    config = with_problem_selection(config, problem_selection)
    all_problems = tuple(load_problems(config.benchmark.corpus))
    condition = _condition_name(
        config, deliberation_enabled=deliberation_enabled
    )
    checkpoint = load_checkpoint(config, all_problems, condition=condition)
    problems = select_problems(checkpoint, config.benchmark.selection)
    if not problems:
        return Session(config, None, None, None, None, (), checkpoint)
    prompts = configure_prompt_catalog(config.prompts.path)
    model = get_model_provider(config.model)
    analogy_model = (
        get_model_provider(replace(config.model, name=config.analogy.model_name))
        if config.analogy.enabled and config.analogy.model_name
        else model
    )
    verifier = LeanSubprocessVerifier(LEAN_PROJECT_ROOT)
    return Session(config, prompts, model, analogy_model, verifier, problems, checkpoint)


def run_solve(args: argparse.Namespace) -> int:
    """Solve configured problems, using analogy retries only when Lean gets stuck."""

    session = get_session(
        args.config,
        False if getattr(args, "deliberation_enabled", False) else args.analogy_enabled,
        args.problem_selection,
        getattr(args, "deliberation_enabled", False),
    )
    config = session.config
    deliberation_enabled = getattr(args, "deliberation_enabled", False)

    print(f"MODEL: {config.model.name}")
    print(f"PROBLEMS: {len(session.problems)} from {config.benchmark.corpus}")
    print(f"SELECTION: {config.benchmark.selection}")
    print(
        f"SEARCH: top_k={config.search.top_k}, "
        f"samples_per_state={config.search.samples_per_state}, "
        f"max_steps={config.search.max_steps}"
    )
    print(
        f"CONDITION: {_condition_name(config, deliberation_enabled=deliberation_enabled)}; "
        f"planning attempts per stuck state={config.analogy.max_model_calls}"
    )
    print("DIVERSITY: frozen-model 3-nearest-neighbor cosine novelty")
    print("JUDGE: Lean\n")
    if not session.problems:
        print("No unresolved problems for this experiment configuration.")
        return 0
    recipe = (
        run_deliberation_assisted
        if deliberation_enabled
        else run_analogy_assisted
        if config.analogy.enabled
        else run_vanilla
    )
    kwargs = {"checkpoint": session.checkpoint}
    if config.analogy.enabled:
        kwargs["analogy_generator"] = getattr(session, "analogy_model", session.model)
    run = recipe(config, session.problems, session.model, session.verifier, **kwargs)
    print(render_cli_report(run))
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
    analogy.add_argument(
        "--deliberation",
        dest="deliberation_enabled",
        action="store_true",
        help="use a call-budget-matched direct deliberation fallback",
    )
    command.set_defaults(analogy_enabled=None)
    command.set_defaults(deliberation_enabled=False)
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
    except (
        KeyError,
        ModelConfigurationError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "Session",
    "build_parser",
    "get_session",
    "load_config",
    "main",
    "run_solve",
    "with_analogy_enabled",
    "with_problem_selection",
]
