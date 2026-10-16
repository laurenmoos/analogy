"""Single-call story/operator loop with exhaustive deterministic verification."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable

from .analogy_loop import LoopResources, _action_record, _surprise_delta
from .analogy_transfer import AnalogyObservation, NeedAnalysis, derive_need_analysis
from .grounding import GroundedApplication, GroundingOperator, enumerate_applications
from .proof_inventory import LocalProofInventory, build_local_inventory
from .proof_views import ProofStructure, TransitionComparison
from .records import (
    CandidateOrigin, DiscardedSuccessor, DiscardReason, SearchNode, SymbolicState,
    TacticCandidate, TacticStatus,
)
from .replay import proof_state_fingerprint
from .rescue import (
    GenerationAttempt, GenerationStatus, GenericRescueOutcome, PolicyActionRecord,
    ProofRepresentation, ProofRescueContext, RescueAttempt, RescueProposal,
    RescueRollout, RescueTermination,
)
from .story_operator import (
    StoryOperatorDecision, StoryOperatorGenerationFailure, StoryOperatorGenerator,
)
from .surprise import (
    StopReason, SurpriseController, SurpriseDecision, SurpriseObservation, SurpriseState,
)
from .tactic_compiler import (
    TACTIC_COMPILER_VERSION, CompilationDiagnostic, CompiledTactic,
    GroundedTacticCompiler,
)


@dataclass(frozen=True, slots=True)
class StoryOperatorIteration:
    iteration_index: int
    state_before: SymbolicState
    observation: AnalogyObservation
    structure: ProofStructure | None
    representation: ProofRepresentation | None
    analysis: NeedAnalysis | None
    expected_operator: GroundingOperator | None
    action: PolicyActionRecord | None
    decision: StoryOperatorDecision | None
    decision_attempts: tuple[GenerationAttempt, ...]
    inventory: LocalProofInventory | None
    applications: tuple[GroundedApplication, ...]
    compiled_tactics: tuple[CompiledTactic, ...]
    compilation_diagnostics: tuple[CompilationDiagnostic, ...]
    proposals: tuple[RescueProposal, ...]
    attempts: tuple[RescueAttempt, ...]
    predicted_transition: str | None
    actual_transition: str | None
    comparison: TransitionComparison | None
    surprise_delta: float | None
    repeated_state: bool
    controller_decision: SurpriseDecision
    failed_stage: str | None
    failure_diagnostic: str | None
    model_calls: int
    lean_calls: int
    elapsed_seconds: float
    training_eligible: bool
    compiler_version: str = TACTIC_COMPILER_VERSION
    decision_schema_version: str = "story-operator-decision-v1"
    prompt_versions: tuple[tuple[str, str], ...] = ()

    @property
    def surprise(self) -> float | None:
        return None if self.comparison is None else self.comparison.surprise

    @property
    def need(self):
        return None if self.analysis is None else self.analysis.description

    @property
    def source(self):
        return None


@dataclass(frozen=True, slots=True)
class StoryOperatorTrace:
    iterations: tuple[StoryOperatorIteration, ...]
    stop_reason: StopReason
    model_calls: int = 0
    lean_calls: int = 0


# The training API historically names this trace by its strategy, not its schema.
GeneratedAnalogyTrace = StoryOperatorTrace
GeneratedAnalogyIteration = StoryOperatorIteration
GroundedAnalogyIteration = StoryOperatorIteration


@dataclass(frozen=True, slots=True)
class _VerifiedAll:
    proposals: tuple[RescueProposal, ...]
    attempts: tuple[RescueAttempt, ...]
    successor: SearchNode | None
    completed_proof: str | None
    failure: str | None = None
    cancelled: bool = False


class GeneratedDomainAnalogyLoopRunner:
    """Preserve the rescue-runner contract while using one model stage."""

    def __init__(
        self, *, structure_builder: Callable, observation_builder: Callable,
        action_sampler: Callable, decision_generator: StoryOperatorGenerator,
        transition_comparator: Callable, controller: SurpriseController,
        max_model_calls: int, max_lean_calls: int, structure_lean_cost: int = 0,
        iteration_sink: Callable | None = None, base_seed: int = 0,
        compiler: Any = None, inventory_builder: Callable = build_local_inventory,
        **obsolete: Any,
    ) -> None:
        if obsolete:
            raise ValueError(f"obsolete generated-runner arguments: {sorted(obsolete)}")
        for name, value in (("max_model_calls", max_model_calls), ("max_lean_calls", max_lean_calls)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(structure_lean_cost, bool) or not isinstance(structure_lean_cost, int) or structure_lean_cost < 0:
            raise ValueError("structure_lean_cost must be a non-negative integer")
        self.structure_builder = structure_builder
        self.observation_builder = observation_builder
        self.action_sampler = action_sampler
        self.decision_generator = decision_generator
        self.transition_comparator = transition_comparator
        self.controller = controller
        self.max_model_calls = max_model_calls
        self.max_lean_calls = max_lean_calls
        self.structure_lean_cost = structure_lean_cost
        self.iteration_sink = iteration_sink
        self.base_seed = base_seed
        self.compiler = compiler if compiler is not None else GroundedTacticCompiler()
        self.inventory_builder = inventory_builder
        self.live_actions = ()
        self._live_trajectories = []

    def drain_trajectories(self):
        trajectories = tuple(self._live_trajectories)
        self._live_trajectories.clear()
        return trajectories

    def search(self, state, goal, tactic_pool, ordinary_attempts, evaluator, cancellation):
        del tactic_pool
        current, previous, last_tactic = state, None, None
        accepted = tuple(state.context)
        rejected = tuple((item.candidate.source, item.diagnostics) for item in ordinary_attempts)
        predicted = actual = surprise = surprise_delta = None
        repeated = False
        seen = {proof_state_fingerprint(state.expression)}
        resources = LoopResources(0, 0, self.max_model_calls, self.max_lean_calls)
        controller_state = SurpriseState()
        iterations: list[StoryOperatorIteration] = []
        actions: list[Any] = []
        proposals: list[RescueProposal] = []
        attempts: list[RescueAttempt] = []
        generations: list[GenerationAttempt] = []
        latest_successor = None
        completed = None
        stop_reason = StopReason.NO_SUCCESSOR
        failure_status: GenerationStatus | None = None
        rollout_id = f"{state.id}:story-operator:{uuid.uuid4().hex}"

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
            observation = AnalogyObservation(
                goal.expression, current.expression, previous, last_tactic, accepted,
                rejected, None, None, None, predicted, actual, surprise, surprise_delta,
                repeated, index, resources.remaining_model_calls,
                resources.remaining_lean_calls,
            )
            structure = representation = analysis = action = decision = inventory = None
            decision_attempts: tuple[GenerationAttempt, ...] = ()
            applications: tuple[GroundedApplication, ...] = ()
            compiled_tactics: tuple[CompiledTactic, ...] = ()
            compilation_diagnostics: tuple[CompilationDiagnostic, ...] = ()
            local_proposals: tuple[RescueProposal, ...] = ()
            local_attempts: tuple[RescueAttempt, ...] = ()
            comparison = None
            failed_stage = failure_diagnostic = None
            infrastructure_failure = False
            successor = None
            iteration_completed = None
            resources = resources.use_lean(self.structure_lean_cost)

            try:
                structure = self.structure_builder(current)
                context = ProofRescueContext(current, goal, accepted, tuple(item for item, _ in rejected))
                representation = self.observation_builder(observation, structure, context)
                inventory = self.inventory_builder(current, structure, fixed_target=goal.expression)
                analysis = derive_need_analysis(structure, inventory)
                action = self.action_sampler(representation, index)
                actions.append(action)
                try:
                    generated = self.decision_generator.generate(
                        analysis.description, inventory, action, analogy_index=index,
                        cancellation=cancellation,
                    )
                except StoryOperatorGenerationFailure as failure:
                    decision_attempts = failure.attempts
                    resources = resources.use_model(len(failure.attempts))
                    generations.extend(failure.attempts)
                    failure_status = failure.attempts[-1].status if failure.attempts else None
                    failed_stage = "decision"
                    failure_diagnostic = str(failure)
                    raise
                decision = generated.decision
                decision_attempts = generated.attempts
                resources = resources.use_model(len(generated.attempts))
                generations.extend(generated.attempts)
                applications = enumerate_applications(decision.operator, inventory)
                if not applications:
                    failed_stage = "application"
                    failure_diagnostic = "selected operator has no valid current-state application"
                    raise ValueError(failure_diagnostic)
                compilation = self.compiler.compile_all(applications, inventory)
                compiled_tactics = compilation.candidates
                compilation_diagnostics = compilation.diagnostics
                if not compiled_tactics:
                    failed_stage = "compilation"
                    failure_diagnostic = "; ".join(item.message for item in compilation_diagnostics) or "no compiled candidates"
                    raise ValueError(failure_diagnostic)
                if resources.remaining_lean_calls <= 0:
                    stop_reason = StopReason.LEAN_BUDGET
                    raise RuntimeError("Lean budget exhausted before candidate batch")
                verified, resources = self._verify_all(
                    current, compiled_tactics, evaluator, cancellation, resources, seen,
                )
                local_proposals, local_attempts = verified.proposals, verified.attempts
                successor, iteration_completed = verified.successor, verified.completed_proof
                if verified.cancelled:
                    stop_reason = StopReason.CANCELLED
                if verified.failure is not None:
                    infrastructure_failure = True
                    failed_stage = "lean_attempt"
                    failure_diagnostic = verified.failure
                if iteration_completed is not None:
                    completed = iteration_completed
                elif successor is not None:
                    latest_successor = successor
                    previous = current.expression
                    current = successor.state
                    last_tactic = successor.tactic or next(
                        item.proposal.candidate.source for item in local_attempts
                        if item.result.status is TacticStatus.ACCEPTED and item.discarded is None
                    )
                    accepted += (last_tactic,)
                    rejected = ()
                    seen.add(proof_state_fingerprint(current.expression))
                else:
                    rejected = tuple(
                        (item.proposal.candidate.source, item.result.diagnostics)
                        for item in local_attempts if item.result.status is TacticStatus.ERROR
                    )
                predicted = (
                    next((item.predicted_next_subgoal for item in compiled_tactics
                          if any(attempt.proposal.candidate.source == item.text
                                 and attempt.result.status is TacticStatus.COMPLETE
                                 for attempt in local_attempts)), None)
                    or (compiled_tactics[0].predicted_next_subgoal if compiled_tactics else None)
                )
                actual = "no goals" if completed is not None else (
                    successor.state.expression if successor is not None else current.expression
                )
                if successor is not None and completed is None and not infrastructure_failure:
                    comparison = self.transition_comparator(previous, predicted, actual)
            except StoryOperatorGenerationFailure:
                pass
            except Exception as failure:
                if failed_stage is None:
                    infrastructure_failure = True
                    failed_stage = "runtime"
                    failure_diagnostic = f"{type(failure).__name__}: {failure}"

            surprise_delta = _surprise_delta(controller_state, comparison)
            surprise = None if comparison is None else comparison.surprise
            repeated = any(item.discarded is not None and item.discarded.reason is DiscardReason.REPEATED_STATE
                           for item in local_attempts)
            controller_decision = self.controller.decide(controller_state, SurpriseObservation(
                index, surprise, successor is not None, completed is not None,
                cancellation.is_cancelled() or stop_reason is StopReason.CANCELLED,
                False, resources.remaining_model_calls, resources.remaining_lean_calls,
            ))
            if failed_stage is not None and completed is None and successor is None:
                controller_decision = SurpriseDecision(False, stop_reason, controller_decision.state)
            controller_state = controller_decision.state
            iteration = StoryOperatorIteration(
                index, before, observation, structure, representation, analysis,
                None if analysis is None else analysis.expected_operator,
                None if action is None else replace(_action_record(action), seed=self.base_seed + index),
                decision, decision_attempts, inventory, applications, compiled_tactics,
                compilation_diagnostics, local_proposals, local_attempts, predicted, actual,
                comparison, surprise_delta, repeated, controller_decision, failed_stage,
                failure_diagnostic, resources.model_calls, resources.lean_calls,
                time.monotonic() - started,
                action is not None and decision is not None and not infrastructure_failure
                and any(item.result.status in {TacticStatus.ACCEPTED, TacticStatus.COMPLETE}
                        and item.discarded is None for item in local_attempts),
                prompt_versions=(("story_operator", self.decision_generator.prompts.version("story_operator")),),
            )
            iterations.append(iteration)
            proposals.extend(local_proposals)
            attempts.extend(local_attempts)
            if self.iteration_sink is not None:
                self.iteration_sink(rollout_id, iteration)
            stop_reason = controller_decision.reason
            if infrastructure_failure or not controller_decision.continue_loop:
                break

        trace = StoryOperatorTrace(tuple(iterations), stop_reason, resources.model_calls, resources.lean_calls)
        if stop_reason is StopReason.CANCELLED:
            termination = RescueTermination.CANCELLED
        elif completed is not None:
            termination = RescueTermination.COMPLETE
        elif latest_successor is not None:
            termination = RescueTermination.PROGRESS
        elif failure_status is GenerationStatus.RUNTIME_ERROR:
            termination = RescueTermination.GENERATION_ERROR
        elif failure_status is GenerationStatus.INVALID:
            termination = RescueTermination.INVALID_OUTPUT
        elif stop_reason in {StopReason.MODEL_BUDGET, StopReason.LEAN_BUDGET}:
            termination = RescueTermination.BUDGET_EXHAUSTED
        elif attempts and all(item.result.status is TacticStatus.ERROR for item in attempts):
            termination = RescueTermination.REJECTED
        else:
            termination = RescueTermination.EXHAUSTED
        rollout = RescueRollout(
            rollout_id, state.id, _action_record(actions[0] if actions else None),
            tuple(proposals), tuple(attempts), termination,
            generations=tuple(generations), analogy_trace=trace,
        )
        self.live_actions = tuple(actions)
        self._live_trajectories.append((rollout, self.live_actions))
        if termination is RescueTermination.CANCELLED:
            return GenericRescueOutcome(rollouts=(rollout,), cancelled=True)
        return GenericRescueOutcome(completed, None if completed else latest_successor, (rollout,))

    @staticmethod
    def _verify_all(current, candidates, evaluator, cancellation, resources, seen):
        proposals: list[RescueProposal] = []
        attempts: list[RescueAttempt] = []
        successors: list[SearchNode] = []
        completed: list[str] = []
        score = 1.0 / len(candidates)
        for item in candidates:
            if cancellation.is_cancelled():
                return _VerifiedAll(tuple(proposals), tuple(attempts),
                                    successors[0] if successors else None,
                                    completed[0] if completed else None, cancelled=True), resources
            proposal = RescueProposal(
                TacticCandidate(item.text, score, CandidateOrigin.ANALOGICAL),
                item.predicted_next_subgoal,
            )
            proposals.append(proposal)
            resources = resources.use_lean()
            try:
                evaluate_at = getattr(evaluator, "evaluate_at", None)
                evaluation = evaluate_at(current, proposal.candidate) if evaluate_at else evaluator(proposal.candidate)
            except Exception as failure:
                return _VerifiedAll(
                    tuple(proposals), tuple(attempts), successors[0] if successors else None,
                    completed[0] if completed else None,
                    f"{type(failure).__name__}: {failure}",
                ), resources
            discarded = evaluation.discarded
            candidate_state = evaluation.result.state
            if candidate_state is not None and proof_state_fingerprint(candidate_state.expression) in seen:
                discarded = DiscardedSuccessor(evaluation.result, DiscardReason.REPEATED_STATE)
            attempt = RescueAttempt(proposal, evaluation.result, discarded, stage="grounded_compiler")
            attempts.append(attempt)
            infrastructure_failure = getattr(evaluation.result, "infrastructure_failure", None)
            if infrastructure_failure is not None:
                return _VerifiedAll(
                    tuple(proposals), tuple(attempts), successors[0] if successors else None,
                    completed[0] if completed else None,
                    f"{infrastructure_failure}: {'; '.join(evaluation.result.diagnostics)}",
                ), resources
            if evaluation.completed_proof is not None:
                completed.append(evaluation.completed_proof)
            elif evaluation.successor is not None and discarded is None:
                successors.append(evaluation.successor)
        return _VerifiedAll(
            tuple(proposals), tuple(attempts), successors[0] if successors else None,
            completed[0] if completed else None,
        ), resources


__all__ = [
    "GeneratedAnalogyIteration", "GeneratedAnalogyTrace", "GeneratedDomainAnalogyLoopRunner",
    "GroundedAnalogyIteration", "StoryOperatorIteration", "StoryOperatorTrace",
]
