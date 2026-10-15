"""Causal source → alignment → reasoning → tactic → verified trajectory loop."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable

from .analogy_generation import AnalogyGenerationFailure
from .analogy_loop import LoopResources, _action_record, _surprise_delta
from .analogy_transfer import (
    AnalogyAlignment, AnalogyObservation, ProofNeed, ProofReasoningTransfer,
    SourceAnalogy, derive_proof_need,
)
from .analogy_transfer_generation import (
    TransferredTacticPlan, SOURCE_PROMPT_VERSION, ALIGNMENT_PROMPT_VERSION,
    TRANSFER_PROMPT_VERSION, TACTIC_PROMPT_VERSION,
)
from .proof_views import ProofStructure, TransitionComparison
from .records import (
    CandidateOrigin, DiscardedSuccessor, DiscardReason, SymbolicState,
    TacticCandidate, TacticStatus, SearchNode,
)
from .replay import proof_state_fingerprint
from .rescue import (
    GenerationAttempt, GenerationStatus, GenericRescueOutcome, PolicyActionRecord,
    ProofRepresentation, ProofRescueContext, RescueAttempt, RescueProposal,
    RescueRollout, RescueTermination,
)
from .surprise import (
    StopReason, SurpriseController, SurpriseDecision, SurpriseObservation, SurpriseState,
)


@dataclass(frozen=True, slots=True)
class GeneratedAnalogyIteration:
    iteration_index: int
    state_before: SymbolicState
    observation: AnalogyObservation
    structure: ProofStructure | None
    representation: ProofRepresentation | None
    need: ProofNeed | None
    action: PolicyActionRecord | None
    generated_source: SourceAnalogy | None
    source: SourceAnalogy | None
    alignment: AnalogyAlignment | None
    transfer: ProofReasoningTransfer | None
    tactic_plan: TransferredTacticPlan | None
    source_generation_attempts: tuple[GenerationAttempt, ...]
    alignment_generation_attempts: tuple[GenerationAttempt, ...]
    transfer_generation_attempts: tuple[GenerationAttempt, ...]
    tactic_generation_attempts: tuple[GenerationAttempt, ...]
    proposals: tuple[RescueProposal, ...]
    attempts: tuple[RescueAttempt, ...]
    finisher_state: SymbolicState | None
    predicted_transition: str | None
    actual_transition: str | None
    comparison: TransitionComparison | None
    surprise_delta: float | None
    repeated_state: bool
    decision: SurpriseDecision
    failed_stage: str | None
    failure_diagnostic: str | None
    model_calls: int
    lean_calls: int
    elapsed_seconds: float
    training_eligible: bool = True
    transition_before: str | None = None
    prompt_versions: tuple[tuple[str, str], ...] = (
        ("source", SOURCE_PROMPT_VERSION), ("alignment", ALIGNMENT_PROMPT_VERSION),
        ("transfer", TRANSFER_PROMPT_VERSION), ("tactic", TACTIC_PROMPT_VERSION),
    )

    @property
    def surprise(self) -> float | None:
        return None if self.comparison is None else self.comparison.surprise


@dataclass(frozen=True, slots=True)
class GeneratedAnalogyTrace:
    iterations: tuple[GeneratedAnalogyIteration, ...]
    stop_reason: StopReason
    model_calls: int = 0
    lean_calls: int = 0


class _BoundaryStop(Exception):
    def __init__(self, reason: StopReason):
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _Verified:
    proposals: tuple[RescueProposal, ...]
    attempts: tuple[RescueAttempt, ...]
    successor: SearchNode | None
    completed_proof: str | None
    failure: str | None = None


class _VerificationFailure(Exception):
    pass


class GeneratedDomainAnalogyLoopRunner:
    """Coordinate bounded stages while keeping the verified trajectory explicit."""

    def __init__(
        self, *, structure_builder: Callable, observation_builder: Callable,
        action_sampler: Callable, source_generator: Any, alignment_generator: Any,
        transfer_generator: Any, tactic_generator: Any,
        transition_comparator: Callable, controller: SurpriseController,
        tactics_per_iteration: int, max_model_calls: int, max_lean_calls: int,
        structure_lean_cost: int = 0, iteration_sink: Callable | None = None,
        base_seed: int = 0, source_transform: Callable | None = None,
    ) -> None:
        for name, value in (("tactics_per_iteration", tactics_per_iteration),
                            ("max_model_calls", max_model_calls), ("max_lean_calls", max_lean_calls)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(structure_lean_cost, bool) or not isinstance(structure_lean_cost, int) or structure_lean_cost < 0:
            raise ValueError("structure_lean_cost must be a non-negative integer")
        self.structure_builder = structure_builder
        self.observation_builder = observation_builder
        self.action_sampler = action_sampler
        self.source_generator = source_generator
        self.alignment_generator = alignment_generator
        self.transfer_generator = transfer_generator
        self.tactic_generator = tactic_generator
        self.transition_comparator = transition_comparator
        self.controller = controller
        self.tactics_per_iteration = tactics_per_iteration
        self.max_model_calls = max_model_calls
        self.max_lean_calls = max_lean_calls
        self.structure_lean_cost = structure_lean_cost
        self.iteration_sink = iteration_sink
        self.base_seed = base_seed
        self.source_transform = source_transform
        self.live_actions = ()
        self._live_trajectories = []

    def drain_trajectories(self):
        trajectories = tuple(self._live_trajectories)
        self._live_trajectories.clear()
        return trajectories

    def search(self, state, goal, tactic_pool, ordinary_attempts, evaluator, cancellation) -> GenericRescueOutcome:
        del tactic_pool
        current, previous, last_tactic = state, None, None
        accepted = tuple(state.context)
        rejected = tuple((item.candidate.source, item.diagnostics) for item in ordinary_attempts)
        latest_source = latest_alignment = latest_transfer = None
        predicted = actual = surprise = surprise_delta = None
        repeated = False
        seen = {proof_state_fingerprint(state.expression)}
        resources = LoopResources(0, 0, self.max_model_calls, self.max_lean_calls)
        controller_state = SurpriseState()
        iterations, actions, proposals, attempts, generations = [], [], [], [], []
        latest_successor = completed = None
        stop_reason = StopReason.NO_SUCCESSOR
        generation_failure = None
        runtime_failure = None
        rollout_id = f"{state.id}:generated-domain:{uuid.uuid4().hex}"

        for index in range(self.controller.config.max_iterations):
            if cancellation.is_cancelled():
                stop_reason = StopReason.CANCELLED
                break
            if resources.remaining_model_calls <= 0:
                stop_reason = StopReason.MODEL_BUDGET
                break
            if resources.remaining_lean_calls <= self.structure_lean_cost:
                stop_reason = StopReason.LEAN_BUDGET
                break
            started = time.monotonic()
            before = current
            resources = resources.use_lean(self.structure_lean_cost)
            observation = AnalogyObservation(
                goal.expression, current.expression, previous, last_tactic, accepted,
                rejected, latest_source, latest_alignment, latest_transfer, predicted,
                actual, surprise, surprise_delta, repeated, index,
                resources.remaining_model_calls, resources.remaining_lean_calls,
            )
            structure = representation = need = action = None
            generated_source = source = alignment = transfer = plan = None
            stage_attempts = {name: () for name in ("source", "alignment", "transfer", "tactic")}
            local_proposals, local_attempts = [], []
            finisher_state = comparison = failed_stage = None
            failure_diagnostic = None
            active_stage = "structure"
            infrastructure_failure = False
            transition_before = before.expression
            successor = None
            predicted = actual = None
            repeated = False

            def generate(stage, service, *args, **kwargs):
                nonlocal resources, failed_stage, active_stage
                active_stage = stage
                self._check_boundary(resources, cancellation, model=True)
                try:
                    generated = service.generate(*args, analogy_index=index,
                        max_attempts=resources.remaining_model_calls, cancellation=cancellation, **kwargs)
                except AnalogyGenerationFailure as failure:
                    stage_attempts[stage] = failure.attempts
                    resources = resources.use_model(len(failure.attempts))
                    generations.extend(failure.attempts)
                    failed_stage = stage
                    raise
                stage_attempts[stage] = generated.attempts
                resources = resources.use_model(len(generated.attempts))
                generations.extend(generated.attempts)
                return generated

            try:
                structure = self.structure_builder(current)
                self._check_boundary(resources, cancellation)
                # Full observation → deterministic proof need → latent action.
                active_stage = "observation"
                context = ProofRescueContext(current, goal, accepted, tuple(tactic for tactic, _ in rejected))
                representation = self.observation_builder(observation, structure, context)
                active_stage = "proof_need"
                active = current.expression.split("⊢", 1)[-1].split("\n\n", 1)[0].strip()
                need = derive_proof_need(structure, active, fixed_target=goal.expression)
                active_stage = "action"
                action = self.action_sampler(representation, index)
                actions.append(action)
                # Concrete source → alignment → tactic-free reasoning → tactic plan.
                source = generate("source", self.source_generator, observation, need, action).source
                generated_source = source
                if self.source_transform is not None:
                    active_stage = "source_shuffle"
                    source = self.source_transform(source, need, observation)
                alignment = generate("alignment", self.alignment_generator, observation, need, source).alignment
                transfer = generate("transfer", self.transfer_generator, observation, need, source, alignment).transfer
                plan = generate("tactic", self.tactic_generator, current, structure, source, alignment, transfer,
                                candidate_count=self.tactics_per_iteration).plan
                predicted = plan.predicted_state

                # Lean verification is authoritative. Discard repeated candidates.
                active_stage = "verification"
                self._check_boundary(resources, cancellation, lean=True)
                verified, resources = self._verify(
                    current, plan.tactics[:self.tactics_per_iteration], predicted,
                    evaluator, cancellation, resources, seen, "analogy",
                )
                local_proposals.extend(verified.proposals)
                local_attempts.extend(verified.attempts)
                successor, completed = verified.successor, verified.completed_proof
                if verified.failure is not None:
                    raise _VerificationFailure(verified.failure)
                actual = successor.state.expression if successor is not None else ("no goals" if completed else current.expression)

                # A cheap conventional finishing pass precedes every stop decision.
                if successor is not None:
                    previous = current.expression
                    current = successor.state
                    last_tactic = successor.tactic or local_attempts[-1].proposal.candidate.source
                    accepted += (last_tactic,)
                    rejected = ()
                    latest_successor = successor
                    latest_source, latest_alignment, latest_transfer = source, alignment, transfer
                    seen.add(proof_state_fingerprint(current.expression))
                    finisher_state = current
                    if resources.remaining_lean_calls > 0 and not cancellation.is_cancelled():
                        active_stage = "conventional_finisher"
                        finished, resources = self._verify(current, ("assumption",), "no goals",
                            evaluator, cancellation, resources, seen, "conventional_finisher")
                        local_proposals.extend(finished.proposals)
                        local_attempts.extend(finished.attempts)
                        if finished.failure is not None:
                            raise _VerificationFailure(finished.failure)
                        rejected = tuple((item.proposal.candidate.source, item.result.diagnostics)
                                         for item in finished.attempts if item.result.status is TacticStatus.ERROR)
                        if finished.completed_proof is not None:
                            completed = finished.completed_proof
                            transition_before = current.expression
                            predicted = finished.attempts[-1].proposal.predicted_state
                            actual = "no goals"
                        elif finished.successor is not None:
                            previous, current, last_tactic = current.expression, finished.successor.state, "assumption"
                            accepted += (last_tactic,)
                            latest_successor = finished.successor
                            seen.add(proof_state_fingerprint(current.expression))
                            # The next observation and controller compare the
                            # most recent verified transition, including a
                            # finisher that closes only part of the obligation.
                            predicted = finished.attempts[-1].proposal.predicted_state
                            actual = current.expression
                            transition_before = previous
                    if not cancellation.is_cancelled() and completed is None:
                        active_stage = "comparison"
                        comparison = self.transition_comparator(previous, predicted, actual)
                active_stage = "controller"
                decision = self.controller.decide(controller_state, SurpriseObservation(
                    index, None if comparison is None else comparison.surprise,
                    successor is not None, completed is not None, cancellation.is_cancelled(),
                    False, resources.remaining_model_calls, resources.remaining_lean_calls,
                ))
            except AnalogyGenerationFailure as failure:
                generation_failure = failure
                infrastructure_failure = any(
                    attempt.status is GenerationStatus.RUNTIME_ERROR for attempt in failure.attempts
                )
                failure_diagnostic = str(failure)
                reason = (StopReason.CANCELLED if cancellation.is_cancelled() else
                          StopReason.MODEL_BUDGET if resources.remaining_model_calls <= 0 else StopReason.NO_SUCCESSOR)
                decision = SurpriseDecision(False, reason, controller_state)
            except _BoundaryStop as boundary:
                decision = SurpriseDecision(False, boundary.reason, controller_state)
            except Exception as failure:
                infrastructure_failure = True
                failed_stage = active_stage
                failure_diagnostic = str(failure) if isinstance(failure, _VerificationFailure) else f"{type(failure).__name__}: {failure}"
                runtime_failure = failure_diagnostic
                decision = SurpriseDecision(False, StopReason.NO_SUCCESSOR, controller_state)

            # Partial iterations retain their available stages and exact call totals.
            surprise_delta = _surprise_delta(controller_state, comparison)
            surprise = None if comparison is None else comparison.surprise
            controller_state = decision.state
            repeated = any(
                item.discarded is not None
                and item.discarded.reason is DiscardReason.REPEATED_STATE
                for item in local_attempts
            )
            iteration = GeneratedAnalogyIteration(
                iteration_index=index,
                state_before=before,
                observation=observation,
                structure=structure,
                representation=representation,
                need=need,
                action=None if action is None else replace(_action_record(action), seed=self.base_seed + index),
                generated_source=generated_source,
                source=source,
                alignment=alignment,
                transfer=transfer,
                tactic_plan=plan,
                source_generation_attempts=stage_attempts["source"],
                alignment_generation_attempts=stage_attempts["alignment"],
                transfer_generation_attempts=stage_attempts["transfer"],
                tactic_generation_attempts=stage_attempts["tactic"],
                proposals=tuple(local_proposals),
                attempts=tuple(local_attempts),
                finisher_state=finisher_state,
                predicted_transition=predicted,
                actual_transition=actual,
                comparison=comparison,
                surprise_delta=surprise_delta,
                repeated_state=repeated,
                decision=decision,
                failed_stage=failed_stage,
                failure_diagnostic=failure_diagnostic,
                model_calls=resources.model_calls,
                lean_calls=resources.lean_calls,
                elapsed_seconds=time.monotonic() - started,
                training_eligible=(action is not None and not infrastructure_failure
                                   and decision.reason is not StopReason.CANCELLED),
                transition_before=transition_before,
            )
            iterations.append(iteration)
            proposals.extend(local_proposals)
            attempts.extend(local_attempts)
            if self.iteration_sink is not None:
                self.iteration_sink(rollout_id, iteration)
            stop_reason = decision.reason
            if not decision.continue_loop:
                break

        trace = GeneratedAnalogyTrace(tuple(iterations), stop_reason, resources.model_calls, resources.lean_calls)
        if stop_reason is StopReason.CANCELLED:
            termination = RescueTermination.CANCELLED
        elif completed is not None:
            termination = RescueTermination.COMPLETE
        elif latest_successor is not None:
            termination = RescueTermination.PROGRESS
        elif runtime_failure is not None:
            termination = RescueTermination.GENERATION_ERROR
        elif generation_failure is not None:
            termination = (RescueTermination.GENERATION_ERROR if any(
                item.status is GenerationStatus.RUNTIME_ERROR for item in generation_failure.attempts
            ) else RescueTermination.INVALID_OUTPUT)
        elif stop_reason in (StopReason.MODEL_BUDGET, StopReason.LEAN_BUDGET):
            termination = RescueTermination.BUDGET_EXHAUSTED
        elif attempts and all(item.result.status is TacticStatus.ERROR for item in attempts):
            termination = RescueTermination.REJECTED
        else:
            termination = RescueTermination.EXHAUSTED
        rollout = RescueRollout(rollout_id, state.id, _action_record(actions[0] if actions else None),
            tuple(proposals), tuple(attempts), termination, generations=tuple(generations), analogy_trace=trace)
        self.live_actions = tuple(actions)
        self._live_trajectories.append((rollout, self.live_actions))
        if stop_reason is StopReason.CANCELLED:
            return GenericRescueOutcome(rollouts=(rollout,), cancelled=True)
        return GenericRescueOutcome(completed, None if completed else latest_successor, (rollout,))

    @staticmethod
    def _check_boundary(resources, cancellation, *, model=False, lean=False):
        if cancellation.is_cancelled():
            raise _BoundaryStop(StopReason.CANCELLED)
        if model and resources.remaining_model_calls <= 0:
            raise _BoundaryStop(StopReason.MODEL_BUDGET)
        if lean and resources.remaining_lean_calls <= 0:
            raise _BoundaryStop(StopReason.LEAN_BUDGET)

    @staticmethod
    def _verify(current, tactics, predicted, evaluator, cancellation, resources, seen, stage):
        proposals, attempts = [], []
        successor = completed = None
        for tactic in dict.fromkeys(" ".join(tactic.split()) for tactic in tactics):
            if cancellation.is_cancelled() or resources.remaining_lean_calls <= 0:
                break
            proposal = RescueProposal(TacticCandidate(tactic, 1.0 / len(tactics), CandidateOrigin.ANALOGICAL), predicted)
            proposals.append(proposal)
            evaluate_at = getattr(evaluator, "evaluate_at", None)
            resources = resources.use_lean()
            try:
                evaluation = evaluate_at(current, proposal.candidate) if evaluate_at else evaluator(proposal.candidate)
            except Exception as failure:
                return _Verified(tuple(proposals), tuple(attempts), successor, completed,
                                 f"{type(failure).__name__}: {failure}"), resources
            discarded = evaluation.discarded
            candidate_state = evaluation.result.state
            if candidate_state is not None and proof_state_fingerprint(candidate_state.expression) in seen:
                discarded = DiscardedSuccessor(evaluation.result, DiscardReason.REPEATED_STATE)
            attempts.append(RescueAttempt(proposal, evaluation.result, discarded, stage=stage))
            if evaluation.completed_proof is not None:
                completed = evaluation.completed_proof
                break
            if evaluation.successor is not None and discarded is None:
                successor = evaluation.successor
                break
        return _Verified(tuple(proposals), tuple(attempts), successor, completed), resources


__all__ = ["GeneratedAnalogyIteration", "GeneratedAnalogyTrace", "GeneratedDomainAnalogyLoopRunner"]
