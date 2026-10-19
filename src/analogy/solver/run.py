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
) -> tuple[LeanGymAdapter, SearchSession, AnalogyHarness | None]:
    """Initialize the proof, search, and optional analogy cohort."""

    proof = initialize_proof(problem, verifier)
    initial_state = proof.initial_state()
    goal_state = proof.goal_state()

    analogies = None
    analogy_fallback = None
    if with_analogies:
        analogies = initialize_analogy_harness(
            config,
            generator,
            proof,
            model_events,
            initial_state,
            goal_state,
        )
        analogy = select_analogy(score_analogies(None, analogies))
        tactic_generator = analogy.tactic_generator
        analogy_fallback = analogy.fallback
    else:
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
        analogy_fallback=analogy_fallback,
        initial_state=initial_state,
        goal_state=goal_state,
    )
    return proof, search, analogies


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
    run_state = start_run(config, checkpoint)

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
) -> SolveRunRecord:
    """Run the ordinary recipe with analogy rescue at stuck states."""

    initialize_model_protocol(config)
    run_state = start_run(config, checkpoint)

    for problem in problems:
        model_events = []
        search = None
        failure = None
        try:
            proof, search, analogies = _initialize_problem(
                config,
                problem,
                generator,
                verifier,
                model_events,
                with_analogies=True,
            )
            assert analogies is not None

            while search.has_work():
                tactics = generate_and_score_tactics(search)
                results = submit_tactics_to_lean(proof, tactics)
                update_search_with_scored_proof_states(search, results)

                if search.is_stuck():
                    analogies = update_analogies(search, analogies)
                    scored_analogies = score_analogies(search, analogies)
                    analogy = select_analogy(scored_analogies)
                    activate_analogy(search, analogy)
                    rescue = generate_analogy_rescue(search, analogy.scratchpad)
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


__all__ = ["run_analogy_assisted", "run_vanilla"]
