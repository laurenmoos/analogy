#!/usr/bin/env python3
"""Measure the call-count change and isolate deterministic pipeline timings."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch

from analogy.analogy_transfer import Arity, InformationFlow, NeutralProofNeed
from analogy.config import load_config
from analogy.grounding import enumerate_applications
from analogy.proof_inventory import InventoryEntry, InventoryKind, LocalProofInventory
from analogy.prompts import PromptCatalog
from analogy.rl import PolicyAction
from analogy.story_operator import StoryOperatorGenerator
from analogy.tactic_compiler import GroundedTacticCompiler


class _Backend:
    hidden_size = 8

    def __init__(self) -> None:
        self.calls = 0

    def generate_with_delta(self, *_args, **_kwargs) -> str:
        self.calls += 1
        return json.dumps({
            "story": "Three spare keys are available for one matching lock.",
            "operator": "exact_local",
            "bindings": {"key": "local:0"},
        })


def _inventory(index: int) -> LocalProofInventory:
    shape = '{"root":"target","children":[]}'
    return LocalProofInventory(
        f"state:{index}", "P", "P",
        tuple(InventoryEntry(
            f"local:{item}", InventoryKind.LOCAL, f"h{item}", "P", shape,
        ) for item in range(3)),
        (InventoryEntry(
            "active_subgoal", InventoryKind.ACTIVE_SUBGOAL, None, "P", shape,
        ),),
    )


def run(config_path: Path, states: int) -> dict:
    if states < 100:
        raise ValueError("states must be at least 100")
    config = load_config(config_path)
    prompts = PromptCatalog.load(config.prompts.path)
    description = NeutralProofNeed(
        Arity.ONE, Arity.ONE, (InformationFlow.PRESERVE,),
        "a needed item and matching available items", "the need fulfilled",
        ("active_subgoal", "matching_item"),
        "Available items match what is needed.",
    )
    action = PolicyAction(torch.zeros(8), torch.tensor(0.0), torch.tensor(0.0))

    old_backend = _Backend()
    old_started = time.perf_counter()
    for index in range(states):
        # The removed path made source, alignment, transfer, and grounding calls.
        for _stage in range(4):
            old_backend.generate_with_delta((), delta=action.delta, layer=-1, seed=index)
    old_model_seconds = time.perf_counter() - old_started

    backend = _Backend()
    generator = StoryOperatorGenerator(
        backend, prompts, config.analogy.grounding_operators,
        hidden_layer=config.model.hidden_layer, base_seed=config.run.seed,
    )
    compiler = GroundedTacticCompiler()
    model_seconds = enumeration_seconds = compilation_seconds = lean_seconds = 0.0
    repairs = candidates = lean_calls = 0
    started = time.perf_counter()
    for index in range(states):
        inventory = _inventory(index)
        stage = time.perf_counter()
        generated = generator.generate(description, inventory, action, analogy_index=index)
        model_seconds += time.perf_counter() - stage
        repairs += max(len(generated.attempts) - 1, 0)

        stage = time.perf_counter()
        applications = enumerate_applications(generated.decision.operator, inventory)
        enumeration_seconds += time.perf_counter() - stage

        stage = time.perf_counter()
        compiled = compiler.compile_all(applications, inventory).candidates
        compilation_seconds += time.perf_counter() - stage

        stage = time.perf_counter()
        accepted = tuple(candidate.text.startswith("exact h") for candidate in compiled)
        lean_seconds += time.perf_counter() - stage
        if not all(accepted):
            raise AssertionError("fake Lean rejected a deterministic exact candidate")
        candidates += len(compiled)
        lean_calls += len(accepted)
    wall_seconds = time.perf_counter() - started
    if backend.calls != states or repairs:
        raise AssertionError("valid production attempts must use one call and zero repairs")
    return {
        "states": states,
        "old_model_calls": old_backend.calls,
        "new_model_calls": backend.calls,
        "model_call_reduction_percent": 100 * (1 - backend.calls / old_backend.calls),
        "model_call_ratio": old_backend.calls / backend.calls,
        "repairs": repairs,
        "compiled_candidates": candidates,
        "lean_calls": lean_calls,
        "timings_seconds": {
            "recorded_old_four_call_boundary": old_model_seconds,
            "new_model": model_seconds,
            "enumeration": enumeration_seconds,
            "compilation": compilation_seconds,
            "fake_lean": lean_seconds,
            "new_pipeline_wall": wall_seconds,
        },
        "prompt_version": prompts.version("story_operator"),
        "prompt_catalog_hash": prompts.content_hash,
        "operators": [item.value for item in config.analogy.grounding_operators],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--states", type=int, default=100)
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.states), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
