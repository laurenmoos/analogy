from __future__ import annotations

from analogy.daydream import CuriosityOutcome
import json
from dataclasses import replace
from pathlib import Path

from analogy.config import load_config
from analogy.lean_gym.models import (
    Problem,
    ProofState,
    VerificationResult,
    VerificationStatus,
)
from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
from analogy.lean_gym.verifier import LeanGymAdapter, LeanSubprocessVerifier
from analogy.loop import (
    AcceptNovelState,
    LocalRescueSearch,
    NeverCancelled,
    ObservableStucknessPolicy,
    StateIdFingerprinter,
    WhitespaceTacticCanonicalizer,
)
from analogy.model import QwenTacticRanker, run_baseline
from analogy.records import BaselineStatus, SearchStatus, SymbolicState


ROOT = Path(__file__).parents[1]


class SequenceGenerator:
    def __init__(self, outputs: tuple[str, ...]) -> None:
        self.outputs = iter(outputs)
        self.prompts: list[str] = []

    def generate(self, messages, *, seed: int) -> str:
        self.prompts.append("\n".join(message.content for message in messages))
        return next(self.outputs)


class NoCuriosity:
    def search(self, *args):
        return CuriosityOutcome()


def test_ranked_policy_and_real_lean_complete_a_two_step_search() -> None:
    problem = Problem(
        "identity",
        "Identity",
        "(P : Prop)",
        "P → P",
        "fixture.lean",
        "fixture",
        "implication",
        1,
    )
    generator = SequenceGenerator(
        (
            '[{"tactic":"exact missing","score":3},'
            '{"tactic":"intro h","score":2}]',
            '[{"tactic":"exact h","score":1}]',
        )
    )
    adapter = LeanGymAdapter(problem, LeanSubprocessVerifier(LEAN_PROJECT_ROOT))
    ranker = QwenTacticRanker(
        generator,
        top_k=2,
        seed=7,
        failed_tactics_for=adapter.failed_tactics_for,
    )
    search = LocalRescueSearch(
        tactic_ranker=ranker,
        verifier=adapter,
        stuckness_policy=ObservableStucknessPolicy(),
        curiosity_search=NoCuriosity(),
        state_fingerprinter=StateIdFingerprinter(),
        tactic_canonicalizer=WhitespaceTacticCanonicalizer(),
        progress_policy=AcceptNovelState(),
        cancellation=NeverCancelled(),
        ordinary_width=2,
    )

    result = search.search(
        adapter.initial_state(),
        SymbolicState("goal", problem.target, "lean4"),
    )

    assert result.status is SearchStatus.COMPLETE
    assert result.proof is not None
    assert "intro h" in result.proof
    assert "exact h" in result.proof
    assert [attempt.candidate.source for attempt in result.ordinary_expansions[0].attempts] == [
        "exact missing",
        "intro h",
    ]
    assert "Current goals:" in generator.prompts[0]
    assert "Accepted prefix:\n(none)" in generator.prompts[0]
    assert "Failed tactics:\n(none)" in generator.prompts[0]
    assert "Accepted prefix:\nintro h" in generator.prompts[1]


class OutcomeVerifier:
    def initial_state(self, problem):
        return VerificationResult(
            VerificationStatus.OPEN,
            True,
            ProofState.from_text(f"{problem.id} initial"),
            (),
            0,
            1,
        )

    def try_tactic(self, problem, accepted_tactics, candidate):
        if problem.id == "solved":
            return VerificationResult(
                VerificationStatus.COMPLETE, True, None, (), 0, 1
            )
        if problem.id == "invalid":
            return VerificationResult(
                VerificationStatus.REJECTED, False, None, (), 1, 1
            )
        return VerificationResult(
            VerificationStatus.OPEN,
            True,
            ProofState.from_text(f"{problem.id} initial"),
            (),
            0,
            1,
        )


def _baseline_config(tmp_path: Path):
    corpus = tmp_path / "problems.json"
    corpus.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"repository": "fixture", "license": "MIT"},
                "problems": [
                    {
                        "id": problem_id,
                        "title": problem_id,
                        "binders": "(P : Prop) (h : P)",
                        "target": "P",
                        "source_path": "fixture.lean",
                        "source_level": problem_id,
                        "connective": "fixture",
                        "difficulty": difficulty,
                    }
                    for difficulty, problem_id in enumerate(
                        ("solved", "invalid", "repeated", "model_fail"), start=1
                    )
                ],
            }
        ),
        encoding="utf-8",
    )
    config = load_config(ROOT / "config.yaml")
    return replace(
        config,
        experiment=replace(config.experiment, stage="baseline"),
        benchmark=replace(config.benchmark, corpus=corpus),
        search=replace(config.search, top_k=1, max_steps=4),
        run=replace(config.run, output_dir=tmp_path / "runs"),
    )


def test_baseline_run_records_all_outcomes_and_aggregate_counts(tmp_path: Path) -> None:
    generator = SequenceGenerator(
        (
            '[{"tactic":"exact h","score":1}]',
            '[{"tactic":"exact missing","score":1}]',
            '[{"tactic":"skip","score":1}]',
            "not json",
        )
    )

    run = run_baseline(_baseline_config(tmp_path), generator, OutcomeVerifier())

    assert [result.status for result in run.results] == [
        BaselineStatus.COMPLETE,
        BaselineStatus.EXHAUSTED,
        BaselineStatus.EXHAUSTED,
        BaselineStatus.MODEL_FAILURE,
    ]
    assert run.attempted_count == 4
    assert run.solved_count == 1
    assert run.model_failure_count == 1
    assert run.invalid_tactic_count == 1
    assert run.repeated_state_count == 1
    assert run.stuck_state_count == 2
    assert run.verifier_call_count == 3

    directory = tmp_path / "runs" / "baseline" / run.run_id
    assert (directory / "run.json").is_file()
    assert len(tuple(directory.glob("*.jsonl"))) == 4
    persisted = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert persisted["solved_count"] == 1
    assert persisted["model_failure_count"] == 1
