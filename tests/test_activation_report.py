"""Artifacts are exclusive, restartable evidence rather than mutable logs."""
from dataclasses import replace
import json

import pytest

from analogy.experiments import activation_report as report
from analogy.experiments import activation_runner as runner
from test_activation_runner import setup, collect


def completed_run():
    data = setup()
    collection = collect(data)
    frozen = runner.freeze_selections(collection)
    data[4].allowed = True
    trials = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], max_topups=0)
    return runner.ActivationEvaluationRun(collection, frozen, trials), data


def test_exclusive_artifacts_summarize_vectors_and_preserve_every_condition(tmp_path):
    run, _ = completed_run()
    path = report.write_activation_artifacts(run, tmp_path / "run-1")
    assert {p.name for p in path.iterdir()} >= {
        "resolved_config.json", "anchors.jsonl", "prelean.jsonl", "trials.jsonl", "summary.json", "report.md"}
    rows = [json.loads(line) for line in (path / "trials.jsonl").read_text().splitlines()]
    assert len(rows) == 16
    assert {r["outcome"]["condition"] for r in rows} == {
        "neutral", "prompted_analogy", "activation_selected", "shuffled_analogy"}
    assert {r["outcome"]["budget_view"] for r in rows} == {"equal_tactic", "equal_compute"}
    prelean = (path / "prelean.jsonl").read_text()
    assert '"activation_summaries"' in prelean and '"values"' not in prelean
    assert not list(path.glob("*.gz"))
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    with pytest.raises(FileExistsError):
        report.write_activation_artifacts(run, path)
    assert before == {p.name: p.read_bytes() for p in path.iterdir()}


def test_phase_flush_checkpoint_resume_and_identity_validation(tmp_path):
    run, data = completed_run()
    writer = report.ActivationArtifactWriter.create(tmp_path / "partial", run.collection)
    writer.write_frozen(run.selections)
    # A fresh reader sees all frozen cells before any trial is written.
    assert len((writer.path / "prelean.jsonl").read_text().splitlines()) == 2
    writer.close()
    resumed = report.ActivationArtifactWriter.resume(writer.path, report.resume_identity(run.collection))
    frozen = resumed.load_frozen()
    assert [c.winner for c in frozen.cells] == [c.winner for c in run.selections.cells]
    with pytest.raises(ValueError, match="frozen checkpoint"):
        runner.freeze_selections(frozen.collection)
    trials = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], max_topups=0,
                                       trial_sink=resumed.append_trial)
    assert len(resumed.load_trials()) == 2
    resumed.finalize(runner.ActivationEvaluationRun(frozen.collection, frozen, trials))
    with pytest.raises(FileExistsError):
        resumed.append_trial(trials[0])
    resumed.close()
    for key, value in {
        "schema_version": "other", "model_revision": "other", "problem_ids": ["q", "p"],
        "seed_range": [1, 2], "prompt_hash": "changed", "anchor_manifest_hash": "changed",
    }.items():
        identity = report.resume_identity(run.collection)
        identity[key] = value
        with pytest.raises(ValueError, match=key):
            report.ActivationArtifactWriter.resume(writer.path, identity)


def test_report_exposes_per_problem_budgets_uncertainty_and_failure_categories(tmp_path):
    run, _ = completed_run()
    problems = ("imp_intro_05", "iff_intro_03", "iff_intro_05", "not_intro_05", "or_intro_05", "or_intro_06")
    cfg = replace(run.collection.config, problem_ids=problems)
    trials = tuple(replace(run.trials[i % 2], problem_id=pid) for i, pid in enumerate(problems))
    bad = replace(trials[-1].outcomes[0], immediate_utility="infrastructure_failure",
                  verifier_results=("timeout", "rejected"))
    trials = (*trials[:-1], replace(trials[-1], outcomes=(bad, *trials[-1].outcomes[1:])))
    expanded = replace(run, collection=replace(run.collection, config=cfg), trials=trials)
    text = report.render_activation_report(expanded)
    for pid in problems:
        assert text.count(f"| {pid} |") >= 8
    for phrase in ("positive control", "hard negative", "equal_tactic", "equal_compute", "no_stuck_anchor",
                   "failed_selection", "solve", "accepted_progressing", "patch", "predecessor",
                   "score–Lean utility", "novelty", "token positions", "Lean calls", "elapsed",
                   "local causal proxy", "descriptive", "paired"):
        assert phrase in text
    summary = report.summarize_activation_run(expanded)
    row = next(r for r in summary["rows"] if r["problem_id"] == "or_intro_06" and r["condition"] == "neutral" and r["budget_view"] == "equal_tactic")
    assert row["infrastructure_failures"] == {"timeout": 1}
    assert row["tactic_rejections"] == 1
    assert report.summarize_activation_run(expanded) == summary


def test_descriptive_bootstrap_and_paired_counts_have_known_endpoints():
    assert report.descriptive_interval([0, 0, 0], seed=9) == (0.0, 0.0)
    assert report.descriptive_interval([1, 1, 1], seed=9) == (1.0, 1.0)
    assert report.descriptive_interval([], seed=9) is None
    assert report.descriptive_interval([0, 1, 1], seed=9) == report.descriptive_interval([0, 1, 1], seed=9)
    run, _ = completed_run()
    summary = report.summarize_activation_run(run)
    pair = next(p for p in summary["paired"] if p["problem_id"] == "p" and p["comparator"] == "neutral" and p["budget_view"] == "equal_tactic")
    assert (pair["selected_only"], pair["comparator_only"], pair["both"], pair["neither"]) == (1, 0, 0, 0)


def test_diagnostic_flag_is_required_for_activation_vectors(tmp_path):
    run, _ = completed_run()
    config = replace(run.collection.config, persist_full_activations=True)
    collection = replace(run.collection, config=config)
    run = replace(run, collection=collection, selections=replace(run.selections, collection=collection))
    path = report.write_activation_artifacts(run, tmp_path / "diagnostic")
    import gzip
    with gzip.open(path / "activation_vectors.jsonl.gz", "rt") as stream:
        rows = [json.loads(line) for line in stream]
    assert rows and rows[0]["neutral_traces"][0]["activations"][0]["values"] == [1.0, 0.0]


def test_finalization_rejects_missing_or_different_cells(tmp_path):
    run, _ = completed_run()
    writer = report.ActivationArtifactWriter.create(tmp_path / "partial", run.collection)
    writer.write_frozen(run.selections)
    writer.append_trial(run.trials[0])
    with pytest.raises(ValueError, match="every trial"):
        writer.finalize(run)
    writer.append_trial(run.trials[1])
    mismatched = replace(run, trials=(replace(run.trials[0], seed=99), run.trials[1]))
    with pytest.raises(ValueError, match="journal"):
        writer.finalize(mismatched)


@pytest.mark.parametrize("failure,status", [("malformed", "failed_selection"), ("runtime", "invalid_usage")])
def test_failure_summary_never_confuses_missing_usage_with_tactic_rejection(failure, status):
    data = setup(failure=failure)
    collection = collect(data)
    frozen = runner.freeze_selections(collection)
    data[4].allowed = True
    trials = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], max_topups=0)
    summary = report.summarize_activation_run(runner.ActivationEvaluationRun(collection, frozen, trials))
    row = next(r for r in summary["rows"] if r["problem_id"] == "p" and r["condition"] == "activation_selected" and r["budget_view"] == "equal_tactic")
    assert row[status] == 1 and row["tactic_rejections"] == 0
    assert row["eligible"] == (0 if failure == "runtime" else 1)


def test_no_anchor_is_reported_but_excluded_from_rescue_rates():
    data = setup(no_stuck=True)
    collection = collect(data)
    frozen = runner.freeze_selections(collection)
    trials = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5])
    summary = report.summarize_activation_run(runner.ActivationEvaluationRun(collection, frozen, trials))
    assert all(r["no_stuck_anchor"] == 1 and r["eligible"] == 0 and r["solve_rate"] is None for r in summary["rows"])


def test_resume_rejects_tampered_checkpoint_and_incomplete_freeze(tmp_path):
    run, _ = completed_run()
    writer = report.ActivationArtifactWriter.create(tmp_path / "partial", run.collection)
    with pytest.raises(ValueError, match="fully frozen"):
        writer.load_frozen()
    writer.write_frozen(run.selections)
    with (writer.path / "prelean.jsonl").open("a") as stream:
        stream.write("{}\n")
    writer.close()
    with pytest.raises(ValueError, match="hash mismatch"):
        report.ActivationArtifactWriter.resume(writer.path, report.resume_identity(run.collection))


@pytest.mark.parametrize("failed_name", ["report.md", "summary.json", "complete.json"])
def test_interrupted_finalization_recovers_without_replacing_published_files(tmp_path, monkeypatch, failed_name):
    run, _ = completed_run()
    writer = report.ActivationArtifactWriter.create(tmp_path / "run", run.collection)
    writer.write_frozen(run.selections)
    for trial in run.trials:
        writer.append_trial(trial)
    publish = report._atomic_new

    def interrupt(path, content):
        if path.name == failed_name:
            raise OSError("publication interrupted")
        publish(path, content)

    monkeypatch.setattr(report, "_atomic_new", interrupt)
    with pytest.raises(OSError, match="publication interrupted"):
        writer.finalize(replace(run, elapsed_seconds=12.5))
    before = {p.name: (p.read_bytes(), p.stat().st_ino) for p in writer.path.iterdir() if p.name in ("report.md", "summary.json")}
    writer.close()
    monkeypatch.setattr(report, "_atomic_new", publish)
    with report.ActivationArtifactWriter.resume(writer.path, report.resume_identity(run.collection)) as resumed:
        resumed.finalize(replace(run, elapsed_seconds=999.0))
        resumed.finalize(run)
        assert resumed.is_complete()
        for name, (content, inode) in before.items():
            assert (resumed.path / name).read_bytes() == content
            assert (resumed.path / name).stat().st_ino == inode
        assert json.loads((resumed.path / "summary.json").read_text())["elapsed_seconds"] == 12.5
        with pytest.raises(FileExistsError):
            resumed.append_trial(run.trials[0])


def test_conflicting_final_file_fails_without_replacement(tmp_path):
    run, _ = completed_run()
    writer = report.ActivationArtifactWriter.create(tmp_path / "run", run.collection)
    writer.write_frozen(run.selections)
    for trial in run.trials:
        writer.append_trial(trial)
    (writer.path / "report.md").write_text("conflicting report")
    with pytest.raises(ValueError, match="conflict"):
        writer.finalize(run)
    assert (writer.path / "report.md").read_text() == "conflicting report"
    assert not (writer.path / "complete.json").exists()


@pytest.mark.parametrize("field,value", [
    ("model_revision", "different-weights"), ("layers", [42]),
    ("evaluation_cells", [["q", 0], ["p", 0]]),
    ("config", {"$type": "ActivationEvalConfig", "seed_count": 99}),
    ("app_config", {"$type": "AppConfig", "model": {"max_new_tokens": 999}}),
])
@pytest.mark.parametrize("filename", ["resolved_config.json", "frozen.json"])
def test_resume_rejects_divergent_checkpoint_copies_before_decoding(tmp_path, monkeypatch, field, value, filename):
    run, _ = completed_run()
    writer = report.ActivationArtifactWriter.create(tmp_path / "run", run.collection)
    writer.write_frozen(run.selections)
    writer.close()
    path = writer.path / filename
    metadata = json.loads(path.read_text())
    metadata[field] = value
    path.write_text(json.dumps(metadata))
    def forbidden_decode(_):
        pytest.fail("checkpoint decoded before identity validation")
    monkeypatch.setattr(report, "_decode", forbidden_decode)
    with pytest.raises(ValueError, match="identity|checkpoint"):
        report.ActivationArtifactWriter.resume(writer.path, report.resume_identity(run.collection))


def test_scope_cannot_finalize_same_incomplete_subset_as_journal(tmp_path):
    run, _ = completed_run()
    writer = report.ActivationArtifactWriter.create(tmp_path / "run", run.collection)
    writer.write_frozen(run.selections)
    writer.append_trial(run.trials[0])
    with pytest.raises(ValueError, match="scope"):
        writer.finalize(replace(run, trials=run.trials[:1]))
    assert not (writer.path / "summary.json").exists()


@pytest.mark.parametrize("mutation", ["duplicate", "metadata", "outer_key", "extra", "reordered"])
def test_trial_journal_rejects_inconsistent_or_out_of_scope_rows(tmp_path, mutation):
    run, _ = completed_run()
    writer = report.ActivationArtifactWriter.create(tmp_path / "run", run.collection)
    writer.write_frozen(run.selections)
    for trial in run.trials:
        writer.append_trial(trial)
    path = writer.path / "trials.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if mutation == "duplicate":
        rows.append(rows[0])
    elif mutation == "metadata":
        rows[1]["trial"]["status"] = "invalid_usage"
    elif mutation == "outer_key":
        rows[0]["seed"] = 99
    elif mutation == "extra":
        for row in rows[8:]:
            row["problem_id"] = row["trial"]["problem_id"] = "outside"
    else:
        rows = rows[8:] + rows[:8]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(ValueError, match="trial|scope|metadata"):
        writer.finalize(run)


def test_exclusive_process_lock_releases_on_close_and_abrupt_exit(tmp_path):
    import subprocess
    import sys
    run, _ = completed_run()
    writer = report.ActivationArtifactWriter.create(tmp_path / "run", run.collection)
    writer.write_frozen(run.selections)
    script = "from analogy.experiments.activation_report import ActivationArtifactWriter; import sys, os; w=ActivationArtifactWriter.open_locked(sys.argv[1]); os._exit(0)"
    blocked = subprocess.run([sys.executable, "-c", script, str(writer.path)], capture_output=True, text=True)
    assert blocked.returncode != 0 and "writer lock" in blocked.stderr
    writer.close()
    crashed = subprocess.run([sys.executable, "-c", script, str(writer.path)], capture_output=True, text=True)
    assert crashed.returncode == 0
    with report.ActivationArtifactWriter.resume(writer.path, report.resume_identity(run.collection)) as resumed:
        resumed.append_trial(run.trials[0])


def test_missing_prelean_timing_is_unavailable():
    run, _ = completed_run()
    cells = tuple(replace(c, elapsed_seconds=None) for c in run.collection.cells)
    run = replace(run, collection=replace(run.collection, cells=cells))
    assert report.summarize_activation_run(run)["prelean_elapsed_seconds"] is None


def test_old_unlocked_artifacts_are_rejected_without_adding_files(tmp_path):
    path = tmp_path / "old-run"
    path.mkdir()
    (path / "resolved_config.json").write_text('{"identity":{"schema_version":"activation-analogy-artifacts-v1"}}')
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    with pytest.raises((OSError, ValueError)):
        report.ActivationArtifactWriter.resume(path, {})
    assert before == {p.name: p.read_bytes() for p in path.iterdir()}
