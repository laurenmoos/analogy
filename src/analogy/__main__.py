"""Command-line composition root for configured experiments."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import ConfigError, load_config, load_curriculum_config
from .grounded_curriculum import CurriculumError, build_grounded_curriculum
from .lean_gym.resources import LEAN_PROJECT_ROOT
from .lean_gym.verifier import LeanSubprocessVerifier
from .model import ModelConfigurationError, QwenGenerator, run_baseline, run_capability
from .model_backend import load_model_backend
from .run_audit import AuditRunError, audit_analogy_run
from .training import evaluate_direct_rescue, train_direct_rescue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analogical Lean experiment runner")
    parser.add_argument("command", nargs="?", choices=("build-grounded-curriculum",))
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="path to the experiment configuration",
    )
    parser.add_argument(
        "--audit-source",
        type=Path,
        help="completed rescue run to inspect after an audit_analogy experiment",
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        help="separate output directory for post-hoc audit artifacts",
    )
    parser.add_argument("--audit-seed", type=int, help="post-hoc judge seed")
    parser.add_argument(
        "--audit-max-calls",
        type=int,
        default=100,
        help="maximum total judge model calls, including schema repairs",
    )
    parser.add_argument(
        "--audit-max-repair-calls",
        type=int,
        default=1,
        help="schema-repair attempts per judgment",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "build-grounded-curriculum":
        try:
            curriculum = load_curriculum_config(args.config)
            summary = build_grounded_curriculum(curriculum, LeanSubprocessVerifier(curriculum.lean_project))
        except (ConfigError, CurriculumError, ValueError, OSError) as error:
            print(f"Curriculum error: {error}", file=sys.stderr)
            return 2
        print(f"build-grounded-curriculum: {summary.retained}/{summary.attempted} retained; "
              f"nonterminal={summary.nonterminal}; completions={summary.completions}; variants={summary.variants}")
        print(f"manifest: {summary.manifest_path}")
        return 0
    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    if config.experiment.stage not in {
        "capability",
        "baseline",
        "train_rescue",
        "evaluate_rescue",
        "audit_analogy",
    }:
        print(
            f"Unsupported experiment stage: {config.experiment.stage}",
            file=sys.stderr,
        )
        return 2
    if config.experiment.stage in {"train_rescue", "evaluate_rescue"}:
        if not config.rl.enabled:
            print(
                "Rescue stages require rl.enabled=true",
                file=sys.stderr,
            )
            return 2
        if config.experiment.stage == "evaluate_rescue" and config.rl.checkpoint is None:
            print(
                "evaluate_rescue requires rl.checkpoint",
                file=sys.stderr,
            )
            return 2
    if config.experiment.stage == "audit_analogy":
        missing = [
            name
            for name, value in (
                ("--audit-source", args.audit_source),
                ("--audit-output", args.audit_output),
            )
            if value is None
        ]
        if missing:
            print(
                f"audit_analogy requires {' and '.join(missing)}",
                file=sys.stderr,
            )
            return 2
    try:
        generator = load_model_backend(config.model)
    except ModelConfigurationError as error:
        print(f"Model configuration error: {error}", file=sys.stderr)
        return 2
    if config.experiment.stage == "audit_analogy":
        assert args.audit_source is not None and args.audit_output is not None
        try:
            summary = audit_analogy_run(
                args.audit_source,
                args.audit_output,
                generator,
                config.benchmark.corpus,
                seed=(config.run.seed if args.audit_seed is None else args.audit_seed),
                max_calls=args.audit_max_calls,
                max_repair_calls=args.audit_max_repair_calls,
            )
        except (AuditRunError, ValueError) as error:
            print(f"Audit error: {error}", file=sys.stderr)
            return 2
        print(
            f"audit_analogy: {summary.judged}/{summary.attempted} judged; "
            f"failures={summary.failures}"
        )
        return 0
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

    if config.experiment.stage == "train_rescue":
        summary = train_direct_rescue(config, generator, verifier)
        print(
            f"train_rescue: {summary.epochs_completed} epochs; "
            f"checkpoint={summary.best_checkpoint}"
        )
        return 0

    if config.experiment.stage == "evaluate_rescue":
        assert config.rl.checkpoint is not None
        summary = evaluate_direct_rescue(
            config, generator, verifier, config.rl.checkpoint
        )
        print(f"evaluate_rescue: {summary.run_id}")
        for condition in summary.condition_summaries:
            print(f"{condition.name}: {condition.solved}/{condition.attempted} solved")
        return 0

    run = run_baseline(config, generator, verifier)
    print(f"baseline: {run.solved_count}/{run.attempted_count} solved")
    print(f"stuck states: {run.stuck_state_count}")
    print(f"invalid tactics: {run.invalid_tactic_count}")
    print(f"verifier calls: {run.verifier_call_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
