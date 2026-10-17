"""Model-backed proof-state encoding and tactic ranking."""

from __future__ import annotations

from typing import Any

from ..inference.model_provider import TextGenerator
from ..inference.parser import parse_tactic_candidates
from ..inference.prompt_runner import PromptRunner
from ..inference.prompts import prompt_catalog, render_lean_goal_for_model
from ..records import TacticCandidate

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
    ) -> tuple[TacticCandidate, ...]:
        accepted = "\n".join(state.context) if state.context else "(none)"
        failed_values = tuple(self._failed_tactics_for(state.id))
        failed = "\n".join(failed_values) if failed_values else "(none)"
        messages = prompt_catalog().messages(
            "conventional_ranker",
            overall_target=render_lean_goal_for_model(goal.expression),
            current_goals=render_lean_goal_for_model(state.expression),
            accepted=accepted,
            failed=failed,
            top_k=self._top_k,
        )
        run = PromptRunner(self._generator).run(
            messages,
            parser=lambda raw: parse_tactic_candidates(raw, self._top_k),
            seed=self._seed,
        )
        error = run.error or RuntimeError("generation cancelled") if run.value is None else None
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
                        }
                        for item in run.attempts
                    ],
                    "normalized_candidates": [
                        item.source for item in (run.value or ())
                    ],
                    "error": None if error is None else f"{type(error).__name__}: {error}",
                }
            )
        if run.value is not None:
            return run.value
        if self._tolerate_failure:
            return ()
        raise TacticRankingError(f"{type(error).__name__}: {error}") from error


__all__ = ["TacticRanker", "TacticRankingError"]
