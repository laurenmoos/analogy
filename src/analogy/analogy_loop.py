"""Readable surprise-driven analogy refinement loop.

The coordinator deliberately keeps the control flow in one place.  Helpers only
construct immutable records or perform one bounded operation, so experiments can
change stopping rules without hiding when generation or Lean is called.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable

from .analogy_generation import (
    AnalogyGenerationContext,
    AnalogyGenerationFailure,
    IterationSummary,
    TacticGenerationContext,
)
from .analogy_stream import AnalogyHop, AnalogyStream, AnalogyTacticPlan
from .proof_views import ProofViews, TransitionComparison
from .records import CandidateOrigin, SearchNode, SymbolicState, TacticCandidate, TacticStatus
from .rescue import (
    GenerationAttempt,
    GenericRescueOutcome,
    PolicyActionRecord,
    ProofRescueContext,
    RescueAttempt,
    RescueProposal,
    RescueRollout,
    RescueTermination,
    TacticEvaluation,
)
from .surprise import (
    StopReason,
    SurpriseController,
    SurpriseDecision,
    SurpriseObservation,
    SurpriseState,
)


@dataclass(frozen=True, slots=True)
class LoopResources:
    model_calls: int
    lean_calls: int
    max_model_calls: int
    max_lean_calls: int

    @property
    def remaining_model_calls(self) -> int:
        return self.max_model_calls - self.model_calls

    @property
    def remaining_lean_calls(self) -> int:
        return self.max_lean_calls - self.lean_calls

    def use_model(self, count: int) -> LoopResources:
        return LoopResources(
            self.model_calls + count,
            self.lean_calls,
            self.max_model_calls,
            self.max_lean_calls,
        )

    def use_lean(self, count: int = 1) -> LoopResources:
        return LoopResources(
            self.model_calls,
            self.lean_calls + count,
            self.max_model_calls,
            self.max_lean_calls,
        )


@dataclass(frozen=True, slots=True)
class AnalogyIteration:
    iteration_index: int
    state_before: SymbolicState
    views: ProofViews
    hop: AnalogyHop
    hop_generation_attempts: tuple[GenerationAttempt, ...]
    action: PolicyActionRecord
    tactic_plan: AnalogyTacticPlan
    tactic_generation_attempts: tuple[GenerationAttempt, ...]
    proposals: tuple[RescueProposal, ...]
    attempts: tuple[RescueAttempt, ...]
    predicted_transition: str
    actual_transition: str | None
    comparison: TransitionComparison | None
    surprise_delta: float | None
    decision: SurpriseDecision
    model_calls: int
    lean_calls: int
    elapsed_seconds: float

    @property
    def surprise(self) -> float | None:
        return None if self.comparison is None else self.comparison.surprise


@dataclass(frozen=True, slots=True)
class AnalogyLoopTrace:
    iterations: tuple[AnalogyIteration, ...]
    stop_reason: StopReason
    model_calls: int = 0
    lean_calls: int = 0


@dataclass(frozen=True, slots=True)
class _VerifiedPlan:
    proposals: tuple[RescueProposal, ...]
    attempts: tuple[RescueAttempt, ...]
    completed_proof: str | None
    successor: SearchNode | None


class AnalogyLoopRunner:
    """Generate an analogy, decode tactics, verify, then refine on surprise."""

    def __init__(
        self,
        *,
        proof_view_builder: Callable[[ProofRescueContext], ProofViews],
        action_sampler: Callable[[ProofViews, int], Any],
        analogy_generator: Any,
        tactic_generator: Any,
        transition_comparator: Callable[[str, str, str], TransitionComparison],
        controller: SurpriseController,
        tactics_per_iteration: int,
        max_model_calls: int,
        max_lean_calls: int,
        iteration_sink: Callable[[str, AnalogyIteration], None] | None = None,
        tactic_reranker: Callable[
            [TacticGenerationContext, AnalogyTacticPlan], AnalogyTacticPlan
        ] | None = None,
        proof_view_lean_cost: int = 0,
    ) -> None:
        for value, name in (
            (tactics_per_iteration, "tactics_per_iteration"),
            (max_model_calls, "max_model_calls"),
            (max_lean_calls, "max_lean_calls"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self.proof_view_builder = proof_view_builder
        self.action_sampler = action_sampler
        self.analogy_generator = analogy_generator
        self.tactic_generator = tactic_generator
        self.transition_comparator = transition_comparator
        self.controller = controller
        self.tactics_per_iteration = tactics_per_iteration
        self.max_model_calls = max_model_calls
        self.max_lean_calls = max_lean_calls
        self.iteration_sink = iteration_sink
        self.tactic_reranker = tactic_reranker
        if (
            isinstance(proof_view_lean_cost, bool)
            or not isinstance(proof_view_lean_cost, int)
            or proof_view_lean_cost < 0
        ):
            raise ValueError("proof_view_lean_cost must be a non-negative integer")
        self.proof_view_lean_cost = proof_view_lean_cost
        self.live_actions: tuple[Any, ...] = ()
        self._live_trajectories: list[tuple[RescueRollout, tuple[Any, ...]]] = []

    def drain_trajectories(self) -> tuple[tuple[RescueRollout, tuple[Any, ...]], ...]:
        """Return completed rollout/action pairs while retaining gradient graphs."""
        drained = tuple(self._live_trajectories)
        self._live_trajectories.clear()
        return drained

    def search(
        self, state, goal, tactic_pool, ordinary_attempts, evaluator, cancellation
    ) -> GenericRescueOutcome:
        """Run the visible generate -> verify -> compare -> decide loop."""
        del tactic_pool
        current = state
        latest_successor = None
        completed_proof = None
        stream = AnalogyStream(())
        iterations: list[AnalogyIteration] = []
        summaries: list[IterationSummary] = []
        actions: list[Any] = []
        proposals: list[RescueProposal] = []
        attempts: list[RescueAttempt] = []
        generations: list[GenerationAttempt] = []
        failed_tactics = tuple(item.candidate.source for item in ordinary_attempts)
        accepted_tactics = tuple(state.context)
        resources = LoopResources(0, 0, self.max_model_calls, self.max_lean_calls)
        controller_state = SurpriseState()
        stop_reason = StopReason.NO_SUCCESSOR
        rollout_id = f"{state.id}:analogy-loop"

        for iteration_index in range(self.controller.config.max_iterations):
            # Boundary 1: do not begin a new card after cancellation or exhaustion.
            preflight = self._preflight(resources, cancellation)
            if preflight is not None:
                stop_reason = preflight
                break
            if resources.remaining_lean_calls <= self.proof_view_lean_cost:
                stop_reason = StopReason.LEAN_BUDGET
                break

            iteration_started = time.monotonic()

            context = ProofRescueContext(current, goal, accepted_tactics, failed_tactics)
            views = self.proof_view_builder(context)
            resources = resources.use_lean(self.proof_view_lean_cost)
            action = self.action_sampler(views, iteration_index)
            actions.append(action)
            analogy_context = AnalogyGenerationContext(
                context, views, views.proof_structure, stream, tuple(summaries)
            )
            try:
                generated_hop = self.analogy_generator.generate(
                    analogy_context,
                    action,
                    iteration_index=iteration_index,
                    max_attempts=resources.remaining_model_calls,
                )
            except AnalogyGenerationFailure as failure:
                generations.extend(failure.attempts)
                resources = resources.use_model(len(failure.attempts))
                stop_reason = self._generation_stop(resources)
                break
            generations.extend(generated_hop.attempts)
            resources = resources.use_model(len(generated_hop.attempts))
            stream = stream.append(generated_hop.hop)

            # Boundary 2: a generated card never forces another model call.
            stop_reason = self._boundary_reason(resources, cancellation, model=True)
            if stop_reason is not None:
                break
            tactic_context = TacticGenerationContext(
                AnalogyGenerationContext(
                    context, views, views.proof_structure, stream, tuple(summaries)
                ),
                generated_hop.hop.hop_id,
            )
            try:
                generated_plan = self.tactic_generator.generate(
                    tactic_context,
                    action,
                    iteration_index=iteration_index,
                    max_attempts=resources.remaining_model_calls,
                )
            except AnalogyGenerationFailure as failure:
                generations.extend(failure.attempts)
                resources = resources.use_model(len(failure.attempts))
                stop_reason = self._generation_stop(resources)
                break
            generations.extend(generated_plan.attempts)
            resources = resources.use_model(len(generated_plan.attempts))

            tactic_plan = generated_plan.plan
            if self.tactic_reranker is not None:
                stop_reason = self._boundary_reason(
                    resources, cancellation, model=True
                )
                if stop_reason is not None:
                    break
                tactic_plan = self.tactic_reranker(tactic_context, tactic_plan)
                resources = resources.use_model(1)

            # Boundary 3: Lean is only called while both control and budget permit.
            stop_reason = self._boundary_reason(resources, cancellation, lean=True)
            if stop_reason is not None:
                break
            verified, resources = self._verify_plan(
                current, tactic_plan, evaluator, cancellation, resources
            )
            proposals.extend(verified.proposals)
            attempts.extend(verified.attempts)
            completed_proof = verified.completed_proof
            if verified.successor is not None:
                latest_successor = verified.successor

            # Boundary 4: transition embeddings are optional after cancellation.
            cancelled_before_comparison = cancellation.is_cancelled()
            comparison = None
            if (
                not cancelled_before_comparison
                and verified.successor is not None
                and completed_proof is None
            ):
                comparison = self.transition_comparator(
                    current.expression,
                    tactic_plan.predicted_state,
                    verified.successor.state.expression,
                )

            observation = SurpriseObservation(
                iteration_index=iteration_index,
                surprise=None if comparison is None else comparison.surprise,
                has_successor=verified.successor is not None,
                completed=completed_proof is not None,
                cancelled=cancelled_before_comparison,
                model_stop=generated_hop.hop.stop,
                remaining_model_calls=resources.remaining_model_calls,
                remaining_lean_calls=resources.remaining_lean_calls,
            )
            decision = self.controller.decide(controller_state, observation)
            surprise_delta = _surprise_delta(controller_state, comparison)
            controller_state = decision.state
            iteration = AnalogyIteration(
                iteration_index,
                current,
                views,
                generated_hop.hop,
                generated_hop.attempts,
                _action_record(action),
                tactic_plan,
                generated_plan.attempts,
                verified.proposals,
                verified.attempts,
                tactic_plan.predicted_state,
                None if verified.successor is None else verified.successor.state.expression,
                comparison,
                surprise_delta,
                decision,
                resources.model_calls,
                resources.lean_calls,
                time.monotonic() - iteration_started,
            )
            iterations.append(iteration)
            if self.iteration_sink is not None:
                self.iteration_sink(rollout_id, iteration)
            summaries.append(_summarize(iteration))
            stop_reason = decision.reason
            if not decision.continue_loop:
                break

            current = verified.successor.state
            accepted_tactics += (verified.attempts[-1].proposal.candidate.source,)

        self.live_actions = tuple(actions)
        trace = AnalogyLoopTrace(
            tuple(iterations), stop_reason, resources.model_calls, resources.lean_calls
        )
        rollout = self._rollout(
            state,
            actions,
            tuple(proposals),
            tuple(attempts),
            tuple(generations),
            stream,
            trace,
            completed_proof,
            latest_successor,
            stop_reason,
        )
        completed_actions = tuple(actions[: len(iterations)])
        self._live_trajectories.append((rollout, completed_actions))
        if stop_reason is StopReason.CANCELLED:
            return GenericRescueOutcome(rollouts=(rollout,), cancelled=True)
        returned_successor = None if completed_proof is not None else latest_successor
        return GenericRescueOutcome(completed_proof, returned_successor, (rollout,))

    def _verify_plan(self, current, plan, evaluator, cancellation, resources):
        tactics = plan.tactics[: self.tactics_per_iteration]
        score = 1.0 / len(tactics)
        proposals = tuple(
            RescueProposal(
                TacticCandidate(source, score, CandidateOrigin.ANALOGICAL),
                plan.predicted_state,
            )
            for source in tactics
        )
        attempts: list[RescueAttempt] = []
        completed = None
        successor = None
        for proposal in proposals:
            # The caller checked before the first Lean call; re-check only between
            # multiple tactic attempts so each additional call remains interruptible.
            if attempts and cancellation.is_cancelled():
                break
            if resources.remaining_lean_calls <= 0:
                break
            evaluate_at = getattr(evaluator, "evaluate_at", None)
            evaluation: TacticEvaluation = (
                evaluate_at(current, proposal.candidate)
                if evaluate_at is not None
                else evaluator(proposal.candidate)
            )
            resources = resources.use_lean()
            attempts.append(
                RescueAttempt(proposal, evaluation.result, evaluation.discarded)
            )
            if evaluation.completed_proof is not None:
                completed = evaluation.completed_proof
                break
            if evaluation.successor is not None:
                successor = evaluation.successor
                break
        return _VerifiedPlan(proposals, tuple(attempts), completed, successor), resources

    @staticmethod
    def _preflight(resources, cancellation):
        return AnalogyLoopRunner._boundary_reason(resources, cancellation, model=True, lean=True)

    @staticmethod
    def _boundary_reason(resources, cancellation, *, model=False, lean=False):
        if cancellation.is_cancelled():
            return StopReason.CANCELLED
        if model and resources.remaining_model_calls <= 0:
            return StopReason.MODEL_BUDGET
        if lean and resources.remaining_lean_calls <= 0:
            return StopReason.LEAN_BUDGET
        return None

    @staticmethod
    def _generation_stop(resources):
        if resources.remaining_model_calls <= 0:
            return StopReason.MODEL_BUDGET
        return StopReason.NO_SUCCESSOR

    @staticmethod
    def _rollout(
        state, actions, proposals, attempts, generations, stream, trace,
        completed_proof, successor, stop_reason,
    ):
        action = actions[0] if actions else None
        action_record = _action_record(action)
        if stop_reason is StopReason.CANCELLED:
            termination = RescueTermination.CANCELLED
        elif completed_proof is not None:
            termination = RescueTermination.COMPLETE
        elif successor is not None:
            termination = RescueTermination.PROGRESS
        elif attempts and all(item.result.status is TacticStatus.ERROR for item in attempts):
            termination = RescueTermination.REJECTED
        elif stop_reason in (StopReason.MODEL_BUDGET, StopReason.LEAN_BUDGET):
            termination = RescueTermination.BUDGET_EXHAUSTED
        else:
            termination = RescueTermination.EXHAUSTED
        return RescueRollout(
            f"{state.id}:analogy-loop",
            state.id,
            action_record,
            proposals,
            attempts,
            termination,
            generations=generations,
            analogy_stream=stream,
            analogy_trace=trace,
        )


def _surprise_delta(
    state: SurpriseState, comparison: TransitionComparison | None
) -> float | None:
    if comparison is None or not state.values:
        return None
    return state.values[-1] - comparison.surprise


def _action_record(action: Any | None) -> PolicyActionRecord:
    return PolicyActionRecord(
        0,
        0.0 if action is None else float(action.delta.detach().norm()),
        0.0 if action is None else float(action.log_probability.detach()),
        0.0 if action is None else float(action.entropy.detach()),
        0.0 if action is None else float(action.radius.detach()),
        0.0 if action is None else float(action.raw_direction_norm.detach()),
    )


def _summarize(iteration: AnalogyIteration) -> IterationSummary:
    return IterationSummary(
        iteration.iteration_index,
        iteration.predicted_transition,
        iteration.actual_transition or "(no successor)",
        iteration.surprise if iteration.surprise is not None else 0.0,
        iteration.surprise_delta,
        iteration.tactic_plan.tactics,
        tuple(item.result.status.value for item in iteration.attempts),
    )


__all__ = [
    "AnalogyIteration",
    "AnalogyLoopRunner",
    "AnalogyLoopTrace",
    "LoopResources",
]
