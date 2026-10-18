"""Model-backed proof-state encoding and tactic ranking."""

from __future__ import annotations

from typing import Any

from ..inference.model_provider import TextGenerator
from ..inference.parser import (
    FilteredCandidatePool,
    IncompleteCandidatePool,
    ModelOutputError,
    parse_tactic_candidates,
)
from ..inference.prompt_runner import PromptRunner
from ..inference.prompts import (
    focused_lean_goal,
    lean_tactic_card,
    prompt_catalog,
    render_lean_goal_for_model,
)
from ..records import CandidateOrigin, Message, TacticCandidate

class TacticRankingError(RuntimeError):
    """Raised when the conventional model cannot produce a candidate pool."""


class TacticRanker:
    """Generate a normalized conventional tactic pool for one proof state."""

    def __init__(
        self,
        generator: TextGenerator,
        *,
        top_k: int,
        seed: int,
        failed_tactics_for: Any | None = None,
        trace_sink: Any | None = None,
        tolerate_failure: bool = False,
    ) -> None:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        self._generator = generator
        self._top_k = top_k
        self._seed = seed
        self._failed_tactics_for = failed_tactics_for or (lambda _state_id: ())
        self._trace_sink = trace_sink
        self._tolerate_failure = tolerate_failure

    def rank(
        self,
        state: Any,
        goal: Any,
        *,
        excluded_tactics: set[str] | frozenset[str] = frozenset(),
    ) -> tuple[TacticCandidate, ...]:
        accepted = "\n".join(state.context) if state.context else "(none)"
        failed_values = tuple(self._failed_tactics_for(state.id))
        failed = "\n".join(failed_values) if failed_values else "(none)"
        messages = prompt_catalog().messages(
            "conventional_ranker",
            overall_target=render_lean_goal_for_model(goal.expression),
            current_goals=render_lean_goal_for_model(focused_lean_goal(state.expression)),
            accepted=accepted,
            failed=failed,
            tactic_card=lean_tactic_card(state.expression),
            top_k=self._top_k,
        )
        discarded_candidates: list[str] = []
        accepted: dict[str, TacticCandidate] = {}
        attempts = []
        attempt_messages = []
        error: Exception | None = None
        active_messages = messages
        for index in range(3):
            batch_discarded: list[str] = []
            blocked = frozenset(excluded_tactics) | frozenset(accepted)
            run = PromptRunner(self._generator).run(
                active_messages,
                parser=lambda raw, current=blocked, removed=batch_discarded: parse_tactic_candidates(
                    raw,
                    self._top_k,
                    discarded_candidates=removed,
                    proof_state=state.expression,
                    excluded_tactics=current,
                ),
                seed=self._seed + index,
                max_attempts=1,
                initial_repair=index > 0,
                schema_errors=(ModelOutputError,),
            )
            attempts.extend(run.attempts)
            attempt_messages.extend([active_messages] * len(run.attempts))
            discarded_candidates.extend(batch_discarded)
            if run.value is not None:
                for candidate in run.value:
                    accepted[candidate.source] = candidate
                error = None
                if not batch_discarded or index == 2:
                    break
                active_messages = _replacement_repair(messages, batch_discarded)
                continue
            error = run.error or RuntimeError("generation cancelled")
            if isinstance(error, (IncompleteCandidatePool, FilteredCandidatePool)) and index < 2:
                active_messages = _replacement_repair(messages, batch_discarded or [str(error)])
                continue
            break

        candidates = tuple(accepted.values())[: self._top_k]
        if candidates:
            total = sum(item.score for item in candidates)
            value = tuple(
                TacticCandidate(item.source, item.score / total, CandidateOrigin.ORDINARY)
                for item in candidates
            )
            error = None
        else:
            value = None
        if self._trace_sink is not None:
            self._trace_sink(
                {
                    "kind": "ordinary_model_call",
                    "state_id": state.id,
                    "messages": [
                        {"role": item.role, "content": item.content}
                        for item in messages
                    ],
                    "responses": [
                        {
                            "seed": item.seed,
                            "raw_text": item.raw_text,
                            "status": item.status.value,
                            "repair": item.repair,
                            "error_kind": item.error_kind,
                            "diagnostic": item.diagnostic,
                        }
                        | {
                            "messages": [
                                {"role": message.role, "content": message.content}
                                for message in attempt_messages[position]
                            ]
                        }
                        for position, item in enumerate(attempts)
                    ],
                    "normalized_candidates": [
                        item.source for item in (value or ())
                    ],
                    "discarded_candidates": discarded_candidates,
                    "error": None if error is None else f"{type(error).__name__}: {error}",
                }
            )
        if value is not None:
            return value
        if self._tolerate_failure:
            return ()
        raise TacticRankingError(f"{type(error).__name__}: {error}") from error


def _incomplete_pool_repair(messages, raw: str, error: Exception):
    return messages + (
        Message(
            "user",
            "Every proposed tactic was incomplete. "
            f"Parser diagnostic: {error}\n"
            "Supply required operands, for example `exact h2`, `apply h1`, "
            "`rw [h1]`, or `rcases h with ⟨hH,hU⟩`. "
            "Return only the requested JSON array.",
        ),
    )


def _replacement_repair(messages, discarded: list[str]):
    return messages + (
        Message(
            "user",
            "These proposals were removed before Lean:\n- "
            + "\n- ".join(discarded)
            + "\nReturn replacement candidates only. Do not repeat any removed or "
            "already tried tactic. Supply every operand, for example `exact h2`, "
            "`apply h1`, `rw [h1]`, or `rcases h with ⟨hH,hU⟩`. "
            "Return only the requested JSON array.",
        ),
    )


__all__ = ["TacticRanker", "TacticRankingError"]
