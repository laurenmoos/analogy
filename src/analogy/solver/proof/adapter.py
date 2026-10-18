"""Adapter between symbolic solver state and low-level Lean verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ...lean_gym.models import (
    Problem,
    VerificationStatus,
    has_unresolved_metavariable,
)
from ...lean_gym.verifier import Verifier
from ...utils.records import SymbolicState, TacticCandidate, TacticResult, TacticStatus

_MAX_FAILURE_FEEDBACK = 8
_MAX_FAILURE_ENTRY_CHARS = 120


@dataclass(frozen=True, slots=True)
class _FailureFeedback:
    state_id: str
    prefix: tuple[str, ...]
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LeanInfrastructureResult(TacticResult):
    """Keep the ERROR contract while retaining a non-proof verifier failure."""

    verification_status: VerificationStatus

    def __post_init__(self) -> None:
        TacticResult.__post_init__(self)
        if (
            self.status is not TacticStatus.ERROR
            or not isinstance(self.verification_status, VerificationStatus)
            or self.verification_status
            not in (VerificationStatus.TIMEOUT, VerificationStatus.INTERNAL_ERROR)
        ):
            raise ValueError(
                "infrastructure results require ERROR and a typed verifier failure"
            )

    @property
    def infrastructure_failure(self) -> str:
        return self.verification_status.value


class LeanGymAdapter:
    """Branch-safe adapter from symbolic search states to Lean proof prefixes."""

    def __init__(self, problem: Problem, verifier: Verifier) -> None:
        self.problem = problem
        self.verifier = verifier
        self._prefixes: dict[str, tuple[str, ...]] = {}
        self._failed: list[_FailureFeedback] = []

    def initial_state(self) -> SymbolicState:
        result = self.verifier.initial_state(self.problem)
        if result.status is not VerificationStatus.OPEN or result.proof_state is None:
            detail = "; ".join(item.message for item in result.diagnostics)
            raise RuntimeError(f"could not initialize {self.problem.id}: {detail}")
        return self._register(result.proof_state.normalized, ())

    def goal_state(self) -> SymbolicState:
        return SymbolicState(
            f"{self.problem.id}:goal", self.problem.target, "lean4"
        )

    def failed_tactics_for(self, state_id: str) -> tuple[str, ...]:
        try:
            prefix = self._prefixes[state_id]
        except KeyError:
            return ()
        eligible: list[str] = []
        for failure in self._failed:
            if failure.state_id == state_id:
                scope = "current"
            elif (
                len(failure.prefix) <= len(prefix)
                and prefix[: len(failure.prefix)] == failure.prefix
            ):
                scope = "ancestor"
            else:
                continue
            eligible.append(f"{scope} state — {failure.text}")
        return tuple(eligible[-_MAX_FAILURE_FEEDBACK:])

    def run_tactic(
        self, state: SymbolicState, candidate: TacticCandidate
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
            if has_unresolved_metavariable(result.proof_state.normalized):
                diagnostic = "unresolved metavariables"
                self._record_failure(state, candidate.source, diagnostic)
                return TacticResult(
                    candidate,
                    TacticStatus.ERROR,
                    diagnostics=diagnostics + (diagnostic,),
                )
            successor = self._register(
                result.proof_state.normalized, prefix + (candidate.source,)
            )
            return TacticResult(
                candidate,
                TacticStatus.ACCEPTED,
                state=successor,
                diagnostics=diagnostics,
            )
        if result.status in (
            VerificationStatus.TIMEOUT,
            VerificationStatus.INTERNAL_ERROR,
        ):
            return LeanInfrastructureResult(
                candidate,
                TacticStatus.ERROR,
                diagnostics=diagnostics or (result.status.value,),
                verification_status=result.status,
            )
        self._record_failure(
            state,
            candidate.source,
            next((item.splitlines()[0] for item in diagnostics if item.strip()), result.status.value),
        )
        return TacticResult(
            candidate,
            TacticStatus.ERROR,
            diagnostics=diagnostics or (result.status.value,),
        )

    def _record_failure(
        self, state: SymbolicState, tactic: str, diagnostic: str
    ) -> None:
        tactic_summary = " ".join(tactic.split())[:48]
        diagnostic_summary = " ".join(diagnostic.split())[:48]
        text = f"{tactic_summary}: {diagnostic_summary}"[:_MAX_FAILURE_ENTRY_CHARS]
        self._failed.append(
            _FailureFeedback(state.id, self._prefixes[state.id], text)
        )

    def _register(self, proof_state: str, prefix: tuple[str, ...]) -> SymbolicState:
        digest = hashlib.sha256(proof_state.encode("utf-8")).hexdigest()[:16]
        state_id = f"{self.problem.id}:{digest}"
        self._prefixes.setdefault(state_id, prefix)
        return SymbolicState(state_id, proof_state, "lean4", prefix)


def initialize_proof(problem: Problem, verifier: Verifier) -> LeanGymAdapter:
    """Create the solver-facing adapter for one Lean problem."""

    return LeanGymAdapter(problem, verifier)

__all__ = [
    "LeanGymAdapter",
    "LeanInfrastructureResult",
    "initialize_proof",
]
