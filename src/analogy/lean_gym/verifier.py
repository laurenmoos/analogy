from __future__ import annotations

import json
import hashlib
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..records import (
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)

from .models import Diagnostic, Problem, ProofState, VerificationResult, VerificationStatus


class Verifier(Protocol):
    def initial_state(self, problem: Problem) -> VerificationResult: ...

    def state_after(self, problem: Problem, accepted_tactics: tuple[str, ...]) -> VerificationResult: ...

    def try_tactic(self, problem: Problem, accepted_tactics: tuple[str, ...], candidate: str) -> VerificationResult: ...

    def verify_complete(self, problem: Problem, tactics: tuple[str, ...]) -> VerificationResult: ...


@dataclass(frozen=True, slots=True)
class RenderedSource:
    text: str
    trace_line: int | None
    sorry_line: int | None


@dataclass(frozen=True, slots=True)
class LeanRun:
    exit_code: int | None
    diagnostics: tuple[Diagnostic, ...]
    duration_ms: int
    timed_out: bool = False
    internal_error: bool = False


class LeanSubprocessVerifier:
    def __init__(
        self,
        project_root: Path,
        timeout_seconds: float = 10.0,
        max_output_bytes: int = 1_048_576,
        lake_executable: str = "lake",
    ) -> None:
        self.project_root = Path(project_root)
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.lake_executable = lake_executable

    def initial_state(self, problem: Problem) -> VerificationResult:
        return self.state_after(problem, ())

    def state_after(self, problem: Problem, accepted_tactics: tuple[str, ...]) -> VerificationResult:
        invalid = self._validate_prefix(accepted_tactics)
        if invalid is not None:
            return invalid
        rendered = self._render_probe(problem, accepted_tactics)
        return self._classify_probe(problem, accepted_tactics, rendered, self._run_lean(rendered.text))

    def try_tactic(
        self,
        problem: Problem,
        accepted_tactics: tuple[str, ...],
        candidate: str,
    ) -> VerificationResult:
        invalid = self._validate_tactic(candidate)
        if invalid is not None:
            return invalid
        prefix_invalid = self._validate_prefix(accepted_tactics)
        if prefix_invalid is not None:
            return prefix_invalid
        tactics = accepted_tactics + (candidate,)
        rendered = self._render_probe(problem, tactics)
        return self._classify_probe(problem, tactics, rendered, self._run_lean(rendered.text))

    def verify_complete(self, problem: Problem, tactics: tuple[str, ...]) -> VerificationResult:
        invalid = self._validate_prefix(tactics)
        if invalid is not None:
            return invalid
        run = self._run_lean(self._render_final(problem, tactics).text)
        if run.timed_out:
            return self._result(VerificationStatus.TIMEOUT, False, None, run)
        if run.internal_error:
            return self._result(VerificationStatus.INTERNAL_ERROR, False, None, run)
        has_error = any(item.severity == "error" for item in run.diagnostics)
        has_sorry = any(item.kind == "hasSorry" for item in run.diagnostics)
        if run.exit_code == 0 and not has_error and not has_sorry:
            return self._result(VerificationStatus.COMPLETE, True, None, run)
        return self._result(VerificationStatus.REJECTED, False, None, run)

    def _classify_probe(
        self,
        problem: Problem,
        tactics: tuple[str, ...],
        rendered: RenderedSource,
        run: LeanRun,
    ) -> VerificationResult:
        if run.timed_out:
            return self._result(VerificationStatus.TIMEOUT, False, None, run)
        if run.internal_error:
            return self._result(VerificationStatus.INTERNAL_ERROR, False, None, run)
        traces = [item for item in run.diagnostics if item.kind == "trace" and item.line == rendered.trace_line]
        errors = [item for item in run.diagnostics if item.severity == "error"]
        if not errors and run.exit_code == 0 and len(traces) == 1 and not traces[0].message.strip():
            return self.verify_complete(problem, tactics)
        if errors or run.exit_code != 0 or len(traces) != 1 or not traces[0].message.strip():
            return self._result(VerificationStatus.REJECTED, False, None, run)
        has_sorry = any(item.kind == "hasSorry" for item in run.diagnostics)
        if not has_sorry:
            protocol = Diagnostic("error", "protocol", "probe did not report generated sorry", None, None)
            altered = LeanRun(run.exit_code, run.diagnostics + (protocol,), run.duration_ms)
            return self._result(VerificationStatus.INTERNAL_ERROR, False, None, altered)
        return self._result(VerificationStatus.OPEN, True, ProofState.from_text(traces[0].message), run)

    def _validate_prefix(self, tactics: tuple[str, ...]) -> VerificationResult | None:
        for tactic in tactics:
            invalid = self._validate_tactic(tactic)
            if invalid is not None:
                return invalid
        return None

    def _validate_tactic(self, tactic: str) -> VerificationResult | None:
        if not isinstance(tactic, str) or not tactic.strip() or "\n" in tactic or "\r" in tactic or "\x00" in tactic or len(tactic) > 4096:
            diagnostic = Diagnostic("error", "input_validation", "tactic must be one nonblank line of at most 4096 characters", None, None)
            return VerificationResult(VerificationStatus.REJECTED, False, None, (diagnostic,), None, 0)
        return None

    @staticmethod
    def _render_probe(problem: Problem, tactics: tuple[str, ...]) -> RenderedSource:
        lines = [problem.theorem_header()]
        lines.extend(f"  {tactic}" for tactic in tactics)
        trace_line = len(lines) + 1
        lines.append("  trace_state")
        sorry_line = len(lines) + 1
        lines.append("  all_goals sorry")
        return RenderedSource("\n".join(lines) + "\n", trace_line, sorry_line)

    @staticmethod
    def _render_final(problem: Problem, tactics: tuple[str, ...]) -> RenderedSource:
        lines = [problem.theorem_header()]
        lines.extend(f"  {tactic}" for tactic in tactics)
        return RenderedSource("\n".join(lines) + "\n", None, None)

    def _run_lean(self, source: str) -> LeanRun:
        started = time.monotonic_ns()
        try:
            with tempfile.TemporaryDirectory(prefix="lean-gym-") as directory:
                source_path = Path(directory) / "Current.lean"
                source_path.write_text(source, encoding="utf-8")
                environment = {**os.environ, "ELAN_TOOLCHAIN": "leanprover/lean4:v4.29.1"}
                completed = subprocess.run(
                    [self.lake_executable, "env", "lean", "--json", str(source_path)],
                    cwd=self.project_root,
                    capture_output=True,
                    text=False,
                    timeout=self.timeout_seconds,
                    check=False,
                    env=environment,
                )
        except subprocess.TimeoutExpired:
            duration = (time.monotonic_ns() - started) // 1_000_000
            diagnostic = Diagnostic("error", "timeout", f"Lean exceeded {self.timeout_seconds:g} seconds", None, None)
            return LeanRun(None, (diagnostic,), duration, timed_out=True)
        except (OSError, ValueError) as exc:
            duration = (time.monotonic_ns() - started) // 1_000_000
            diagnostic = Diagnostic("error", "internal", str(exc), None, None)
            return LeanRun(None, (diagnostic,), duration, internal_error=True)
        duration = (time.monotonic_ns() - started) // 1_000_000
        raw = completed.stdout + completed.stderr
        if len(raw) > self.max_output_bytes:
            diagnostic = Diagnostic("error", "output_limit", f"Lean output exceeded {self.max_output_bytes} bytes", None, None)
            return LeanRun(completed.returncode, (diagnostic,), duration, internal_error=True)
        return LeanRun(completed.returncode, self._parse_diagnostics(raw), duration)

    @staticmethod
    def _parse_diagnostics(raw: bytes) -> tuple[Diagnostic, ...]:
        diagnostics: list[Diagnostic] = []
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise TypeError("diagnostic is not an object")
                position = item.get("pos") if isinstance(item.get("pos"), dict) else {}
                data = item.get("data", "")
                message = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, sort_keys=True)
                diagnostics.append(
                    Diagnostic(
                        severity=str(item.get("severity", "error")),
                        kind=str(item["kind"]) if item.get("kind") is not None else None,
                        message=message,
                        line=position.get("line") if isinstance(position.get("line"), int) else None,
                        column=position.get("column") if isinstance(position.get("column"), int) else None,
                    )
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                diagnostics.append(Diagnostic("error", "protocol", line, None, None))
        return tuple(diagnostics)

    @staticmethod
    def _result(
        status: VerificationStatus,
        accepted: bool,
        proof_state: ProofState | None,
        run: LeanRun,
    ) -> VerificationResult:
        return VerificationResult(status, accepted, proof_state, run.diagnostics, run.exit_code, run.duration_ms)


class LeanGymAdapter:
    """Branch-safe adapter from symbolic search states to Lean proof prefixes."""

    def __init__(self, problem: Problem, verifier: Verifier) -> None:
        self.problem = problem
        self.verifier = verifier
        self._prefixes: dict[str, tuple[str, ...]] = {}
        self._failed: dict[str, list[str]] = {}

    def initial_state(self) -> SymbolicState:
        result = self.verifier.initial_state(self.problem)
        if result.status is not VerificationStatus.OPEN or result.proof_state is None:
            detail = "; ".join(item.message for item in result.diagnostics)
            raise RuntimeError(f"could not initialize {self.problem.id}: {detail}")
        return self._register(result.proof_state.normalized, ())

    def failed_tactics_for(self, state_id: str) -> tuple[str, ...]:
        return tuple(self._failed.get(state_id, ()))

    def run_tactic(
        self,
        state: SymbolicState,
        candidate: TacticCandidate,
    ) -> TacticResult:
        try:
            prefix = self._prefixes[state.id]
        except KeyError as error:
            raise ValueError(f"unknown Lean search state: {state.id}") from error
        result = self.verifier.try_tactic(self.problem, prefix, candidate.source)
        diagnostics = tuple(
            item.message for item in result.diagnostics if item.message.strip()
        )
        if result.status is VerificationStatus.COMPLETE and result.accepted:
            tactics = prefix + (candidate.source,)
            lines = [self.problem.theorem_header()]
            lines.extend(f"  {tactic}" for tactic in tactics)
            return TacticResult(
                candidate,
                TacticStatus.COMPLETE,
                proof="\n".join(lines) + "\n",
                diagnostics=diagnostics,
            )
        if (
            result.status is VerificationStatus.OPEN
            and result.accepted
            and result.proof_state is not None
        ):
            successor = self._register(
                result.proof_state.normalized,
                prefix + (candidate.source,),
            )
            return TacticResult(
                candidate,
                TacticStatus.ACCEPTED,
                state=successor,
                diagnostics=diagnostics,
            )
        self._failed.setdefault(state.id, []).append(candidate.source)
        return TacticResult(
            candidate,
            TacticStatus.ERROR,
            diagnostics=diagnostics or (result.status.value,),
        )

    def _register(
        self,
        proof_state: str,
        prefix: tuple[str, ...],
    ) -> SymbolicState:
        digest = hashlib.sha256(proof_state.encode("utf-8")).hexdigest()[:16]
        state_id = f"{self.problem.id}:{digest}"
        self._prefixes.setdefault(state_id, prefix)
        return SymbolicState(state_id, proof_state, "lean4", prefix)
