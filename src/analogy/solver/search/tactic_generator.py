"""Model-backed tactic generation for proof search."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ...config import AppConfig
from ...model.model_provider import CompletionScore, TextGenerator
from ...model.parser import (
    candidate_rejection_reason,
    FilteredCandidatePool,
    IncompleteCandidatePool,
    ModelOutputError,
    normalize_lean4_tactic,
)
from ...model.prompt_runner import PromptRunner
from ...model.prompts import (
    configure_prompt_catalog,
    focused_lean_goal,
    lean_tactic_card,
    prompt_catalog,
    render_lean_goal_for_model,
)
from ...utils.records import CandidateOrigin, Message, TacticCandidate
from ..analogy.protocol import (
    extract_working_model_block,
    record_model_history,
    render_working_model,
)
from ..analogy.state import (
    AnalogyScratchpad,
    AnalogyState,
    AnalogyStateError,
)
from ..proof.syntax import (
    InventoryError,
    eager_conjunction_tactic,
    matching_hypothesis_tactic,
    proof_goals,
    proposition_root,
)
from ..proof.state import proof_state_fingerprint
from ..scoring import parse_scored_tactic_candidates

if TYPE_CHECKING:
    from ..proof.adapter import LeanGymAdapter
    from .session import SearchSession, TacticBatch


class TacticGenerationError(RuntimeError):
    """Raised when the conventional model cannot produce a candidate pool."""


class TacticGenerator:
    """Generate a normalized conventional tactic pool for one proof state."""

    def __init__(
        self,
        generator: TextGenerator,
        *,
        top_k: int,
        seed: int,
        samples_per_state: int = 1,
        failed_tactics_for: Any,
        trace_sink: Any,
        analogy_scratchpad: AnalogyScratchpad | None = None,
    ) -> None:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        self._generator = generator
        self._top_k = top_k
        self._seed = seed
        if (
            isinstance(samples_per_state, bool)
            or not isinstance(samples_per_state, int)
            or samples_per_state <= 0
        ):
            raise ValueError("samples_per_state must be a positive integer")
        self._samples_per_state = samples_per_state
        self._failed_tactics_for = failed_tactics_for
        self._trace_sink = trace_sink
        self._analogy_scratchpad = analogy_scratchpad

    @property
    def analogy_scratchpad(self) -> AnalogyScratchpad | None:
        """Return the externally owned analogy state bound to this generator."""

        return self._analogy_scratchpad

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
            and self._analogy_scratchpad.current is not None
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
        accepted_order: dict[str, tuple[int, int]] = {}
        dispositions: list[dict[str, Any]] = []
        attempts = []
        attempt_messages = []
        error: Exception | None = None
        active_messages = messages
        max_calls = max(3, self._samples_per_state)
        seen_batch_signatures: set[tuple[str, ...]] = set()
        for index in range(max_calls):
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
                seed=self._sample_seed(state.expression, index),
                max_attempts=1,
                initial_repair=index > 0,
                schema_errors=(ModelOutputError,),
            )
            attempts.extend(run.attempts)
            attempt_messages.extend([active_messages] * len(run.attempts))
            discarded_candidates.extend(batch_discarded)
            for removed in batch_discarded:
                tactic, _, reason = removed.partition(": ")
                dispositions.append({
                    "tactic": tactic,
                    "sample": index,
                    "disposition": (
                        "duplicate"
                        if tactic in accepted or reason == "duplicate proposal"
                        else "incomplete"
                        if reason == "incomplete tactic"
                        else "excluded"
                        if "already tried" in reason
                        else "filtered"
                    ),
                    "reason": reason,
                })
            batch_signature = tuple(sorted({
                *(candidate.source for candidate in (run.value or ())),
                *(removed.partition(": ")[0] for removed in batch_discarded),
            }))
            if batch_signature and batch_signature in seen_batch_signatures:
                dispositions.append({
                    "sample": index,
                    "disposition": "repeated_batch",
                    "tactics": batch_signature,
                    "reason": "replacement generation repeated an earlier normalized tactic list",
                })
                error = run.error or FilteredCandidatePool(
                    "replacement generation repeated an earlier normalized tactic list"
                )
                break
            if batch_signature:
                seen_batch_signatures.add(batch_signature)
            if run.value is not None:
                for position, candidate in enumerate(run.value):
                    if candidate.source in accepted:
                        dispositions.append({
                            "tactic": candidate.source,
                            "sample": index,
                            "position": position,
                            "disposition": "duplicate",
                        })
                        continue
                    accepted[candidate.source] = candidate
                    accepted_order[candidate.source] = (index, position)
                error = None
                if not batch_discarded:
                    break
                if index + 1 >= max_calls:
                    break
                active_messages = (
                    _replacement_repair(
                        self._scratchpad_messages(base_messages, use_analogy_protocol),
                        batch_discarded,
                        require_analogy=use_analogy_protocol,
                    )
                    if batch_discarded
                    else messages
                )
                continue
            error = run.error or RuntimeError("generation cancelled")
            if isinstance(error, ModelOutputError) and index + 1 < max_calls:
                if not isinstance(
                    error,
                    (IncompleteCandidatePool, FilteredCandidatePool),
                ):
                    dispositions.append({
                        "sample": index,
                        "disposition": "sample_error",
                        "reason": f"{type(error).__name__}: {error}",
                    })
                active_messages = _replacement_repair(
                    self._scratchpad_messages(base_messages, use_analogy_protocol),
                    batch_discarded or [str(error)],
                    require_analogy=use_analogy_protocol,
                )
                continue
            if index + 1 < self._samples_per_state:
                dispositions.append({
                    "sample": index,
                    "disposition": "sample_error",
                    "reason": f"{type(error).__name__}: {error}",
                })
                active_messages = messages
                continue
            break

        likelihoods: list[dict[str, Any]] = []
        if accepted:
            ranked = self._rank_by_likelihood(
                messages,
                tuple(accepted.values()),
                accepted_order,
                use_analogy_protocol=use_analogy_protocol,
                trace=likelihoods,
            )
            value = ranked[: self._top_k]
            returned = {item.source for item in value}
            for item in ranked:
                disposition = "returned" if item.source in returned else "below_top_k"
                dispositions.append({
                    "tactic": item.source,
                    "disposition": disposition,
                    "rank": item.model_rank,
                })
                if disposition == "below_top_k":
                    discarded_candidates.append(
                        f"{item.source}: below top_k after likelihood ranking"
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
                "candidate_likelihoods": likelihoods,
                "candidate_dispositions": dispositions,
                "error": None if error is None else f"{type(error).__name__}: {error}",
            }
        )
        if value is not None:
            return value
        raise TacticGenerationError(f"{type(error).__name__}: {error}") from error

    def deterministic_candidate(self, state: Any) -> TacticCandidate | None:
        """Return a safe model-free move for an immediately recognizable goal."""

        source = (
            matching_hypothesis_tactic(state.expression)
            or eager_conjunction_tactic(state.expression, tuple(state.context))
            or _safe_goal_shape_tactic(state.expression)
        )
        if source is None:
            return None
        return TacticCandidate(source, 1.0, CandidateOrigin.ORDINARY)

    def _sample_seed(self, proof_state: str, index: int) -> int:
        if self._samples_per_state == 1:
            return self._seed + index
        payload = f"{self._seed}:{proof_state_fingerprint(proof_state)}:{index}"
        return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:8], "big")

    def _rank_by_likelihood(
        self,
        messages: tuple[Message, ...],
        candidates: tuple[TacticCandidate, ...],
        first_seen: dict[str, tuple[int, int]],
        *,
        use_analogy_protocol: bool,
        trace: list[dict[str, Any]],
    ) -> tuple[TacticCandidate, ...]:
        scorer = getattr(self._generator, "score_completions", None)
        if scorer is None:
            raise TacticGenerationError(
                "model provider does not support completion likelihood scoring"
            )
        completions = []
        for candidate in candidates:
            payload = json.dumps(
                [{"tactic": candidate.source}],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if use_analogy_protocol and self._analogy_scratchpad is not None:
                assert self._analogy_scratchpad.current is not None
                payload = render_working_model(self._analogy_scratchpad.current) + "\n" + payload
            completions.append(payload)
        try:
            scored = tuple(scorer(messages, tuple(completions)))
        except Exception as error:
            raise TacticGenerationError(
                f"completion likelihood scoring failed: {type(error).__name__}: {error}"
            ) from error
        if len(scored) != len(candidates):
            raise TacticGenerationError("completion scorer returned the wrong number of scores")
        if any(not isinstance(item, CompletionScore) for item in scored) or any(
            not math.isfinite(item.total_log_likelihood)
            or not math.isfinite(item.mean_log_likelihood)
            or item.token_count <= 0
            for item in scored
        ):
            raise TacticGenerationError("completion scorer returned an invalid score")
        rows = list(zip(candidates, scored, strict=True))
        rows.sort(
            key=lambda row: (
                -row[1].mean_log_likelihood,
                *first_seen[row[0].source],
                row[0].source,
            )
        )
        maximum = max(row[1].mean_log_likelihood for row in rows)
        weights = [math.exp(row[1].mean_log_likelihood - maximum) for row in rows]
        total = sum(weights)
        ranked = []
        for rank, ((candidate, score), weight) in enumerate(zip(rows, weights, strict=True), start=1):
            ranked.append(
                TacticCandidate(
                    candidate.source,
                    weight / total,
                    candidate.origin,
                    rank,
                )
            )
            trace.append({
                "tactic": candidate.source,
                "completion": score.text,
                "total_log_likelihood": score.total_log_likelihood,
                "mean_log_likelihood": score.mean_log_likelihood,
                "token_count": score.token_count,
                "rank": rank,
            })
        return tuple(ranked)

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
        return messages[0], Message(
            "system", render_working_model(scratchpad.current)
        ), user

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
                analogy, payload = extract_working_model_block(raw)
            except AnalogyStateError as error:
                raise ModelOutputError(str(error)) from error
            if scratchpad.current is not None and analogy != scratchpad.current:
                raise ModelOutputError(
                    "analogy block must remain unchanged during ordinary search"
                )
        candidates = parse_scored_tactic_candidates(
            payload,
            None,
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
            record_model_history(scratchpad, payload)
        elif scratchpad is not None:
            record_model_history(scratchpad, payload)
        return candidates


def generate_and_score_tactics(
    search: SearchSession,
) -> TacticBatch:
    """Generate and score the next ordinary batch for the active search node."""

    if search.pending_batch is not None:
        return search.next_tactic_batch()
    if search.node is None:
        raise RuntimeError("search has no active node")
    if search.candidate_pool is None:
        tried = frozenset(search.tried_tactics)
        try:
            goal = proof_goals(search.node.state.expression)[0]
            root = proposition_root(goal.target)
        except (InventoryError, IndexError):
            goal = None
            root = None
        deterministic_policy = getattr(
            search.context.tactic_ranker,
            "deterministic_candidate",
            None,
        )
        deterministic_candidate = (
            deterministic_policy(search.node.state)
            if callable(deterministic_policy)
            else None
        )
        if deterministic_candidate is not None:
            canonical = search.context.tactic_canonicalizer.canonicalize(
                deterministic_candidate.source
            )
            if canonical not in tried:
                search.begin_candidate_pool((deterministic_candidate,))
                return search.next_tactic_batch()
        if goal is not None and root is not None and (
            root.operator in {"→", "¬"}
            or goal.target.strip().startswith(("∀", "forall "))
        ):
            occupied = {name for name, _ in goal.declarations}
            name = "h"
            suffix = 2
            while name in occupied:
                name = f"h{suffix}"
                suffix += 1
            deterministic = f"intro {name}"
            if deterministic not in tried:
                search.begin_candidate_pool((
                    TacticCandidate(deterministic, 1.0, CandidateOrigin.ORDINARY),
                ))
                return search.next_tactic_batch()
        try:
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
        except TacticGenerationError:
            ranked = ()
        seen = set(tried)
        candidates = []
        for candidate in ranked:
            if candidate.origin is not CandidateOrigin.ORDINARY:
                raise ValueError(
                    "conventional tactic generator must return ordinary candidates"
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


def _safe_goal_shape_tactic(expression: str) -> str | None:
    """Return a safe goal-shape move that does not need model judgment."""

    try:
        goal = proof_goals(expression)[0]
        root = proposition_root(goal.target)
    except (InventoryError, IndexError):
        return None
    target = " ".join(goal.target.split())
    if target == "True":
        return "exact True.intro"
    if root.operator in {"∧", "↔"}:
        return "constructor"
    if root.operator == "∨" and root.parts is not None:
        left, right = (" ".join(item.split()) for item in root.parts)
        if left == right:
            return "left"
    return None


def initialize_tactic_generator(
    config: AppConfig,
    generator: TextGenerator,
    proof: LeanGymAdapter,
    model_events: list[dict[str, Any]],
    *,
    analogy_scratchpad: AnalogyScratchpad | None = None,
) -> TacticGenerator:
    """Create the model-backed tactic generator used by one problem search."""

    return TacticGenerator(
        generator,
        top_k=config.search.top_k,
        seed=config.run.seed,
        samples_per_state=config.search.samples_per_state,
        failed_tactics_for=proof.failed_tactics_for,
        trace_sink=model_events.append,
        analogy_scratchpad=analogy_scratchpad,
    )


def initialize_model_protocol(config: AppConfig) -> None:
    """Validate and activate model-facing prompt templates for one run."""

    configure_prompt_catalog(config.prompts.path)


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
    "TacticGenerationError",
    "TacticGenerator",
    "generate_and_score_tactics",
    "initialize_model_protocol",
    "initialize_tactic_generator",
]
