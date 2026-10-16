"""Two-phase, matched-anchor evaluation of frozen activation selections.

Collection has no verifier capability. Freezing has neither verifier nor model
capability: even causal patches are captured first and replayed from immutable
evidence. Only ``run_lean_conditions`` accepts a verifier. Compute parity applies
to rescue proposal/selection work; outcome-dependent conventional continuation
is separately metered and shares the fixed total tactic-verification cap.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
from typing import Mapping, Sequence

from ..config import AppConfig
from ..inference.model_provider import CompletionActivationTrace, CompletionScore, ModelProvider
from ..inference.parser import normalize_lean4_tactic
from ..inference.prompts import configure_prompt_catalog
from ..lean_gym.models import Problem
from ..lean_gym.verifier import Verifier
from ..records import CandidateOrigin, Message, SearchStatus, SymbolicState, TacticCandidate, TacticStatus
from ..solver.diversity import DiversityObservation, FrozenEmbeddingDiversity
from ..solver.episode import EpisodeContext, run_episode
from ..solver.model_policy import TacticRanker
from ..solver.proof_views import ProofStructure
from ..solver.rescue import GenerationStatus, ProofRescueContext
from ..solver.search import AcceptNovelState, CanonicalStateFingerprinter, ObservableStucknessPolicy, WhitespaceTacticCanonicalizer
from ..transfer.domain import derive_proof_need
from .activation_anchor import replay_anchor
from .activation_generation import (
    ActivationAnalogyGenerator, CandidateGenerationFailure, GeneratedAnalogyCandidate,
    GeneratedTacticSlate, build_tactic_union, generate_neutral_slate,
    generate_prompted_slate, generate_selected_slate, generate_shuffled_slate,
    render_activation_tactic_messages, shuffle_alignment,
)
from .activation_metrics import (
    DiagonalCalibration, NeutralVector, RawComponents, ScoredCandidateMeasurement,
    clip_patch_delta, concatenate_layers, fit_leave_one_problem_out,
    measure_candidate, score_candidate, select_candidate,
)
from .activation_records import (
    ActivationCondition, ActivationEvalConfig, ActivationTrialRecord, AnchorRecord,
    ConditionOutcome,
)


ARMS = tuple(ActivationCondition)


@dataclass(frozen=True, slots=True)
class ModelCharge:
    """Measured operation/group; candidate generation can contain four calls.

    Capture totals include all backend passes. ``None`` denotes unknown usage,
    never a free or zero-cost operation.
    """

    operation: str
    processed_token_positions: int | None
    candidate_id: str | None = None
    completion: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    candidate: GeneratedAnalogyCandidate
    analogy_traces: tuple[CompletionActivationTrace, ...]
    patched_scores: tuple[CompletionScore, ...]
    without_previous_scores: tuple[CompletionScore, ...] | None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreLeanCell:
    anchor: AnchorRecord
    context: ProofRescueContext
    neutral: GeneratedTacticSlate | None
    candidates: tuple[GeneratedAnalogyCandidate | CandidateGenerationFailure, ...]
    tactic_union: tuple[str, ...]
    neutral_traces: tuple[CompletionActivationTrace, ...]
    evidence: tuple[CandidateEvidence, ...]
    charges: tuple[ModelCharge, ...]
    status: str
    diagnostics: tuple[str, ...] = ()

    @property
    def processed_token_positions(self) -> int | None:
        if any(charge.processed_token_positions is None for charge in self.charges):
            return None
        return sum(charge.processed_token_positions for charge in self.charges)


@dataclass(frozen=True, slots=True)
class PreLeanCollection:
    config: ActivationEvalConfig
    app_config: AppConfig
    cells: tuple[PreLeanCell, ...]
    layers: tuple[int, ...]
    model_revision: str


@dataclass(frozen=True, slots=True)
class FrozenCell:
    prelean: PreLeanCell
    calibration: DiagonalCalibration | None
    measurements: tuple[ScoredCandidateMeasurement, ...]
    winner: ScoredCandidateMeasurement | None
    status: str


@dataclass(frozen=True, slots=True)
class FrozenSelections:
    collection: PreLeanCollection
    cells: tuple[FrozenCell, ...]


@dataclass(frozen=True, slots=True)
class MatchedConditionOutcome(ConditionOutcome):
    budget_view: str = "equal_tactic"
    immediate_utility: str = "unavailable"
    rescue_lean_calls: int = 0
    continuation_lean_calls: int = 0
    continuation_status: str | None = None
    continuation_processed_token_positions: int | None = 0
    processed_token_positions: int | None = 0
    compute_ceiling: int | None = None
    final_tactic_cap: int = 0
    lean_call_cap: int = 0
    generation_seeds: tuple[int, ...] = ()
    topup_estimates: tuple[int, ...] = ()
    topup_admitted: tuple[bool, ...] = ()
    diversity: tuple[DiversityObservation, ...] = ()
    continuation_verifier_results: tuple[str, ...] = ()
    charges: tuple[ModelCharge, ...] = ()
    generations: tuple[GeneratedTacticSlate, ...] = ()

    @property
    def lean_calls(self) -> int:
        return self.rescue_lean_calls + self.continuation_lean_calls


@dataclass(frozen=True, slots=True)
class MatchedActivationTrialRecord(ActivationTrialRecord):
    equal_compute_outcomes: tuple[MatchedConditionOutcome, ...] = ()
    compute_ceiling: int | None = None
    status: str = "ready"
    rescue_eligible: bool = True
    selected_candidate_id: str | None = None
    calibration: DiagonalCalibration | None = None
    execution_order: tuple[ActivationCondition, ...] = ()
    prelean_charges: tuple[ModelCharge, ...] = ()
    diagnostics: tuple[str, ...] = ()
    causal_ranking_changes: tuple[tuple[str, bool | None], ...] = ()
    generation_failures: tuple[CandidateGenerationFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class ActivationEvaluationRun:
    collection: PreLeanCollection
    selections: FrozenSelections
    trials: tuple[MatchedActivationTrialRecord, ...]


def condition_order(seed: int) -> tuple[ActivationCondition, ...]:
    offset = seed % len(ARMS)
    return ARMS[offset:] + ARMS[:offset]


def generation_seed(anchor: AnchorRecord, phase: str, index: int = 0) -> int:
    """Stable namespaces independent of execution order; top-ups use odd seeds.

    Candidate generation internally consumes a contiguous block, so reserve its
    own high-bit namespace. All other calls use even/odd base/top-up namespaces.
    """
    digest = hashlib.sha256(f"{anchor.problem_id}:{anchor.seed}:{phase}:{index}".encode()).digest()
    return (int.from_bytes(digest[:4], "big") & 0x3fffffff) * 2 + int(phase.startswith("topup:"))


def _context(anchor: AnchorRecord) -> ProofRescueContext:
    # The immutable theorem header is a reference-free fixed target.
    return ProofRescueContext(anchor.current_state, SymbolicState(anchor.problem_id, anchor.theorem, "lean4"),
                              anchor.accepted_prefix, anchor.failed_tactics)


def _unknown(result: GeneratedTacticSlate | GeneratedAnalogyCandidate | CandidateGenerationFailure) -> bool:
    return any(attempt.status is GenerationStatus.RUNTIME_ERROR for attempt in result.attempts)


def _generation_charge(result, operation: str, candidate_id: str | None = None) -> ModelCharge:
    return ModelCharge(operation, None if _unknown(result) else result.processed_token_positions, candidate_id)


class _GenerationAccounting:
    """Preserve Task 3's raw attempts when post-generation accounting fails.

    Task 3 counts usage after constructing the prompt-run evidence. Returning a
    temporary zero lets that evidence reach this boundary; ``finish`` annotates
    every failed count as a runtime error before any charge or validity decision.
    Consequently the enclosing charge is always unknown, never valid zero usage.
    This adapter is used only for generation, never for admission estimates.
    """

    def __init__(self, provider):
        self.provider = provider
        self.failures = {}

    def generate(self, messages, *, seed):
        return self.provider.generate(messages, seed=seed)

    def count_token_positions(self, messages, completion):
        try:
            return self.provider.count_token_positions(messages, completion)
        except Exception as error:
            self.failures[(messages, completion)] = f"token accounting: {type(error).__name__}: {error}"
            return 0

    def finish(self, result):
        attempts, errors = [], []
        # Task 3 permits exactly one attempt per rendered stage.
        for messages, attempt in zip(result.messages, result.attempts, strict=True):
            diagnostic = self.failures.get((messages, attempt.raw_text))
            if diagnostic is not None:
                errors.append(diagnostic)
                attempt = replace(attempt, status=GenerationStatus.RUNTIME_ERROR,
                                  error_kind="token_accounting", diagnostic="; ".join(
                                      value for value in (attempt.diagnostic, diagnostic) if value))
            attempts.append(attempt)
        if not errors:
            return result
        updates = {"attempts": tuple(attempts)}
        if isinstance(result, GeneratedTacticSlate):
            updates["error"] = "; ".join(value for value in (result.error, *errors) if value)
        elif isinstance(result, CandidateGenerationFailure):
            updates["diagnostic"] = "; ".join((result.diagnostic, *errors))
        return replace(result, **updates)


def collect_prelean(
    config: ActivationEvalConfig, app_config: AppConfig, anchors: Sequence[AnchorRecord],
    generator: ModelProvider, *, proof_structures: Mapping[tuple[str, int], ProofStructure],
) -> PreLeanCollection:
    """Collect every cell before calibration, using only supplied anchor data.

    ``proof_structures`` must cover exactly the stuck anchors. Obtain these
    structures during manifest preparation, never by invoking Lean here.
    Unknown model usage excludes the entire cell from both budget comparisons.
    """
    anchors = tuple(anchors)
    expected = {(pid, seed) for pid in config.problem_ids for seed in config.seeds}
    keys = tuple((a.problem_id, a.seed) for a in anchors)
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("anchor manifest must cover every configured problem/seed exactly once")
    structures = dict(proof_structures)
    if set(structures) != {(a.problem_id, a.seed) for a in anchors if not a.no_stuck_anchor} or any(
        not isinstance(s, ProofStructure) for s in structures.values()
    ):
        raise ValueError("proof structure mapping must cover exactly the stuck anchors")
    configure_prompt_catalog(app_config.prompts.path)
    layers = tuple(generator.resolve_decoder_layers(config.activation_layer_count, through=app_config.model.hidden_layer))
    cells = []
    for anchor in anchors:
        context = _context(anchor)
        if anchor.no_stuck_anchor:
            cells.append(PreLeanCell(anchor, context, None, (), (), (), (), (), "no_stuck_anchor"))
            continue
        structure = structures[(anchor.problem_id, anchor.seed)]
        accounting = _GenerationAccounting(generator)
        service = ActivationAnalogyGenerator(accounting, config)
        neutral = accounting.finish(generate_neutral_slate(accounting, context, seed=generation_seed(anchor, "neutral"),
                                                           tactics_per_slate=config.tactics_per_slate))
        candidates = tuple(accounting.finish(candidate) for candidate in service.generate_candidates(context, structure, derive_proof_need(
            structure, context.state.expression, fixed_target=context.goal.expression),
            seed=(1 << 40) + generation_seed(anchor, "candidates") * (4 * config.analogy_candidates)))
        charges = [_generation_charge(neutral, "neutral_generation")]
        charges.extend(_generation_charge(c, "candidate_generation", c.candidate_id if isinstance(c, CandidateGenerationFailure)
                                         else c.record.candidate_id) for c in candidates)
        union = build_tactic_union(neutral, candidates, limit=config.tactic_union_limit)
        traces, evidence, diagnostics = [], [], list(accounting.failures.values())
        status = "invalid_usage" if any(c.processed_token_positions is None for c in charges) else "ready"
        if status == "ready":
            for tactic in union:
                try:
                    trace = generator.capture_completion_trace(neutral.messages[0], tactic, layers=layers)
                    charges.append(ModelCharge("neutral_capture", trace.processed_token_positions, completion=tactic))
                    traces.append(trace)
                except Exception as error:
                    charges.append(ModelCharge("neutral_capture", None, completion=tactic))
                    diagnostics.append(f"neutral capture: {type(error).__name__}: {error}")
                    status = "invalid_usage"
                    break
        if status == "ready":
            for candidate in candidates:
                if isinstance(candidate, CandidateGenerationFailure):
                    continue
                cid = candidate.record.candidate_id
                kwargs = dict(previous_state=anchor.predecessor_state.expression if anchor.predecessor_state else None,
                              last_tactic=anchor.accepted_prefix[-1] if anchor.accepted_prefix else None,
                              tactics_per_slate=config.tactics_per_slate)
                messages = render_activation_tactic_messages(context, candidate, **kwargs)
                contextual, patched, without, errors = [], [], [], []
                for tactic, baseline in zip(union, traces):
                    operation = "analogy_capture"
                    try:
                        trace = generator.capture_completion_trace(messages, tactic, layers=layers)
                        charges.append(ModelCharge(operation, trace.processed_token_positions, cid, tactic))
                        contextual.append(trace)
                        delta = clip_patch_delta(tuple(b - a for a, b in zip(
                            baseline.activations[-1].values, trace.activations[-1].values)), max_delta_norm=app_config.rl.max_delta_norm)
                        operation = "patch_score"
                        score = generator.score_completion_with_delta(neutral.messages[0], tactic, delta=delta, layer=layers[-1])
                        charges.append(ModelCharge(operation, generator.count_token_positions(neutral.messages[0], tactic), cid, tactic))
                        patched.append(score)
                        if anchor.predecessor_state is not None:
                            operation = "predecessor_ablation_score"
                            omitted = render_activation_tactic_messages(context, candidate, include_previous=False, **kwargs)
                            scores = generator.score_completions(omitted, (tactic,))
                            charges.append(ModelCharge(operation, generator.count_token_positions(omitted, tactic), cid, tactic))
                            if len(scores) != 1:
                                raise ValueError("predecessor scorer returned a mismatched slate")
                            without.append(scores[0])
                    except Exception as error:
                        charges.append(ModelCharge(operation, None, cid, tactic))
                        errors.append(f"{operation}: {type(error).__name__}: {error}")
                        status = "invalid_usage"
                        break
                evidence.append(CandidateEvidence(candidate, tuple(contextual), tuple(patched),
                                                  tuple(without) if anchor.predecessor_state else None, tuple(errors)))
                if status != "ready":
                    break
        cells.append(PreLeanCell(anchor, context, neutral, candidates, union, tuple(traces), tuple(evidence),
                                 tuple(charges), status, tuple(diagnostics)))
    return PreLeanCollection(config, app_config, tuple(cells), layers, generator.model_revision)


class _RecordedPatches:
    """A pure evidence reader satisfying Task 5's patch-scoring interface."""

    def __init__(self, scores: tuple[CompletionScore, ...]):
        self.scores = iter(scores)

    def score_completion_with_delta(self, messages, completion, *, delta, layer):
        score = next(self.scores)
        if score.text != completion:
            raise ValueError("recorded patch does not match tactic order")
        return score


def freeze_selections(collection: PreLeanCollection) -> FrozenSelections:
    """Pure cross-fit and freeze: there is no model or verifier argument."""
    config = collection.config
    usable = tuple(c for c in collection.cells if c.status == "ready")
    vectors = tuple(NeutralVector(c.anchor.problem_id, concatenate_layers(t)) for c in usable for t in c.neutral_traces)
    try:
        covariance = fit_leave_one_problem_out(vectors, (), problem_ids=config.problem_ids, variance_floor=config.variance_floor)
    except ValueError:
        covariance = {}
    def measure(cell, calibration):
        measurements = []
        if calibration is not None and cell.status == "ready":
            for evidence in cell.evidence:
                if evidence.diagnostics:
                    continue
                measurements.append(measure_candidate(
                    evidence.candidate.record.candidate_id, cell.neutral_traces, evidence.analogy_traces,
                    provider=_RecordedPatches(evidence.patched_scores), neutral_messages=cell.neutral.messages[0],
                    calibration=calibration, intervention_layer=collection.layers[-1],
                    max_delta_norm=collection.app_config.rl.max_delta_norm, weights=config.score_weights,
                    with_previous_scores=tuple(t.score for t in evidence.analogy_traces) if evidence.without_previous_scores is not None else None,
                    without_previous_scores=evidence.without_previous_scores,
                ))
        return tuple(measurements)

    # Use the target's own held-out covariance to measure its training rows.
    # Reusing each donor's independently cross-fitted displacement would let the
    # target's vectors leak indirectly through the donors' covariance estimates.
    calibrations = {}
    for target, calibration in covariance.items():
        components = tuple(
            RawComponents(c.anchor.problem_id, m.reasoning_displacement, m.causal_effect, m.previous_state_value)
            for c in usable if c.anchor.problem_id != target for m in measure(c, calibration)
        )
        calibrations[target] = fit_leave_one_problem_out(
            vectors, components, problem_ids=config.problem_ids, variance_floor=config.variance_floor,
        )[target]
    cells = []
    for cell in collection.cells:
        calibration = calibrations.get(cell.anchor.problem_id)
        measurements = measure(cell, calibration)
        scored = tuple(score_candidate(m, calibration=calibration, weights=config.score_weights) for m in measurements) if calibration else ()
        winner = select_candidate(scored, calibration=calibration, weights=config.score_weights) if calibration else None
        status = cell.status if cell.status != "ready" else "ready" if winner else "failed_selection"
        cells.append(FrozenCell(cell, calibration, scored, winner, status))
    return FrozenSelections(collection, tuple(cells))


@dataclass(frozen=True, slots=True)
class _ArmSlate:
    tactics: tuple[str, ...] = ()
    cost: int | None = 0
    seeds: tuple[int, ...] = ()
    messages: tuple[Message, ...] = ()
    estimates: tuple[int, ...] = ()
    admitted: tuple[bool, ...] = ()
    diagnostics: tuple[str, ...] = ()
    charges: tuple[ModelCharge, ...] = ()
    generations: tuple[GeneratedTacticSlate, ...] = ()


def _arm_slate(generated: GeneratedTacticSlate, *, seed: int, charges=()) -> _ArmSlate:
    charge = _generation_charge(generated, "final_generation")
    prior = sum(c.processed_token_positions for c in charges) if all(c.processed_token_positions is not None for c in charges) else None
    cost = None if prior is None or charge.processed_token_positions is None else prior + charge.processed_token_positions
    return _ArmSlate(tuple(dict.fromkeys(normalize_lean4_tactic(t.candidate.source) for t in generated.tactics)),
                     cost, (seed,), generated.messages[0], diagnostics=(generated.error,) if generated.error else (),
                     charges=(*charges, charge), generations=(generated,))


def _topup(slate: _ArmSlate, arm: ActivationCondition, cell: PreLeanCell, generator: ModelProvider,
           config: ActivationEvalConfig, app: AppConfig, ceiling: int, maximum: int) -> _ArmSlate:
    """Admit one bounded generation at a time using its exact rendered prompt."""
    result = slate
    for index in range(maximum):
        estimate = generator.count_token_positions(result.messages, "") + app.model.max_new_tokens
        if estimate <= 0:
            raise ValueError("top-up estimate must be positive")
        admitted = result.cost is not None and result.cost + estimate <= ceiling
        result = replace(result, estimates=(*result.estimates, estimate), admitted=(*result.admitted, admitted))
        if not admitted:
            break
        seed = generation_seed(cell.anchor, f"topup:{arm.value}", index)
        # Reuse attempt_index=0: the estimate and generated prompt are identical.
        generate = generate_neutral_slate if arm is ActivationCondition.NEUTRAL else generate_prompted_slate
        accounting = _GenerationAccounting(generator)
        generated = accounting.finish(generate(accounting, cell.context, seed=seed, tactics_per_slate=config.tactics_per_slate))
        extra = _arm_slate(generated, seed=seed)
        if extra.messages != result.messages:
            raise ValueError("top-up prompt changed after admission")
        cost = None if extra.cost is None else result.cost + extra.cost
        diagnostics = (*result.diagnostics, *extra.diagnostics)
        if extra.cost is not None and extra.cost > estimate:
            diagnostics += ("provider exceeded conservative generation allowance",)
            cost = None
        result = replace(result, tactics=tuple(dict.fromkeys((*result.tactics, *extra.tactics))), cost=cost,
                         seeds=(*result.seeds, seed), diagnostics=diagnostics, charges=(*result.charges, *extra.charges),
                         generations=(*result.generations, *extra.generations))
        if cost is None:
            break
    return result


class _ContinuationBudget:
    def __init__(self, lean, cap: int):
        self.lean, self.remaining = lean, cap
        self.results = []

    def is_cancelled(self):
        return self.remaining <= 0

    def run_tactic(self, state, candidate):
        if self.remaining <= 0:
            raise RuntimeError("continuation exhausted its Lean cap")
        self.remaining -= 1
        result = self.lean.run_tactic(state, candidate)
        self.results.append(result)
        return result


class _BoundedRanker:
    def __init__(self, ranker, budget):
        self.ranker, self.budget = ranker, budget

    def rank(self, state, goal):
        return self.ranker.rank(state, goal)[:self.budget.remaining]


class _GenerationMeter:
    def __init__(self, provider):
        self.provider = provider
        self.positions = 0

    def generate(self, messages, *, seed):
        try:
            output = self.provider.generate(messages, seed=seed)
            self.positions += self.provider.count_token_positions(messages, output)
            return output
        except Exception:
            self.positions = None
            raise


def _label(result) -> str:
    infrastructure = getattr(result, "infrastructure_failure", None)
    return infrastructure or ("completed" if result.status is TacticStatus.COMPLETE else
                              "accepted" if result.status is TacticStatus.ACCEPTED else "rejected")


def _ranking_change(cell: PreLeanCell, evidence: CandidateEvidence) -> bool | None:
    baseline = tuple(t.score for t in cell.neutral_traces)
    patched = evidence.patched_scores
    if not baseline or len(baseline) != len(patched) or evidence.diagnostics:
        return None
    if any(left.text != right.text or not math.isfinite(right.total_log_likelihood)
           for left, right in zip(baseline, patched)):
        return None
    rank = lambda scores: tuple(sorted(range(len(scores)), key=lambda i: -scores[i].total_log_likelihood))
    return rank(baseline) != rank(patched)


def _evaluate_arm(frozen: FrozenCell, problem: Problem, verifier: Verifier, generator: ModelProvider,
                  app: AppConfig, arm: ActivationCondition, slate: _ArmSlate, *, view: str,
                  ceiling: int | None, final_cap: int, lean_cap: int, max_expansions: int,
                  unavailable: str | None = None) -> MatchedConditionOutcome:
    cell = frozen.prelean
    base = dict(condition=arm, generated_tactics=slate.tactics, budget_view=view,
                processed_token_positions=slate.cost, compute_ceiling=ceiling, final_tactic_cap=final_cap,
                lean_call_cap=lean_cap, generation_seeds=slate.seeds, topup_estimates=slate.estimates,
                topup_admitted=slate.admitted, charges=slate.charges, generations=slate.generations)
    if unavailable is not None:
        return MatchedConditionOutcome(**base, verifier_results=(), successor_state_hashes=(), completed=False,
                                       immediate_utility=unavailable, diagnostics=slate.diagnostics)
    lean = replay_anchor(cell.anchor, problem, verifier)
    diversity = FrozenEmbeddingDiversity(generator, layer=app.analogy.transition_embedding_layer)
    fingerprint = CanonicalStateFingerprinter()
    seen = {cell.anchor.state_hash}
    results, hashes, observations, diagnostics = [], [], [], list(slate.diagnostics)

    def observe(state):
        try:
            observation = diversity.observe(state)
            if observation not in observations:
                observations.append(observation)
        except Exception as error:
            diagnostics.append(f"proof-state diversity: {type(error).__name__}: {error}")

    utility, completed, successor = "rejected", False, None
    for tactic in slate.tactics[:min(final_cap, lean_cap)]:
        result = lean.run_tactic(cell.anchor.current_state, TacticCandidate(tactic, 1.0, CandidateOrigin.ORDINARY))
        results.append(_label(result))
        diagnostics.extend(result.diagnostics)
        if result.status is TacticStatus.COMPLETE:
            utility, completed = "completed", True
            break
        if result.status is TacticStatus.ACCEPTED:
            key = fingerprint.fingerprint(result.state)
            if key not in seen:
                seen.add(key)
                successor, utility = result.state, "accepted_progressing"
                hashes.append(key)
                observe(successor)
                break
            results[-1] = "accepted_repeated"
        elif getattr(result, "infrastructure_failure", None):
            utility = "infrastructure_failure"
    continuation, continuation_calls, continuation_positions, continuation_results = None, 0, 0, ()
    if successor is not None and len(results) < lean_cap and max_expansions > 1:
        budget = _ContinuationBudget(lean, lean_cap - len(results))
        meter = _GenerationMeter(generator)
        ranker = TacticRanker(meter, top_k=app.search.top_k, seed=cell.anchor.seed, failed_tactics_for=lean.failed_tactics_for)
        try:
            output = run_episode(EpisodeContext(
                tactic_ranker=_BoundedRanker(ranker, budget), verifier=budget,
                stuckness_policy=ObservableStucknessPolicy(), state_fingerprinter=fingerprint,
                tactic_canonicalizer=WhitespaceTacticCanonicalizer(), progress_policy=AcceptNovelState(),
                cancellation=budget, ordinary_width=app.search.top_k, max_expansions=max_expansions - 1,
            ), successor, cell.context.goal)
            continuation = output.status.value
            completed = output.status is SearchStatus.COMPLETE
            # Diversity is an observation, not a change to conventional search
            # ordering. Its failure cannot prevent theorem continuation.
            for expansion in output.ordinary_expansions:
                for node in expansion.successors:
                    observe(node.state)
        except Exception as error:
            continuation = "failed"
            diagnostics.append(f"continuation: {type(error).__name__}: {error}")
        continuation_calls = len(budget.results)
        continuation_results = tuple(_label(result) for result in budget.results)
        continuation_positions = meter.positions
        for result in budget.results:
            if result.status is TacticStatus.ACCEPTED:
                key = fingerprint.fingerprint(result.state)
                if key not in hashes:
                    hashes.append(key)
    return MatchedConditionOutcome(**base, verifier_results=tuple(results), successor_state_hashes=tuple(hashes),
                                   completed=completed, diagnostics=tuple(diagnostics), immediate_utility=utility,
                                   rescue_lean_calls=len(results), continuation_lean_calls=continuation_calls,
                                   continuation_status=continuation, continuation_processed_token_positions=continuation_positions,
                                   continuation_verifier_results=continuation_results, diversity=tuple(observations))


def run_lean_conditions(
    selections: FrozenSelections, problems: Sequence[Problem], verifier: Verifier, *, generator: ModelProvider,
    lean_call_cap: int | None = None, max_topups: int = 64,
) -> tuple[MatchedActivationTrialRecord, ...]:
    """Replay frozen selections independently in both budget views.

    The Lean cap counts ``try_tactic`` calls, including continuation. Two replay
    integrity calls (initialization and prefix reconstruction) are fixed overhead
    per executed arm/view. Extra compute candidates retain stable generation order.
    """
    if not isinstance(selections, FrozenSelections):
        raise TypeError("Lean evaluation requires FrozenSelections")
    collection = selections.collection
    config, app = collection.config, collection.app_config
    provider_allowance = getattr(generator, "max_new_tokens", getattr(generator, "_max_new_tokens", app.model.max_new_tokens))
    if provider_allowance != app.model.max_new_tokens:
        raise ValueError("provider completion allowance must match the recorded model configuration")
    cap = app.analogy.max_lean_calls if lean_call_cap is None else lean_call_cap
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0 or isinstance(max_topups, bool) or not isinstance(max_topups, int) or max_topups < 0:
        raise ValueError("Lean cap must be positive and top-up count nonnegative")
    problem_map = {p.id: p for p in problems}
    if len(problem_map) != len(problems) or set(problem_map) != set(config.problem_ids):
        raise ValueError("problems must match configured problem IDs exactly")
    records = []
    for frozen in selections.cells:
        cell = frozen.prelean
        order = condition_order(cell.anchor.seed)
        baseline, compute = {}, {}
        status, ceiling = frozen.status, None
        if status not in ("no_stuck_anchor", "invalid_usage"):
            selected = next((c for c in cell.candidates if isinstance(c, GeneratedAnalogyCandidate)
                             and frozen.winner is not None and c.record.candidate_id == frozen.winner.candidate_id), None)
            for arm in order:
                seed = generation_seed(cell.anchor, arm.value)
                kwargs = dict(seed=seed, tactics_per_slate=config.tactics_per_slate)
                if arm is ActivationCondition.NEUTRAL:
                    slate = _arm_slate(cell.neutral, seed=generation_seed(cell.anchor, "neutral"))
                elif arm is ActivationCondition.PROMPTED:
                    try:
                        accounting = _GenerationAccounting(generator)
                        slate = _arm_slate(accounting.finish(generate_prompted_slate(accounting, cell.context, **kwargs)), seed=seed)
                    except Exception as error:
                        slate = _ArmSlate(cost=None, seeds=(seed,), diagnostics=(f"final generation: {type(error).__name__}: {error}",),
                                          charges=(ModelCharge("final_generation", None),))
                elif selected is None:
                    slate = _ArmSlate(diagnostics=("no scorable analogy candidate",))
                else:
                    generate = generate_selected_slate if arm is ActivationCondition.SELECTED else generate_shuffled_slate
                    if arm is ActivationCondition.SHUFFLED:
                        try:
                            shuffle_alignment(selected.alignment, problem_id=cell.context.goal.id, seed=seed)
                        except ValueError as error:
                            baseline[arm] = _ArmSlate(diagnostics=(str(error),))
                            continue
                    try:
                        accounting = _GenerationAccounting(generator)
                        generated = accounting.finish(generate(accounting, cell.context, selected,
                                             previous_state=cell.anchor.predecessor_state.expression if cell.anchor.predecessor_state else None,
                                             last_tactic=cell.anchor.accepted_prefix[-1] if cell.anchor.accepted_prefix else None, **kwargs))
                        slate = _arm_slate(generated, seed=seed, charges=cell.charges if arm is ActivationCondition.SELECTED else ())
                    except Exception as error:
                        slate = _ArmSlate(cost=None, seeds=(seed,), diagnostics=(f"final generation: {type(error).__name__}: {error}",),
                                          charges=(ModelCharge("final_generation", None),))
                baseline[arm] = slate
            if any(s.cost is None for s in baseline.values()):
                status = "invalid_usage"
            elif selected is not None:
                ceiling = baseline[ActivationCondition.SELECTED].cost
            compute = dict(baseline)
            if ceiling is not None and status != "invalid_usage":
                for arm in order:
                    if arm in (ActivationCondition.NEUTRAL, ActivationCondition.PROMPTED):
                        if compute[arm].cost > ceiling:
                            compute[arm] = replace(compute[arm], cost=None, diagnostics=(*compute[arm].diagnostics, "baseline exceeds selected compute ceiling"))
                        else:
                            compute[arm] = _topup(compute[arm], arm, cell, generator, config, app, ceiling, max_topups)
                if any(s.cost is None for s in compute.values()):
                    status = "invalid_usage"
        outcomes_by_view = []
        for view, slates in (("equal_tactic", baseline), ("equal_compute", compute)):
            outcomes = []
            for arm in order:
                unavailable = status if status in ("no_stuck_anchor", "invalid_usage") else None
                if unavailable is None and frozen.winner is None and (arm in (ActivationCondition.SELECTED, ActivationCondition.SHUFFLED) or view == "equal_compute"):
                    unavailable = "failed_selection" if arm in (ActivationCondition.SELECTED, ActivationCondition.SHUFFLED) else "compute_ceiling_unavailable"
                slate = slates.get(arm, _ArmSlate())
                if unavailable is None and arm is ActivationCondition.SHUFFLED and not slate.tactics and slate.diagnostics:
                    unavailable = "shuffled_unavailable"
                # Equal-compute may submit extra generated tactics, but never
                # exceeds the same Lean cap. Equal-tactic keeps the slate cap.
                final_cap = config.tactics_per_slate if view == "equal_tactic" else cap
                outcomes.append(_evaluate_arm(frozen, problem_map[cell.anchor.problem_id], verifier, generator, app,
                                               arm, slate, view=view, ceiling=ceiling, final_cap=final_cap,
                                               lean_cap=cap, max_expansions=app.search.max_steps, unavailable=unavailable))
            outcomes_by_view.append(tuple(outcomes))
        diagnostics = cell.diagnostics
        if any(outcome.continuation_processed_token_positions is None
               for outcomes in outcomes_by_view for outcome in outcomes):
            status = "invalid_usage"
            diagnostics += ("unknown continuation model usage; trial excluded from valid experimental evidence",)
        records.append(MatchedActivationTrialRecord(
            cell.anchor.problem_id, cell.anchor.seed, cell.anchor,
            tuple(c.record for c in cell.candidates if isinstance(c, GeneratedAnalogyCandidate)),
            frozen.measurements, outcomes_by_view[0], equal_compute_outcomes=outcomes_by_view[1], compute_ceiling=ceiling,
            status=status, rescue_eligible=status not in ("no_stuck_anchor", "invalid_usage"),
            selected_candidate_id=frozen.winner.candidate_id if frozen.winner else None,
            calibration=frozen.calibration, execution_order=order, prelean_charges=cell.charges, diagnostics=diagnostics,
            causal_ranking_changes=tuple((e.candidate.record.candidate_id, _ranking_change(cell, e)) for e in cell.evidence),
            generation_failures=tuple(c for c in cell.candidates if isinstance(c, CandidateGenerationFailure)),
        ))
    return tuple(records)


def run_activation_evaluation(
    config: ActivationEvalConfig, app_config: AppConfig, anchors: Sequence[AnchorRecord],
    generator: ModelProvider, problems: Sequence[Problem], verifier: Verifier, *,
    proof_structures: Mapping[tuple[str, int], ProofStructure], lean_call_cap: int | None = None, max_topups: int = 64,
) -> ActivationEvaluationRun:
    collection = collect_prelean(config, app_config, anchors, generator, proof_structures=proof_structures)
    selections = freeze_selections(collection)
    trials = run_lean_conditions(selections, problems, verifier, generator=generator,
                                 lean_call_cap=lean_call_cap, max_topups=max_topups)
    return ActivationEvaluationRun(collection, selections, trials)


__all__ = ["ModelCharge", "CandidateEvidence", "PreLeanCell", "PreLeanCollection", "FrozenCell", "FrozenSelections",
           "MatchedConditionOutcome", "MatchedActivationTrialRecord", "ActivationEvaluationRun", "condition_order",
           "generation_seed", "collect_prelean", "freeze_selections", "run_lean_conditions", "run_activation_evaluation"]
