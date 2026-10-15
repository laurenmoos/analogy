from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import analogy.__main__ as cli
from analogy.records import BaselineProblemResult, BaselineStatus, BenchmarkRun, CapabilityResult


ROOT = Path(__file__).parents[1]


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
