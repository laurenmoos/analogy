"""Run the actual CLI orchestration with deterministic external dependencies."""
from dataclasses import replace
import json
from types import SimpleNamespace
import pytest

from analogy import __main__ as cli
from analogy.experiments import activation_report as report
from analogy.experiments import activation_cli
from test_activation_runner import setup, STRUCTURE, Lean, Provider


def test_cli_builds_structures_freezes_then_runs_all_arms_and_resumes(tmp_path, monkeypatch):
    cfg, app, problems, _, _, _, _ = setup()
    cfg = replace(cfg, base_config=app)
    phase = {"prelean": False, "frozen": False}

    class AuditedProvider(Provider):
        def capture_completion_trace(self, *args, **kwargs):
            phase["prelean"] = True
            return super().capture_completion_trace(*args, **kwargs)

    class AuditedLean(Lean):
        def proof_structure_after(self, problem, prefix):
            assert not phase["prelean"]
            return STRUCTURE

        def initial_state(self, problem):
            if phase["prelean"]:
                assert phase["frozen"]
            return super().initial_state(problem)

        def try_tactic(self, problem, prefix, tactic):
            if phase["prelean"]:
                assert phase["frozen"]
            return super().try_tactic(problem, prefix, tactic)

    provider, lean = AuditedProvider(), AuditedLean()
    monkeypatch.setattr(activation_cli, "load_activation_eval_config", lambda _: cfg)
    monkeypatch.setattr(activation_cli, "get_runtime", lambda _: SimpleNamespace(config=app, model=provider, verifier=lean, problems=problems))
    original = report.ActivationArtifactWriter.write_frozen

    def freeze(self, selections):
        assert len([c for c in provider.calls if c[0] == "capture"]) == 8
        original(self, selections)
        phase["frozen"] = True

    monkeypatch.setattr(report.ActivationArtifactWriter, "write_frozen", freeze)
    path = tmp_path / "run"
    assert cli.main(["activation-eval", "--output-dir", str(path)]) == 0
    summary = json.loads((path / "summary.json").read_text())
    assert json.loads((path / "resolved_config.json").read_text())["layers"] == [0]
    assert len(summary["rows"]) == 16
    assert len([r for r in summary["rows"] if r["solves"] == 1]) == 4
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    calls = len(provider.calls), len(lean.calls)
    assert cli.main(["activation-eval", "--resume", str(path)]) == 0
    assert (len(provider.calls), len(lean.calls)) == calls
    assert cli.main(["activation-eval", "--output-dir", str(path)]) == 2
    assert before == {p.name: p.read_bytes() for p in path.iterdir()}
    provider.model_revision = "changed"
    assert cli.main(["activation-eval", "--resume", str(path)]) == 2


@pytest.mark.parametrize("scope,expected_trials", [([], 2), (["--problem", "p", "--max-cells", "1"], 1)])
def test_interrupted_cli_resumes_frozen_selection_without_prelean_or_repeating_cells(tmp_path, monkeypatch, scope, expected_trials):
    cfg, app, problems, _, _, provider, _ = setup()
    cfg = replace(cfg, base_config=app)

    class StructuredLean(Lean):
        def proof_structure_after(self, problem, prefix):
            return STRUCTURE

    lean = StructuredLean()
    monkeypatch.setattr(activation_cli, "load_activation_eval_config", lambda _: cfg)
    monkeypatch.setattr(activation_cli, "get_runtime", lambda _: SimpleNamespace(config=app, model=provider, verifier=lean, problems=problems))
    append = report.ActivationArtifactWriter.append_trial

    def interrupt_after_first(self, trial):
        append(self, trial)
        raise KeyboardInterrupt("simulated interruption after a flushed cell")

    monkeypatch.setattr(report.ActivationArtifactWriter, "append_trial", interrupt_after_first)
    path = tmp_path / "resume"
    with pytest.raises(KeyboardInterrupt):
        cli.main(["activation-eval", "--output-dir", str(path), *scope])
    first = (path / "trials.jsonl").read_bytes()
    capture_count = len([c for c in provider.calls if c[0] == "capture"])
    first_problem_calls = len([c for c in lean.calls if c[0] == "p"])
    monkeypatch.setattr(report.ActivationArtifactWriter, "append_trial", append)
    assert cli.main(["activation-eval", "--resume", str(path)]) == 0
    assert (path / "trials.jsonl").read_bytes().startswith(first)
    assert len([c for c in provider.calls if c[0] == "capture"]) == capture_count
    assert len([c for c in lean.calls if c[0] == "p"]) == first_problem_calls
    assert len(report.ActivationArtifactWriter(path).load_trials()) == expected_trials


def test_anchor_preparation_flushes_completed_cells_before_next_model_failure(tmp_path, monkeypatch):
    cfg, app, problems, _, _, provider, _ = setup()
    cfg = replace(cfg, base_config=app)

    class FailsOnSecondAnchor(Lean):
        def initial_state(self, problem):
            if problem.id == "q":
                raise RuntimeError("anchor infrastructure unavailable")
            return super().initial_state(problem)

    monkeypatch.setattr(activation_cli, "load_activation_eval_config", lambda _: cfg)
    monkeypatch.setattr(activation_cli, "get_runtime", lambda _: SimpleNamespace(config=app, model=provider, verifier=FailsOnSecondAnchor(), problems=problems))
    path = tmp_path / "anchor-partial"
    with pytest.raises(RuntimeError, match="anchor infrastructure"):
        cli.main(["activation-eval", "--output-dir", str(path)])
    anchors = (path / "anchors.jsonl").read_text().splitlines()
    assert len(anchors) == 1 and json.loads(anchors[0])["problem_id"] == "p"


def test_competing_cli_resume_fails_before_runtime_loading(tmp_path, monkeypatch):
    from test_activation_report import completed_run
    run, _ = completed_run()
    writer = report.ActivationArtifactWriter.create(tmp_path / "locked", run.collection)
    writer.write_frozen(run.selections)
    monkeypatch.setattr(activation_cli, "load_activation_eval_config", lambda _: run.collection.config)
    def forbidden_runtime(_):
        pytest.fail("runtime/model loaded while another writer holds the run")
    monkeypatch.setattr(activation_cli, "get_runtime", forbidden_runtime)
    assert cli.main(["activation-eval", "--resume", str(writer.path)]) == 2
    writer.close()


def test_cli_recovers_final_publication_and_validates_seed_override(tmp_path, monkeypatch):
    cfg, app, problems, _, _, provider, _ = setup()
    cfg = replace(cfg, base_config=app)
    class StructuredLean(Lean):
        def proof_structure_after(self, problem, prefix):
            return STRUCTURE
    lean = StructuredLean()
    monkeypatch.setattr(activation_cli, "load_activation_eval_config", lambda _: cfg)
    monkeypatch.setattr(activation_cli, "get_runtime", lambda _: SimpleNamespace(config=app, model=provider, verifier=lean, problems=problems))
    publish = report._atomic_new
    def fail_summary(path, content):
        if path.name == "summary.json":
            raise OSError("summary publication interrupted")
        publish(path, content)
    monkeypatch.setattr(report, "_atomic_new", fail_summary)
    path = tmp_path / "finalization"
    assert cli.main(["activation-eval", "--output-dir", str(path), "--seed", "7"]) == 2
    assert (path / "report.md").exists() and not (path / "complete.json").exists()
    original_report = (path / "report.md").read_bytes()
    calls = len(provider.calls), len(lean.calls)
    monkeypatch.setattr(report, "_atomic_new", publish)
    assert cli.main(["activation-eval", "--resume", str(path), "--seed", "7"]) == 0
    assert (len(provider.calls), len(lean.calls)) == calls
    assert (path / "report.md").read_bytes() == original_report
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    assert cli.main(["activation-eval", "--resume", str(path), "--seed", "8"]) == 2
    assert before == {p.name: p.read_bytes() for p in path.iterdir()}
