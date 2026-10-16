#!/usr/bin/env python3
"""Run the real one-call production decision/grounding/compiler/Lean path."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

import torch

from analogy.analogy_transfer import derive_need_analysis
from analogy.config import load_config
from analogy.grounding import enumerate_applications
from analogy.lean_gym.models import Problem
from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
from analogy.lean_gym.verifier import LeanGymAdapter, LeanSubprocessVerifier
from analogy.model_backend import load_model_backend
from analogy.prompts import PromptCatalog
from analogy.records import CandidateOrigin, TacticCandidate, TacticStatus
from analogy.rl import PolicyAction
from analogy.story_operator import StoryOperatorGenerationFailure, StoryOperatorGenerator
from analogy.tactic_compiler import GroundedTacticCompiler


def _problem(item: dict) -> Problem:
    return Problem(
        item["id"], item["id"], item["binders"], item["target"],
        "StoryOperatorProductionSmoke.lean", "smoke", "ambiguous", 1,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--max-fixtures", type=int, default=1)
    parser.add_argument("--fixture-offset", type=int, default=0)
    args = parser.parse_args()
    if args.max_fixtures <= 0 or args.fixture_offset < 0:
        raise ValueError("max-fixtures must be positive and fixture-offset non-negative")

    config = load_config(args.config)
    fixtures = json.loads(args.fixtures.read_text(encoding="utf-8"))[
        args.fixture_offset:args.fixture_offset + args.max_fixtures
    ]
    backend = load_model_backend(config.model)
    decision_generator = StoryOperatorGenerator(
        backend, PromptCatalog.load(config.prompts.path),
        config.analogy.grounding_operators,
        hidden_layer=config.model.hidden_layer, base_seed=config.run.seed,
    )
    verifier = LeanSubprocessVerifier(LEAN_PROJECT_ROOT)
    compiler = GroundedTacticCompiler()
    rows = []
    for index, fixture in enumerate(fixtures):
        adapter = LeanGymAdapter(_problem(fixture), verifier)
        state = adapter.initial_state()
        for tactic in fixture["accepted_prefix"]:
            outcome = adapter.run_tactic(
                state, TacticCandidate(tactic, 1.0, CandidateOrigin.ORDINARY),
            )
            if outcome.status is not TacticStatus.ACCEPTED or outcome.state is None:
                raise ValueError(f"fixture {fixture['id']} prefix did not remain open")
            state = outcome.state
        structure = adapter.proof_structure_for(state)
        inventory = adapter.local_inventory_for(state)
        analysis = derive_need_analysis(structure, inventory)
        delta = torch.randn(
            backend.hidden_size, generator=torch.Generator().manual_seed(config.run.seed + index),
        )
        action = PolicyAction(delta, torch.tensor(0.0), torch.tensor(0.0))

        started = time.perf_counter()
        try:
            generated = decision_generator.generate(
                analysis.description, inventory, action, analogy_index=index,
            )
        except StoryOperatorGenerationFailure as failure:
            rows.append({
                "fixture_id": fixture["id"],
                "expected_operator": (
                    analysis.expected_operator.value if analysis.expected_operator else None
                ),
                "model_calls": len(failure.attempts),
                "model_seconds": time.perf_counter() - started,
                "raw": failure.attempts[-1].raw_text if failure.attempts else "",
                "error": str(failure.__cause__ or failure),
                "applications": 0,
                "compiled_candidates": [],
                "lean_statuses": [],
                "lean_success": False,
            })
            continue
        model_seconds = time.perf_counter() - started
        applications = enumerate_applications(generated.decision.operator, inventory)
        compiled = compiler.compile_all(applications, inventory)
        outcomes = tuple(adapter.run_tactic(
            state, TacticCandidate(candidate.text, 1.0, CandidateOrigin.ANALOGICAL),
        ) for candidate in compiled.candidates)
        rows.append({
            "fixture_id": fixture["id"],
            "expected_operator": (
                analysis.expected_operator.value if analysis.expected_operator else None
            ),
            "decision": asdict(generated.decision),
            "model_calls": len(generated.attempts),
            "model_seconds": model_seconds,
            "applications": len(applications),
            "compiled_candidates": [item.text for item in compiled.candidates],
            "lean_statuses": [item.status.value for item in outcomes],
            "lean_success": any(
                item.status in {TacticStatus.ACCEPTED, TacticStatus.COMPLETE}
                for item in outcomes
            ),
        })
    print(json.dumps({
        "prompt": "story_operator",
        "prompt_version": decision_generator.prompts.version("story_operator"),
        "fixtures": rows,
        "total_model_calls": sum(row["model_calls"] for row in rows),
        "total_lean_calls": sum(len(row["lean_statuses"]) for row in rows),
    }, ensure_ascii=False, indent=2, sort_keys=True, default=lambda value: value.value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
