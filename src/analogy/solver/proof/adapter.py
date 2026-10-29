"""Adapter between symbolic solver state and low-level Lean verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from ...lean_gym.models import (
    Problem,
    VerificationStatus,
    has_unresolved_metavariable,
)
from ...lean_gym.verifier import Verifier
from ...utils.records import SymbolicState, TacticCandidate, TacticResult, TacticStatus
from .state import canonical_proof_state

_MAX_FAILURE_FEEDBACK = 8


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
        self._known_states: set[str] = set()
        self._cache: dict[tuple[str, str], object] = {}
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
        if state_id not in self._known_states:
            return ()
        eligible = [
            f"current state — {failure.text}"
            for failure in self._failed
            if failure.state_id == state_id
        ]
        return tuple(eligible[-_MAX_FAILURE_FEEDBACK:])

    def run_tactic(
        self, state: SymbolicState, candidate: TacticCandidate
    ) -> TacticResult:
        if state.id not in self._known_states:
            raise ValueError(f"unknown Lean search state: {state.id}")
        prefix = state.context
        cache_key = (
            canonical_proof_state(state.expression),
            " ".join(candidate.source.split()),
        )
        result = self._cache.get(cache_key)
        cache_hit = result is not None
        if result is None:
            result = self.verifier.try_tactic(self.problem, prefix, candidate.source)
            if result.status not in (
                VerificationStatus.TIMEOUT,
                VerificationStatus.INTERNAL_ERROR,
            ):
                self._cache[cache_key] = result
        diagnostics = tuple(
            item.message for item in result.diagnostics if item.message.strip()
        )
        if result.status is VerificationStatus.COMPLETE and result.accepted:
            tactics = prefix + (candidate.source,)
            final = self.verifier.verify_complete(self.problem, tactics)
            final_diagnostics = tuple(
                item.message
                for item in final.diagnostics
                if item.message.strip()
            )
            if final.status in (
                VerificationStatus.TIMEOUT,
                VerificationStatus.INTERNAL_ERROR,
            ):
                return LeanInfrastructureResult(
                    candidate,
                    TacticStatus.ERROR,
                    diagnostics=final_diagnostics or (final.status.value,),
                    verification_status=final.status,
                )
            if final.status is not VerificationStatus.COMPLETE or not final.accepted:
                diagnostic = next(
                    (item for item in final_diagnostics if item.strip()),
                    final.status.value,
                )
                self._record_failure(state, candidate.source, diagnostic)
                return TacticResult(
                    candidate,
                    TacticStatus.ERROR,
                    diagnostics=final_diagnostics or (final.status.value,),
                    cache_hit=cache_hit,
                )
            lines = [self.problem.theorem_header()]
            lines.extend(f"  {tactic}" for tactic in tactics)
            return TacticResult(
                candidate,
                TacticStatus.COMPLETE,
                proof="\n".join(lines) + "\n",
                diagnostics=diagnostics,
                cache_hit=cache_hit,
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
                    cache_hit=cache_hit,
                )
            successor = self._register(
                result.proof_state.normalized, prefix + (candidate.source,)
            )
            return TacticResult(
                candidate,
                TacticStatus.ACCEPTED,
                state=successor,
                diagnostics=diagnostics,
                cache_hit=cache_hit,
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
            next((item for item in diagnostics if item.strip()), result.status.value),
        )
        return TacticResult(
            candidate,
            TacticStatus.ERROR,
            diagnostics=diagnostics or (result.status.value,),
            cache_hit=cache_hit,
        )

    def _record_failure(
        self, state: SymbolicState, tactic: str, diagnostic: str
    ) -> None:
        tactic_summary = " ".join(tactic.split())
        text = f"{tactic_summary}: {diagnostic.strip()}"
        self._failed.append(
            _FailureFeedback(state.id, state.context, text)
        )

    def _register(self, proof_state: str, prefix: tuple[str, ...]) -> SymbolicState:
        digest = hashlib.sha256(
            canonical_proof_state(proof_state).encode("utf-8")
        ).hexdigest()[:16]
        state_id = f"{self.problem.id}:{digest}"
        self._known_states.add(state_id)
        return SymbolicState(state_id, proof_state, "lean4", prefix)


class TacticSubmitter(Protocol):
    def run_tactic(
        self, state: SymbolicState, candidate: TacticCandidate
    ) -> TacticResult: ...


def initialize_proof(problem: Problem, verifier: Verifier) -> LeanGymAdapter:
    """Create the solver-facing adapter for one Lean problem."""

    return LeanGymAdapter(problem, verifier)

__all__ = [
    "LeanGymAdapter",
    "LeanInfrastructureResult",
    "TacticSubmitter",
    "initialize_proof",
]
