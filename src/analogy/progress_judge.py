"""Post-hoc LLM diagnosis for logged analogy-loop iterations.

This module has no dependency on proof search or RL.  It accepts immutable
records reconstructed from completed run artifacts and returns descriptive
judgments that cannot affect the source experiment.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .records import Message


class AuditStage(str, Enum):
    ANALOGY = "analogy"
    TACTIC_TRANSLATION = "tactic_translation"
    LEAN_EXECUTION = "lean_execution"
    PROOF_PROGRESS = "proof_progress"
    WORKING = "working"


@dataclass(frozen=True, slots=True)
class AuditIteration:
    run_id: str
    problem_id: str
    phase: str
    rollout_id: str
    iteration_index: int
    target: str
    state_before: str
    analogy_card: str
    predicted_transition: str
    tactics: tuple[str, ...]
    lean_outcomes: tuple[str, ...]
    lean_diagnostics: tuple[str, ...]
    actual_transition: str | None
    surprise: float | None
    stop_decision: str

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "problem_id",
            "phase",
            "rollout_id",
            "target",
            "state_before",
            "analogy_card",
            "predicted_transition",
            "stop_decision",
        ):
            _nonblank(getattr(self, name), name)
        if (
            isinstance(self.iteration_index, bool)
            or not isinstance(self.iteration_index, int)
            or self.iteration_index < 0
        ):
            raise ValueError("iteration_index must be a non-negative integer")
        for name in ("tactics", "lean_outcomes", "lean_diagnostics"):
            values = getattr(self, name)
            if isinstance(values, (str, bytes)):
                raise ValueError(f"{name} must be a sequence of strings")
            normalized = tuple(values)
            if any(not isinstance(item, str) for item in normalized):
                raise ValueError(f"{name} must contain only strings")
            object.__setattr__(self, name, normalized)
        if self.actual_transition is not None:
            _nonblank(self.actual_transition, "actual_transition")
        if self.surprise is not None:
            surprise = _finite_number(self.surprise, "surprise")
            if surprise < 0.0:
                raise ValueError("surprise must be non-negative")
            object.__setattr__(self, "surprise", surprise)


@dataclass(frozen=True, slots=True)
class AuditJudgment:
    stage: AuditStage
    progress_score: float
    analogy_quality: float
    confidence: float
    diagnosis: str
    evidence: str
    suggested_fix: str

    def __post_init__(self) -> None:
        if not isinstance(self.stage, AuditStage):
            raise ValueError("stage must be an AuditStage")
        for name, lower, upper in (
            ("progress_score", -1.0, 1.0),
            ("analogy_quality", 0.0, 1.0),
            ("confidence", 0.0, 1.0),
        ):
            value = _finite_number(getattr(self, name), name)
            if not lower <= value <= upper:
                raise ValueError(f"{name} must be in [{lower:g}, {upper:g}]")
            object.__setattr__(self, name, value)
        for name in ("diagnosis", "evidence", "suggested_fix"):
            value = _nonblank(getattr(self, name), name).strip()
            if len(value) > 400:
                raise ValueError(f"{name} must contain at most 400 characters")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class JudgeAttempt:
    judgment: AuditJudgment | None
    raw_outputs: tuple[str, ...]
    seeds: tuple[int, ...]
    failure: str | None = None

    def __post_init__(self) -> None:
        if len(self.raw_outputs) != len(self.seeds):
            raise ValueError("raw_outputs and seeds must have equal length")
        if self.judgment is None and not self.failure:
            raise ValueError("failed judge attempts require a failure")
        if self.judgment is not None and self.failure is not None:
            raise ValueError("successful judge attempts cannot have a failure")


_JUDGMENT_KEYS = {
    "stage",
    "progress_score",
    "analogy_quality",
    "confidence",
    "diagnosis",
    "evidence",
    "suggested_fix",
}


def parse_audit_judgment(raw: str) -> AuditJudgment:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("audit judgment must be exactly one JSON object") from error
    if not isinstance(payload, dict) or set(payload) != _JUDGMENT_KEYS:
        raise ValueError("audit judgment must contain exactly the required keys")
    try:
        stage = AuditStage(payload["stage"])
    except (TypeError, ValueError) as error:
        raise ValueError("stage must name a supported audit stage") from error
    return AuditJudgment(
        stage=stage,
        progress_score=payload["progress_score"],
        analogy_quality=payload["analogy_quality"],
        confidence=payload["confidence"],
        diagnosis=payload["diagnosis"],
        evidence=payload["evidence"],
        suggested_fix=payload["suggested_fix"],
    )


class LLMProcessJudge:
    def __init__(
        self, generator: Any, *, base_seed: int, max_repair_calls: int = 1
    ) -> None:
        if isinstance(base_seed, bool) or not isinstance(base_seed, int):
            raise ValueError("base_seed must be an integer")
        if (
            isinstance(max_repair_calls, bool)
            or not isinstance(max_repair_calls, int)
            or max_repair_calls < 0
        ):
            raise ValueError("max_repair_calls must be a non-negative integer")
        self.generator = generator
        self.base_seed = base_seed
        self.max_repair_calls = max_repair_calls

    def evaluate(self, iteration: AuditIteration) -> JudgeAttempt:
        if not isinstance(iteration, AuditIteration):
            raise ValueError("iteration must be an AuditIteration")
        messages = _judge_messages(iteration)
        raw_outputs: list[str] = []
        seeds: list[int] = []
        failure = "audit judgment must be exactly one JSON object"
        for attempt_index in range(self.max_repair_calls + 1):
            seed = _operation_seed(self.base_seed, iteration, attempt_index)
            active_messages = (
                messages
                if attempt_index == 0
                else _repair_messages(messages, raw_outputs[-1], failure)
            )
            try:
                raw = self.generator.generate(active_messages, seed=seed)
            except Exception as error:
                raw = ""
                failure = f"{type(error).__name__}: {error}"
            else:
                try:
                    judgment = parse_audit_judgment(raw)
                except ValueError as error:
                    failure = str(error)
                else:
                    raw_outputs.append(raw)
                    seeds.append(seed)
                    return JudgeAttempt(
                        judgment, tuple(raw_outputs), tuple(seeds), None
                    )
            raw_outputs.append(raw)
            seeds.append(seed)
        return JudgeAttempt(None, tuple(raw_outputs), tuple(seeds), failure)


def _judge_messages(iteration: AuditIteration) -> tuple[Message, ...]:
    successor = iteration.actual_transition or "No accepted successor"
    prompt = (
        "Diagnose this completed analogy-loop iteration after the experiment. "
        "You are an observer only: do not assign reward or claim proof correctness.\n"
        f"Theorem target:\n{iteration.target}\n"
        f"State before:\n{iteration.state_before}\n"
        f"Analogy card:\n{iteration.analogy_card}\n"
        f"Predicted transition:\n{iteration.predicted_transition}\n"
        f"Proposed tactics: {json.dumps(iteration.tactics)}\n"
        f"Lean outcomes: {json.dumps(iteration.lean_outcomes)}\n"
        f"Lean diagnostics: {json.dumps(iteration.lean_diagnostics)}\n"
        f"Actual successor:\n{successor}\n"
        f"Embedding surprise: {iteration.surprise}\n"
        f"Loop decision: {iteration.stop_decision}\n"
        "Choose the earliest stage that best explains the result: analogy, "
        "tactic_translation, lean_execution, proof_progress, or working. Return "
        "exactly one JSON object with stage, progress_score in [-1,1], "
        "analogy_quality in [0,1], confidence in [0,1], diagnosis, evidence, "
        "and suggested_fix. Keep each text field under 400 characters."
    )
    return (
        Message("system", "Audit a logged proof-search iteration without affecting it."),
        Message("user", prompt),
    )


def _repair_messages(
    original: tuple[Message, ...], raw: str, failure: str
) -> tuple[Message, ...]:
    return original + (
        Message("assistant", raw or "<empty output>"),
        Message(
            "user",
            f"The output failed validation: {failure}. Return only the required "
            "JSON object with no Markdown or commentary.",
        ),
    )


def _operation_seed(
    base_seed: int, iteration: AuditIteration, attempt_index: int
) -> int:
    payload = "\x1f".join(
        (
            "post-hoc-analogy-audit-v1",
            str(base_seed),
            iteration.run_id,
            iteration.problem_id,
            iteration.phase,
            iteration.rollout_id,
            str(iteration.iteration_index),
            str(attempt_index),
        )
    )
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:8], "big")


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank text")
    return value


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be a finite number")
    return normalized


__all__ = [
    "AuditIteration",
    "AuditJudgment",
    "AuditStage",
    "JudgeAttempt",
    "LLMProcessJudge",
    "parse_audit_judgment",
]
