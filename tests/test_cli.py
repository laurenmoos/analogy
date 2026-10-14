from __future__ import annotations

from pathlib import Path

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
