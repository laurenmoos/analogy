"""Two-condition probe orchestration with durable resume evidence."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from .config import AppConfig
from .lean_gym.corpus import load_problems
from .lean_gym.models import Problem
from .lean_gym.resources import LEAN_PROJECT_ROOT
from .lean_gym.verifier import LeanSubprocessVerifier
from .main import load_config
from .model.model_provider import get_model_provider
from .solver.report.evaluation import (
    EvaluationRecord,
    finalize_evaluation,
    load_evaluation_manifest,
    write_evaluation_manifest,
)
from .solver.run import run_analogy_assisted, run_deliberation_assisted
from .utils.checkpoint import load_checkpoint


PROBE_PROBLEM_IDS = (
    "propl_ood_168469097189615988227299206974",
    "propl_ood_358233606958956408248147497163",
    "propl_ood_265557872740597889312779794693",
)
SMALL_PROBLEM_IDS = (
    "propl_ood_307714199757159990961917100773",
    "propl_ood_301873284032250623028148772769",
    "propl_ood_249474521826265955663880755174",
    "propl_ood_317064830399563761278070121273",
    "propl_ood_198299705576588914912737276887",
    "propl_ood_208645874253727215108588452622",
    "propl_ood_252955983084163427303699743884",
    "propl_ood_54781560256269106948607279652",
    "propl_ood_205602444047427159151729097987",
    "propl_ood_55195369758345374712361994170",
)
CONDITIONS = (
    "direct_deliberation",
    "ranked_analogy",
)

TrialExecutor = Callable[[str, AppConfig, Problem, Any, Any, Any], Any]


def execute_probe(
    config: AppConfig,
    problems: Sequence[Problem],
    model: Any,
    verifier: Any,
    *,
    evaluation_id: str | None = None,
    trial_executor: TrialExecutor | None = None,
) -> EvaluationRecord:
    """Run or resume the fixed six-trial probe."""

    return _execute_evaluation(
        config,
        problems,
        model,
        verifier,
        phase="probe",
        required_problem_ids=PROBE_PROBLEM_IDS,
        evaluation_id=evaluation_id,
        trial_executor=trial_executor,
    )


def execute_small(
    config: AppConfig,
    problems: Sequence[Problem],
    model: Any,
    verifier: Any,
    *,
    evaluation_id: str | None = None,
    trial_executor: TrialExecutor | None = None,
) -> EvaluationRecord:
    """Run or resume the frozen twenty-trial small evaluation."""

    return _execute_evaluation(
        config,
        problems,
        model,
        verifier,
        phase="small",
        required_problem_ids=SMALL_PROBLEM_IDS,
        evaluation_id=evaluation_id,
        trial_executor=trial_executor,
    )


def _execute_evaluation(
    config: AppConfig,
    problems: Sequence[Problem],
    model: Any,
    verifier: Any,
    *,
    phase: str,
    required_problem_ids: tuple[str, ...],
    evaluation_id: str | None,
    trial_executor: TrialExecutor | None,
) -> EvaluationRecord:

    normalized = tuple(problems)
    problem_ids = tuple(problem.id for problem in normalized)
    if problem_ids != required_problem_ids:
        raise ValueError(
            f"{phase} requires ordered problem IDs {required_problem_ids!r}; "
            f"got {problem_ids!r}"
        )
    identity = _evaluation_identity(config, normalized, phase=phase)
    active_id = evaluation_id or f"{phase}-{identity[:16]}"
    output_directory = config.run.output_dir.parent / "evaluations" / active_id
    manifest_path = output_directory / "manifest.json"
    if manifest_path.exists():
        manifest = load_evaluation_manifest(manifest_path)
        if manifest.get("identity_sha256") != identity:
            raise ValueError(
                f"evaluation {active_id!r} belongs to different frozen inputs"
            )
    else:
        manifest = _new_manifest(
            active_id, identity, config, problem_ids, phase=phase
        )
        write_evaluation_manifest(manifest_path, manifest)

    executor = trial_executor or _execute_trial
    trial_indexes = {
        (item.get("condition"), item.get("problem_id"), item.get("seed")): index
        for index, item in enumerate(manifest["trials"])
    }
    for problem in normalized:
        for condition in CONDITIONS:
            key = (condition, problem.id, 0)
            prior_index = trial_indexes.get(key)
            if (
                prior_index is not None
                and manifest["trials"][prior_index].get("status") == "terminal"
            ):
                continue
            trial_config = _condition_config(config, condition)
            checkpoint = load_checkpoint(
                trial_config, normalized, condition=condition
            )
            trial = {
                "condition": condition,
                "problem_id": problem.id,
                "seed": 0,
                "status": "running",
                "checkpoint_signature": checkpoint.signature,
            }
            if prior_index is None:
                prior_index = len(manifest["trials"])
                trial_indexes[key] = prior_index
                manifest["trials"].append(trial)
            else:
                manifest["trials"][prior_index] = trial
            manifest["status"] = "running"
            write_evaluation_manifest(manifest_path, manifest)
            try:
                run = executor(
                    condition,
                    trial_config,
                    problem,
                    model,
                    verifier,
                    checkpoint,
                )
                trial.update(
                    {
                        "status": "terminal",
                        "run_directory": str(Path(run.output_directory).resolve()),
                    }
                )
            except Exception as error:
                trial.update(
                    {
                        "status": "failed",
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
            manifest["trials"][prior_index] = trial
            write_evaluation_manifest(manifest_path, manifest)
    return finalize_evaluation(manifest_path)


def run_evaluation(config_path: Path, *, phase: str) -> int:
    """Load expensive dependencies once and execute one supported phase."""

    if phase not in {"probe", "small"}:
        raise ValueError(f"unsupported evaluation phase {phase!r}")
    config = load_config(Path(config_path))
    if config.analogy.model_name not in {None, config.model.name}:
        raise ValueError(
            f"{phase} requires one shared model for ordinary and analogy calls"
        )
    required_problem_ids = (
        PROBE_PROBLEM_IDS if phase == "probe" else SMALL_PROBLEM_IDS
    )
    by_id = {problem.id: problem for problem in load_problems(config.benchmark.corpus)}
    missing = [
        problem_id for problem_id in required_problem_ids if problem_id not in by_id
    ]
    if missing:
        raise ValueError(f"{phase} corpus is missing problem IDs: {missing}")
    problems = tuple(by_id[problem_id] for problem_id in required_problem_ids)
    model = get_model_provider(config.model)
    verifier = LeanSubprocessVerifier(LEAN_PROJECT_ROOT)
    executor = execute_probe if phase == "probe" else execute_small
    record = executor(config, problems, model, verifier)
    print(f"EVALUATION: {record.status}")
    print(f"DETAILS: {record.output_directory}")
    return 0 if record.complete else 1


def _execute_trial(
    condition: str,
    config: AppConfig,
    problem: Problem,
    model: Any,
    verifier: Any,
    checkpoint: Any,
) -> Any:
    if condition == "direct_deliberation":
        return run_deliberation_assisted(
            config, (problem,), model, verifier, checkpoint=checkpoint
        )
    return run_analogy_assisted(
        config,
        (problem,),
        model,
        verifier,
        checkpoint=checkpoint,
        analogy_generator=model,
    )


def _condition_config(config: AppConfig, condition: str) -> AppConfig:
    if condition not in CONDITIONS:
        raise ValueError(f"unknown evaluation condition {condition!r}")
    analogy = replace(
        config.analogy,
        enabled=condition != "direct_deliberation",
        selection="structural",
    )
    return replace(
        config,
        analogy=analogy,
        benchmark=replace(config.benchmark, selection="exhaustive"),
        run=replace(config.run, seed=0),
    )


def _evaluation_identity(
    config: AppConfig, problems: tuple[Problem, ...], *, phase: str
) -> str:
    payload = {
        "protocol": "analogy-probe-v2" if phase == "probe" else "analogy-small-v1",
        "problem_ids": [problem.id for problem in problems],
        "conditions": CONDITIONS,
        "seed": 0,
        "model": asdict(config.model),
        "search": asdict(config.search),
        "max_model_calls": config.analogy.max_model_calls,
        "prompts_sha256": _file_sha256(config.prompts.path),
        "corpus_sha256": _file_sha256(config.benchmark.corpus),
        "calibration_sha256": (
            _file_sha256(config.analogy.calibration_path)
            if config.analogy.calibration_path is not None
            else None
        ),
    }
    return _sha256_json(payload)


def _new_manifest(
    evaluation_id: str,
    identity: str,
    config: AppConfig,
    problem_ids: tuple[str, ...],
    *,
    phase: str,
) -> dict[str, Any]:
    environment = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    environment["sha256"] = _sha256_json(environment)
    return {
        "schema_version": 1,
        "evaluation_id": evaluation_id,
        "identity_sha256": identity,
        "phase": phase,
        "status": "pending",
        "conditions": list(CONDITIONS),
        "problem_ids": list(problem_ids),
        "seeds": [0],
        "expected_trial_count": len(CONDITIONS) * len(problem_ids),
        "source": {
            "commit": _git_value("rev-parse", "HEAD"),
            "dirty": bool(_git_value("status", "--porcelain")),
        },
        "inputs": {
            "model": config.model.name,
            "model_revision": config.model.revision,
            "prompts_sha256": _file_sha256(config.prompts.path),
            "corpus_sha256": _file_sha256(config.benchmark.corpus),
            "calibration_sha256": (
                _file_sha256(config.analogy.calibration_path)
                if config.analogy.calibration_path is not None
                else None
            ),
            "config_sha256": _sha256_json(_frozen_config_payload(config)),
        },
        "environment": environment,
        "trials": [],
        "validation": {"errors": []},
        "metrics": {},
    }


def _git_value(*arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ("git", *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _frozen_config_payload(config: AppConfig) -> dict[str, Any]:
    return {
        "model": asdict(config.model),
        "search": asdict(config.search),
        "analogy": {
            "enabled": config.analogy.enabled,
            "selection": config.analogy.selection,
            "max_model_calls": config.analogy.max_model_calls,
            "model_name": config.analogy.model_name,
            "calibration_sha256": (
                _file_sha256(config.analogy.calibration_path)
                if config.analogy.calibration_path is not None
                else None
            ),
        },
        "benchmark": {
            "selection": config.benchmark.selection,
            "corpus_sha256": _file_sha256(config.benchmark.corpus),
        },
        "prompts_sha256": _file_sha256(config.prompts.path),
        "run_seed": config.run.seed,
    }


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "CONDITIONS",
    "PROBE_PROBLEM_IDS",
    "SMALL_PROBLEM_IDS",
    "execute_probe",
    "execute_small",
    "run_evaluation",
]
