from __future__ import annotations

import json
import random
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from analogy.config import load_config
from analogy.analogy_loop import AnalogyLoopTrace
from analogy.analogy_stream import AnalogyHop
from analogy.lean_gym.models import Problem
from analogy.records import (
    CandidateOrigin,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)
from analogy.rescue import (
    GenerationAttempt,
    GenerationStatus,
    PolicyActionRecord,
    RescueAttempt,
    RescueProposal,
    RescueRollout,
    RescueTermination,
)
from analogy.rl import PolicyAction, TrainingSample
from analogy.training import (
    ProblemEpisode,
    RescueRunWriter,
    build_evaluation_conditions,
    evaluate_direct_rescue,
    group_sibling_samples,
    load_training_checkpoint,
    save_training_checkpoint,
    split_problems,
    train_direct_rescue,
    rollout_is_trainable,
    shuffle_analogy_assignments,
    _summarize_condition,
)
from analogy.surprise import StopReason
from analogy.replay import StuckStateRecord, proof_state_fingerprint


ROOT = Path(__file__).parents[1]


def problem(identifier, connective, difficulty=1):
    return Problem(identifier, identifier, "(P : Prop)", "P → P", "x", "x", connective, difficulty)


def test_structural_split_is_stable_disjoint_and_complete() -> None:
    problems = (
        problem("iff", "iff"),
        problem("or", "or"),
        problem("not", "not"),
        problem("imp", "implication"),
        problem("and", "and"),
    )

    first = split_problems(problems)
    second = split_problems(tuple(reversed(problems)))

    assert first == second
    assert [item.id for item in first.train] == ["and", "imp", "or"]
    assert [item.id for item in first.validation] == ["not"]
    assert [item.id for item in first.test] == ["iff"]
    assert {item.id for item in first.train + first.validation + first.test} == {
        item.id for item in problems
    }


def test_split_rejects_unassigned_or_overlapping_connectives() -> None:
    with pytest.raises(ValueError, match="unassigned"):
        split_problems((problem("xor", "xor"),))
    with pytest.raises(ValueError, match="disjoint"):
        split_problems(
            (problem("or", "or"),),
            train_connectives=("or",),
            validation_connectives=("or",),
            test_connectives=("iff",),
        )


def sample(state_id, reward):
    selected = TacticCandidate("simp", 1.0, CandidateOrigin.ANALOGICAL)
    proposal = RescueProposal(selected, "goal")
    result = TacticResult(selected, TacticStatus.ERROR)
    rollout = RescueRollout(
        f"r-{state_id}-{reward}",
        state_id,
        PolicyActionRecord(1, 0.0, 0.0, 0.0),
        (proposal,),
        (RescueAttempt(proposal, result),),
        RescueTermination.REJECTED,
    )
    theta = torch.nn.Parameter(torch.tensor(0.0))
    return TrainingSample(
        rollout,
        PolicyAction(torch.zeros(1), theta, theta * 0.0),
        reward,
    )


def test_training_samples_are_grouped_only_by_sibling_stuck_state() -> None:
    groups = group_sibling_samples((sample("a", 1.0), sample("b", 2.0), sample("a", 3.0)))

    assert [[item.reward for item in group] for group in groups] == [[1.0, 3.0], [2.0]]


@pytest.mark.parametrize(
    ("termination", "expected"),
    (
        (RescueTermination.COMPLETE, True),
        (RescueTermination.PROGRESS, True),
        (RescueTermination.REJECTED, True),
        (RescueTermination.EXHAUSTED, True),
        (RescueTermination.BUDGET_EXHAUSTED, True),
        (RescueTermination.CANCELLED, False),
        (RescueTermination.GENERATION_ERROR, False),
        (RescueTermination.INVALID_OUTPUT, True),
    ),
)
def test_trainable_rollouts_include_valid_zero_reward_outcomes(
    termination, expected
) -> None:
    assert rollout_is_trainable(termination) is expected


def test_run_writer_is_non_overwriting_and_flushes_jsonl(tmp_path: Path) -> None:
    writer = RescueRunWriter.create(tmp_path, "fixed-run", {"seed": 7})
    writer.append_jsonl("rollouts.jsonl", {"id": 1})
    writer.append_jsonl("rollouts.jsonl", {"id": 2})

    assert [json.loads(line)["id"] for line in (writer.path / "rollouts.jsonl").read_text().splitlines()] == [1, 2]
    assert (writer.path / "generations.jsonl").exists()
    assert (writer.path / "analogy_iterations.jsonl").exists()
    with pytest.raises(FileExistsError):
        RescueRunWriter.create(tmp_path, "fixed-run", {"seed": 7})


def test_evaluation_validates_checkpoint_before_creating_run_directory(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (ROOT / "config.yaml")
        .read_text()
        .replace("output_dir: runs", f"output_dir: {tmp_path / 'runs'}")
        .replace(
            "corpus: src/analogy/lean_gym/corpus/problems.json",
            f"corpus: {ROOT / 'src/analogy/lean_gym/corpus/problems.json'}",
        )
        .replace("rl:\n  enabled: false", "rl:\n  enabled: true"),
        encoding="utf-8",
    )
    config = load_config(config_path)
    checkpoint = tmp_path / "bad.pt"
    torch.save({"config_hash": "not-this-config"}, checkpoint)
    generator = type("Generator", (), {"hidden_size": 2})()

    with pytest.raises(ValueError, match="config hash"):
        evaluate_direct_rescue(config, generator, object(), checkpoint)

    assert not (config.run.output_dir / "rescue" / "evaluate-bad").exists()


def test_evaluation_run_id_is_not_overwritten_by_shuffled_problem_ids(
    tmp_path: Path, monkeypatch
) -> None:
    import analogy.training as training

    base = load_config(ROOT / "config.yaml")
    checkpoint = tmp_path / "epoch-0001.pt"
    config = replace(
        base,
        experiment=replace(base.experiment, stage="evaluate_rescue"),
        analogy=replace(base.analogy, enabled=True),
        rl=replace(base.rl, enabled=True, checkpoint=checkpoint),
        run=replace(base.run, output_dir=tmp_path / "runs"),
    )
    problems = (problem("iff-a", "iff"), problem("iff-b", "iff"))
    split = split_problems(
        problems,
        train_connectives=config.rl.train_connectives,
        validation_connectives=config.rl.validation_connectives,
        test_connectives=config.rl.test_connectives,
    )
    generator = type("Generator", (), {"hidden_size": 2})()
    policy = training._new_policy(config, generator)
    torch.save(
        {
            "config_hash": training._config_hash(config),
            "split": asdict(split),
            "policy_schema_version": policy.schema_version,
            "policy": policy.state_dict(),
        },
        checkpoint,
    )
    hop = AnalogyHop(
        "hop-0", None, "bridge", "cross a gap", ("supports connect",),
        (("bridge", "implication"),), "cross", "reach target", "same shape", False,
    )

    def execute(config, generator, verifier, item, phase, policy, **kwargs):
        del config, generator, verifier, policy, kwargs
        if phase != "analogy_loop":
            return ProblemEpisode(item.id, False, (), (), 0, 0)
        state_before = SymbolicState(item.id, f"⊢ {item.target}", "lean4")
        iteration = SimpleNamespace(
            hop=hop,
            state_before=state_before,
            attempts=(),
            actual_transition=None,
            repair_generation_attempts=(),
            repair_succeeded=False,
            surprise=None,
        )
        trace = AnalogyLoopTrace((iteration,), StopReason.NO_SUCCESSOR)
        rollout = RescueRollout(
            f"r-{item.id}", item.id, PolicyActionRecord(0, 0.0, 0.0, 0.0),
            (), (), RescueTermination.EXHAUSTED, analogy_trace=trace,
        )
        return ProblemEpisode(item.id, False, (rollout,), (), 0, 0)

    monkeypatch.setattr(training, "load_problems", lambda path: problems)
    monkeypatch.setattr(training, "_execute_problem", execute)
    monkeypatch.setattr(training, "_persist_episode", lambda *args: None)

    summary = evaluate_direct_rescue(config, generator, object(), checkpoint)

    assert summary.run_id == "evaluate-epoch-0001"
    persisted = json.loads((summary.run_directory / "summary.json").read_text())
    assert persisted["run_id"] == "evaluate-epoch-0001"


def test_checkpoint_round_trip_restores_rng_optimizer_and_policy(tmp_path: Path) -> None:
    from analogy.rl import RewardBaseline

    policy = torch.nn.Linear(1, 1)
    optimizer = torch.optim.Adam(policy.parameters(), lr=0.01)
    loss = policy(torch.ones((1, 1))).sum()
    loss.backward()
    optimizer.step()
    split = split_problems((problem("imp", "implication"), problem("not", "not"), problem("iff", "iff")))
    path = tmp_path / "epoch.pt"
    random.seed(9)
    torch.manual_seed(9)

    baseline = RewardBaseline(decay=0.5)
    baseline.update("train", 0.75)
    save_training_checkpoint(
        path, policy, optimizer, 1, split, "hash", reward_baseline=baseline
    )
    expected_python = random.random()
    expected_torch = torch.rand(1)
    with torch.no_grad():
        policy.weight.add_(10)
    random.seed(1)
    torch.manual_seed(1)

    restored_baseline = RewardBaseline(decay=0.5)
    epoch = load_training_checkpoint(
        path,
        policy,
        optimizer,
        split,
        "hash",
        reward_baseline=restored_baseline,
    )

    assert epoch == 1
    assert random.random() == expected_python
    assert torch.equal(torch.rand(1), expected_torch)
    assert restored_baseline.value("train") == 0.75
    with pytest.raises(ValueError, match="config"):
        load_training_checkpoint(path, policy, optimizer, split, "other")


def test_checkpoint_rejects_policy_schema_mismatch(tmp_path: Path) -> None:
    from analogy.rl import DirectRescuePolicy

    policy = DirectRescuePolicy(2, 4, 0.5, -2.0)
    optimizer = torch.optim.Adam(policy.parameters(), lr=0.01)
    split = split_problems(
        (
            problem("imp", "implication"),
            problem("not", "not"),
            problem("iff", "iff"),
        )
    )
    path = tmp_path / "schema.pt"
    save_training_checkpoint(path, policy, optimizer, 1, split, "hash")
    payload = torch.load(path, map_location="cpu", weights_only=False)
    assert payload["policy_schema_version"] == 3
    payload["policy_schema_version"] = 2
    torch.save(payload, path)

    with pytest.raises(ValueError, match="policy schema.*2.*3"):
        load_training_checkpoint(path, policy, optimizer, split, "hash")


def test_direct_policy_receives_neutral_channels_and_remaining_resource_scalars():
    from analogy.rescue import (
        DirectRescueRunner, ProofRepresentation, ProofRescueContext, TacticEvaluation,
    )
    from analogy.records import Embedding
    from analogy.rl import DirectRescuePolicy
    from analogy.loop import NeverCancelled

    observations = []

    class ObservingPolicy(DirectRescuePolicy):
        def sample(self, representation, generator):
            observations.append(representation)
            return super().sample(representation, generator)

    def encode(context):
        return ProofRepresentation(context, "language", "structure", Embedding("fake", (1.0, 0.0)), Embedding("fake", (0.0, 1.0)))

    outputs = iter([
        '{"predicted_state":"next", "tactics":["bad1"]}',
        '{"predicted_state":"next", "tactics":["bad2"]}',
    ])
    runner = DirectRescueRunner(
        encoder=SimpleNamespace(encode=encode),
        policy=ObservingPolicy(2, 4, 0.5, -2.0),
        generator=SimpleNamespace(generate_with_delta=lambda *args, **kwargs: next(outputs)),
        rollouts_per_state=2, tactics_per_rollout=1, max_rescue_verifier_calls=4,
        max_model_calls=4, base_seed=3, hidden_layer=-1,
    )
    runner.search(
        SymbolicState("s", "⊢ P", "lean4"), SymbolicState("g", "P", "lean4"), (), (),
        lambda candidate: TacticEvaluation(TacticResult(candidate, TacticStatus.ERROR)),
        NeverCancelled(),
    )
    assert [item.scalar_features for item in observations] == [
        (0.0, 1.0, 1.0, 0.0), (1.0, 0.75, 0.75, 1.0),
    ]
    assert all(item.transition_embedding.values == (0.0, 0.0) for item in observations)
    assert all(item.analogy_embedding.values == (0.0, 0.0) for item in observations)


def test_legacy_sampler_normalizes_cumulative_resources_and_resets_each_invocation():
    from analogy.training import _new_analogy_runner
    from analogy.proof_views import ProofStructure
    from analogy.records import Embedding
    from analogy.rescue import ProofRescueContext
    from analogy.rl import DirectRescuePolicy

    observations = []

    class ObservingPolicy(DirectRescuePolicy):
        def sample(self, representation, generator):
            observations.append(representation)
            return super().sample(representation, generator)

    base = load_config(ROOT / "config.yaml")
    config = replace(base, analogy=replace(base.analogy, max_iterations=3, max_model_calls=8, max_lean_calls=6))
    generator = SimpleNamespace(embed_text=lambda *args, **kwargs: Embedding("fake", (1.0, 0.0)))
    runner = _new_analogy_runner(
        config, generator, SimpleNamespace(proof_structure_for=lambda state: ProofStructure(1, '"atom"', ())),
        ObservingPolicy(2, 4, 0.5, -2.0),
    )
    state = SymbolicState("s", "⊢ P", "lean4")
    context = ProofRescueContext(state, state, (), ())
    views = runner.proof_view_builder(context)
    runner.action_sampler(views, 0)
    runner.iteration_sink("r", SimpleNamespace(model_calls=2, lean_calls=3))
    views = runner.proof_view_builder(context)
    runner.action_sampler(views, 1)
    runner.action_sampler(views, 0)
    assert [item.scalar_features for item in observations] == [
        (0.0, 1.0, 5 / 6, 0.0), (0.5, 0.75, 2 / 6, 1.0), (0.0, 1.0, 5 / 6, 0.0),
    ]
    assert all(item.transition_embedding.values == (0.0, 0.0) for item in observations)
    assert all(item.analogy_embedding.values == (0.0, 0.0) for item in observations)


def test_matched_conditions_share_budgets_and_seed_family(tmp_path: Path) -> None:
    config = load_config(ROOT / "config.yaml")
    config = replace(config, analogy=replace(config.analogy, enabled=True))

    conditions = build_evaluation_conditions(config)

    assert {item.name for item in conditions} == {
        "conventional", "learned_direct", "analogy_loop", "shuffled_analogy"
    }
    assert {item.ordinary_width for item in conditions} == {config.search.top_k}
    assert {item.max_rescue_verifier_calls for item in conditions} == {config.rl.max_rescue_verifier_calls}
    rescue = [item for item in conditions if item.name != "conventional"]
    assert len({item.budget for item in rescue}) == 1


def test_disabled_analogy_omits_analogy_evaluation_conditions() -> None:
    config = load_config(ROOT / "config.yaml")

    conditions = build_evaluation_conditions(config)

    assert [item.name for item in conditions] == ["conventional", "learned_direct"]


def test_shuffled_analogies_change_assignment_without_crossing_split() -> None:
    def record(identifier: str) -> StuckStateRecord:
        proof_state = f"⊢ {identifier}"
        return StuckStateRecord(
            problem(identifier, "iff"), "test", (), proof_state,
            proof_state_fingerprint(proof_state), ("simp",), ("simp",),
        )

    records = (record("test-a"), record("test-b"))
    shuffled = shuffle_analogy_assignments(records, seed=7)

    assert {item.target.problem.id for item in shuffled} == {"test-a", "test-b"}
    assert all(item.source.problem.id != item.target.problem.id for item in shuffled)
    assert all(item.source.split == item.target.split == "test" for item in shuffled)


def test_condition_summary_reports_verifier_backed_repair_and_transition_metrics():
    def attempt(source, status, diagnostics=()):
        candidate = TacticCandidate(source, 1.0, CandidateOrigin.ANALOGICAL)
        proposal = RescueProposal(candidate, "predicted")
        state_after = SymbolicState("next", "⊢ Q", "lean4")
        result = TacticResult(
            candidate,
            status,
            state=state_after if status is TacticStatus.ACCEPTED else None,
            proof="by exact h" if status is TacticStatus.COMPLETE else None,
            diagnostics=diagnostics,
        )
        return RescueAttempt(proposal, result)

    rejected = attempt("bad", TacticStatus.ERROR, ("unknown identifier",))
    repair_error = attempt("almost", TacticStatus.ERROR, ("type mismatch",))
    repaired_progress = attempt("fixed", TacticStatus.ACCEPTED)
    completed = attempt("exact h", TacticStatus.COMPLETE)
    repair_generation = GenerationAttempt(
        1, "{}", GenerationStatus.VALID, repair=True
    )

    def trace(iteration):
        return AnalogyLoopTrace((iteration,), StopReason.NO_SUCCESSOR, 0, 0)

    rejected_iteration = SimpleNamespace(
        attempts=(rejected,),
        repair_generation_attempts=(),
        repair_succeeded=False,
        actual_transition=None,
        surprise=None,
    )
    repaired_iteration = SimpleNamespace(
        attempts=(repair_error, repaired_progress),
        repair_generation_attempts=(repair_generation,),
        repair_succeeded=True,
        actual_transition="⊢ Q",
        surprise=0.4,
    )
    completed_iteration = SimpleNamespace(
        attempts=(completed,),
        repair_generation_attempts=(),
        repair_succeeded=False,
        actual_transition=None,
        surprise=None,
    )
    action = PolicyActionRecord(0, 0.0, 0.0, 0.0)
    rollouts = (
        RescueRollout(
            "rejected", "s1", action, (rejected.proposal,), (rejected,),
            RescueTermination.REJECTED, analogy_trace=trace(rejected_iteration),
        ),
        RescueRollout(
            "progress", "s2", action,
            (repair_error.proposal, repaired_progress.proposal),
            (repair_error, repaired_progress), RescueTermination.PROGRESS,
            generations=(repair_generation,), analogy_trace=trace(repaired_iteration),
        ),
        RescueRollout(
            "complete", "s3", action, (completed.proposal,), (completed,),
            RescueTermination.COMPLETE, analogy_trace=trace(completed_iteration),
        ),
    )
    episodes = tuple(
        ProblemEpisode(
            f"p{index}",
            rollout.termination is RescueTermination.COMPLETE,
            (rollout,),
            (),
            0,
            len(rollout.attempts),
        )
        for index, rollout in enumerate(rollouts)
    )

    summary = _summarize_condition("analogy_loop", episodes, set())

    assert summary.lean_accepted_tactic_rate == pytest.approx(0.5)
    assert summary.successor_rate == pytest.approx(1 / 3)
    assert summary.diagnostic_repair_attempts == 1
    assert summary.diagnostic_repair_successes == 1
    assert summary.diagnostic_repair_success_rate == pytest.approx(1.0)
    assert summary.non_null_surprise_rate == pytest.approx(1 / 3)
    assert summary.completion_per_lean_call == pytest.approx(0.25)


def test_training_updates_train_only_and_persists_untrainable_rollouts(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (ROOT / "config.yaml")
        .read_text()
        .replace("output_dir: runs", f"output_dir: {tmp_path / 'runs'}")
        .replace("epochs: 10", "epochs: 1")
        .replace("rl:\n  enabled: false", "rl:\n  enabled: true"),
        encoding="utf-8",
    )
    config = load_config(config_path)
    problems = (
        problem("train", "implication"),
        problem("validation", "not"),
        problem("test", "iff"),
    )
    policy = torch.nn.Linear(1, 1, bias=False)
    optimizer = torch.optim.SGD(policy.parameters(), lr=0.1)
    phases = []

    def run_problem(item, phase, received_policy):
        phases.append((item.id, phase))
        selected = TacticCandidate("bad", 1.0, CandidateOrigin.ANALOGICAL)
        proposal = RescueProposal(selected, "still stuck")
        result = TacticResult(selected, TacticStatus.ERROR)
        record = RescueRollout(
            f"r-{item.id}", item.id, PolicyActionRecord(1, 0.0, 0.0, 0.0),
            (proposal,), (RescueAttempt(proposal, result),), RescueTermination.REJECTED,
        )
        if item.id == "train":
            action = PolicyAction(torch.zeros(1), received_policy.weight.sum(), received_policy.weight.sum() * 0.0)
            return ProblemEpisode(item.id, False, (record,), (TrainingSample(record, action, 1.0),), 1, 1)
        return ProblemEpisode(item.id, False, (record,), (), 1, 1, failure="interrupted")

    before = policy.weight.detach().clone()
    summary = train_direct_rescue(
        config,
        object(),
        object(),
        problems=problems,
        policy=policy,
        optimizer=optimizer,
        problem_runner=run_problem,
        run_id="training-run",
    )

    assert not torch.equal(policy.weight, before)
    assert phases == [("train", "train"), ("validation", "validation")]
    assert "test" not in {identifier for identifier, _ in phases}
    lines = (summary.run_directory / "rollouts.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_replay_training_reconstructs_and_starts_from_recorded_states(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (ROOT / "config.yaml")
        .read_text()
        .replace("output_dir: runs", f"output_dir: {tmp_path / 'runs'}")
        .replace("epochs: 10", "epochs: 1")
        .replace("rl:\n  enabled: false", "rl:\n  enabled: true"),
        encoding="utf-8",
    )
    config = load_config(config_path)

    def replay_record(identifier, connective, split):
        proof_state = f"h : P\n⊢ P -- {identifier}"
        return StuckStateRecord(
            problem(identifier, connective),
            split,
            ("intro h",),
            proof_state,
            proof_state_fingerprint(proof_state),
            ("exact h",),
            ("simp",),
        )

    records = (
        replay_record("train-replay", "implication", "train"),
        replay_record("validation-replay", "not", "validation"),
    )
    reconstructed = []
    executed = []

    def reconstruct(item, verifier):
        del verifier
        state = SymbolicState(item.problem.id, item.proof_state, "lean4", item.accepted_tactics)
        reconstructed.append(item.problem.id)
        return state

    def execute(config, generator, verifier, problem, phase, policy, **kwargs):
        del config, generator, verifier, policy
        executed.append((problem.id, phase, kwargs["initial_state"].id))
        return ProblemEpisode(problem.id, False, (), (), 0, 0)

    monkeypatch.setattr("analogy.training.reconstruct_stuck_state", reconstruct)
    monkeypatch.setattr("analogy.training._execute_problem", execute)
    policy = torch.nn.Linear(1, 1)

    train_direct_rescue(
        config,
        object(),
        object(),
        policy=policy,
        optimizer=torch.optim.SGD(policy.parameters(), lr=0.1),
        replay_records=records,
        run_id="replay-run",
    )

    assert reconstructed == ["train-replay", "validation-replay"]
    assert executed == [
        ("train-replay", "train", "train-replay"),
        ("validation-replay", "validation", "validation-replay"),
    ]


def test_replay_training_rejects_record_that_crosses_connective_split(
    tmp_path: Path,
) -> None:
    config = load_config(ROOT / "config.yaml")
    proof_state = "⊢ P ↔ P"
    leaked = StuckStateRecord(
        problem("held-out", "iff"),
        "train",
        (),
        proof_state,
        proof_state_fingerprint(proof_state),
        ("rfl",),
        ("simp",),
    )

    with pytest.raises(ValueError, match="configured test split"):
        train_direct_rescue(
            replace(config, rl=replace(config.rl, enabled=True)),
            object(),
            object(),
            replay_records=(leaked,),
        )
