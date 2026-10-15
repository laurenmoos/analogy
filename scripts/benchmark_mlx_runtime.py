#!/usr/bin/env python3
"""Measure the exact 8B MLX operations used by the analogy experiment."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import platform
from pathlib import Path
import time
import uuid

import torch

from analogy.config import load_config
from analogy.lean_gym.corpus import load_problems
from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
from analogy.lean_gym.verifier import LeanSubprocessVerifier
from analogy.model import capability_prompt
from analogy.model_backend import load_model_backend
from analogy.runtime_probe import (
    OperationCounts,
    RuntimeMeasurement,
    experiment_episode_count,
    project_runtime,
    runtime_report_payload,
)
from analogy.training import build_evaluation_conditions, split_problems


def _elapsed(call):
    started = time.monotonic()
    value = call()
    return value, time.monotonic() - started


def _projection_payload(measurement, counts, nominal_seconds):
    projection = project_runtime(
        measurement, counts, nominal_budget_seconds=nominal_seconds
    )
    return {
        "counts": asdict(counts),
        "projection": asdict(projection),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if config.model.backend != "mlx":
        parser.error("runtime probe requires model.backend=mlx")

    generator, load_seconds = _elapsed(
        lambda: load_model_backend(config.model)
    )
    problems = load_problems(config.benchmark.corpus)
    problem = next(
        item
        for item in problems
        if item.id == "imp_intro_01"
    )
    messages = capability_prompt(problem)
    normal_output, normal_generation = _elapsed(
        lambda: generator.generate(messages, seed=config.run.seed)
    )
    zero_delta = torch.zeros(generator.hidden_size)
    steered_output, delta_generation = _elapsed(
        lambda: generator.generate_with_delta(
            messages,
            delta=zero_delta,
            layer=config.model.hidden_layer,
            seed=config.run.seed,
        )
    )
    _, first_embedding = _elapsed(
        lambda: generator.embed_text(problem.target, layer=config.model.hidden_layer)
    )
    _, second_embedding = _elapsed(
        lambda: generator.embed_text(
            f"{problem.binders}\n⊢ {problem.target}",
            layer=config.model.hidden_layer,
        )
    )
    scores, scoring_seconds = _elapsed(
        lambda: generator.score_completions(
            messages, ("exact bakery_service p", "aesop")
        )
    )
    verifier = LeanSubprocessVerifier(LEAN_PROJECT_ROOT)
    verification, lean_seconds = _elapsed(
        lambda: verifier.try_tactic(
            problem, (), "exact bakery_service p"
        )
    )

    measurement = RuntimeMeasurement(
        load=load_seconds,
        generation=max(normal_generation, delta_generation),
        embedding=(first_embedding + second_embedding) / 2.0,
        scoring=scoring_seconds,
        lean=lean_seconds,
    )
    machine = f"{platform.platform()} / {platform.machine()}"
    evaluation_conditions = len(build_evaluation_conditions(config))
    smoke_episodes = experiment_episode_count(
        train=2,
        validation=2,
        test=2,
        epochs=config.rl.epochs,
        evaluation_conditions=evaluation_conditions,
    )
    split = split_problems(
        problems,
        train_connectives=config.rl.train_connectives,
        validation_connectives=config.rl.validation_connectives,
        test_connectives=config.rl.test_connectives,
    )
    full_episodes = experiment_episode_count(
        train=len(split.train),
        validation=len(split.validation),
        test=len(split.test),
        epochs=config.rl.epochs,
        evaluation_conditions=evaluation_conditions,
    )
    smoke_counts = OperationCounts(
        5 * smoke_episodes, 8 * smoke_episodes, 0, 5 * smoke_episodes
    )
    full_counts = OperationCounts(
        5 * full_episodes, 8 * full_episodes, 0, 5 * full_episodes
    )
    per_episode_nominal = (
        config.model.timeout_seconds * config.analogy.max_model_calls
    )
    report = runtime_report_payload(
        model_identifier=config.model.name,
        machine=machine,
        measurement=measurement,
        counts=full_counts,
        projection=project_runtime(
            measurement,
            full_counts,
            nominal_budget_seconds=full_episodes * per_episode_nominal,
        ),
    )
    report.update(
        {
            "model_revision": generator.model_revision,
            "raw_operations": {
                "normal_generation_seconds": normal_generation,
                "delta_generation_seconds": delta_generation,
                "first_embedding_seconds": first_embedding,
                "second_embedding_seconds": second_embedding,
                "scoring_seconds": scoring_seconds,
                "lean_seconds": lean_seconds,
            },
            "outputs": {
                "normal": normal_output,
                "zero_delta": steered_output,
                "score_order": [item.text for item in sorted(
                    scores,
                    key=lambda item: item.mean_log_likelihood,
                    reverse=True,
                )],
                "lean_status": verification.status.value,
            },
            "projection_basis": (
                "Typical estimate assumes one ordinary generation and two "
                "two-call analogy iterations per episode. Nominal budgets use "
                "the configured eight-call episode budget. The nominal budget "
                "is not a guaranteed wall-clock upper bound because model "
                "timeouts are checked after calls return."
            ),
            "episode_counts": {
                "smoke": smoke_episodes,
                "full": full_episodes,
                "evaluation_conditions": evaluation_conditions,
            },
            "smoke": _projection_payload(
                measurement, smoke_counts, smoke_episodes * per_episode_nominal
            ),
            "full": _projection_payload(
                measurement, full_counts, full_episodes * per_episode_nominal
            ),
        }
    )
    output = config.run.output_dir / "probes" / uuid.uuid4().hex
    output.mkdir(parents=True, exist_ok=False)
    target = output / "runtime.json"
    target.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
