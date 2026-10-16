from __future__ import annotations

import json
import hashlib
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ..records import (
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)
from ..proof_views import ProofStructure, ProofViewError

if TYPE_CHECKING:
    from ..proof_inventory import LocalProofInventory

from .models import Diagnostic, Problem, ProofState, VerificationResult, VerificationStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class LeanInfrastructureResult(TacticResult):
    """Keep the ERROR contract while retaining a non-proof verifier failure."""

    verification_status: VerificationStatus

    def __post_init__(self) -> None:
        TacticResult.__post_init__(self)
        if (self.status is not TacticStatus.ERROR
                or not isinstance(self.verification_status, VerificationStatus)
                or self.verification_status not in (
                    VerificationStatus.TIMEOUT, VerificationStatus.INTERNAL_ERROR,
                )):
            raise ValueError("infrastructure results require ERROR and a typed verifier failure")

    @property
    def infrastructure_failure(self) -> str:
        return self.verification_status.value


_LEAN_STRUCTURE_PRELUDE = r'''import Lean
open Lean Meta Elab Tactic

partial def analogyExprShape (needle : Option Expr) (expression : Expr)
    (depth : Nat := 0) : MetaM Json := do
  if depth >= 8 then
    return Json.mkObj [("root", Json.str "opaque"), ("children", Json.arr #[])]
  let expression ← instantiateMVars expression
  if let some target := needle then
    if expression == target then
      return Json.mkObj [("root", Json.str "target"), ("children", Json.arr #[])]
  match expression with
  | .forallE _ domain body _ =>
      let root := if body.hasLooseBVar 0 then "forall" else "implication"
      let left ← analogyExprShape needle domain (depth + 1)
      let right ← analogyExprShape needle body (depth + 1)
      return Json.mkObj [("root", Json.str root), ("children", Json.arr #[left, right])]
  | _ =>
      let root := match expression.getAppFn.constName? with
        | some ``And => "and"
        | some ``Or => "or"
        | some ``Iff => "iff"
        | some ``Eq => "equality"
        | some ``Not => "not"
        | some ``Exists => "exists"
        | some ``False => "false"
        | _ => if expression.isApp then "application" else "atom"
      let children ← expression.getAppArgs.mapM fun child =>
        analogyExprShape needle child (depth + 1)
      return Json.mkObj [("root", Json.str root), ("children", Json.arr children)]

elab "trace_proof_structure" : tactic => do
  let goals ← getGoals
  match goals[0]? with
  | none => pure ()
  | some goal => goal.withContext do
      let target ← instantiateMVars (← goal.getType)
      let targetJson ← analogyExprShape none target
      let mut hypotheses : Array Json := #[]
      for declaration in (← getLCtx) do
        if !declaration.isImplementationDetail && (← isProp declaration.type) then
          let shape ← analogyExprShape (some target) declaration.type
          hypotheses := hypotheses.push shape
      let payload := Json.mkObj [
        ("goal_count", toJson goals.length),
        ("target", targetJson),
        ("hypotheses", Json.arr hypotheses)
      ]
      logInfo m!"ANALOGY_PROOF_STRUCTURE:{payload.compress}"
'''


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

    def proof_structure_after(
        self, problem: Problem, accepted_tactics: tuple[str, ...]
    ) -> ProofStructure:
        invalid = self._validate_prefix(accepted_tactics)
        if invalid is not None:
            raise ProofViewError("cannot extract structure from an invalid tactic prefix")
        source = self._render_structure_probe(problem, accepted_tactics)
        run = self._run_lean(source)
        if run.timed_out or run.internal_error or run.exit_code != 0:
            detail = "; ".join(item.message for item in run.diagnostics)
            raise ProofViewError(f"Lean structure extraction failed: {detail}")
        payloads = []
        for diagnostic in run.diagnostics:
            marker = "ANALOGY_PROOF_STRUCTURE:"
            if marker in diagnostic.message:
                payloads.append(diagnostic.message.split(marker, 1)[1].strip())
        if len(payloads) != 1:
            raise ProofViewError("Lean emitted an invalid number of proof structures")
        try:
            payload = json.loads(payloads[0])
            return ProofStructure(
                int(payload["goal_count"]),
                json.dumps(payload["target"], sort_keys=True, separators=(",", ":")),
                tuple(
                    json.dumps(item, sort_keys=True, separators=(",", ":"))
                    for item in payload["hypotheses"]
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProofViewError("Lean proof structure payload is malformed") from error

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

    @staticmethod
    def _render_structure_probe(
        problem: Problem, tactics: tuple[str, ...]
    ) -> str:
        lines = [_LEAN_STRUCTURE_PRELUDE, problem.theorem_header()]
        lines.extend(f"  {tactic}" for tactic in tactics)
        lines.extend(("  trace_proof_structure", "  all_goals sorry"))
        return "\n".join(lines) + "\n"

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

    def proof_structure_for(self, state: SymbolicState) -> ProofStructure:
        """Extract the name-insensitive Lean shape for a registered search state."""
        try:
            prefix = self._prefixes[state.id]
        except KeyError as error:
            raise ValueError(f"unknown Lean search state: {state.id}") from error
        extractor = getattr(self.verifier, "proof_structure_after", None)
        if extractor is None:
            raise ProofViewError("configured verifier cannot extract proof structure")
        return extractor(self.problem, prefix)

    def local_inventory_for(self, state: SymbolicState) -> LocalProofInventory:
        """Build exact local grounding from one structure extraction."""
        from ..proof_inventory import build_local_inventory

        return build_local_inventory(
            state, self.proof_structure_for(state), fixed_target=self.problem.target,
        )

    def register_replay_state(self, state: SymbolicState) -> SymbolicState:
        """Register a separately reconstructed, verified state with this adapter."""
        if not isinstance(state, SymbolicState):
            raise ValueError("replay state must be a SymbolicState")
        self._prefixes[state.id] = tuple(state.context)
        return state

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
        if result.status in (VerificationStatus.TIMEOUT, VerificationStatus.INTERNAL_ERROR):
            return LeanInfrastructureResult(candidate, TacticStatus.ERROR,
                diagnostics=diagnostics or (result.status.value,), verification_status=result.status)
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
