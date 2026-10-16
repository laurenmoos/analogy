#!/usr/bin/env python3
"""Standalone matched direct-choice versus story-mediated operator ablation."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Sequence

import torch

from analogy.analogy_transfer import TransferOutputError, derive_need_analysis
from analogy.config import load_config
from analogy.grounding import GroundingOperator, enumerate_applications
from analogy.lean_gym.models import Problem
from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
from analogy.lean_gym.verifier import LeanGymAdapter, LeanSubprocessVerifier
from analogy.model_backend import load_model_backend
from analogy.proof_inventory import render_model_inventory
from analogy.prompts import PromptCatalog
from analogy.records import CandidateOrigin, TacticCandidate, TacticStatus
from analogy.story_operator import parse_story_operator_decision
from analogy.tactic_compiler import GroundedTacticCompiler


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransferOutputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_plain_operator(raw: str, enabled: tuple[GroundingOperator, ...]) -> GroundingOperator:
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
    except (TypeError, ValueError) as error:
        raise TransferOutputError(f"plain output must be strict JSON: {error}") from error
    if not isinstance(value, dict) or set(value) != {"operator"}:
        raise TransferOutputError("plain output must contain exactly operator")
    try:
        operator = GroundingOperator(value["operator"])
    except (TypeError, ValueError) as error:
        raise TransferOutputError("plain operator is unknown") from error
    if operator not in enabled:
        raise TransferOutputError("plain operator is disabled")
    return operator


def _count_tokens(generator, value):
    counter = getattr(generator, "count_tokens", None)
    if callable(counter):
        return counter(value)
    tokenizer = getattr(generator, "tokenizer", None) or getattr(generator, "_tokenizer", None)
    if tokenizer is None:
        return None
    if isinstance(value, tuple):
        rendered = tokenizer.apply_chat_template(
            [{"role": item.role, "content": item.content} for item in value],
            tokenize=False, add_generation_prompt=True, enable_thinking=False,
        )
        value = rendered
    return len(tokenizer.encode(value, add_special_tokens=False))


def _run_arm(
    name, catalog, generator, description, inventory, operators, delta, seed, hidden_layer,
):
    values = {
        "need_json": json.dumps(asdict(description), ensure_ascii=False, sort_keys=True),
        "operators_json": json.dumps([item.value for item in operators]),
    }
    if name == "story":
        values["inventory_json"] = render_model_inventory(inventory)
    messages = catalog.messages(f"ablation_{name}", **values)
    started = time.perf_counter()
    raw = generator.generate_with_delta(
        messages, delta=delta.detach(), layer=hidden_layer, seed=seed,
    )
    latency = time.perf_counter() - started
    prompt_tokens = _count_tokens(generator, messages)
    output_tokens = _count_tokens(generator, raw)
    result = {
        "raw": raw, "messages": messages,
        "model_seconds": latency,
        "prompt_characters": sum(len(item.content.encode("utf-8")) for item in messages),
        "output_characters": len(raw.encode("utf-8")),
        "prompt_tokens": prompt_tokens, "output_tokens": output_tokens,
    }
    try:
        if name == "plain":
            result.update(operator=parse_plain_operator(raw, operators), story=None)
        else:
            decision = parse_story_operator_decision(raw, inventory, operators)
            result.update(operator=decision.operator, story=decision.story)
    except Exception as error:
        result["parse_error"] = error
    return result


def benchmark(
    fixtures: Sequence[dict[str, Any]], catalog: PromptCatalog, generator: Any,
    operators: tuple[GroundingOperator, ...], latent_deltas: Sequence[torch.Tensor],
    reconstruct: Callable, verify: Callable, output_dir: Path, *, hidden_layer: int,
    base_seed: int,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for fixture_index, fixture in enumerate(fixtures):
        description, inventory, expected = reconstruct(fixture)
        for sample_index, delta in enumerate(latent_deltas):
            order = ("plain", "story") if fixture_index % 2 == 0 else ("story", "plain")
            seed = base_seed + fixture_index * len(latent_deltas) + sample_index
            for order_index, arm in enumerate(order):
                row = {
                    "fixture_id": fixture["id"], "fixture_index": fixture_index,
                    "latent_sample": sample_index, "arm": arm, "order": order_index,
                    "seed": seed, "expected_operator": expected.value if expected else None,
                    "enabled_operators": [item.value for item in operators],
                    "valid": False, "first_pick_success": False,
                    "any_candidate_success": False, "zero_applications": False,
                    "candidate_count": 0, "lean_calls": 0,
                }
                try:
                    generated = _run_arm(
                        arm, catalog, generator, description, inventory, operators,
                        delta, seed, hidden_layer,
                    )
                    row.update({
                        "story": generated.get("story"), "raw": generated["raw"],
                        "model_seconds": generated["model_seconds"],
                        "prompt_characters": generated["prompt_characters"],
                        "output_characters": generated["output_characters"],
                        "prompt_tokens": generated["prompt_tokens"],
                        "output_tokens": generated["output_tokens"],
                    })
                    if generated.get("parse_error") is not None:
                        raise generated["parse_error"]
                    row["selected_operator"] = generated["operator"].value
                    applications = enumerate_applications(generated["operator"], inventory)
                    row["zero_applications"] = not applications
                    compilation = GroundedTacticCompiler().compile_all(applications, inventory)
                    row["candidate_count"] = len(compilation.candidates)
                    lean_started = time.perf_counter()
                    outcomes = tuple(verify(fixture, compilation.candidates))
                    row["lean_seconds"] = time.perf_counter() - lean_started
                    if len(outcomes) != len(compilation.candidates):
                        raise ValueError("verifier must return one outcome per candidate")
                    row["lean_calls"] = len(outcomes)
                    row["first_pick_success"] = bool(outcomes and outcomes[0])
                    row["any_candidate_success"] = any(outcomes)
                    row["valid"] = True
                except Exception as error:
                    row["error"] = f"{type(error).__name__}: {error}"
                rows.append(row)

    with (output / "attempts.jsonl").open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    arms = {}
    for arm in ("plain", "story"):
        selected = [row for row in rows if row["arm"] == arm]
        valid = [row for row in selected if row["valid"]]
        confusion = defaultdict(Counter)
        for row in valid:
            confusion[row["expected_operator"]][row["selected_operator"]] += 1
        arms[arm] = {
            "calls": len(selected),
            "valid": len(valid),
            "invalid_outputs": len(selected) - len(valid),
            "first_pick_successes": sum(row["first_pick_success"] for row in valid),
            "any_candidate_successes": sum(row["any_candidate_success"] for row in valid),
            "zero_applications": sum(row["zero_applications"] for row in valid),
            "candidate_count": sum(row["candidate_count"] for row in valid),
            "lean_calls": sum(row["lean_calls"] for row in valid),
            "prompt_characters": sum(row.get("prompt_characters", 0) for row in selected),
            "output_characters": sum(row.get("output_characters", 0) for row in selected),
            "prompt_tokens": sum(
                row["prompt_tokens"] for row in selected if row.get("prompt_tokens") is not None
            ),
            "prompt_token_samples": sum(
                row.get("prompt_tokens") is not None for row in selected
            ),
            "output_tokens": sum(
                row["output_tokens"] for row in selected if row.get("output_tokens") is not None
            ),
            "output_token_samples": sum(
                row.get("output_tokens") is not None for row in selected
            ),
            "model_seconds": sum(row.get("model_seconds", 0.0) for row in selected),
            "lean_seconds": sum(row.get("lean_seconds", 0.0) for row in valid),
            "by_order": {
                str(order): {
                    "attempts": sum(row["order"] == order for row in selected),
                    "successes": sum(row["order"] == order and row["first_pick_success"] for row in valid),
                } for order in (0, 1)
            },
            "confusion": {str(expected): dict(counts) for expected, counts in confusion.items()},
        }
    paired = []
    keys = sorted({(row["fixture_id"], row["latent_sample"]) for row in rows})
    for fixture_id, sample in keys:
        pair = {row["arm"]: row for row in rows
                if row["fixture_id"] == fixture_id and row["latent_sample"] == sample}
        paired.append({
            "fixture_id": fixture_id, "latent_sample": sample,
            "first_pick_delta": int(pair["story"]["first_pick_success"])
            - int(pair["plain"]["first_pick_success"]),
            "any_candidate_delta": int(pair["story"]["any_candidate_success"])
            - int(pair["plain"]["any_candidate_success"]),
        })
    summary = {
        "fixtures": len(fixtures), "latent_samples": len(latent_deltas),
        "operators": [item.value for item in operators], "arms": arms, "paired": paired,
        "prompt_versions": {name: catalog.version(f"ablation_{name}") for name in ("plain", "story")},
        "prompt_catalog_hash": catalog.content_hash,
    }
    story_sets = defaultdict(set)
    for row in rows:
        if row["arm"] == "story" and row["valid"] and row.get("story"):
            story_sets[row["fixture_id"]].add(row["story"])
    story_counts = {fixture_id: len(stories) for fixture_id, stories in story_sets.items()}
    summary["story_diversity"] = {
        "unique_by_fixture": story_counts,
        "mean_unique_stories": (
            sum(story_counts.values()) / len(story_counts) if story_counts else 0.0
        ),
        "fixtures_with_multiple_stories": sum(count > 1 for count in story_counts.values()),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    call_mismatch = arms["plain"]["calls"] != arms["story"]["calls"]
    prompt_ratio = (arms["story"]["prompt_characters"]
                    / max(arms["plain"]["prompt_characters"], 1))
    output_ratio = (arms["story"]["output_characters"]
                    / max(arms["plain"]["output_characters"], 1))
    latency_ratio = (arms["story"]["model_seconds"]
                     / max(arms["plain"]["model_seconds"], 1e-12))
    candidate_ratio = (arms["story"]["candidate_count"]
                       / max(arms["plain"]["candidate_count"], 1))
    order_deltas = {
        arm: arms[arm]["by_order"]["0"]["successes"]
        - arms[arm]["by_order"]["1"]["successes"]
        for arm in ("plain", "story")
    }
    report = [
        "# Story/operator ablation", "",
        "| arm | calls | invalid | first-pick | any-candidate | prompt chars | prompt tokens | output chars | output tokens | model s | candidates | Lean calls |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ("plain", "story"):
        item = arms[arm]
        report.append(
            f"| {arm} | {item['calls']} | {item['invalid_outputs']} | {item['first_pick_successes']} | "
            f"{item['any_candidate_successes']} | {item['prompt_characters']} | "
            f"{item['prompt_tokens'] if item['prompt_token_samples'] else 'n/a'} | "
            f"{item['output_characters']} | "
            f"{item['output_tokens'] if item['output_token_samples'] else 'n/a'} | "
            f"{item['model_seconds']:.6f} | "
            f"{item['candidate_count']} | {item['lean_calls']} |"
        )
    report.extend(["", f"Call-count mismatch: {'YES' if call_mismatch else 'no'}.",
                   f"Story/plain prompt-character ratio: {prompt_ratio:.3f}.",
                   f"Story/plain output-character ratio: {output_ratio:.3f}.",
                   f"Story/plain model-latency ratio: {latency_ratio:.3f}.",
                   f"Story/plain candidate-count ratio: {candidate_ratio:.3f}.",
                   f"First-minus-second order success deltas: {order_deltas}.",
                   "Token totals are recorded when the backend exposes a tokenizer.",
                   f"Mean unique stories per fixture: {summary['story_diversity']['mean_unique_stories']:.3f}.", ""])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    return summary


class _FakeGenerator:
    hidden_size = 8

    def generate_with_delta(self, messages, **_kwargs):
        if messages[0].content.startswith("Choose one configured proof operation directly"):
            return '{"operator":"exact_local"}'
        return '{"story":"A matching spare is already available.","operator":"exact_local","bindings":{}}'


def _fixture_problem(item):
    return Problem(item["id"], item["id"], item["binders"], item["target"],
                   "StoryOperatorAblation.lean", "ablation", "ambiguous", 1)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fake-backend", action="store_true")
    parser.add_argument("--max-fixtures", type=int)
    parser.add_argument("--latent-samples", type=int)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    fixtures = json.loads(args.fixtures.read_text(encoding="utf-8"))
    if not isinstance(fixtures, list) or any(
        not isinstance(item, dict) or set(item) != {"id", "binders", "target", "accepted_prefix"}
        for item in fixtures
    ):
        raise ValueError("fixtures must be a list with exactly id, binders, target, accepted_prefix")
    if args.max_fixtures is not None:
        if args.max_fixtures <= 0:
            raise ValueError("max-fixtures must be positive")
        fixtures = fixtures[:args.max_fixtures]
    catalog = PromptCatalog.load(config.prompts.path)
    generator = _FakeGenerator() if args.fake_backend else load_model_backend(config.model)
    verifier = LeanSubprocessVerifier(LEAN_PROJECT_ROOT)
    contexts = {}

    def reconstruct(fixture):
        problem = _fixture_problem(fixture)
        adapter = LeanGymAdapter(problem, verifier)
        state = adapter.initial_state()
        for tactic in fixture["accepted_prefix"]:
            result = adapter.run_tactic(
                state, TacticCandidate(tactic, 1.0, CandidateOrigin.ORDINARY)
            )
            if result.status is not TacticStatus.ACCEPTED or result.state is None:
                raise ValueError(f"fixture {fixture['id']} has an invalid or completing prefix")
            state = result.state
        structure = adapter.proof_structure_for(state)
        inventory = adapter.local_inventory_for(state)
        analysis = derive_need_analysis(structure, inventory)
        contexts[fixture["id"]] = (adapter, state)
        return analysis.description, inventory, analysis.expected_operator

    def verify(fixture, candidates):
        adapter, state = contexts[fixture["id"]]
        return tuple(
            adapter.run_tactic(
                state, TacticCandidate(item.text, 1.0, CandidateOrigin.ANALOGICAL)
            ).status in {TacticStatus.ACCEPTED, TacticStatus.COMPLETE}
            for item in candidates
        )

    sample_count = args.latent_samples or config.rl.rollouts_per_state
    if sample_count <= 0:
        raise ValueError("latent-samples must be positive")
    deltas = tuple(
        torch.randn(generator.hidden_size, generator=torch.Generator().manual_seed(config.run.seed + index))
        for index in range(sample_count)
    )
    benchmark(
        fixtures, catalog, generator, config.analogy.grounding_operators, deltas,
        reconstruct, verify, args.output_dir, hidden_layer=config.model.hidden_layer,
        base_seed=config.run.seed,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"story operator ablation failed: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2)
