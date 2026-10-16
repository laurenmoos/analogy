"""Compact curriculum scheduling, training identity, and reporting coverage."""
import json
from dataclasses import asdict, replace
from pathlib import Path

import torch

import analogy.training as training
from analogy.config import load_config
from analogy.grounded_curriculum import build_grounded_curriculum
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


def test_grounded_profile_uses_curriculum_and_matched_raw_ablation():
    cfg = load_config(ROOT / "config.mlx-8b-grounded-analogy.yaml")
    assert cfg.rl.curriculum_manifest is not None
    assert cfg.model.max_new_tokens >= 512
    conditions = training.build_evaluation_conditions(cfg)
    assert {item.name for item in conditions} >= {"generated_raw", "generated_unsteered", "generated_random", "generated_learned", "generated_shuffled", "analogy_loop", "conventional"}
    assert len({item.budget for item in conditions}) == 1


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
        assert set(kwargs) == {"initial_state", "iteration_sink"}
        state = kwargs["initial_state"]
        started.append((problem.id, phase, state.context))
        assert "reference_tactic" not in asdict(state)
        return training.ProblemEpisode(problem.id, False, (), (), 0, 0,
            proof_path_id=kwargs["iteration_sink"].proof_path_id)
    monkeypatch.setattr(training, "_execute_problem", execute)
    policy = torch.nn.Linear(1, 1)
    summary = training.train_direct_rescue(config, object(), verifier, policy=policy,
        optimizer=torch.optim.SGD(policy.parameters(), lr=0.1), run_id="curriculum")
    assert sorted(started) == [("train_a", "train", ()), ("train_a", "train", ("intro analogy_h",)),
        ("validation_a", "validation", ()), ("validation_a", "validation", ("intro analogy_h",))]
    episodes = [json.loads(line) for line in (summary.run_directory / "problems.jsonl").read_text().splitlines()]
    assert len({item["transition_id"] for item in episodes}) == 4
    assert all(item["proof_path_id"] for item in episodes)
    assert set(loaded_splits) == {"train", "validation"}


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
    summary = training.evaluate_direct_rescue(config, generator, verifier, checkpoint)
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
