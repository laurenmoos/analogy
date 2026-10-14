from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from .models import Problem, ProofState, TraceEvent, VerificationResult, VerificationStatus
from .traces import TraceWriter, make_event
from .verifier import Verifier


class SessionError(RuntimeError):
    pass


class SessionInitializationError(SessionError):
    pass


class SessionCommandError(SessionError):
    pass


class SessionClosedError(SessionError):
    pass


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    problem: Problem
    accepted_tactics: tuple[str, ...]
    proof_state: ProofState | None
    complete: bool


def render_proof(problem: Problem, tactics: tuple[str, ...]) -> str:
    lines = [problem.theorem_header()]
    lines.extend(f"  {tactic}" for tactic in tactics)
    return "\n".join(lines) + "\n"


class ProofSession:
    def __init__(
        self,
        problem: Problem,
        verifier: Verifier,
        trace_writer: TraceWriter,
        session_id: str,
        proof_state: ProofState,
    ) -> None:
        self.problem = problem
        self.verifier = verifier
        self.trace_writer = trace_writer
        self.session_id = session_id
        self._accepted_tactics: tuple[str, ...] = ()
        self._proof_state: ProofState | None = proof_state
        self._complete = False
        self._closed = False
        self._events: list[TraceEvent] = []

    @classmethod
    def start(
        cls,
        problem: Problem,
        verifier: Verifier,
        trace_writer: TraceWriter,
        session_id: str | None = None,
    ) -> ProofSession:
        initial = verifier.initial_state(problem)
        if initial.status is not VerificationStatus.OPEN or initial.proof_state is None:
            trace_writer.close()
            messages = "; ".join(item.message for item in initial.diagnostics)
            raise SessionInitializationError(f"could not initialize {problem.id}: {messages}")
        instance = cls(problem, verifier, trace_writer, session_id or str(uuid.uuid4()), initial.proof_state)
        instance._emit(
            "session_start",
            {"state_after": initial.proof_state.text, "accepted_prefix": [], "complete": False},
        )
        return instance

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def snapshot(self) -> SessionSnapshot:
        return SessionSnapshot(self.problem, self._accepted_tactics, self._proof_state, self._complete)

    def submit_tactic(self, tactic: str) -> VerificationResult:
        self._ensure_open()
        if self._complete:
            raise SessionCommandError("proof is complete; undo or reset before submitting another tactic")
        before = self._proof_state.text if self._proof_state else None
        result = self.verifier.try_tactic(self.problem, self._accepted_tactics, tactic)
        if result.accepted and result.status in {VerificationStatus.OPEN, VerificationStatus.COMPLETE}:
            self._accepted_tactics += (tactic,)
            self._proof_state = result.proof_state
            self._complete = result.status is VerificationStatus.COMPLETE
        self._emit(
            "tactic_attempt",
            {
                "tactic": tactic,
                "accepted": result.accepted,
                "status": result.status.value,
                "state_before": before,
                "state_after": self._proof_state.text if self._proof_state else None,
                "accepted_prefix": list(self._accepted_tactics),
                "lean_exit_code": result.exit_code,
                "diagnostics": [item.to_dict() for item in result.diagnostics],
                "duration_ms": result.duration_ms,
            },
        )
        if result.status is VerificationStatus.COMPLETE and result.accepted:
            self._emit("completion", {"accepted_prefix": list(self._accepted_tactics), "tactic_count": len(self._accepted_tactics)})
        return result

    def undo(self) -> SessionSnapshot:
        self._ensure_open()
        if not self._accepted_tactics:
            raise SessionCommandError("nothing to undo")
        candidate = self._accepted_tactics[:-1]
        result = self.verifier.state_after(self.problem, candidate)
        if result.status is not VerificationStatus.OPEN or result.proof_state is None:
            raise SessionCommandError("Lean could not reconstruct the previous state")
        removed = self._accepted_tactics[-1]
        self._accepted_tactics = candidate
        self._proof_state = result.proof_state
        self._complete = False
        self._emit("undo", {"removed_tactic": removed, "state_after": result.proof_state.text, "accepted_prefix": list(candidate)})
        return self.snapshot()

    def reset(self) -> SessionSnapshot:
        self._ensure_open()
        result = self.verifier.initial_state(self.problem)
        if result.status is not VerificationStatus.OPEN or result.proof_state is None:
            raise SessionCommandError("Lean could not reconstruct the initial state")
        removed_count = len(self._accepted_tactics)
        self._accepted_tactics = ()
        self._proof_state = result.proof_state
        self._complete = False
        self._emit("reset", {"removed_count": removed_count, "state_after": result.proof_state.text, "accepted_prefix": []})
        return self.snapshot()

    def save(self, path: Path) -> Path:
        self._ensure_open()
        destination = Path(path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(render_proof(self.problem, self._accepted_tactics))
        self._emit("save", {"path": str(destination), "complete": self._complete})
        return destination

    def close(self, reason: str = "quit") -> None:
        if self._closed:
            return
        self._emit("session_end", {"reason": reason, "complete": self._complete})
        self.trace_writer.close()
        self._closed = True

    def _emit(self, event_type: str, payload: dict[str, object]) -> None:
        event = make_event(self.session_id, self.problem.id, len(self._events), event_type, payload)
        self.trace_writer.write(event)
        self._events.append(event)

    def _ensure_open(self) -> None:
        if self._closed:
            raise SessionClosedError("session is closed")
