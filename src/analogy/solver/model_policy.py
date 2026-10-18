"""Model-backed proof-state encoding and tactic ranking."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..config import AppConfig
from ..model.model_provider import TextGenerator
from ..model.parser import (
    candidate_rejection_reason,
    FilteredCandidatePool,
    IncompleteCandidatePool,
    ModelOutputError,
    normalize_lean4_tactic,
    parse_tactic_candidates,
)
from ..model.prompt_runner import PromptRunner
from ..model.prompts import (
    focused_lean_goal,
    lean_tactic_card,
    prompt_catalog,
    render_lean_goal_for_model,
)
from ..utils.records import CandidateOrigin, Message, TacticCandidate
from .analogy.state import (
    AnalogyScratchpad,
    AnalogyState,
    AnalogyStateError,
    extract_analogy_block,
)

if TYPE_CHECKING:
    from .episode import SearchSession, TacticBatch
    from .proof.adapter import LeanGymAdapter

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
        failed_tactics_for: Any,
        trace_sink: Any,
        analogy_scratchpad: AnalogyScratchpad | None = None,
    ) -> None:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        self._generator = generator
        self._top_k = top_k
        self._seed = seed
        self._failed_tactics_for = failed_tactics_for
        self._trace_sink = trace_sink
        self._analogy_scratchpad = analogy_scratchpad

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
        use_analogy_protocol = (
            self._analogy_scratchpad is not None
            and not self._analogy_scratchpad.pending_rebuild
        )
        base_messages = prompt_catalog().messages(
            (
                "analogy_conventional_ranker"
                if use_analogy_protocol
                else "conventional_ranker"
            ),
            overall_target=render_lean_goal_for_model(goal.expression),
            current_goals=render_lean_goal_for_model(focused_lean_goal(state.expression)),
            accepted=accepted,
            failed=failed,
            tactic_card=lean_tactic_card(state.expression),
            top_k=self._top_k,
        )
        messages = self._scratchpad_messages(base_messages, use_analogy_protocol)
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
                parser=lambda raw, current=blocked, removed=batch_discarded: self._parse_output(
                    raw,
                    proof_state=state.expression,
                    excluded_tactics=current,
                    discarded_candidates=removed,
                    use_analogy_protocol=use_analogy_protocol,
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
                active_messages = _replacement_repair(
                    self._scratchpad_messages(base_messages, use_analogy_protocol),
                    batch_discarded,
                    require_analogy=use_analogy_protocol,
                )
                continue
            error = run.error or RuntimeError("generation cancelled")
            if isinstance(error, (IncompleteCandidatePool, FilteredCandidatePool)) and index < 2:
                active_messages = _replacement_repair(
                    self._scratchpad_messages(base_messages, use_analogy_protocol),
                    batch_discarded or [str(error)],
                    require_analogy=use_analogy_protocol,
                )
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
        raise TacticRankingError(f"{type(error).__name__}: {error}") from error

    def _scratchpad_messages(
        self,
        messages: tuple[Message, ...],
        use_analogy_protocol: bool = True,
    ) -> tuple[Message, ...]:
        scratchpad = self._analogy_scratchpad
        if scratchpad is None or not use_analogy_protocol:
            return messages
        user = messages[1]
        if scratchpad.history:
            user = Message(
                user.role,
                user.content + "\n\n" + prompt_catalog().text(
                    "proof_search_history",
                    history="\n".join(scratchpad.history),
                ),
            )
        if scratchpad.current is None:
            return messages[0], user
        return messages[0], Message("system", scratchpad.current.render()), user

    def _parse_output(
        self,
        raw: str,
        *,
        proof_state: str,
        excluded_tactics: set[str] | frozenset[str],
        discarded_candidates: list[str],
        use_analogy_protocol: bool = True,
    ) -> tuple[TacticCandidate, ...]:
        scratchpad = self._analogy_scratchpad
        analogy: AnalogyState | None = None
        payload = raw
        if scratchpad is not None and use_analogy_protocol:
            try:
                analogy, payload = extract_analogy_block(raw)
            except AnalogyStateError as error:
                raise ModelOutputError(str(error)) from error
            if scratchpad.current is not None and analogy != scratchpad.current:
                raise ModelOutputError(
                    "analogy block must remain unchanged during ordinary search"
                )
        candidates = parse_tactic_candidates(
            payload,
            self._top_k,
            discarded_candidates=discarded_candidates,
            proof_state=proof_state,
            excluded_tactics=excluded_tactics,
        )
        if scratchpad is not None and use_analogy_protocol:
            if scratchpad.current is None:
                assert analogy is not None
                try:
                    scratchpad.initialize(analogy)
                except AnalogyStateError as error:
                    raise ModelOutputError(str(error)) from error
            scratchpad.record_history(payload)
        elif scratchpad is not None:
            scratchpad.record_history(payload)
        return candidates


def generate_tactics_with_model_policy(search: SearchSession) -> TacticBatch:
    """Generate the next ordinary batch for the active search node."""

    if search.pending_batch is not None:
        return search.next_tactic_batch()
    if search.node is None:
        raise RuntimeError("search has no active node")
    if search.candidate_pool is None:
        tried = frozenset(search.tried_tactics)
        try:
            ranked = search.context.tactic_ranker.rank(
                search.node.state,
                search.goal,
                excluded_tactics=tried,
            )
        except TypeError as error:
            if "excluded_tactics" not in str(error):
                raise
            ranked = search.context.tactic_ranker.rank(
                search.node.state,
                search.goal,
            )
        seen = set(tried)
        candidates = []
        for candidate in ranked:
            if candidate.origin is not CandidateOrigin.ORDINARY:
                raise ValueError(
                    "conventional tactic policy must return ordinary candidates"
                )
            normalized = normalize_lean4_tactic(
                candidate.source, search.node.state.expression
            )
            if candidate_rejection_reason(
                normalized,
                search.node.state.expression,
                excluded_tactics=tried,
            ) is not None:
                continue
            candidate = replace(candidate, source=normalized)
            key = search.context.tactic_canonicalizer.canonicalize(normalized)
            if key not in seen:
                seen.add(key)
                candidates.append(candidate)
        search.begin_candidate_pool(tuple(candidates))
    return search.next_tactic_batch()


def initialize_model_policy(
    config: AppConfig,
    generator: TextGenerator,
    proof: LeanGymAdapter,
    model_events: list[dict[str, Any]],
    *,
    analogy_scratchpad: AnalogyScratchpad | None = None,
) -> TacticRanker:
    """Create the ordinary tactic policy used by one problem search."""

    return TacticRanker(
        generator,
        top_k=config.search.top_k,
        seed=config.run.seed,
        failed_tactics_for=proof.failed_tactics_for,
        trace_sink=model_events.append,
        analogy_scratchpad=analogy_scratchpad,
    )


def _replacement_repair(
    messages, discarded: list[str], *, require_analogy: bool = False
):
    catalog = prompt_catalog()
    contract = catalog.text(
        "ranker_analogy_output_contract"
        if require_analogy
        else "ranker_json_output_contract"
    )
    return messages + (
        Message(
            "user",
            catalog.text(
                "ranker_replacement_repair",
                discarded="\n- ".join(discarded),
                contract=contract,
            ),
        ),
    )


__all__ = [
    "TacticRanker",
    "TacticRankingError",
    "generate_tactics_with_model_policy",
    "initialize_model_policy",
]
