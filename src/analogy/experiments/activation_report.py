"""Exclusive, append-and-flush experiment journals and descriptive reports.

Resume starts only at a fully frozen selection checkpoint. Default checkpoints
retain pooled-vector summary statistics, likelihoods and the *recorded* winner;
they cannot recalibrate or reselect. No pickle or arbitrary imports are used.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, fields, is_dataclass, replace
from enum import Enum
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import tempfile
from typing import get_args, get_origin, get_type_hints

from .. import config as config_module, records as records_module
from ..inference import model_provider
from ..solver import diversity, rescue
from ..transfer import domain
from . import activation_generation, activation_metrics, activation_records, activation_runner as runner
from .activation_records import ActivationCondition

SCHEMA_VERSION = "activation-analogy-artifacts-v1"
_MODULES = (config_module, records_module, model_provider, diversity, rescue, domain,
            activation_generation, activation_metrics, activation_records, runner)
_TYPES = {cls.__name__: cls for module in _MODULES for cls in vars(module).values()
          if isinstance(cls, type) and is_dataclass(cls)}
_POSITIVE = {"imp_intro_05", "iff_intro_03"}
_HARD = {"iff_intro_05", "not_intro_05", "or_intro_05", "or_intro_06"}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _vector_summary(activation):
    values = activation.values
    return {"layer": activation.layer, "dimensions": len(values), "mean": statistics.fmean(values),
            "norm": math.hypot(*values), "sha256": _hash(values)}


def _encode(value, *, vectors=False):
    if isinstance(value, model_provider.CompletionActivationTrace) and not vectors:
        value = runner.FrozenCompletionTrace(value.score, value.completion_token_count,
            value.processed_token_positions, tuple(_vector_summary(a) for a in value.activations))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return {"$path": str(value)}
    if is_dataclass(value):
        return {"$type": type(value).__name__, **{f.name: _encode(getattr(value, f.name), vectors=vectors) for f in fields(value)}}
    if isinstance(value, dict):
        return {str(k): _encode(v, vectors=vectors) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(v, vectors=vectors) for v in value]
    return value


def _coerce(annotation, value):
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if get_origin(annotation) is tuple:
        args = get_args(annotation)
        if len(args) == 2 and args[-1] is Ellipsis:
            return tuple(_coerce(args[0], item) for item in value)
        return tuple(_coerce(t, item) for t, item in zip(args, value))
    return value


def _decode(value):
    if isinstance(value, list):
        return tuple(_decode(v) for v in value)
    if not isinstance(value, dict):
        return value
    if "$path" in value:
        return Path(value["$path"])
    if "$type" not in value:
        return {k: _decode(v) for k, v in value.items()}
    name = value["$type"]
    if name not in _TYPES:
        raise ValueError(f"unknown checkpoint record type: {name}")
    cls = _TYPES[name]
    hints = get_type_hints(cls)
    return cls(**{k: _coerce(hints.get(k), _decode(v)) for k, v in value.items() if k != "$type"})


def resume_identity(collection):
    """Bind the exact ordered experiment, prompt bytes and immutable manifest."""
    config = collection.config
    return {
        "schema_version": SCHEMA_VERSION, "model_revision": collection.model_revision,
        "problem_ids": list(config.problem_ids), "seed_range": [config.seed_start, config.seed_count],
        "prompt_hash": hashlib.sha256(collection.app_config.prompts.path.read_bytes()).hexdigest(),
        "anchor_manifest_hash": _hash([_encode(c.anchor) for c in collection.cells]),
        "config_hash": _hash(_encode(config)),
    }


def _atomic_new(path, text):
    """Publish a complete file without replacing an existing destination."""
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # atomic, exclusive publication
    finally:
        os.unlink(temporary)


def _rows(path):
    try:
        return [json.loads(line) for line in path.read_text().splitlines()]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"incomplete/corrupt journal {path.name}; preserved without overwrite") from error


class ActivationArtifactWriter:
    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def begin(cls, path):
        """Reserve the run before expensive anchoring; incomplete runs stay visible."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=False)
        writer = cls(path)
        for name in ("anchors.jsonl", "prelean.jsonl", "trials.jsonl", "collection.jsonl"):
            (path / name).touch(exist_ok=False)
        return writer

    @classmethod
    def create(cls, path, collection, *, evaluation_cells=None):
        writer = cls.begin(path)
        for cell in collection.cells:
            writer.append_anchor(cell.anchor)
        writer.seal_manifest(collection, evaluation_cells=evaluation_cells)
        return writer

    def append_anchor(self, anchor):
        if (self.path / "resolved_config.json").exists():
            raise FileExistsError("anchor manifest is sealed")
        self._append("anchors.jsonl", _encode(anchor))

    def seal_manifest(self, collection, *, evaluation_cells=None):
        if _rows(self.path / "anchors.jsonl") != [_encode(c.anchor) for c in collection.cells]:
            raise ValueError("anchor journal does not match manifest")
        payload = {"identity": resume_identity(collection), "config": _encode(collection.config),
                   "app_config": _encode(collection.app_config), "layers": list(collection.layers),
                   "model_revision": collection.model_revision,
                   "evaluation_cells": list(evaluation_cells) if evaluation_cells is not None else
                       [(c.anchor.problem_id, c.anchor.seed) for c in collection.cells]}
        _atomic_new(self.path / "resolved_config.json", _json(payload) + "\n")

    def evaluation_cells(self):
        metadata = json.loads((self.path / "resolved_config.json").read_text())
        return tuple(tuple(key) for key in metadata["evaluation_cells"])

    @classmethod
    def resume(cls, path, identity):
        writer = cls(path)
        saved = json.loads((writer.path / "resolved_config.json").read_text())["identity"]
        for key in ("schema_version", "model_revision", "problem_ids", "seed_range", "prompt_hash", "anchor_manifest_hash", "config_hash"):
            if saved.get(key) != identity.get(key):
                raise ValueError(f"resume identity mismatch: {key}")
        anchors = _rows(writer.path / "anchors.jsonl")
        if _hash(anchors) != saved["anchor_manifest_hash"]:
            raise ValueError("resume identity mismatch: anchor_manifest_hash")
        writer.load_frozen()  # Never reconstruct a selection from incomplete summaries.
        return writer

    def _append(self, name, value):
        if (self.path / "summary.json").exists():
            raise FileExistsError("completed run is immutable")
        with (self.path / name).open("a", encoding="utf-8") as stream:
            stream.write(_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def append_collection(self, cell):
        self._append("collection.jsonl", _encode(cell))

    def write_frozen(self, selections):
        if (self.path / "prelean.jsonl").stat().st_size or (self.path / "frozen.json").exists():
            raise FileExistsError("frozen selections already recorded")
        for cell in selections.cells:
            self._append("prelean.jsonl", _encode(cell))
        metadata = json.loads((self.path / "resolved_config.json").read_text())
        metadata["layers"] = list(selections.collection.layers)
        metadata["checkpoint_hash"] = hashlib.sha256((self.path / "prelean.jsonl").read_bytes()).hexdigest()
        _atomic_new(self.path / "frozen.json", _json(metadata) + "\n")
        if selections.collection.config.persist_full_activations:
            with (self.path / "activation_vectors.jsonl.gz").open("xb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
                    for cell in selections.collection.cells:
                        stream.write((_json(_encode(cell, vectors=True)) + "\n").encode())

    def load_frozen(self):
        if not (self.path / "frozen.json").exists():
            raise ValueError("resume requires a fully frozen pre-Lean checkpoint")
        metadata = json.loads((self.path / "frozen.json").read_text())
        if metadata["checkpoint_hash"] != hashlib.sha256((self.path / "prelean.jsonl").read_bytes()).hexdigest():
            raise ValueError("frozen checkpoint hash mismatch")
        cells = tuple(_decode(row) for row in _rows(self.path / "prelean.jsonl"))
        config = _decode(metadata["config"])
        expected = tuple((p, s) for p in config.problem_ids for s in config.seeds)
        if tuple((c.prelean.anchor.problem_id, c.prelean.anchor.seed) for c in cells) != expected:
            raise ValueError("frozen checkpoint does not cover the ordered manifest")
        collection = runner.PreLeanCollection(config, _decode(metadata["app_config"]),
            tuple(c.prelean for c in cells), tuple(metadata["layers"]), metadata["model_revision"])
        return runner.FrozenSelections(collection, cells)

    def append_trial(self, trial):
        keys = {(r["problem_id"], r["seed"]) for r in _rows(self.path / "trials.jsonl")}
        if (trial.problem_id, trial.seed) in keys:
            raise FileExistsError("trial cell already journaled")
        meta = _encode(replace(trial, outcomes=(), equal_compute_outcomes=()))
        # One row per problem/seed/condition/view. Resume requires the entire
        # eight-row cell, so partial rows cannot be mistaken for completion.
        for outcome in (*trial.outcomes, *trial.equal_compute_outcomes):
            self._append("trials.jsonl", {"problem_id": trial.problem_id, "seed": trial.seed,
                                         "trial": meta, "outcome": _encode(outcome)})

    def load_trials(self):
        groups = {}
        for row in _rows(self.path / "trials.jsonl"):
            groups.setdefault((row["problem_id"], row["seed"]), []).append(row)
        trials = []
        for rows in groups.values():
            outcomes = tuple(_decode(row["outcome"]) for row in rows)
            expected = {(arm, view) for arm in ActivationCondition for view in ("equal_tactic", "equal_compute")}
            if len(rows) != 8 or {(o.condition, o.budget_view) for o in outcomes} != expected:
                raise ValueError("partial trial cell; journal preserved without overwrite")
            trials.append(replace(_decode(rows[0]["trial"]), outcomes=tuple(o for o in outcomes if o.budget_view == "equal_tactic"),
                                  equal_compute_outcomes=tuple(o for o in outcomes if o.budget_view == "equal_compute")))
        return tuple(trials)

    def finalize(self, run):
        if (self.path / "summary.json").exists():
            raise FileExistsError("completed run is immutable")
        journal = self.load_trials()
        if len(journal) != len(run.trials):
            raise ValueError("finalization requires every trial to be flushed")
        if _encode(journal) != _encode(run.trials):
            raise ValueError("finalization run does not match the trial journal")
        _atomic_new(self.path / "report.md", render_activation_report(run))
        _atomic_new(self.path / "summary.json", _json(summarize_activation_run(run)) + "\n")


def write_activation_artifacts(run, output_dir: Path) -> Path:
    """Write a complete run to the exact *new* run directory supplied."""
    writer = ActivationArtifactWriter.create(output_dir, run.collection)
    writer.write_frozen(run.selections)
    for trial in run.trials:
        writer.append_trial(trial)
    writer.finalize(run)
    return writer.path


def descriptive_interval(values, *, seed, draws=2000):
    """Run-seeded percentile bootstrap of the paired-seed mean; not inference."""
    values = tuple(values)
    if not values:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(draws))
    return means[int(.025 * (draws - 1))], means[int(.975 * (draws - 1))]


def _mean(values):
    values = tuple(v for v in values if v is not None)
    return statistics.fmean(values) if values else None


def _correlation(pairs):
    if len(pairs) < 2:
        return None
    x, y = zip(*pairs)
    if len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return statistics.correlation(x, y)


def summarize_activation_run(run):
    config = run.collection.config
    rows, paired, associations = [], [], []
    for problem in config.problem_ids:
        trials = tuple(t for t in run.trials if t.problem_id == problem)
        for view, attr in (("equal_tactic", "outcomes"), ("equal_compute", "equal_compute_outcomes")):
            for arm in ActivationCondition:
                all_pairs = [(t, o) for t in trials for o in getattr(t, attr) if o.condition is arm]
                eligible = [(t, o) for t, o in all_pairs if t.rescue_eligible and o.immediate_utility not in
                            ("compute_ceiling_unavailable", "shuffled_unavailable")]
                outcomes = [o for _, o in eligible]
                labels = Counter(label for _, o in all_pairs for label in (*o.verifier_results, *o.continuation_verifier_results))
                # Everything other than known tactic outcomes is retained by its
                # typed infrastructure label, never merged into rejection.
                infrastructure = {k: v for k, v in labels.items() if k not in ("completed", "accepted", "accepted_repeated", "rejected")}
                scores = [next((m for m in t.measurements if m.candidate_id == t.selected_candidate_id), None) for t, _ in eligible]
                values = [int(o.completed) for o in outcomes]
                positions = [o.processed_token_positions for _, o in all_pairs]
                calls = sum(o.lean_calls for _, o in all_pairs)
                progressed = sum(o.immediate_utility == "accepted_progressing" for o in outcomes)
                rows.append({
                    "problem_id": problem, "role": "positive control" if problem in _POSITIVE else "hard negative" if problem in _HARD else "test",
                    "condition": arm.value, "budget_view": view, "cells": len(trials), "eligible": len(outcomes),
                    "no_stuck_anchor": sum(t.status == "no_stuck_anchor" for t in trials),
                    "invalid_usage": sum(t.status == "invalid_usage" for t in trials),
                    "failed_selection": sum(t.status == "failed_selection" for t in trials),
                    "solves": sum(values), "solve_rate": _mean(values),
                    "solve_rate_descriptive_ci95": descriptive_interval(values, seed=config.seed_start),
                    "accepted_progressing": progressed, "progress_per_lean_call": progressed / calls if calls else None,
                    "tactic_rejections": labels["rejected"], "infrastructure_failures": infrastructure,
                    "reasoning_displacement": _mean(m.reasoning_displacement for m in scores if m),
                    "patch_likelihood_effect": _mean(m.causal_effect for m in scores if m),
                    "predecessor_value": _mean(m.previous_state_value for m in scores if m),
                    "causal_ranking_changes": sum(changed is True for t, _ in eligible for _, changed in t.causal_ranking_changes),
                    "unique_successor_states": len({h for o in outcomes for h in o.successor_state_hashes}),
                    "proof_state_novelty": _mean(d.novelty for o in outcomes for d in o.diversity),
                    "processed_token_positions": None if any(v is None for v in positions) else sum(positions),
                    "continuation_token_positions": None if any(o.continuation_processed_token_positions is None for _, o in all_pairs) else sum(o.continuation_processed_token_positions for _, o in all_pairs),
                    "lean_calls": calls, "elapsed_seconds": _mean(o.elapsed_seconds for _, o in all_pairs),
                })
            for other in (ActivationCondition.NEUTRAL, ActivationCondition.PROMPTED, ActivationCondition.SHUFFLED):
                counts = Counter()
                differences = []
                for trial in trials:
                    arms = {o.condition: o for o in getattr(trial, attr)}
                    if not trial.rescue_eligible or other not in arms or ActivationCondition.SELECTED not in arms:
                        continue
                    selected, comparator = arms[ActivationCondition.SELECTED], arms[other]
                    if any(o.immediate_utility in ("compute_ceiling_unavailable", "shuffled_unavailable") for o in (selected, comparator)):
                        continue
                    counts["both" if selected.completed and comparator.completed else "selected_only" if selected.completed else
                           "comparator_only" if comparator.completed else "neither"] += 1
                    differences.append(int(selected.completed) - int(comparator.completed))
                paired.append({"problem_id": problem, "budget_view": view, "comparator": other.value,
                               **{k: counts[k] for k in ("selected_only", "comparator_only", "both", "neither")},
                               "solve_difference": _mean(differences),
                               "difference_descriptive_ci95": descriptive_interval(differences, seed=config.seed_start)})
            pairs = []
            for trial in trials:
                if not trial.rescue_eligible:
                    continue
                measurement = next((m for m in trial.measurements if m.candidate_id == trial.selected_candidate_id), None)
                outcome = next((o for o in getattr(trial, attr) if o.condition is ActivationCondition.SELECTED), None)
                utility = {"rejected": 0, "accepted_progressing": 1, "completed": 2}
                if measurement and measurement.selection_score is not None and outcome and outcome.immediate_utility in utility:
                    pairs.append((measurement.selection_score, utility[outcome.immediate_utility]))
            associations.append({"problem_id": problem, "budget_view": view, "n": len(pairs),
                                 "pearson_score_utility": _correlation(pairs)})
    return {"schema_version": SCHEMA_VERSION, "uncertainty": "descriptive paired-seed bootstrap, not inferential",
            "bootstrap_seed": config.seed_start, "bootstrap_draws": 2000, "rows": rows, "paired": paired,
            "score_lean_utility_association": associations, "elapsed_seconds": run.elapsed_seconds,
            "activation_layers": list(run.collection.layers), "model_revision": run.collection.model_revision,
            "model_revision_resolved": not run.collection.model_revision.startswith("unresolved:"),
            "prelean_elapsed_seconds": sum(c.elapsed_seconds or 0 for c in run.collection.cells),
            "limitations": "Single-boundary single-layer patching is a local causal proxy, not a reconstruction of the analogy trajectory."}


def _display(value):
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return f"{value:.5g}"
    if isinstance(value, (tuple, list)):
        return "[" + ", ".join(_display(v) for v in value) + "]"
    return str(value)


def render_activation_report(run) -> str:
    summary = summarize_activation_run(run)
    lines = ["# Activation-selected analogy evaluation", "",
             f"Model revision: `{run.collection.model_revision}`; resolved layers: {run.collection.layers}.",
             "Uncertainty is descriptive paired-seed bootstrap (95% percentile intervals), not inferential; six problems do not support population claims.",
             "Equal tactic/Lean budget measures selection quality including its extra model work. Equal compute caps rescue model token positions; continuation work is separately metered.",
             "no_stuck_anchor and invalid_usage cells are excluded from rescue denominators; failed_selection remains a failed rescue. Infrastructure failures retain their typed labels.", "",
             "| Problem | Role | Budget | Condition | Eligible | no_stuck_anchor | invalid_usage | failed_selection | solves | solve rate [CI95] | accepted_progressing / Lean call | Infrastructure | Rejections |",
             "|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|---:|"]
    if not summary["model_revision_resolved"]:
        lines[2:2] = ["UNRESOLVED MODEL REVISION: local metadata did not identify an immutable snapshot. These artifacts do not establish a frozen real-model result; resume validates only the unresolved label.", ""]
    for row in summary["rows"]:
        lines.append("| " + " | ".join(_display(v) for v in (
            row["problem_id"], row["role"], row["budget_view"], row["condition"], row["eligible"], row["no_stuck_anchor"],
            row["invalid_usage"], row["failed_selection"], row["solves"],
            f'{_display(row["solve_rate"])} {_display(row["solve_rate_descriptive_ci95"])}', row["progress_per_lean_call"],
            row["infrastructure_failures"], row["tactic_rejections"])) + " |")
    lines.extend(["", "Activation metrics describe the selected candidate; they are not proof-state novelty or proof progress.", "",
                  "| Problem | Budget | Condition | Displacement | patch likelihood | predecessor | Ranking changes | Unique states | novelty | token positions | Continuation positions | Lean calls | elapsed mean seconds |",
                  "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for row in summary["rows"]:
        lines.append("| " + " | ".join(_display(row[k]) for k in ("problem_id", "budget_view", "condition", "reasoning_displacement",
            "patch_likelihood_effect", "predecessor_value", "causal_ranking_changes", "unique_successor_states", "proof_state_novelty",
            "processed_token_positions", "continuation_token_positions", "lean_calls", "elapsed_seconds")) + " |")
    lines.extend(["", "Exact paired solve counts (selected vs comparator):", "",
                  "| Problem | Budget | Comparator | Selected only | Comparator only | Both | Neither | Difference CI95 |",
                  "|---|---|---|---:|---:|---:|---:|---|"])
    for row in summary["paired"]:
        lines.append("| " + " | ".join(_display(row[k]) for k in ("problem_id", "budget_view", "comparator", "selected_only", "comparator_only", "both", "neither", "difference_descriptive_ci95")) + " |")
    lines.extend(["", "Pre-Lean score–Lean utility association: Pearson correlation across paired seeds of the selected score with immediate utility (rejected=0, accepted-progressing=1, completed=2). Constant or insufficient samples are unavailable.", "",
                  "| Problem | Budget | N | Correlation |", "|---|---|---:|---:|"])
    for row in summary["score_lean_utility_association"]:
        lines.append(f'| {row["problem_id"]} | {row["budget_view"]} | {row["n"]} | {_display(row["pearson_score_utility"])} |')
    lines.extend(["", f'Total elapsed seconds: {_display(run.elapsed_seconds)}; pre-Lean elapsed: {_display(summary["prelean_elapsed_seconds"])}.',
                  "Lean calls count tactic attempts, including continuation; initialization and anchor replay are additional fixed overhead. Provider forwards, generated-token totals and peak memory are unavailable from the current runner ledger; token positions are authoritative.",
                  summary["limitations"], ""])
    return "\n".join(lines)
