"""Compute-matched direct deliberation for semantically recoverable stuck states."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Callable

from ..config import AppConfig
from ..model.parser import normalize_lean4_tactic
from ..model.prompt_runner import GenerationStatus
from ..model.prompts import (
    focused_lean_goal,
    lean_tactic_card,
    prompt_catalog,
    render_lean_goal_for_model,
)
from ..utils.records import (
    CandidateOrigin,
    Message,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)
from .analogy.rescue import (
    AnalogyGenerationAttempt,
    AnalogyRescueOutcome,
    RescueAttempt,
    RescueOutputError,
    RescueProposal,
    RescueRollout,
    RescueTermination,
    TacticEvaluation,
    _fallback_seed,
    _focused_goal_is_disjunction,
    _parse_tactic_only,
)
from .analogy.state import AnalogyScratchpad
from .proof.semantic import render_countermodel_table, render_semantic_guidance


def parse_deliberation_plan(text: str, proof_state: str) -> dict[str, Any]:
    """Validate one direct proof plan without accepting story-shaped context."""

    try:
        payload = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError) as error:
        raise RescueOutputError("direct plan must be strict JSON") from error
    required = {"analysis", "branches", "recommended_approach"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise RescueOutputError(
            "direct plan must contain exactly analysis, branches, and recommended_approach"
        )
    if any(
        not isinstance(payload[field], str) or not payload[field].strip()
        for field in ("analysis", "recommended_approach")
    ):
        raise RescueOutputError("direct plan text fields must be nonblank")
    branches = payload["branches"]
    if not isinstance(branches, list) or not branches:
        raise RescueOutputError("direct plan branches must be a nonempty list")
    fields = {"tactic", "resulting_obligation"}
    tactics = []
    for branch in branches:
        if not isinstance(branch, dict) or set(branch) != fields:
            raise RescueOutputError(
                "each direct branch must contain exactly tactic and resulting_obligation"
            )
        if any(
            not isinstance(branch[field], str) or not branch[field].strip()
            for field in fields
        ):
            raise RescueOutputError("direct branch fields must be nonblank")
        tactics.append(" ".join(branch["tactic"].split()))
    if _focused_goal_is_disjunction(proof_state) and not {
        "left",
        "right",
    }.issubset(tactics):
        raise RescueOutputError(
            "a disjunction plan must compare both left and right branches"
        )
    return payload


def _parse_deliberation_tactic(
    text: str,
    proof_state: str,
    *,
    attempted_tactics: set[str],
) -> RescueProposal:
    try:
        payload = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError) as error:
        raise RescueOutputError("direct tactic output must be strict JSON") from error
    if _focused_goal_is_disjunction(proof_state):
        if not isinstance(payload, dict) or set(payload) != {
            "decision",
            "tactic",
            "predicted_state",
        }:
            raise RescueOutputError(
                "direct tactic output must contain decision, tactic, and predicted_state"
            )
        decision = payload.get("decision")
        if (
            isinstance(decision, dict)
            and set(decision) == {"current_proof_situation"}
            and isinstance(decision["current_proof_situation"], dict)
            and set(decision["current_proof_situation"])
            == {"options", "selection_reason"}
        ):
            nested = decision["current_proof_situation"]
            decision = {
                "current_proof_situation": "the current focused Lean proof state",
                "options": nested["options"],
                "selection_reason": nested["selection_reason"],
            }
        if not isinstance(decision, dict) or set(decision) != {
            "current_proof_situation",
            "options",
            "selection_reason",
        }:
            raise RescueOutputError(
                "direct decision must contain current_proof_situation, options, and selection_reason"
            )
        options = decision["options"]
        if not isinstance(options, list):
            raise RescueOutputError("direct decision options must be a list")
        translated_options = []
        for option in options:
            if not isinstance(option, dict) or set(option) != {
                "tactic",
                "resulting_obligation",
            }:
                raise RescueOutputError(
                    "each direct option must contain tactic and resulting_obligation"
                )
            translated_options.append({
                "tactic": option["tactic"],
                "everyday_consequence": option["resulting_obligation"],
                "proof_consequence": option["resulting_obligation"],
            })
        payload = {
            "decision": {
                "current_everyday_state": decision["current_proof_situation"],
                "options": translated_options,
                "selection_reason": decision["selection_reason"],
            },
            "tactic": payload["tactic"],
            "predicted_state": payload["predicted_state"],
        }
    proposal = _parse_tactic_only(
        json.dumps(payload),
        proof_state,
        attempted_tactics=attempted_tactics,
    )
    return replace(
        proposal,
        candidate=replace(
            proposal.candidate, origin=CandidateOrigin.DELIBERATIVE
        ),
    )


class DeliberationFallback:
    """Spend the analogy planning-call envelope on direct proof reasoning."""

    def __init__(
        self,
        generator: Any,
        *,
        max_model_calls: int,
        seed: int,
        trace_sink: Callable[[dict[str, Any]], None] | None = None,
        initial_proposal: RescueProposal | None = None,
        initial_state_id: str | None = None,
    ) -> None:
        if isinstance(max_model_calls, bool) or not isinstance(max_model_calls, int) or max_model_calls <= 0:
            raise ValueError("max_model_calls must be a positive integer")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a non-negative integer")
        if (initial_proposal is None) != (initial_state_id is None):
            raise ValueError("initial proposal and state id must be supplied together")
        self._generator = generator
        self._max_model_calls = max_model_calls
        self._seed = seed
        self._trace_sink = trace_sink
        self._initial_proposal = initial_proposal
        self._initial_state_id = initial_state_id
        # Shared rescue plumbing requires an owned scratchpad. The direct
        # condition never reads or writes an analogy model into it.
        self.scratchpad = AnalogyScratchpad()

    def search(
        self,
        state: SymbolicState,
        goal: SymbolicState,
        ordinary_attempts: tuple[TacticResult, ...],
        evaluator: Callable[[TacticCandidate], TacticEvaluation],
        cancellation: Any,
        semantic_guidance: str,
        dependency_table: str = "(none)",
        *,
        force_domain_change: bool = False,
        proof_belief: Any | None = None,
    ) -> AnalogyRescueOutcome:
        del force_domain_change, proof_belief
        if cancellation.is_cancelled():
            return AnalogyRescueOutcome(cancelled=True)
        attempted_order = []
        attempted_tactics = set()
        for attempt in ordinary_attempts:
            tactic = normalize_lean4_tactic(
                attempt.candidate.source, state.expression
            )
            if tactic not in attempted_tactics:
                attempted_tactics.add(tactic)
                attempted_order.append(tactic)
        if self._initial_proposal is not None and self._initial_state_id == state.id:
            proposal = self._initial_proposal
            self._initial_proposal = None
            self._initial_state_id = None
            return self._evaluate_proposal(
                state, proposal, evaluator, (), (), "initial"
            )

        base_messages = prompt_catalog().messages(
            "deliberation_plan",
            overall_target=render_lean_goal_for_model(goal.expression),
            current_goals=render_lean_goal_for_model(
                focused_lean_goal(state.expression)
            ),
            tried_tactics="\n".join(attempted_order) or "(none)",
            semantic_guidance=semantic_guidance,
            dependency_table=dependency_table,
            tactic_card=lean_tactic_card(state.expression),
        )
        plans: list[tuple[int, dict[str, Any], AnalogyGenerationAttempt]] = []
        rollouts: list[RescueRollout] = []
        active_messages = base_messages
        for index in range(self._max_model_calls):
            seed = _fallback_seed(self._seed, state, index)
            raw = ""
            try:
                raw = self._generator.generate(active_messages, seed=seed)
                plan = parse_deliberation_plan(raw, state.expression)
                generation = AnalogyGenerationAttempt(
                    seed, raw, GenerationStatus.VALID, index > 0
                )
                plans.append((index, plan, generation))
                self._trace(
                    "deliberation_plan_call", state, active_messages, seed, raw
                )
            except Exception as error:
                generation = AnalogyGenerationAttempt(
                    seed,
                    raw,
                    GenerationStatus.INVALID,
                    index > 0,
                    "schema",
                    str(error),
                )
                rollouts.append(RescueRollout(
                    f"{state.id}:deliberation:{index}",
                    state.id,
                    (),
                    (),
                    RescueTermination.INVALID_OUTPUT,
                    generations=(generation,),
                ))
                self._trace(
                    "deliberation_plan_call",
                    state,
                    active_messages,
                    seed,
                    raw,
                    error,
                )
                active_messages = base_messages + (
                    Message(
                        "user",
                        prompt_catalog().text(
                            "deliberation_invalid_repair",
                            diagnostic=error,
                            previous_response=raw or "(no response)",
                        ),
                    ),
                )

        for rank, (index, plan, plan_generation) in enumerate(plans, 1):
            tactic_messages = prompt_catalog().messages(
                "deliberation_tactic",
                current_goals=render_lean_goal_for_model(
                    focused_lean_goal(state.expression)
                ),
                tried_tactics="\n".join(attempted_order) or "(none)",
                semantic_guidance=semantic_guidance,
                dependency_table=dependency_table,
                tactic_card=lean_tactic_card(state.expression),
                direct_plan=json.dumps(plan, indent=2),
            )
            seed = _fallback_seed(self._seed + 10_000, state, rank - 1)
            raw = ""
            try:
                raw = self._generator.generate(tactic_messages, seed=seed)
                proposal = _parse_deliberation_tactic(
                    raw,
                    state.expression,
                    attempted_tactics=attempted_tactics,
                )
                proposal = replace(
                    proposal,
                    candidate=replace(proposal.candidate, model_rank=rank),
                )
                self._trace(
                    "deliberation_tactic_call",
                    state,
                    tactic_messages,
                    seed,
                    raw,
                )
            except Exception as error:
                status = (
                    RescueTermination.DUPLICATE
                    if "already attempted" in str(error)
                    else RescueTermination.INVALID_OUTPUT
                )
                generation = AnalogyGenerationAttempt(
                    seed,
                    raw,
                    GenerationStatus.DUPLICATE
                    if status is RescueTermination.DUPLICATE
                    else GenerationStatus.INVALID,
                    rank > 1,
                    "duplicate_tactic"
                    if status is RescueTermination.DUPLICATE
                    else "schema",
                    str(error),
                )
                rollouts.append(RescueRollout(
                    f"{state.id}:deliberation:{index}",
                    state.id,
                    (),
                    (),
                    status,
                    generations=(plan_generation, generation),
                    analogy_trace=plan,
                ))
                self._trace(
                    "deliberation_tactic_call",
                    state,
                    tactic_messages,
                    seed,
                    raw,
                    error,
                )
                continue
            attempted_tactics.add(proposal.candidate.source)
            attempted_order.append(proposal.candidate.source)
            result = self._evaluate_proposal(
                state,
                proposal,
                evaluator,
                tuple(rollouts),
                (plan_generation,),
                str(index),
                trace=plan,
            )
            if result.completed_proof is not None or result.successor is not None:
                return result
            rollouts = list(result.rollouts)
        return AnalogyRescueOutcome(rollouts=tuple(rollouts))

    def _evaluate_proposal(
        self,
        state: SymbolicState,
        proposal: RescueProposal,
        evaluator: Callable[[TacticCandidate], TacticEvaluation],
        prior_rollouts: tuple[RescueRollout, ...],
        generations: tuple[AnalogyGenerationAttempt, ...],
        suffix: str,
        *,
        trace: Any | None = None,
    ) -> AnalogyRescueOutcome:
        evaluation = evaluator(proposal.candidate)
        attempt = RescueAttempt(proposal, evaluation.result, evaluation.discarded)
        termination = (
            RescueTermination.COMPLETE
            if evaluation.completed_proof is not None
            else RescueTermination.PROGRESS
            if evaluation.successor is not None
            else RescueTermination.REJECTED
            if evaluation.result.status is TacticStatus.ERROR
            else RescueTermination.EXHAUSTED
        )
        rollout = RescueRollout(
            f"{state.id}:deliberation:{suffix}",
            state.id,
            (proposal,),
            (attempt,),
            termination,
            generations=generations,
            analogy_trace=trace,
        )
        rollouts = prior_rollouts + (rollout,)
        return AnalogyRescueOutcome(
            completed_proof=evaluation.completed_proof,
            successor=evaluation.successor,
            rollouts=rollouts,
        )

    def _trace(
        self,
        kind: str,
        state: SymbolicState,
        messages: tuple[Message, ...],
        seed: int,
        raw: str,
        error: Exception | None = None,
    ) -> None:
        if self._trace_sink is None:
            return
        event = {
            "kind": kind,
            "state_id": state.id,
            "seed": seed,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "raw_text": raw,
            "status": "invalid" if error is not None else "valid",
        }
        if error is not None:
            event["diagnostic"] = f"{type(error).__name__}: {error}"
        self._trace_sink(event)


def initialize_deliberation_fallback(
    config: AppConfig,
    generator: Any,
    model_events: list[dict[str, Any]],
    state: SymbolicState,
    goal: SymbolicState,
    ordinary_attempts: tuple[TacticResult, ...],
    semantic_guidance: str,
    dependency_table: str,
) -> DeliberationFallback:
    """Create the first direct plan and tactic under the analogy call budget."""

    attempted = []
    attempted_set = set()
    for attempt in ordinary_attempts:
        tactic = normalize_lean4_tactic(attempt.candidate.source, state.expression)
        if tactic not in attempted_set:
            attempted_set.add(tactic)
            attempted.append(tactic)
    base_messages = prompt_catalog().messages(
        "deliberation_plan",
        overall_target=render_lean_goal_for_model(goal.expression),
        current_goals=render_lean_goal_for_model(
            focused_lean_goal(state.expression)
        ),
        tried_tactics="\n".join(attempted) or "(none)",
        semantic_guidance=semantic_guidance,
        dependency_table=dependency_table,
        tactic_card=lean_tactic_card(state.expression),
    )
    active_messages = base_messages
    plan = None
    for index in range(config.analogy.max_model_calls):
        seed = _fallback_seed(config.run.seed, state, index)
        raw = ""
        try:
            raw = generator.generate(active_messages, seed=seed)
            plan = parse_deliberation_plan(raw, state.expression)
            model_events.append({
                "kind": "deliberation_initial_plan_call",
                "state_id": state.id,
                "attempt_index": index + 1,
                "messages": [
                    {"role": item.role, "content": item.content}
                    for item in active_messages
                ],
                "seed": seed,
                "raw_text": raw,
                "status": "valid",
            })
            break
        except Exception as error:
            model_events.append({
                "kind": "deliberation_initial_plan_call",
                "state_id": state.id,
                "attempt_index": index + 1,
                "messages": [
                    {"role": item.role, "content": item.content}
                    for item in active_messages
                ],
                "seed": seed,
                "raw_text": raw,
                "status": "invalid",
                "diagnostic": f"{type(error).__name__}: {error}",
            })
            active_messages = base_messages + (
                Message(
                    "user",
                    prompt_catalog().text(
                        "deliberation_invalid_repair",
                        diagnostic=error,
                        previous_response=raw or "(no response)",
                    ),
                ),
            )
    if plan is None:
        raise RescueOutputError("no valid direct deliberation plan was generated")
    tactic_messages = prompt_catalog().messages(
        "deliberation_tactic",
        current_goals=render_lean_goal_for_model(
            focused_lean_goal(state.expression)
        ),
        tried_tactics="\n".join(attempted) or "(none)",
        semantic_guidance=semantic_guidance,
        dependency_table=dependency_table,
        tactic_card=lean_tactic_card(state.expression),
        direct_plan=json.dumps(plan, indent=2),
    )
    tactic_seed = _fallback_seed(config.run.seed + 10_000, state, 0)
    raw_tactic = generator.generate(tactic_messages, seed=tactic_seed)
    try:
        proposal = _parse_deliberation_tactic(
            raw_tactic,
            state.expression,
            attempted_tactics=attempted_set,
        )
    except Exception as error:
        model_events.append({
            "kind": "deliberation_initial_tactic_call",
            "state_id": state.id,
            "messages": [
                {"role": item.role, "content": item.content}
                for item in tactic_messages
            ],
            "seed": tactic_seed,
            "raw_text": raw_tactic,
            "status": "invalid",
            "diagnostic": f"{type(error).__name__}: {error}",
        })
        raise
    model_events.append({
        "kind": "deliberation_initial_tactic_call",
        "state_id": state.id,
        "messages": [
            {"role": item.role, "content": item.content}
            for item in tactic_messages
        ],
        "seed": tactic_seed,
        "raw_text": raw_tactic,
        "status": "valid",
        "tactic": proposal.candidate.source,
    })
    return DeliberationFallback(
        generator,
        max_model_calls=config.analogy.max_model_calls,
        seed=config.run.seed,
        trace_sink=model_events.append,
        initial_proposal=proposal,
        initial_state_id=state.id,
    )


def activate_deliberation(search: Any, fallback: DeliberationFallback) -> None:
    """Install direct rescue without changing ordinary tactic ranking."""

    search.context = replace(search.context, analogy_fallback=fallback)


def deliberation_context(search: Any) -> tuple[str, str]:
    """Render the same semantic evidence consumed by analogy recovery."""

    if search.recovery is None:
        raise RuntimeError("search has no pending recovery")
    return (
        render_semantic_guidance(search.recovery),
        render_countermodel_table(search.recovery.countermodel_table),
    )


__all__ = [
    "DeliberationFallback",
    "activate_deliberation",
    "deliberation_context",
    "initialize_deliberation_fallback",
    "parse_deliberation_plan",
]
