"""Standalone activation experiment CLI using the current runtime boundary."""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import time
from uuid import uuid4

from ..runtime import get_runtime
from .activation_records import load_activation_eval_config


def run_activation_eval(args: argparse.Namespace) -> int:
    """Prepare exact Lean structures, then freeze all selections before rescue."""
    from .activation_anchor import build_anchor
    from . import activation_report as artifacts, activation_runner as evaluation
    from ..solver.rescue import ProofRescueContext

    started = time.monotonic()
    config = load_activation_eval_config(args.config)
    if args.seed is not None:
        if args.seed < 0:
            raise ValueError("seed must be nonnegative")
        config = replace(config, seed_start=args.seed, seed_count=1)
    if args.problem is not None and args.problem not in config.problem_ids:
        raise ValueError("problem must belong to the configured benchmark")
    if args.max_cells is not None and args.max_cells <= 0:
        raise ValueError("max-cells must be positive")
    if args.output_dir is not None and args.output_dir.exists():
        raise FileExistsError(f"run directory already exists: {args.output_dir}")
    runtime = get_runtime(config.base_config_path)
    app = runtime.config
    by_id = {p.id: p for p in runtime.problems}
    if any(pid not in by_id for pid in config.problem_ids):
        raise ValueError("configured activation problem is missing from the corpus")
    problems = tuple(by_id[pid] for pid in config.problem_ids)
    evaluation_cells = tuple((pid, seed) for pid in config.problem_ids for seed in config.seeds
                             if args.problem is None or pid == args.problem)
    if args.max_cells is not None:
        evaluation_cells = evaluation_cells[:args.max_cells]
    if args.resume is not None:
        reader = artifacts.ActivationArtifactWriter(args.resume)
        frozen = reader.load_frozen()
        current = replace(frozen.collection, config=config, app_config=app, model_revision=runtime.model.model_revision)
        writer = artifacts.ActivationArtifactWriter.resume(args.resume, artifacts.resume_identity(current))
        saved_scope = writer.evaluation_cells()
        if (args.problem is not None or args.max_cells is not None) and evaluation_cells != saved_scope:
            raise ValueError("resume evaluation cell scope mismatch")
        evaluation_cells = saved_scope
        if (writer.path / "summary.json").exists():
            print(f"ACTIVATION EVALUATION (already complete): {writer.path}")
            return 0
        prior = writer.load_trials()
    else:
        # Anchoring/structure extraction are conventional Lean preparation.
        # No verifier capability enters collection or freezing below.
        output = args.output_dir or (Path(".artifacts/runs/activation_analogy") / uuid4().hex)
        writer = artifacts.ActivationArtifactWriter.begin(output)
        anchors = []
        for problem in problems:
            for seed in config.seeds:
                anchor = build_anchor(problem, runtime.model, runtime.verifier, app, seed=seed)
                writer.append_anchor(anchor)
                anchors.append(anchor)
        anchors = tuple(anchors)
        structures = {(a.problem_id, a.seed): runtime.verifier.proof_structure_after(by_id[a.problem_id], a.accepted_prefix)
                      for a in anchors if not a.no_stuck_anchor}
        skeleton = tuple(evaluation.PreLeanCell(a, ProofRescueContext(a.current_state, a.current_state, a.accepted_prefix, a.failed_tactics),
                                               None, (), (), (), (), (), "pending") for a in anchors)
        layers = tuple(runtime.model.resolve_decoder_layers(config.activation_layer_count, through=app.model.hidden_layer))
        initial = evaluation.PreLeanCollection(config, app, skeleton, layers, runtime.model.model_revision)
        writer.seal_manifest(initial, evaluation_cells=evaluation_cells)
        collection = evaluation.collect_prelean(config, app, anchors, runtime.model, proof_structures=structures,
                                                cell_sink=writer.append_collection)
        frozen = evaluation.freeze_selections(collection)
        writer.write_frozen(frozen)
        prior = ()
    done = {(trial.problem_id, trial.seed) for trial in prior}
    cells = tuple(c for c in frozen.cells if (c.prelean.anchor.problem_id, c.prelean.anchor.seed) in evaluation_cells)
    pending = replace(frozen, cells=tuple(c for c in cells if (c.prelean.anchor.problem_id, c.prelean.anchor.seed) not in done))
    trials = evaluation.run_lean_conditions(pending, problems, runtime.verifier, generator=runtime.model,
                                            trial_sink=writer.append_trial)
    run = evaluation.ActivationEvaluationRun(frozen.collection, frozen, (*prior, *trials), time.monotonic() - started)
    writer.finalize(run)
    print(f"ACTIVATION EVALUATION: {writer.path}")
    return 0


def build_activation_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Frozen activation-selected analogy evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    activation = commands.add_parser("activation-eval", help="run frozen activation-selected analogy evaluation")
    activation.add_argument("--config", type=Path, default=Path("config.activation-analogy.yaml"))
    activation.add_argument("--problem", help="evaluate this problem; retain all configured calibration donors")
    activation.add_argument("--seed", type=int, help="evaluate one paired seed across the calibration manifest")
    activation.add_argument("--max-cells", type=int, help="cap Lean evaluation cells after full pre-Lean calibration")
    destination = activation.add_mutually_exclusive_group()
    destination.add_argument("--output-dir", type=Path, help="exact new run directory (must not exist)")
    destination.add_argument("--resume", type=Path, help="resume an existing fully frozen checkpoint")
    activation.set_defaults(handler=run_activation_eval)
    return parser
