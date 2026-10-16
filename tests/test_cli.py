from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import analogy.__main__ as cli
from analogy.model import ModelConfigurationError
from analogy.records import BaselineProblemResult, BaselineStatus, BenchmarkRun, CapabilityResult


ROOT = Path(__file__).parents[1]


def test_curriculum_cli_builds_without_loading_any_model(monkeypatch, tmp_path, capsys):
    from test_grounded_curriculum import config, PrefixVerifier, rows
    cfg = config(tmp_path)
    monkeypatch.setattr(cli, "load_curriculum_config", lambda path: cfg)
    monkeypatch.setattr(cli, "LeanSubprocessVerifier", PrefixVerifier)
    monkeypatch.setattr(cli, "load_model_backend", lambda config: (_ for _ in ()).throw(AssertionError("model load")))
    assert cli.main(["build-grounded-curriculum", "--config", "unused.yaml"]) == 0
    assert "2/2 retained" in capsys.readouterr().out
    assert len(rows(cfg.output_dir / "transitions.jsonl")) == 2


def test_grounded_curriculum_cli_has_nonterminal_then_second_analogy_completion(monkeypatch, tmp_path):
    import json
    from dataclasses import replace
    import analogy.training as training
    from analogy.config import load_config
    from analogy.grounded_curriculum import build_grounded_curriculum
    from analogy.records import CandidateOrigin, TacticCandidate
    from test_grounded_curriculum import config as curriculum_config, PrefixVerifier
    from test_analogy_transfer_loop import Encoder, grounded_payload
    from test_analogy_transfer_generation import RecordingBackend, SOURCE, ALIGNMENT, TRANSFER

    cfg = curriculum_config(tmp_path, tactics=("intro analogy_h",), ids=("train", "validation"))
    cfg.split_manifest.write_text(json.dumps({"schema_version": 1,
        "train": ["train"], "validation": ["validation"], "test": []}))
    manifest = build_grounded_curriculum(cfg, PrefixVerifier(cfg.lean_project)).manifest_path
    config = load_config(ROOT / "config.mlx-8b-grounded-analogy.yaml")
    config = replace(config, benchmark=replace(config.benchmark, corpus=cfg.problems),
        run=replace(config.run, output_dir=tmp_path / "runs"),
        rl=replace(config.rl, curriculum_manifest=manifest))
    source = json.loads(SOURCE)
    source["transformation"]["operation"] = "introduce"
    alignment = json.loads(ALIGNMENT)
    alignment["mapping"]["shelved wheel"] = "introduced_assumption"
    first_transfer = dict(json.loads(TRANSFER), active_subgoal="P → P", premises=[],
        predicted_next_subgoal="P", completion_expected=False)
    first_plan = grounded_payload(PrefixVerifier.states[0], proposition="P")
    first_plan.update(bindings=[], completion_expected=False, expected_next_subgoal="P")
    first_plan["steps"][0].update(operator="introduce", inputs=[], input_propositions=[],
        output_proposition="P", closes_subgoal=False)
    second_plan = grounded_payload("h : P\nanalogy_h : P\n⊢ P", proposition="P", index=1)
    second_plan["steps"][0]["output_proposition"] = "P"
    second_transfer = dict(json.loads(TRANSFER), active_subgoal="P")
    records = [json.dumps(source), json.dumps(alignment), json.dumps(first_transfer), json.dumps(first_plan),
        SOURCE, ALIGNMENT, json.dumps(second_transfer), json.dumps(second_plan)]

    class Backend(RecordingBackend, Encoder):
        hidden_size = 2

    class Ranker:
        def rank(self, state, goal):
            return (TacticCandidate("ordinary_bad", 1.0, CandidateOrigin.ORDINARY),)

    backend = Backend(records * 2)
    class Verifier(PrefixVerifier):
        def proof_structure_after(self, problem, prefix):
            from analogy.proof_views import ProofStructure
            shape = super().proof_structure_after(problem, prefix)
            return shape if not prefix else ProofStructure(1, '{"root":"atom"}', ('{"root":"target"}',))

    monkeypatch.setattr(cli, "load_config", lambda path: config)
    monkeypatch.setattr(cli, "load_model_backend", lambda config: backend)
    monkeypatch.setattr(cli, "LeanSubprocessVerifier", Verifier)
    monkeypatch.setattr(training, "QwenTacticRanker", lambda *args, **kwargs: Ranker())
    assert cli.main(["--config", "grounded.yaml"]) == 0
    run = next((tmp_path / "runs" / "rescue").iterdir())
    rows = [json.loads(line) for line in (run / "analogy_iterations.jsonl").read_text().splitlines()]
    assert len(rows) == 4
    assert [row["iteration_index"] for row in rows] == [0, 1, 0, 1]
    assert all(row["observation"]["actual_transition"] for row in rows if row["iteration_index"] == 1)
    episodes = [json.loads(line) for line in (run / "problems.jsonl").read_text().splitlines()]
    assert all(row["completed"] and row["transition_id"] for row in episodes)
    assert len({row["proof_path_id"] for row in episodes}) == 2
    metrics = [json.loads(line) for line in (run / "metrics.jsonl").read_text().splitlines()]
    assert len(metrics) == 1
    assert abs(metrics[0]["mean_reward"] - 0.97) < 1e-6
    text = (run / "readable_traces.md").read_text()
    assert "GROUNDED ROLE BINDINGS" in text and "COMPILED TACTICS" in text


def test_generated_profile_cli_writes_connected_trace(monkeypatch, tmp_path, capsys):
    """Catch broken generated-profile composition or missing trace finalization."""
    import json
    from dataclasses import replace
    import analogy.training as training
    from analogy.config import load_config
    from analogy.records import CandidateOrigin, TacticCandidate, TacticResult, TacticStatus
    from test_analogy_transfer_loop import Encoder, Evaluator, outputs, state, STRUCTURE
    from test_analogy_transfer_generation import RecordingBackend
    from test_training import problem

    profile = ROOT / "config.mlx-8b-generated-analogy.yaml"
    assert profile.exists(), "generated-domain CLI profile is required"
    config = load_config(profile)
    config = replace(config, run=replace(config.run, output_dir=tmp_path),
                     analogy=replace(config.analogy, tactics_per_iteration=1))

    class Backend(RecordingBackend, Encoder):
        hidden_size = 2

    backend = Backend(outputs(1) * 2)
    evaluator = Evaluator(finish=True)

    class Adapter:
        def initial_state(self):
            return state()

        def failed_tactics_for(self, current):
            return ()

        def proof_structure_for(self, current):
            return STRUCTURE

        def run_tactic(self, current, candidate):
            if candidate.origin is CandidateOrigin.ORDINARY:
                return TacticResult(candidate, TacticStatus.ERROR, diagnostics=("unknown identifier",))
            return evaluator.evaluate_at(current, candidate).result

    class Ranker:
        def rank(self, current, goal):
            return (TacticCandidate("ordinary_bad", 1.0, CandidateOrigin.ORDINARY),)

    monkeypatch.setattr(cli, "load_config", lambda path: config)
    monkeypatch.setattr(cli, "load_model_backend", lambda config: backend)
    monkeypatch.setattr(cli, "LeanSubprocessVerifier", lambda root: object())
    monkeypatch.setattr(training, "load_problems", lambda path: (
        problem("train", "implication"), problem("validation", "not"), problem("test", "iff")))
    monkeypatch.setattr(training, "LeanGymAdapter", lambda *args: Adapter())
    monkeypatch.setattr(training, "QwenTacticRanker", lambda *args, **kwargs: Ranker())

    assert cli.main(["--config", str(profile)]) == 0
    run = next((tmp_path / "rescue").iterdir())
    episodes = [json.loads(line) for line in (run / "problems.jsonl").read_text().splitlines()]
    assert all(episode["completed"] for episode in episodes)
    trace = (run / "readable_traces.md").read_text()
    assert "SOURCE ANALOGY" in trace
    assert "TRANSFERRED PROOF REASONING" in trace
    assert "conventional_finisher" in trace
    assert "train_rescue: 1 epochs" in capsys.readouterr().out


def test_generated_strategy_does_not_construct_rescue_for_conventional(monkeypatch):
    import analogy.training as training
    from analogy.config import load_config
    from analogy.records import CandidateOrigin, SymbolicState, TacticCandidate, TacticResult, TacticStatus
    from test_training import problem

    profile = ROOT / "config.mlx-8b-generated-analogy.yaml"
    assert profile.exists(), "generated-domain CLI profile is required"
    config = load_config(profile)

    class Adapter:
        def initial_state(self):
            return SymbolicState("initial", "h : P\n⊢ P", "lean4")

        def failed_tactics_for(self, current):
            return ()

        def run_tactic(self, current, candidate):
            return TacticResult(candidate, TacticStatus.COMPLETE, proof="by assumption")

    class Ranker:
        def rank(self, current, goal):
            return (TacticCandidate("assumption", 1.0, CandidateOrigin.ORDINARY),)

    monkeypatch.setattr(training, "LeanGymAdapter", lambda *args: Adapter())
    monkeypatch.setattr(training, "QwenTacticRanker", lambda *args, **kwargs: Ranker())
    monkeypatch.setattr(training, "_new_analogy_runner", lambda *args, **kwargs:
                        (_ for _ in ()).throw(AssertionError("unexpected analogy construction")))
    episode = training._execute_problem(config, object(), object(), problem("ordinary", "iff"), "conventional", None)
    assert episode.completed
    assert not episode.rollouts


def test_model_load_error_names_exact_identifier(monkeypatch, capsys) -> None:
    identifier = "mlx-community/Qwen3-8B-4bit"
    monkeypatch.setattr(
        cli,
        "load_model_backend",
        lambda config: (_ for _ in ()).throw(
            ModelConfigurationError(f"failed to load MLX model {identifier}")
        ),
    )

    status = cli.main(["--config", str(ROOT / "config.mlx-8b-fast.yaml")])

    assert status == 2
    assert identifier in capsys.readouterr().err


def test_capability_stage_runs_model_and_reports_passing_gate(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (ROOT / "config.yaml")
        .read_text(encoding="utf-8")
        .replace("stage: baseline", "stage: capability"),
        encoding="utf-8",
    )
    generator = object()
    verifier = object()
    monkeypatch.setattr(
        cli.QwenGenerator,
        "from_config",
        lambda config: generator,
    )
    monkeypatch.setattr(cli, "LeanSubprocessVerifier", lambda root: verifier)
    monkeypatch.setattr(
        cli,
        "run_capability",
        lambda config, received_generator, received_verifier: (
            CapabilityResult("identity", ("intro h", "exact h"), True, None, 0.2),
        ),
    )

    status = cli.main(["--config", str(config_path)])

    output = capsys.readouterr()
    assert status == 0
    assert "capability: 1/1 solved" in output.out
    assert "gate: passed" in output.out
    assert output.err == ""


def test_unknown_stage_returns_a_clear_error(tmp_path: Path, capsys) -> None:
    source = (ROOT / "config.yaml").read_text(encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(
        source.replace("stage: capability", "stage: future").replace(
            "stage: baseline", "stage: future"
        ),
        encoding="utf-8",
    )

    status = cli.main(["--config", str(path)])

    output = capsys.readouterr()
    assert status == 2
    assert "Unsupported experiment stage: future" in output.err


def test_baseline_stage_runs_ranked_search_and_reports_counts(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (ROOT / "config.yaml")
        .read_text(encoding="utf-8")
        .replace("stage: capability", "stage: baseline"),
        encoding="utf-8",
    )
    generator = object()
    verifier = object()
    outcome = BaselineProblemResult(
        "identity", BaselineStatus.COMPLETE, "by exact h\n", None, 1, 1, 0, 0, 0, 0
    )
    run = BenchmarkRun("run-1", (outcome,), 0.1)
    monkeypatch.setattr(cli.QwenGenerator, "from_config", lambda config: generator)
    monkeypatch.setattr(cli, "LeanSubprocessVerifier", lambda root: verifier)
    monkeypatch.setattr(
        cli,
        "run_baseline",
        lambda config, received_generator, received_verifier: run,
    )

    status = cli.main(["--config", str(config_path)])

    output = capsys.readouterr()
    assert status == 0
    assert "baseline: 1/1 solved" in output.out
    assert "stuck states: 0" in output.out


def test_train_rescue_stage_dispatches_resolved_configuration(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (ROOT / "config.yaml")
        .read_text(encoding="utf-8")
        .replace("stage: baseline", "stage: train_rescue")
        .replace("rl:\n  enabled: false", "rl:\n  enabled: true"),
        encoding="utf-8",
    )
    received = []
    monkeypatch.setattr(cli.QwenGenerator, "from_config", lambda config: object())
    monkeypatch.setattr(cli, "LeanSubprocessVerifier", lambda root: object())
    monkeypatch.setattr(
        cli,
        "train_direct_rescue",
        lambda config, generator, verifier: received.append(config)
        or SimpleNamespace(run_id="run", epochs_completed=1, best_checkpoint=tmp_path / "best.pt"),
    )

    status = cli.main(["--config", str(config_path)])

    assert status == 0
    assert received[0].rl.enabled is True
    assert "train_rescue: 1 epochs" in capsys.readouterr().out


def test_evaluate_rescue_requires_checkpoint_before_model_load(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (ROOT / "config.yaml")
        .read_text(encoding="utf-8")
        .replace("stage: baseline", "stage: evaluate_rescue")
        .replace("rl:\n  enabled: false", "rl:\n  enabled: true"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli.QwenGenerator,
        "from_config",
        lambda config: (_ for _ in ()).throw(AssertionError("model must not load")),
    )

    status = cli.main(["--config", str(config_path)])

    assert status == 2
    assert "checkpoint" in capsys.readouterr().err


def test_evaluate_rescue_dispatches_checkpoint(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    checkpoint = tmp_path / "learned.pt"
    checkpoint.write_bytes(b"checkpoint")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (ROOT / "config.yaml")
        .read_text(encoding="utf-8")
        .replace("stage: baseline", "stage: evaluate_rescue")
        .replace("rl:\n  enabled: false", "rl:\n  enabled: true")
        .replace("checkpoint: null", f"checkpoint: {checkpoint}"),
        encoding="utf-8",
    )
    received = []
    monkeypatch.setattr(cli.QwenGenerator, "from_config", lambda config: object())
    monkeypatch.setattr(cli, "LeanSubprocessVerifier", lambda root: object())
    monkeypatch.setattr(
        cli,
        "evaluate_direct_rescue",
        lambda config, generator, verifier, path: received.append(path)
        or SimpleNamespace(run_id="evaluation", condition_summaries=()),
    )

    status = cli.main(["--config", str(config_path)])

    assert status == 0
    assert received == [checkpoint]
    assert "evaluate_rescue:" in capsys.readouterr().out


def test_rescue_stages_accept_analogy_enabled(tmp_path: Path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (ROOT / "config.yaml")
        .read_text(encoding="utf-8")
        .replace("stage: baseline", "stage: train_rescue")
        .replace("rl:\n  enabled: false", "rl:\n  enabled: true")
        .replace("analogy:\n  enabled: false", "analogy:\n  enabled: true"),
        encoding="utf-8",
    )

    monkeypatch.setattr(cli.QwenGenerator, "from_config", lambda config: object())
    monkeypatch.setattr(cli, "LeanSubprocessVerifier", lambda root: object())
    monkeypatch.setattr(
        cli,
        "train_direct_rescue",
        lambda config, generator, verifier: SimpleNamespace(
            epochs_completed=1, best_checkpoint=tmp_path / "best.pt"
        ),
    )

    assert cli.main(["--config", str(config_path)]) == 0
    assert "train_rescue: 1 epochs" in capsys.readouterr().out


def test_audit_stage_requires_source_and_output_before_model_load(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (ROOT / "config.yaml")
        .read_text(encoding="utf-8")
        .replace("stage: baseline", "stage: audit_analogy"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "load_model_backend",
        lambda config: (_ for _ in ()).throw(AssertionError("model must not load")),
    )

    status = cli.main(["--config", str(config_path)])

    assert status == 2
    assert "--audit-source" in capsys.readouterr().err


def test_audit_stage_dispatches_without_constructing_lean_verifier(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        (ROOT / "config.yaml")
        .read_text(encoding="utf-8")
        .replace("stage: baseline", "stage: audit_analogy"),
        encoding="utf-8",
    )
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "audit"
    generator = object()
    received = []
    monkeypatch.setattr(cli, "load_model_backend", lambda config: generator)
    monkeypatch.setattr(
        cli,
        "LeanSubprocessVerifier",
        lambda root: (_ for _ in ()).throw(AssertionError("Lean must not load")),
    )
    monkeypatch.setattr(
        cli,
        "audit_analogy_run",
        lambda source_run, output_dir, received_generator, corpus, **kwargs:
        received.append(
            (source_run, output_dir, received_generator, corpus, kwargs)
        )
        or SimpleNamespace(attempted=7, judged=6, failures=1),
    )

    status = cli.main(
        [
            "--config",
            str(config_path),
            "--audit-source",
            str(source),
            "--audit-output",
            str(output),
            "--audit-seed",
            "17",
            "--audit-max-calls",
            "9",
            "--audit-max-repair-calls",
            "2",
        ]
    )

    assert status == 0
    assert received == [
        (
            source,
            output,
            generator,
            config_path.parent / "src/analogy/lean_gym/corpus/problems.json",
            {"seed": 17, "max_calls": 9, "max_repair_calls": 2},
        )
    ]
    assert "audit_analogy: 6/7 judged; failures=1" in capsys.readouterr().out
