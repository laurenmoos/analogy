"""Filesystem and checkpoint storage for solver reports."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ...config import AppConfig
from ...utils.checkpoint import Checkpoint, record_status
from .records import ProblemRunRecord, RunState, SolveRunRecord
from .render import render_run_report


def start_run(config: AppConfig, checkpoint: Checkpoint | None = None) -> RunState:
    """Create report storage before any proof computation begins."""

    run_id = uuid.uuid4().hex
    output_directory = config.run.output_dir / "solve" / run_id
    output_directory.mkdir(parents=True, exist_ok=False)
    return RunState(config, checkpoint, run_id, output_directory, time.monotonic())


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def write_problem_record(run: RunState, record: ProblemRunRecord) -> None:
    """Persist one problem and add it to the in-memory run report."""

    _write_json(run.output_directory / f"{record.problem.id}.json", record.payload)
    if run.checkpoint is not None:
        record_status(run.checkpoint, record.problem.id, record.outcome.status.value)
    run.traces.append(record.trace)
    run.results.append(record.outcome)


def write_run_record(run_state: RunState, run: SolveRunRecord) -> None:
    """Persist final run metadata and its human-readable report."""

    config = run_state.config
    checkpoint = run_state.checkpoint
    _write_json(
        run.output_directory / "run.json",
        {
            "run_id": run.run_id,
            "analogy_enabled": run.analogy_enabled,
            "model": config.model.name,
            "top_k": config.search.top_k,
            "max_steps": config.search.max_steps,
            "max_analogy_model_calls_per_stuck_state": config.analogy.max_model_calls,
            "problem_index_signature": (
                checkpoint.signature if checkpoint is not None else None
            ),
            "problem_index_fingerprints": (
                checkpoint.fingerprints if checkpoint is not None else None
            ),
            "elapsed_seconds": run.elapsed_seconds,
            "summary": asdict(run.summary),
        },
    )
    (run.output_directory / "report.md").write_text(
        render_run_report(run, run_state.traces),
        encoding="utf-8",
    )


__all__ = ["start_run", "write_problem_record", "write_run_record"]
