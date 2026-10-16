"""Post-hoc LLM diagnosis for logged analogy-loop iterations.

This module has no dependency on proof search or RL.  It accepts immutable
records reconstructed from completed run artifacts and returns descriptive
judgments that cannot affect the source experiment.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .records import Message
from .prompts import prompt_catalog


class AuditStage(str, Enum):
    ANALOGY = "analogy"
    TACTIC_TRANSLATION = "tactic_translation"
    LEAN_EXECUTION = "lean_execution"
    PROOF_PROGRESS = "proof_progress"
    WORKING = "working"


def persisted_stage_evidence(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Describe saved evidence without running parsing, compilation, or Lean.

    Version gates are intentionally local to the observer; loading an old trace
    must not invoke current solver logic or reconstruct a missing grounded plan.
    """
    for name, supported in (("grounded_plan_schema_version", "grounded-plan-v1"),
                            ("compiler_version", "grounded-compiler-v1")):
        value = row.get(name)
        if value is not None and value != supported:
            raise ValueError(f"unsupported {name} version: {value!r}")
    grounded = any(name in row for name in (
        "inventory", "grounded_plan", "grounded_plan_schema_version", "compiled_tactics"))
    generated = grounded or "source" in row or "generated_source" in row
    kind = "grounded_generated" if grounded else "raw_generated" if generated else "legacy"
    failed = row.get("failed_stage")

    def stage(name: str, payload: Any, available: bool) -> dict[str, Any]:
        return {"status": "failed" if failed == name else "available" if available else "unavailable",
                "payload": payload,
                "diagnostic": row.get("failure_diagnostic") if failed == name else None}

    stages = {}
    if generated:
        for name in ("source", "alignment", "transfer"):
            stages[name] = stage(name, {"record": row.get(name),
                "generation_attempts": row.get(f"{name}_generation_attempts", [])}, row.get(name) is not None)
    else:
        stages["analogy"] = stage("analogy", row.get("hop"), row.get("hop") is not None)
    if grounded:
        stages["inventory"] = stage("inventory", row.get("inventory"), row.get("inventory") is not None)
        stages["grounding"] = stage("grounding", row.get("grounded_plan"), row.get("grounded_plan") is not None)
        stages["grounding"]["generation_attempts"] = row.get("grounding_generation_attempts", [])
        stages["compilation"] = stage("compilation", {
            "candidates": row.get("compiled_tactics", []),
            "diagnostics": row.get("compilation_diagnostics", []),
            "compiler_version": row.get("compiler_version"),
            "schema_version": row.get("grounded_plan_schema_version"),
        }, bool(row.get("compiled_tactics") or row.get("compilation_diagnostics")))
        if row.get("compiled_tactics"):
            # Task 4 labels either compilation failure with the same stage;
            # an original candidate is persisted evidence that the first compiled.
            stages["compilation"]["status"] = "available"
            stages["compilation"]["diagnostic"] = None
        stages["repair"] = stage("grounding_repair", {
            "plan": row.get("repaired_grounded_plan"),
            "generation_attempts": row.get("grounding_repair_attempts", []),
            "candidates": row.get("repaired_compiled_tactics", []),
            "diagnostics": row.get("repair_compilation_diagnostics", []),
        }, row.get("repaired_grounded_plan") is not None)
        if row.get("repair_compilation_diagnostics") and not row.get("repaired_compiled_tactics"):
            stages["repair"]["status"] = "failed"
            stages["repair"]["diagnostic"] = row.get("failure_diagnostic")
    else:
        stages["tactic"] = stage("tactic", {"plan": row.get("tactic_plan"),
            "generation_attempts": row.get("tactic_generation_attempts", [])}, row.get("tactic_plan") is not None)
    stages["lean"] = stage("lean", row.get("attempts", []), bool(row.get("attempts")))
    return kind, stages


@dataclass(frozen=True, slots=True)
class LeanAttemptEvidence:
    source: str
    status: str
    diagnostics: tuple[str, ...]
    discard_reason: str | None
    successor: str | None
    verification_status: str | None = None
    infrastructure_failure: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _nonblank(self.source, "source"))
        object.__setattr__(self, "status", _nonblank(self.status, "status"))
        for name in ("verification_status", "infrastructure_failure"):
            if getattr(self, name) is not None:
                _nonblank(getattr(self, name), name)
        if isinstance(self.diagnostics, (str, bytes)) or any(
            not isinstance(item, str) for item in self.diagnostics
        ):
            raise ValueError("diagnostics must be a sequence of strings")
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if self.discard_reason is not None:
            object.__setattr__(
                self,
                "discard_reason",
                _nonblank(self.discard_reason, "discard_reason"),
            )
        if self.successor is not None:
            object.__setattr__(
                self, "successor", _nonblank(self.successor, "successor")
            )


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
    discard_reasons: tuple[str, ...]
    actual_transition: str | None
    surprise: float | None
    stop_decision: str
    lean_attempts: tuple[LeanAttemptEvidence, ...]
    proof_path_id: str | None = None
    record_kind: str = "legacy"
    stage_payloads: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.record_kind not in {"legacy", "raw_generated", "grounded_generated"}:
            raise ValueError("unsupported audit record kind")
        if not isinstance(self.stage_payloads, dict):
            raise ValueError("stage_payloads must be an object")
        if self.proof_path_id is not None:
            _nonblank(self.proof_path_id, "proof_path_id")
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
        for name in (
            "tactics",
            "lean_outcomes",
            "lean_diagnostics",
            "discard_reasons",
        ):
            values = getattr(self, name)
            if isinstance(values, (str, bytes)):
                raise ValueError(f"{name} must be a sequence of strings")
            normalized = tuple(values)
            if any(not isinstance(item, str) for item in normalized):
                raise ValueError(f"{name} must contain only strings")
            object.__setattr__(self, name, normalized)
        if isinstance(self.lean_attempts, (str, bytes)) or any(
            not isinstance(item, LeanAttemptEvidence) for item in self.lean_attempts
        ):
            raise ValueError("lean_attempts must contain LeanAttemptEvidence values")
        object.__setattr__(self, "lean_attempts", tuple(self.lean_attempts))
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
        payload = json.loads(raw, object_pairs_hook=_unique_object)
    except (TypeError, json.JSONDecodeError, RecursionError) as error:
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

    def evaluate(
        self, iteration: AuditIteration, *, max_calls: int | None = None
    ) -> JudgeAttempt:
        if not isinstance(iteration, AuditIteration):
            raise ValueError("iteration must be an AuditIteration")
        if max_calls is not None and (
            isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls <= 0
        ):
            raise ValueError("max_calls must be a positive integer")
        messages = _judge_messages(iteration)
        raw_outputs: list[str] = []
        seeds: list[int] = []
        failure = "audit judgment must be exactly one JSON object"
        attempt_limit = self.max_repair_calls + 1
        if max_calls is not None:
            attempt_limit = min(attempt_limit, max_calls)
        for attempt_index in range(attempt_limit):
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
    return prompt_catalog().messages(
        "progress_audit",
        target=iteration.target,
        state_before=iteration.state_before,
        analogy_card=iteration.analogy_card,
        record_kind=iteration.record_kind,
        stage_payloads=json.dumps(iteration.stage_payloads, sort_keys=True),
        predicted_transition=iteration.predicted_transition,
        lean_attempts=json.dumps([asdict(item) for item in iteration.lean_attempts]),
        tactics=json.dumps(iteration.tactics),
        lean_outcomes=json.dumps(iteration.lean_outcomes),
        lean_diagnostics=json.dumps(iteration.lean_diagnostics),
        discard_reasons=json.dumps(iteration.discard_reasons),
        successor=successor,
        surprise=iteration.surprise,
        stop_decision=iteration.stop_decision,
    )


def _repair_messages(
    original: tuple[Message, ...], raw: str, failure: str
) -> tuple[Message, ...]:
    repair = prompt_catalog().messages(
        "progress_audit_repair", failure=failure, raw=raw or "<empty output>"
    )
    return original + (Message("assistant", raw or "<empty output>"), repair[1])


def _operation_seed(
    base_seed: int, iteration: AuditIteration, attempt_index: int
) -> int:
    identity = (
        "post-hoc-analogy-audit-v1",
        str(base_seed),
        iteration.run_id,
        iteration.problem_id,
        iteration.phase,
        iteration.rollout_id,
        str(iteration.iteration_index),
        str(attempt_index),
    )
    # Preserve historical seeds, while isolating repeated state invocations.
    if iteration.proof_path_id is not None:
        identity += (iteration.proof_path_id,)
    payload = "\x1f".join(identity)
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:8], "big")


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank text")
    return value


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be a finite number")
    return normalized


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


__all__ = [
    "AuditIteration",
    "AuditJudgment",
    "AuditStage",
    "JudgeAttempt",
    "LeanAttemptEvidence",
    "LLMProcessJudge",
    "parse_audit_judgment",
]
