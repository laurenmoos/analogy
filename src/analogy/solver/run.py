"""Visible recipes for vanilla and analogy-assisted proof-search runs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..config import AppConfig
from ..lean_gym.models import Problem
from ..lean_gym.verifier import Verifier
from ..model.model_provider import TextGenerator
from ..utils.checkpoint import Checkpoint
from .analogy.harness import (
    AnalogyHarness,
    AnalogyHarnessInitializationError,
    AnalogyStoryInitializationError,
    activate_analogy,
    initialize_analogy_harness,
    score_analogies,
    select_analogy,
    update_analogies,
)
from .analogy.rescue import (
    generate_analogy_rescue,
    submit_analogy_rescue_to_lean,
)
from .analogy.structural_scoring import build_proof_belief
from .deliberation import (
    activate_deliberation,
    deliberation_context,
    initialize_deliberation_fallback,
)
from .proof.state import proof_state_fingerprint
from .proof.adapter import LeanGymAdapter, initialize_proof
from .report import (
    SolveRunRecord,
    build_problem_record,
    build_run_record,
    start_run,
    write_problem_record,
    write_run_record,
)
from .search import (
    SearchSession,
    generate_and_score_tactics,
    initialize_model_protocol,
    initialize_search,
    initialize_tactic_generator,
    submit_tactics_to_lean,
    update_search_with_scored_proof_states,
)


def _initialize_problem(
    config: AppConfig,
    problem: Problem,
    generator: TextGenerator,
    verifier: Verifier,
    model_events: list[dict[str, Any]],
    *,
    with_analogies: bool,
    analogy_generator: TextGenerator | None = None,
) -> tuple[LeanGymAdapter, SearchSession, AnalogyHarness | None]:
    """Initialize the proof, search, and optional analogy cohort."""

    proof = initialize_proof(problem, verifier)
    initial_state = proof.initial_state()
    goal_state = proof.goal_state()

    tactic_generator = initialize_tactic_generator(
        config,
        generator,
        proof,
        model_events,
    )

    search = initialize_search(
        config,
        generator,
        proof,
        tactic_generator,
        analogy_fallback=None,
        analogy_recovery_enabled=with_analogies,
        initial_state=initial_state,
        goal_state=goal_state,
    )
    return proof, search, None


def run_vanilla(
    config: AppConfig,
    problems: Sequence[Problem],
    generator: TextGenerator,
    verifier: Verifier,
    *,
    checkpoint: Checkpoint | None = None,
) -> SolveRunRecord:
    """Run the ordinary tactic-generation and Lean search recipe."""

    initialize_model_protocol(config)
    run_state = start_run(
        config,
        checkpoint,
        model_revision=getattr(generator, "model_revision", None),
    )

    for problem in problems:
        model_events = []
        search = None
        failure = None
        try:
            proof, search, _ = _initialize_problem(
                config,
                problem,
                generator,
                verifier,
                model_events,
                with_analogies=False,
            )

            while search.has_work():
                tactics = generate_and_score_tactics(search)
                results = submit_tactics_to_lean(proof, tactics)
                update_search_with_scored_proof_states(search, results)
        except Exception as error:
            failure = error

        record = build_problem_record(problem, search, model_events, failure)
        write_problem_record(run_state, record)

    run = build_run_record(
        run_state, label="analogy-off", analogy_enabled=False
    )
    write_run_record(run_state, run)
    return run


def run_analogy_assisted(
    config: AppConfig,
    problems: Sequence[Problem],
    generator: TextGenerator,
    verifier: Verifier,
    *,
    checkpoint: Checkpoint | None = None,
    analogy_generator: TextGenerator | None = None,
) -> SolveRunRecord:
    """Run the ordinary recipe with analogy rescue at stuck states."""

    initialize_model_protocol(config)
    active_analogy_generator = analogy_generator or generator
    run_state = start_run(
        config,
        checkpoint,
        model_revision=getattr(generator, "model_revision", None),
        analogy_model_revision=getattr(
            active_analogy_generator, "model_revision", None
        ),
    )

    for problem in problems:
        model_events = []
        failed_initializations: set[str] = set()
        search = None
        analogies: AnalogyHarness | None = None
        failure = None
        try:
            proof, search, _ = _initialize_problem(
                config,
                problem,
                generator,
                verifier,
                model_events,
                with_analogies=True,
                analogy_generator=active_analogy_generator,
            )
            while search.has_work():
                tactics = generate_and_score_tactics(search)
                results = submit_tactics_to_lean(proof, tactics)
                update_search_with_scored_proof_states(search, results)

                if search.is_stuck():
                    rescue_node = search.rescue_node
                    if rescue_node is None:
                        raise RuntimeError("stuck search lacks a rescue node")
                    fingerprinter = getattr(
                        getattr(search, "context", None), "state_fingerprinter", None
                    )
                    fingerprint = (
                        fingerprinter.fingerprint(rescue_node.state)
                        if fingerprinter is not None
                        else proof_state_fingerprint(rescue_node.state.expression)
                    )
                    if fingerprint in failed_initializations:
                        model_events.append({
                            "kind": "analogy_initialization_cache_hit",
                            "state_id": rescue_node.state.id,
                            "fingerprint": fingerprint,
                        })
                        search.abandon_pending_rescue()
                        continue
                    try:
                        if analogies is None:
                            analogies = initialize_analogy_harness(
                                config,
                                generator,
                                proof,
                                model_events,
                                rescue_node.state,
                                search.goal,
                                proof_belief=build_proof_belief(search),
                                analogy_generator=analogy_generator or generator,
                            )
                            selected = _select_initial_analogy(
                                search,
                                analogies,
                                proof,
                                rescue_node.state,
                                model_events,
                            )
                            analogies = AnalogyHarness(
                                (selected,),
                                analogies.scorer,
                                analogies.prepared_recoveries,
                            )
                        analogies = update_analogies(search, analogies)
                    except AnalogyStoryInitializationError:
                        failed_initializations.add(fingerprint)
                        search.abandon_pending_rescue()
                        continue
                    except AnalogyHarnessInitializationError:
                        search.abandon_pending_rescue()
                        continue
                    analogy = analogies.tracks[0]
                    activate_analogy(search, analogy)
                    rescue = generate_analogy_rescue(
                        search,
                        analogy.scratchpad,
                    )
                    result = submit_analogy_rescue_to_lean(proof, rescue)
                    update_search_with_scored_proof_states(search, result)
        except Exception as error:
            failure = error

        record = build_problem_record(problem, search, model_events, failure)
        write_problem_record(run_state, record)

    run = build_run_record(
        run_state, label="analogy-on", analogy_enabled=True
    )
    write_run_record(run_state, run)
    return run


def _select_initial_analogy(
    search: SearchSession,
    analogies: AnalogyHarness,
    proof: LeanGymAdapter,
    state: Any,
    model_events: list[dict[str, Any]],
) -> Any:
    """Rank the initial cohort and evaluate unused tactics off the live path."""

    scored = score_analogies(search, analogies)
    selected = select_analogy(scored)
    model_events.append({
        "kind": "analogy_initial_selection",
        "state_id": state.id,
        "selected_track_id": selected.id,
        "scores": [
            {
                "track_id": item.track.id,
                "score": item.score,
                "tactic": item.track.initial_proposal.candidate.source,
            }
            for item in scored
        ],
    })
    for item in scored:
        if item.track is selected:
            continue
        proposal = item.track.initial_proposal
        result = proof.verifier.try_tactic(
            proof.problem,
            state.context,
            proposal.candidate.source,
        )
        model_events.append({
            "kind": "analogy_initial_counterfactual",
            "state_id": state.id,
            "track_id": item.track.id,
            "score": item.score,
            "tactic": proposal.candidate.source,
            "predicted_state": proposal.predicted_state,
            "verifier_status": result.status.value,
            "accepted": result.accepted,
            "duration_ms": result.duration_ms,
            "diagnostics": [
                diagnostic.to_dict() for diagnostic in result.diagnostics
            ],
        })
    return selected


def run_deliberation_assisted(
    config: AppConfig,
    problems: Sequence[Problem],
    generator: TextGenerator,
    verifier: Verifier,
    *,
    checkpoint: Checkpoint | None = None,
) -> SolveRunRecord:
    """Run ordinary search with a call-budget-matched direct fallback."""

    initialize_model_protocol(config)
    run_state = start_run(
        config,
        checkpoint,
        model_revision=getattr(generator, "model_revision", None),
        analogy_model_revision=getattr(generator, "model_revision", None),
    )
    for problem in problems:
        model_events = []
        failed_initializations: set[str] = set()
        search = None
        fallback = None
        failure = None
        try:
            proof, search, _ = _initialize_problem(
                config,
                problem,
                generator,
                verifier,
                model_events,
                with_analogies=True,
            )
            while search.has_work():
                tactics = generate_and_score_tactics(search)
                results = submit_tactics_to_lean(proof, tactics)
                update_search_with_scored_proof_states(search, results)
                if search.is_stuck():
                    rescue_node = search.rescue_node
                    ordinary = search.rescue_ordinary
                    if rescue_node is None or ordinary is None:
                        raise RuntimeError("stuck search lacks a rescue state")
                    fingerprinter = getattr(search.context, "state_fingerprinter", None)
                    fingerprint = (
                        fingerprinter.fingerprint(rescue_node.state)
                        if fingerprinter is not None
                        else proof_state_fingerprint(rescue_node.state.expression)
                    )
                    if fingerprint in failed_initializations:
                        search.abandon_pending_rescue()
                        continue
                    if fallback is None:
                        semantic_guidance, dependency_table = deliberation_context(
                            search
                        )
                        try:
                            fallback = initialize_deliberation_fallback(
                                config,
                                generator,
                                model_events,
                                rescue_node.state,
                                search.goal,
                                ordinary.attempts,
                                semantic_guidance,
                                dependency_table,
                            )
                        except Exception:
                            failed_initializations.add(fingerprint)
                            search.abandon_pending_rescue()
                            continue
                    activate_deliberation(search, fallback)
                    rescue = generate_analogy_rescue(
                        search, fallback.scratchpad
                    )
                    result = submit_analogy_rescue_to_lean(proof, rescue)
                    update_search_with_scored_proof_states(search, result)
        except Exception as error:
            failure = error
        record = build_problem_record(problem, search, model_events, failure)
        write_problem_record(run_state, record)
    run = build_run_record(
        run_state, label="deliberation", analogy_enabled=False
    )
    write_run_record(run_state, run)
    return run


__all__ = [
    "run_analogy_assisted",
    "run_deliberation_assisted",
    "run_vanilla",
]
