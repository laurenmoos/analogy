"""Curriculum isolation, verifier-only credit, and checkpoint compatibility."""
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
import torch

import analogy.training as training
from analogy.config import load_config
from analogy.grounded_curriculum import OnlineCurriculumState, build_grounded_curriculum
from analogy.replay import proof_state_fingerprint
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


def test_curriculum_reconstruction_checks_prefix_state_hash_before_runner():
    from test_training import problem
    state = OnlineCurriculumState("a", ("intro h",), proof_state_fingerprint(PrefixVerifier.states[1]), "train", "transition-a")
    verifier = PrefixVerifier(Path("unused"))
    reconstructed = training.reconstruct_curriculum_state(state, problem("a", "implication"), verifier)
    assert reconstructed.context == ("intro h",)
    assert reconstructed.expression == PrefixVerifier.states[1]
    with pytest.raises(ValueError, match="state hash.*saved=.*replayed="):
        training.reconstruct_curriculum_state(replace(state, current_state_hash="stale"), problem("a", "implication"), verifier)


def test_grounded_checkpoint_rejects_raw_or_other_manifest_before_loading_weights(tmp_path):
    from test_training import problem
    policy = torch.nn.Linear(1, 1)
    optimizer = torch.optim.Adam(policy.parameters())
    split = training.split_problems((problem("a", "implication"),))
    checkpoint = tmp_path / "checkpoint.pt"
    identity = {"training_schema_version": "grounded-training-v1", "curriculum_manifest_hash": "one", "decoder": "grounded"}
    training.save_training_checkpoint(checkpoint, policy, optimizer, 1, split, "config", artifact_identity=identity)
    with pytest.raises(ValueError, match="curriculum_manifest_hash.*saved='one'.*expected='two'"):
        training.load_training_checkpoint(checkpoint, policy, optimizer, split, "config",
            artifact_identity={**identity, "curriculum_manifest_hash": "two"})
    training.save_training_checkpoint(tmp_path / "raw.pt", policy, optimizer, 1, split, "config")
    with pytest.raises(ValueError, match="artifact identity.*saved=None"):
        training.load_training_checkpoint(tmp_path / "raw.pt", policy, optimizer, split, "config", artifact_identity=identity)


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


@pytest.mark.parametrize("later_failure", ["compile", "runtime", "malformed"])
def test_useful_first_action_keeps_credit_after_later_failure(later_failure):
    from test_analogy_transfer_loop import grounded_runner, grounded_payload, run, Evaluator
    from test_analogy_transfer_generation import SOURCE, ALIGNMENT, TRANSFER
    from analogy.rl import discounted_iteration_samples
    # First exact_local is accepted as a novel open state by this Lean boundary.
    first = grounded_payload()
    second = grounded_payload("h : D\n⊢ D", operator="rewrite_forward", proposition="D", index=1)
    second["steps"][0]["output_proposition"] = "D"
    transfer = dict(json.loads(TRANSFER), active_subgoal="D", predicted_next_subgoal="D")
    extra = ([SOURCE, ALIGNMENT, json.dumps(transfer), json.dumps(second)] if later_failure == "compile"
             else [SOURCE, ALIGNMENT, "invalid", "invalid"] if later_failure == "malformed" else [])
    result, backend, _ = grounded_runner(first, extra=extra)
    outcome = run(result, Evaluator())
    rollout, actions = result.drain_trajectories()[0]
    assert len(rollout.analogy_trace.iterations) == 2
    assert rollout.analogy_trace.iterations[0].training_eligible
    assert not rollout.analogy_trace.iterations[1].training_eligible
    samples = discounted_iteration_samples(rollout.analogy_trace, actions, rollout,
        verified_reward=0.1, discount=0.9, iteration_cost=0.01)
    assert len(samples) == 1 and samples[0].reward == 0.1


def test_funnel_denominators_are_attempts_at_each_stage():
    from test_analogy_transfer_loop import grounded_runner, run, GroundedEvaluator
    result, _, _ = grounded_runner()
    rollout = run(result, GroundedEvaluator()).rollouts[0]
    episode = training.ProblemEpisode("p", True, (rollout,), (), 0, 2)
    summary = training._summarize_condition("grounded", (episode,), set())
    assert summary.funnel["source"] == {"accepted": 1, "attempted": 1}
    assert summary.funnel["grounded_plan"] == {"accepted": 1, "attempted": 1}
    assert summary.funnel["compiler"] == {"accepted": 1, "attempted": 1}
    assert summary.funnel["lean_acceptance"] == {"accepted": 1, "attempted": 1}
    assert summary.funnel["second_analogy"] == {"accepted": 0, "attempted": 0}


def test_generated_sample_cannot_get_positive_credit_without_verified_novel_transition():
    from test_analogy_transfer_loop import grounded_runner, grounded_payload, run, GroundedEvaluator
    from analogy.rl import discounted_iteration_samples
    result, _, _ = grounded_runner(grounded_payload(operator="rewrite_forward"))
    run(result, GroundedEvaluator())
    rollout, actions = result.drain_trajectories()[0]
    trace = replace(rollout.analogy_trace, iterations=tuple(replace(item, training_eligible=True)
        for item in rollout.analogy_trace.iterations))
    assert discounted_iteration_samples(trace, actions, rollout, verified_reward=1,
        discount=0.9, iteration_cost=0.01) == ()


def test_condition_model_denominator_includes_ordinary_generation():
    episode = training.ProblemEpisode("ordinary", True, (), (), 2, 3)
    summary = training._summarize_condition("conventional", (episode,), set())
    assert summary.model_calls == 2
    assert summary.completion_per_model_call == 0.5


def test_funnel_counts_each_novel_successor_and_requires_a_valid_second_source():
    from test_analogy_transfer_loop import runner, outputs, run, Evaluator, state
    from analogy.records import SearchNode, TacticResult, TacticStatus
    from analogy.rescue import TacticEvaluation
    class PartialFinisher(Evaluator):
        def evaluate_at(self, current, candidate):
            if candidate.source == "assumption" and current.id == "D":
                successor = state("E")
                return TacticEvaluation(TacticResult(candidate, TacticStatus.ACCEPTED, state=successor),
                    successor=SearchNode(successor, 1.0, current.id, candidate.source))
            return super().evaluate_at(current, candidate)
    result, _, _ = runner(outputs(1))
    rollout = run(result, PartialFinisher()).rollouts[0]
    summary = training._summarize_condition("generated", (
        training.ProblemEpisode("p", False, (rollout,), (), 0, 4),), set())
    assert summary.funnel["novel_successor"] == {"accepted": 2, "attempted": 2}
    assert summary.funnel["second_analogy"] == {"accepted": 0, "attempted": 1}


def test_ordinary_generation_failure_before_expansion_is_counted(monkeypatch):
    from types import SimpleNamespace
    from analogy.records import SymbolicState
    from test_training import problem
    class Backend:
        def generate(self, messages, *, seed):
            raise RuntimeError("planned generation failure")
    adapter = SimpleNamespace(
        initial_state=lambda: SymbolicState("s", "h : P\n⊢ P", "lean4"),
        failed_tactics_for=lambda state: ())
    monkeypatch.setattr(training, "LeanGymAdapter", lambda *args: adapter)
    episode = training._execute_problem(load_config(ROOT / "config.yaml"), Backend(), object(),
        problem("p", "iff"), "conventional", None)
    assert episode.ordinary_expansions == 0
    assert "planned generation failure" in episode.failure
    assert training._summarize_condition("conventional", (episode,), set()).model_calls == 1
