"""Compact curriculum scheduling, training identity, and reporting coverage."""
import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import torch

import analogy.training as training
from analogy import __main__ as cli
from analogy.config import load_config
from analogy.grounded_curriculum import build_grounded_curriculum
from analogy.grounding import GroundingOperator
from analogy.records import TacticStatus
from analogy.rescue import RescueTermination
from analogy.surprise import StopReason
from test_grounded_curriculum import config as curriculum_config, PrefixVerifier

ROOT = Path(__file__).parents[1]


def test_curriculum_schedule_is_deterministic_epoch_sensitive_and_split_isolated(tmp_path):
    cfg = curriculum_config(tmp_path, ids=("a", "b", "c"))
    cfg.split_manifest.write_text(json.dumps({"schema_version": 1,
        "train": ["a", "b"], "validation": [], "test": ["c"]}))
    manifest = build_grounded_curriculum(cfg, PrefixVerifier(cfg.lean_project)).manifest_path
    first = training.schedule_curriculum_states(manifest, "train", seed=4, epoch=1)
    assert first == training.schedule_curriculum_states(manifest, "train", seed=4, epoch=1)
    assert first != training.schedule_curriculum_states(manifest, "train", seed=4, epoch=2)
    assert {item.problem_id for item in first} == {"a", "b"}
    assert len({item.transition_id for item in first}) == 4
    assert all(set(asdict(item)) == {"problem_id", "accepted_prefix", "current_state_hash",
                                   "split", "transition_id"} for item in first)


def test_grounded_profile_uses_one_call_identity_without_production_ablation_arms():
    cfg = load_config(ROOT / "config.mlx-8b-grounded-analogy.yaml")
    assert cfg.rl.curriculum_manifest is not None
    assert cfg.model.max_new_tokens >= 512
    conditions = training.build_evaluation_conditions(cfg)
    names = {item.name for item in conditions}
    assert names >= {"generated_unsteered", "generated_random", "generated_learned", "analogy_loop", "conventional"}
    assert names.isdisjoint({"generated_raw", "generated_shuffled", "ablation_plain", "ablation_story"})
    assert len({item.budget for item in conditions}) == 1
    identity = training._artifact_identity(
        replace(cfg, rl=replace(cfg.rl, curriculum_manifest=None))
    )
    assert set(identity) == {
        "training_schema_version", "curriculum_manifest_hash", "inventory_schema",
        "decision_schema", "compiler_version", "operators", "prompt_catalog_hash",
        "prompt_versions",
    }
    assert identity["prompt_versions"] == {"story_operator": "story-operator-v2"}


def test_curriculum_training_replays_prefix_and_persists_transition_without_answer(tmp_path, monkeypatch):
    cfg = curriculum_config(tmp_path, ids=("train_a", "validation_a", "heldout"))
    cfg.split_manifest.write_text(json.dumps({"schema_version": 1,
        "train": ["train_a"], "validation": ["validation_a"], "test": ["heldout"]}))
    verifier = PrefixVerifier(cfg.lean_project)
    manifest = build_grounded_curriculum(cfg, verifier).manifest_path
    config = load_config(ROOT / "config.mlx-8b-generated-analogy.yaml")
    config = replace(config, benchmark=replace(config.benchmark, corpus=cfg.problems),
        run=replace(config.run, output_dir=tmp_path / "runs"),
        rl=replace(config.rl, curriculum_manifest=manifest))
    started = []
    loaded_splits = []
    original_loader = training.load_online_curriculum_states
    def online_loader(manifest, split):
        loaded_splits.append(split)
        return original_loader(manifest, split)
    monkeypatch.setattr(training, "load_online_curriculum_states", online_loader)
    def execute(config, generator, verifier, problem, phase, policy, **kwargs):
        # Only the verified current state and past actions cross the runner boundary.
        assert set(kwargs) == {"initial_state", "iteration_sink", "sample_offset"}
        state = kwargs["initial_state"]
        started.append((problem.id, phase, state.context))
        assert "reference_tactic" not in asdict(state)
        return training.ProblemEpisode(problem.id, False, (), (), 0, 0,
            proof_path_id=kwargs["iteration_sink"].proof_path_id)
    monkeypatch.setattr(training, "_execute_problem", execute)
    policy = torch.nn.Linear(1, 1)
    progress = []
    summary = training.train_direct_rescue(config, object(), verifier, policy=policy,
        optimizer=torch.optim.SGD(policy.parameters(), lr=0.1), run_id="curriculum",
        progress=progress.append)
    assert started.count(("train_a", "train", ())) == config.rl.rollouts_per_state
    assert started.count(("train_a", "train", ("intro analogy_h",))) == config.rl.rollouts_per_state
    assert started.count(("validation_a", "validation", ())) == 1
    assert started.count(("validation_a", "validation", ("intro analogy_h",))) == 1
    episodes = [json.loads(line) for line in (summary.run_directory / "problems.jsonl").read_text().splitlines()]
    assert len({item["transition_id"] for item in episodes}) == 4
    assert all(item["proof_path_id"] for item in episodes)
    assert set(loaded_splits) == {"train", "validation"}
    readable = (summary.run_directory / "run.log").read_text()
    assert "TRAINING RUN" in readable
    assert "Epoch 1 of 1" in readable
    assert "Training state 1 of 2: train_a" in readable
    assert "Result: not solved" in readable
    assert "Story diversity: unavailable" in readable
    assert "Validation: 0 of 2 solved" in readable
    assert "Checkpoint saved:" in readable
    assert "TRAINING COMPLETE" in readable
    assert progress == readable.splitlines()


def test_curriculum_paired_outcomes_keep_opposite_results_on_distinct_prefixes(tmp_path, monkeypatch):
    cfg = curriculum_config(tmp_path, ids=("heldout",))
    cfg.split_manifest.write_text(json.dumps({"schema_version": 1,
        "train": [], "validation": [], "test": ["heldout"]}))
    verifier = PrefixVerifier(cfg.lean_project)
    manifest = build_grounded_curriculum(cfg, verifier).manifest_path
    base = load_config(ROOT / "config.yaml")
    config = replace(base, benchmark=replace(base.benchmark, corpus=cfg.problems),
        run=replace(base.run, output_dir=tmp_path / "runs"),
        rl=replace(base.rl, enabled=True, curriculum_manifest=manifest))
    generator = type("Generator", (), {"hidden_size": 2})()
    policy = training._new_policy(config, generator)
    split = training._curriculum_problem_split(manifest, training.load_problems(cfg.problems))
    checkpoint = tmp_path / "epoch.pt"
    torch.save({"config_hash": training._config_hash(config), "split": asdict(split),
        "artifact_identity": training._artifact_identity(config),
        "policy_schema_version": policy.schema_version, "policy": policy.state_dict()}, checkpoint)
    conditions = training.build_evaluation_conditions(config)[:2]
    monkeypatch.setattr(training, "build_evaluation_conditions", lambda config: conditions)

    def execute(config, generator, verifier, problem, phase, policy, **kwargs):
        # Baseline solves only the initial state; direct rescue solves only its successor.
        completed = bool(kwargs["initial_state"].context) == (phase != "conventional")
        return training.ProblemEpisode(problem.id, completed, (), (), 0, 1)

    monkeypatch.setattr(training, "_execute_problem", execute)
    progress = []
    summary = training.evaluate_direct_rescue(
        config, generator, verifier, checkpoint, progress=progress.append,
    )
    baseline, direct = summary.condition_summaries
    states = training.load_online_curriculum_states(manifest, "test")
    initial_id = next(item.transition_id for item in states if not item.accepted_prefix)
    successor_id = next(item.transition_id for item in states if item.accepted_prefix)
    assert baseline.solved_ids == (initial_id,)
    assert baseline.newly_solved_ids == baseline.regressed_ids == ()
    assert direct.solved_ids == direct.newly_solved_ids == (successor_id,)
    assert direct.regressed_ids == (initial_id,)
    assert [(item.solved, item.attempted) for item in summary.condition_summaries] == [(1, 2), (1, 2)]
    assert direct.funnel["completion"] == {"accepted": 1, "attempted": 2}
    readable = (summary.run_directory / "run.log").read_text()
    assert "INFERENCE RUN" in readable
    assert f"Condition 1 of {len(conditions)}: {conditions[0].name}" in readable
    assert "Problem 1 of 2: heldout" in readable
    assert "Condition result: 1 of 2 solved" in readable
    assert "INFERENCE COMPLETE" in readable
    assert progress == readable.splitlines()


def test_episode_log_explains_story_operation_candidate_lean_result_reward_and_cost():
    decision = SimpleNamespace(
        story="A spare key already fits the lock.",
        operator=GroundingOperator.EXACT_LOCAL,
    )
    candidate = SimpleNamespace(text="exact h")
    attempt = SimpleNamespace(
        proposal=SimpleNamespace(candidate=SimpleNamespace(source="exact h")),
        result=SimpleNamespace(status=TacticStatus.COMPLETE),
    )
    iteration = SimpleNamespace(
        decision=decision,
        compiled_tactics=(candidate,),
        attempts=(attempt,),
        failed_stage=None,
        failure_diagnostic=None,
    )
    rollout = SimpleNamespace(
        analogy_trace=SimpleNamespace(iterations=(iteration,)),
        reward=1.0,
    )
    episode = training.ProblemEpisode(
        "easy", True, (rollout,), (), 0, 2,
        proof_trace={"ordinary_expansions": [{"attempts": [{
            "candidate": {"source": "assumption"}, "status": TacticStatus.ERROR,
        }]}]},
        model_calls=1,
    )

    text = "\n".join(training._readable_episode_lines(episode))

    assert "Result: SOLVED" in text
    assert "Ordinary Lean: assumption -> error" in text
    assert "Story: A spare key already fits the lock." in text
    assert "Operation: exact_local" in text
    assert "Candidate: exact h" in text
    assert "Lean: exact h -> complete" in text
    assert "Reward: +1.000" in text
    assert "Cost: 1 model call; 2 Lean checks" in text


def test_episode_log_explains_direct_rescue_lean_result_without_analogy_trace():
    attempt = SimpleNamespace(
        proposal=SimpleNamespace(candidate=SimpleNamespace(source="apply h")),
        result=SimpleNamespace(status=TacticStatus.ACCEPTED),
    )
    rollout = SimpleNamespace(analogy_trace=None, attempts=(attempt,), reward=0.1)
    episode = training.ProblemEpisode(
        "direct", False, (rollout,), (), 0, 1, model_calls=1,
    )

    text = "\n".join(training._readable_episode_lines(episode))

    assert "Lean: apply h -> accepted" in text
    assert "Reward: +0.100" in text


def test_episode_log_handles_legacy_analogy_and_explains_unsolved_stop_reason():
    iteration = SimpleNamespace(
        decision=SimpleNamespace(reason=StopReason.NO_SUCCESSOR),
        hop=SimpleNamespace(situation="A bridge has one missing plank."),
        tactic_plan=SimpleNamespace(tactics=("cases h",)),
        attempts=(),
    )
    trace = SimpleNamespace(
        iterations=(iteration,), stop_reason=StopReason.NO_SUCCESSOR, model_calls=2,
    )
    rollout = SimpleNamespace(
        analogy_trace=trace,
        termination=RescueTermination.EXHAUSTED,
        reward=-0.1,
    )
    episode = training.ProblemEpisode("legacy", False, (rollout,), (), 0, 0)

    text = "\n".join(training._readable_episode_lines(episode))

    assert "Analogy: A bridge has one missing plank." in text
    assert "Candidate: cases h" in text
    assert "Stopped: no successor" in text
    assert "Cost: 2 model calls; 0 Lean checks" in text


def test_episode_log_explains_ordinary_search_exhaustion_without_rollout():
    episode = training.ProblemEpisode(
        "ordinary", False, (), (), 1, 1,
        proof_trace={"ordinary_expansions": [], "termination": "exhausted"},
        model_calls=1,
    )

    text = "\n".join(training._readable_episode_lines(episode))

    assert "Stopped: exhausted" in text


def test_training_cli_prints_only_the_same_readable_log_saved_by_runner(monkeypatch, capsys):
    config = load_config(ROOT / "config.mlx-8b-grounded-analogy.yaml")
    monkeypatch.setattr(cli, "load_config", lambda _path: config)
    monkeypatch.setattr(cli, "configure_prompt_catalog", lambda _path: None)
    monkeypatch.setattr(cli, "load_model_backend", lambda _config: object())
    monkeypatch.setattr(cli, "LeanSubprocessVerifier", lambda _root: object())

    def train(_config, _generator, _verifier, *, progress):
        progress("TRAINING RUN")
        progress("TRAINING COMPLETE")
        return SimpleNamespace(epochs_completed=1, best_checkpoint=Path("epoch.pt"))

    monkeypatch.setattr(cli, "train_direct_rescue", train)

    assert cli.main(["--config", "ignored.yaml"]) == 0
    assert capsys.readouterr().out == "TRAINING RUN\nTRAINING COMPLETE\n"


def test_legacy_paired_outcomes_fall_back_to_problem_identity():
    episode = training.ProblemEpisode("new-theorem", True, (), (), 0, 2)
    summary = training._summarize_condition("direct", (episode,), {"baseline-theorem"})
    assert summary.solved_ids == summary.newly_solved_ids == ("new-theorem",)
    assert summary.regressed_ids == ("baseline-theorem",)
    assert (summary.solved, summary.attempted, summary.completion_per_lean_call) == (1, 1, 0.5)


def test_condition_model_denominator_includes_ordinary_generation():
    episode = training.ProblemEpisode("ordinary", True, (), (), 2, 3)
    summary = training._summarize_condition("conventional", (episode,), set())
    assert summary.model_calls == 2
    assert summary.completion_per_model_call == 0.5
