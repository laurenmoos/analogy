"""Command-line composition root for configured experiments."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import ConfigError, load_config
from .lean_gym.resources import LEAN_PROJECT_ROOT
from .lean_gym.verifier import LeanSubprocessVerifier
from .model import ModelConfigurationError, QwenGenerator, run_baseline, run_capability


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analogical Lean experiment runner")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="path to the experiment configuration",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    if config.experiment.stage not in {"capability", "baseline"}:
        print(
            f"Unsupported experiment stage: {config.experiment.stage}",
            file=sys.stderr,
        )
        return 2

    try:
        generator = QwenGenerator.from_config(config.model)
    except ModelConfigurationError as error:
        print(f"Model configuration error: {error}", file=sys.stderr)
        return 2
    verifier = LeanSubprocessVerifier(LEAN_PROJECT_ROOT)
    if config.experiment.stage == "capability":
        results = run_capability(config, generator, verifier)
        solved = sum(item.solved for item in results)
        print(f"capability: {solved}/{len(results)} solved")
        for result in results:
            disposition = "solved" if result.solved else result.failure
            print(f"{result.problem_id}: {disposition}; tactics={list(result.tactics)!r}")
        passed = solved >= config.experiment.minimum_solved
        print(f"gate: {'passed' if passed else 'failed'}")
        return 0 if passed else 1

    run = run_baseline(config, generator, verifier)
    print(f"baseline: {run.solved_count}/{run.attempted_count} solved")
    print(f"stuck states: {run.stuck_state_count}")
    print(f"invalid tactics: {run.invalid_tactic_count}")
    print(f"verifier calls: {run.verifier_call_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
