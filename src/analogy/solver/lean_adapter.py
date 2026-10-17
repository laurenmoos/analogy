"""Adapter between symbolic solver state and low-level Lean verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..lean_gym.models import Problem, VerificationStatus
from ..lean_gym.verifier import Verifier
from ..records import SymbolicState, TacticCandidate, TacticResult, TacticStatus
from .proof_views import ProofStructure, ProofViewError

if TYPE_CHECKING:
    from .proof_inventory import LocalProofInventory


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
        try:
            prefix = self._prefixes[state.id]
        except KeyError as error:
            raise ValueError(f"unknown Lean search state: {state.id}") from error
        extractor = getattr(self.verifier, "proof_structure_after", None)
        if extractor is None:
            raise ProofViewError("configured verifier cannot extract proof structure")
        return extractor(self.problem, prefix)

    def local_inventory_for(self, state: SymbolicState) -> LocalProofInventory:
        from .proof_inventory import build_local_inventory

        return build_local_inventory(
            state, self.proof_structure_for(state), fixed_target=self.problem.target
        )

    def register_replay_state(self, state: SymbolicState) -> SymbolicState:
        if not isinstance(state, SymbolicState):
            raise ValueError("replay state must be a SymbolicState")
        self._prefixes[state.id] = tuple(state.context)
        return state

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
        self._failed.setdefault(state.id, []).append(candidate.source)
        return TacticResult(
            candidate,
            TacticStatus.ERROR,
            diagnostics=diagnostics or (result.status.value,),
        )

    def _register(self, proof_state: str, prefix: tuple[str, ...]) -> SymbolicState:
        digest = hashlib.sha256(proof_state.encode("utf-8")).hexdigest()[:16]
        state_id = f"{self.problem.id}:{digest}"
        self._prefixes.setdefault(state_id, prefix)
        return SymbolicState(state_id, proof_state, "lean4", prefix)


__all__ = ["LeanGymAdapter", "LeanInfrastructureResult"]
