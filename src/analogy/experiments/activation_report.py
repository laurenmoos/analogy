"""Exclusive, append-and-flush experiment journals and descriptive reports.

Collection resumes from atomic working checkpoints. After freezing, default
artifacts retain vector summaries, likelihoods and the *recorded* winner;
they cannot recalibrate or reselect. No pickle or arbitrary imports are used.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, fields, is_dataclass, replace
from enum import Enum
import gzip
import fcntl
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
from ..solver import diversity, rescue, proof_views
from ..transfer import domain
from . import activation_generation, activation_metrics, activation_records, activation_runner as runner
from .activation_records import ActivationCondition

SCHEMA_VERSION = "activation-analogy-artifacts-v3"
_MODULES = (config_module, records_module, model_provider, diversity, rescue, proof_views, domain,
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
        result = {"$type": type(value).__name__, **{f.name: _encode(getattr(value, f.name), vectors=vectors) for f in fields(value)}}
        if isinstance(value, activation_metrics.ScoredCandidateMeasurement) and not vectors:
            result["patch_deltas"] = []
            result["patch_delta_summaries"] = [{"dimensions": len(v), "norm": math.hypot(*v), "sha256": _hash(v)} for v in value.patch_deltas] or list(value.patch_delta_summaries)
        return result
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


def resume_identity(collection, *, evaluation_cells=None):
    """Bind the exact ordered experiment, prompt bytes and immutable manifest."""
    config = collection.config
    return {
        "schema_version": SCHEMA_VERSION, "model_revision": collection.model_revision,
        "problem_ids": list(config.problem_ids), "seed_range": [config.seed_start, config.seed_count],
        "prompt_hash": hashlib.sha256(collection.app_config.prompts.path.read_bytes()).hexdigest(),
        "anchor_manifest_hash": _hash([_encode(c.anchor) for c in collection.cells]),
        "config_hash": _hash(_encode(config)),
        "app_config_hash": _hash(_encode(collection.app_config)),
        "layers": list(collection.layers),
        "model_revision_resolved": not collection.model_revision.startswith("unresolved:"),
        "evaluation_cells": [list(key) for key in evaluation_cells] if evaluation_cells is not None else
            [[c.anchor.problem_id, c.anchor.seed] for c in collection.cells],
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


def _atomic_replace(path, text):
    """Commit a whole transaction; interruption leaves the previous file intact."""
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _rows(path):
    try:
        return [json.loads(line) for line in path.read_text().splitlines()]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"incomplete/corrupt journal {path.name}; preserved without overwrite") from error


def _publish_or_validate(path, text):
    if path.exists():
        if path.read_bytes() != text.encode("utf-8"):
            raise ValueError(f"immutable publication conflict: {path.name}")
    else:
        _atomic_new(path, text)


class ActivationArtifactWriter:
    """One POSIX flock owner per active run; lock files are never unlinked.

    macOS/Linux release flock on descriptor close or process death. Keeping the
    stable lock inode avoids stale-lock deletion races and makes crashes safe.
    Read-only instances may inspect data, but every mutation requires ownership.
    """
    def __init__(self, path):
        self.path = Path(path)
        self._lock_file = None

    @classmethod
    def open_locked(cls, path):
        writer = cls(path)
        stream = (writer.path / ".writer.lock").open("r+b")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            stream.close()
            raise FileExistsError(f"run writer lock already held: {writer.path}") from error
        except BaseException:
            stream.close()
            raise
        writer._lock_file = stream
        return writer

    def close(self):
        if getattr(self, "_lock_file", None) is not None:
            stream, self._lock_file = self._lock_file, None
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()

    def __enter__(self):
        self._require_lock()
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        self.close()

    def _require_lock(self):
        if self._lock_file is None:
            raise RuntimeError("artifact mutation requires the process-held writer lock")

    def _mutable(self):
        self._require_lock()
        if (self.path / "finalization.json").exists() or (self.path / "complete.json").exists():
            raise FileExistsError("completed/finalizing run is immutable")

    @classmethod
    def begin(cls, path):
        path = Path(path)
        path.mkdir(parents=True, exist_ok=False)
        (path / ".writer.lock").touch(exist_ok=False)
        writer = cls.open_locked(path)
        try:
            for name in ("anchors.jsonl", "prelean.jsonl", "trials.jsonl", "collection.jsonl"):
                (path / name).touch(exist_ok=False)
        except BaseException:
            writer.close()
            raise
        return writer

    @classmethod
    def create(cls, path, collection, *, evaluation_cells=None):
        writer = cls.begin(path)
        try:
            for cell in collection.cells:
                writer.append_anchor(cell.anchor)
            writer.seal_manifest(collection, evaluation_cells=evaluation_cells)
        except BaseException:
            writer.close()
            raise
        return writer

    def append_anchor(self, anchor):
        self._mutable()
        if (self.path / "resolved_config.json").exists():
            raise FileExistsError("anchor manifest is sealed")
        self._append("anchors.jsonl", _encode(anchor))

    def seal_manifest(self, collection, *, evaluation_cells=None):
        self._mutable()
        if _rows(self.path / "anchors.jsonl") != [_encode(c.anchor) for c in collection.cells]:
            raise ValueError("anchor journal does not match manifest")
        identity = resume_identity(collection, evaluation_cells=evaluation_cells)
        payload = {"identity": identity, "identity_hash": _hash(identity),
                   "config": _encode(collection.config), "app_config": _encode(collection.app_config),
                   "layers": list(collection.layers), "model_revision": collection.model_revision,
                   "evaluation_cells": identity["evaluation_cells"]}
        self._validate_metadata(payload)
        _atomic_new(self.path / "resolved_config.json", _json(payload) + "\n")

    def _validate_metadata(self, metadata):
        """Validate raw JSON bindings before constructing execution objects."""
        try:
            identity = metadata["identity"]
            if identity["schema_version"] != SCHEMA_VERSION or _hash(identity) != metadata["identity_hash"]:
                raise ValueError("checkpoint identity hash/schema mismatch")
            config = metadata["config"]
            checks = {
                "config_hash": _hash(config), "app_config_hash": _hash(metadata["app_config"]),
                "model_revision": metadata["model_revision"], "layers": metadata["layers"],
                "model_revision_resolved": not metadata["model_revision"].startswith("unresolved:"),
                "evaluation_cells": metadata["evaluation_cells"],
                "problem_ids": config["problem_ids"], "seed_range": [config["seed_start"], config["seed_count"]],
                "anchor_manifest_hash": _hash(_rows(self.path / "anchors.jsonl")),
            }
            for key, value in checks.items():
                if identity.get(key) != value:
                    raise ValueError(f"checkpoint identity mismatch: {key}")
            manifest = [[pid, seed] for pid in config["problem_ids"]
                        for seed in range(config["seed_start"], config["seed_start"] + config["seed_count"])]
            anchors = _rows(self.path / "anchors.jsonl")
            if [[a["problem_id"], a["seed"]] for a in anchors] != manifest:
                raise ValueError("checkpoint identity anchor order mismatch")
            scope = metadata["evaluation_cells"]
            if not scope or scope != [key for key in manifest if key in scope]:
                raise ValueError("checkpoint identity evaluation scope mismatch")
        except (KeyError, TypeError) as error:
            raise ValueError("malformed checkpoint identity metadata") from error
        return metadata

    def _metadata(self):
        return self._validate_metadata(json.loads((self.path / "resolved_config.json").read_text()))

    def evaluation_cells(self):
        return tuple(tuple(key) for key in self._metadata()["evaluation_cells"])

    def load_manifest(self):
        saved = self.load_collection()
        anchors = tuple(_decode(a) for a in _rows(self.path / "anchors.jsonl"))
        cells = tuple(runner.PreLeanCell(a, runner._context(a), None, (), (), (), (), (), "pending") for a in anchors)
        return replace(saved, cells=cells)

    def write_structures(self, structures):
        self._mutable()
        payload = [[list(key), _encode(value)] for key, value in structures.items()]
        _atomic_new(self.path / "structures.json", _json({"identity_hash": self._metadata()["identity_hash"],
                     "structures": payload, "sha256": _hash(payload)}) + "\n")

    def load_structures(self):
        payload = json.loads((self.path / "structures.json").read_text())
        if payload["identity_hash"] != self._metadata()["identity_hash"] or payload["sha256"] != _hash(payload["structures"]):
            raise ValueError("structure checkpoint identity/hash mismatch")
        return {tuple(key): _decode(value) for key, value in payload["structures"]}

    def validate_identity(self, identity):
        self._require_lock()
        saved = self._metadata()["identity"]
        for key in saved:
            if saved[key] != identity.get(key):
                raise ValueError(f"resume identity mismatch: {key}")
        if set(identity) != set(saved):
            raise ValueError("resume identity field mismatch")

    @classmethod
    def resume(cls, path, identity):
        writer = cls.open_locked(path)
        try:
            writer.validate_identity(identity)
            writer.load_frozen()
        except BaseException:
            writer.close()
            raise
        return writer

    def _append(self, name, value):
        self._mutable()
        with (self.path / name).open("a", encoding="utf-8") as stream:
            stream.write(_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def append_collection(self, cell):
        self._mutable()
        if (self.path / "frozen.json").exists():
            raise FileExistsError("collection is already frozen")
        saved = self.load_collection()
        index = len(saved.cells)
        anchors = _rows(self.path / "anchors.jsonl")
        if index >= len(anchors) or _encode(cell.anchor) != anchors[index]:
            raise ValueError("collection cell must match next manifest anchor")
        payload = _encode(cell, vectors=True)
        # Working recovery data is needed for cross-fit; deleted after freeze.
        envelope = {"identity_hash": self._metadata()["identity_hash"], "cell": payload, "sha256": _hash(payload)}
        _atomic_new(self.path / f".collection-{index:06d}.json", _json(envelope) + "\n")
        _atomic_replace(self.path / "collection.jsonl", "".join(_json(_encode(c)) + "\n" for c in (*saved.cells, cell)))

    def collection_provider(self, provider, anchor):
        """Replay durable model results within an interrupted collection cell.

        This private working journal contains vectors needed for calibration;
        it is removed once the immutable frozen checkpoint is published.
        """
        self._mutable()
        if (self.path / "frozen.json").exists():
            raise FileExistsError("collection is already frozen")
        metadata = self._metadata()
        if _encode(anchor) not in _rows(self.path / "anchors.jsonl"):
            raise ValueError("collection provider anchor is outside the manifest")
        writer = self
        prefix = _hash(_encode(anchor))
        operations = {"generate_with_usage", "capture_completion_trace", "score_completion_with_delta", "score_completions"}
        class CheckpointProvider:
            index = 0
            def __getattr__(self, name):
                method = getattr(provider, name)
                if name not in operations:
                    return method
                def invoke(*args, **kwargs):
                    writer._mutable()
                    path = writer.path / f".model-{prefix}-{self.index:06d}.json"
                    request = _encode((name, args, kwargs), vectors=True)
                    if path.exists():
                        saved = json.loads(path.read_text())
                        if saved["identity_hash"] != metadata["identity_hash"] or saved["request"] != request:
                            raise ValueError("collection operation request/identity mismatch")
                        if saved["sha256"] != _hash(saved["result"]):
                            raise ValueError("collection operation result hash mismatch")
                        result = _decode(saved["result"])
                    else:
                        result = method(*args, **kwargs)
                        payload = _encode(result, vectors=True)
                        _atomic_new(path, _json({"identity_hash": metadata["identity_hash"], "request": request,
                                               "result": payload, "sha256": _hash(payload)}) + "\n")
                    self.index += 1
                    return result
                return invoke
        return CheckpointProvider()

    def load_collection(self):
        metadata = self._metadata()
        anchors = _rows(self.path / "anchors.jsonl")
        cells = []
        for path in sorted(self.path.glob(".collection-*.json")):
            envelope = json.loads(path.read_text())
            payload = envelope["cell"]
            if envelope["identity_hash"] != metadata["identity_hash"] or envelope["sha256"] != _hash(payload):
                raise ValueError("collection checkpoint identity/hash mismatch")
            if len(cells) >= len(anchors) or payload["anchor"] != anchors[len(cells)]:
                raise ValueError("collection checkpoint anchor order mismatch")
            cells.append(_decode(payload))
        return runner.PreLeanCollection(_decode(metadata["config"]), _decode(metadata["app_config"]), tuple(cells),
                                       tuple(metadata["layers"]), metadata["model_revision"])

    def write_frozen(self, selections):
        self._mutable()
        if (self.path / "frozen.json").exists():
            raise FileExistsError("frozen selections already recorded")
        metadata = self._metadata()
        self.validate_identity(resume_identity(selections.collection, evaluation_cells=self.evaluation_cells()))
        if [_encode(c.prelean.anchor) for c in selections.cells] != _rows(self.path / "anchors.jsonl"):
            raise ValueError("frozen checkpoint anchor manifest mismatch")
        _atomic_replace(self.path / "prelean.jsonl", "".join(_json(_encode(cell, vectors=selections.collection.config.persist_full_activations)) + "\n" for cell in selections.cells))
        metadata["prelean_sha256"] = hashlib.sha256((self.path / "prelean.jsonl").read_bytes()).hexdigest()
        metadata["checkpoint_hash"] = _hash({"identity_hash": metadata["identity_hash"],
                                            "prelean_sha256": metadata["prelean_sha256"]})
        _atomic_new(self.path / "frozen.json", _json(metadata) + "\n")
        self._discard_working_collection()
        if selections.collection.config.persist_full_activations:
            with (self.path / "activation_vectors.jsonl.gz").open("xb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
                    for cell in selections.collection.cells:
                        stream.write((_json(_encode(cell, vectors=True)) + "\n").encode())

    def load_frozen(self):
        if not (self.path / "frozen.json").exists():
            raise ValueError("resume requires a fully frozen pre-Lean checkpoint")
        resolved = self._metadata()
        metadata = json.loads((self.path / "frozen.json").read_text())
        copies = {k: v for k, v in metadata.items() if k not in ("prelean_sha256", "checkpoint_hash")}
        if copies != resolved:
            raise ValueError("resolved/frozen checkpoint identity divergence")
        digest = hashlib.sha256((self.path / "prelean.jsonl").read_bytes()).hexdigest()
        if metadata.get("prelean_sha256") != digest or metadata.get("checkpoint_hash") != _hash(
            {"identity_hash": resolved["identity_hash"], "prelean_sha256": digest}
        ):
            raise ValueError("frozen checkpoint hash mismatch")
        raw_cells = _rows(self.path / "prelean.jsonl")
        if [c["prelean"]["anchor"] for c in raw_cells] != _rows(self.path / "anchors.jsonl"):
            raise ValueError("frozen checkpoint anchor manifest mismatch")
        cells = tuple(_decode(row) for row in raw_cells)
        collection = runner.PreLeanCollection(_decode(resolved["config"]), _decode(resolved["app_config"]),
            tuple(c.prelean for c in cells), tuple(resolved["layers"]), resolved["model_revision"])
        return runner.FrozenSelections(collection, cells)

    def _discard_working_collection(self):
        self._require_lock()
        for pattern in (".collection-*.json", ".model-*.json"):
            for path in self.path.glob(pattern):
                path.unlink()

    @staticmethod
    def _trial_rows(trial):
        meta = _encode(replace(trial, outcomes=(), equal_compute_outcomes=()))
        return [{"problem_id": trial.problem_id, "seed": trial.seed, "trial": meta, "outcome": _encode(outcome)}
                for outcome in (*trial.outcomes, *trial.equal_compute_outcomes)]

    def _validated_trials(self, rows, *, require_complete=False):
        scope = self.evaluation_cells()
        if len(rows) % 8:
            raise ValueError("partial trial cell; journal preserved without overwrite")
        if len(rows) > len(scope) * 8:
            raise ValueError("duplicate/extra trial cells outside evaluation scope")
        trials = []
        anchors = {(a["problem_id"], a["seed"]): a for a in _rows(self.path / "anchors.jsonl")}
        expected = {(arm.value, view) for arm in ActivationCondition for view in ("equal_tactic", "equal_compute")}
        for index in range(0, len(rows), 8):
            group = rows[index:index + 8]
            key = scope[index // 8]
            meta = group[0]["trial"]
            if any((r["problem_id"], r["seed"]) != key or r["trial"] != meta for r in group):
                raise ValueError("trial metadata or ordered evaluation scope mismatch")
            if (meta["problem_id"], meta["seed"]) != key or meta["anchor"] != anchors[key]:
                raise ValueError("trial metadata does not match its anchor/scope")
            outcomes = [r["outcome"] for r in group]
            if {(o["condition"], o["budget_view"]) for o in outcomes} != expected:
                raise ValueError("trial conditions/views are incomplete or duplicated")
            if any(o["budget_view"] != ("equal_tactic" if i < 4 else "equal_compute") for i, o in enumerate(outcomes)):
                raise ValueError("trial budget view ordering mismatch")
            decoded = tuple(_decode(o) for o in outcomes)
            trials.append(replace(_decode(meta), outcomes=decoded[:4], equal_compute_outcomes=decoded[4:]))
        if require_complete and len(trials) != len(scope):
            raise ValueError("every trial in the saved evaluation scope must be flushed before finalization")
        return tuple(trials)

    def append_trial(self, trial):
        self._mutable()
        rows = _rows(self.path / "trials.jsonl")
        if (trial.problem_id, trial.seed) in {(r["problem_id"], r["seed"]) for r in rows}:
            raise FileExistsError("trial cell already journaled")
        additions = self._trial_rows(trial)
        self._validated_trials([*rows, *additions])
        _atomic_replace(self.path / "trials.jsonl", "".join(_json(row) + "\n" for row in (*rows, *additions)))

    def load_trials(self, *, require_complete=False):
        return self._validated_trials(_rows(self.path / "trials.jsonl"), require_complete=require_complete)

    def is_complete(self):
        if not (self.path / "complete.json").exists():
            return False
        metadata = self._metadata()
        self.load_frozen()
        self.load_trials(require_complete=True)
        marker = json.loads((self.path / "complete.json").read_text())
        expected = {"identity_hash": metadata["identity_hash"], **{
            name: hashlib.sha256((self.path / name).read_bytes()).hexdigest()
            for name in ("trials.jsonl", "finalization.json", "report.md", "summary.json")}}
        if marker != expected:
            raise ValueError("completed artifact hash conflict")
        return True

    def finalize(self, run):
        self._require_lock()
        frozen = self.load_frozen()
        self._discard_working_collection()
        journal = self.load_trials(require_complete=True)
        if _encode(journal) != _encode(run.trials):
            raise ValueError("finalization run does not match the trial journal")
        if _encode(run.collection) != _encode(frozen.collection) or _encode(run.selections) != _encode(frozen):
            raise ValueError("finalization run does not match the frozen checkpoint")
        metadata = self._metadata()
        plan_path = self.path / "finalization.json"
        if plan_path.exists():
            # Preserve the original timed invocation while retrying publication.
            existing = json.loads(plan_path.read_text())
            run = replace(run, elapsed_seconds=existing["elapsed_seconds"])
        plan = {"identity_hash": metadata["identity_hash"],
                "trials_sha256": hashlib.sha256((self.path / "trials.jsonl").read_bytes()).hexdigest(),
                "elapsed_seconds": run.elapsed_seconds, "report": render_activation_report(run),
                "summary": _json(summarize_activation_run(run)) + "\n"}
        _publish_or_validate(plan_path, _json(plan) + "\n")
        _publish_or_validate(self.path / "report.md", plan["report"])
        _publish_or_validate(self.path / "summary.json", plan["summary"])
        marker = {"identity_hash": metadata["identity_hash"], **{
            name: hashlib.sha256((self.path / name).read_bytes()).hexdigest()
            for name in ("trials.jsonl", "finalization.json", "report.md", "summary.json")}}
        _publish_or_validate(self.path / "complete.json", _json(marker) + "\n")


def write_activation_artifacts(run, output_dir: Path) -> Path:
    """Write a complete run to the exact *new* run directory supplied."""
    with ActivationArtifactWriter.create(output_dir, run.collection) as writer:
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
                rescue_calls = sum(o.rescue_lean_calls for o in outcomes)
                causal = [value for o in outcomes for _, value in o.verified_tactic_causal_effects]
                rows.append({
                    "problem_id": problem, "role": "positive control" if problem in _POSITIVE else "hard negative" if problem in _HARD else "test",
                    "condition": arm.value, "budget_view": view, "cells": len(trials), "eligible": len(outcomes),
                    "no_stuck_anchor": sum(t.status == "no_stuck_anchor" for t in trials),
                    "invalid_usage": sum(t.status == "invalid_usage" for t in trials),
                    "failed_selection": sum(t.status == "failed_selection" for t in trials),
                    "solves": sum(values), "solve_rate": _mean(values),
                    "solve_rate_descriptive_ci95": descriptive_interval(values, seed=config.seed_start),
                    "accepted_progressing": progressed, "progress_per_lean_call": progressed / rescue_calls if rescue_calls else None,
                    "eligible_rescue_lean_calls": rescue_calls,
                    "immediate_rescue_solves": sum(o.immediate_utility == "completed" for o in outcomes),
                    "continuation_solves": sum(o.completed and o.immediate_utility != "completed" for o in outcomes),
                    "verified_tactic_causal_effect": _mean(causal),
                    "verified_tactic_causal_coverage": sum(v is not None for v in causal) / len(causal) if causal else None,
                    "verified_tactic_causal_measured": sum(v is not None for v in causal),
                    "verified_tactic_causal_submitted": len(causal),
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
            "prelean_elapsed_seconds": None if any(c.elapsed_seconds is None for c in run.collection.cells) else sum(c.elapsed_seconds for c in run.collection.cells),
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
             "Equal tactic/Lean budget measures selection quality including its extra model work. Equal compute caps all solve-contributing token positions, including continuation and post-selection causal diagnostics. Continuation is separately metered and stops before a call that cannot fit.",
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
    lines.extend(["", "Post-selection causal effect on tactics actually submitted to Lean (including rejected submissions). The selected arm reuses union scores; new final tactics receive separately charged diagnostics after the winner freezes. Coverage is measured/submitted immediate-rescue tactics; continuation tactics are outside this metric. Union-wide patch likelihood above is a separate selection statistic.", "",
                  "| Problem | Budget | Condition | Immediate solves | Continuation solves | Verified-tactic causal effect | Coverage |",
                  "|---|---|---|---:|---:|---:|---:|"])
    for row in summary["rows"]:
        lines.append("| " + " | ".join(_display(row[k]) for k in ("problem_id", "budget_view", "condition",
            "immediate_rescue_solves", "continuation_solves", "verified_tactic_causal_effect", "verified_tactic_causal_coverage")) + " |")
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
