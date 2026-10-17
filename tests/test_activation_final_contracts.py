"""Final-review contracts: budgets, exact usage, controls, and recovery."""
from dataclasses import replace
import json

import pytest

from analogy.experiments import activation_runner as runner, activation_report as report
from analogy.experiments.activation_records import ActivationCondition as Arm
from analogy.experiments.activation_generation import generate_shuffled_slate
from test_activation_generation import generated, ScriptedProvider, CONTEXT, tactics, config
from test_activation_runner import setup, collect
from test_activation_report import completed_run


def test_continuation_cannot_cross_total_compute_ceiling():
    data = setup()
    frozen = runner.freeze_selections(collect(data)).cells[0]
    data[4].allowed = True
    slate = runner._ArmSlate(tactics=("prompted",), cost=12)
    outcome = runner._evaluate_arm(frozen, data[2][0], data[4], data[5], data[1],
        Arm.PROMPTED, slate, view="equal_compute", ceiling=13, final_cap=1,
        lean_cap=3, max_expansions=3)
    assert outcome.immediate_utility == "accepted_progressing"
    assert outcome.continuation_status == "budget_exhausted"
    assert outcome.continuation_lean_calls == 0
    assert outcome.processed_token_positions <= 13


def test_generation_uses_provider_steps_instead_of_retokenized_text():
    from analogy.experiments.activation_generation import generate_neutral_slate
    from types import SimpleNamespace
    class Exact(ScriptedProvider):
        def generate_with_usage(self, messages, *, seed):
            return SimpleNamespace(text=tactics(), processed_token_positions=17)
        def count_token_positions(self, *args):
            raise AssertionError("decoded text must not be retokenized")
    slate = generate_neutral_slate(Exact([]), CONTEXT, seed=1)
    assert slate.processed_token_positions == 17
    assert len(slate.tactics) == 1


def test_profile_sampling_is_strict_and_does_not_mutate_base():
    cfg = config()
    app = cfg.generation_config(cfg.base_config)
    assert app.model.do_sample and app.model.temperature > 0 and 0 < app.model.top_p <= 1
    assert app is not cfg.base_config
    with pytest.raises(ValueError):
        replace(cfg, sampling_temperature=0)
    with pytest.raises(ValueError):
        replace(cfg, sampling_top_p=1.1)


def test_shuffled_prompt_removes_the_entire_target_transfer():
    candidate = generated()
    slate = generate_shuffled_slate(ScriptedProvider([tactics()]), CONTEXT, candidate, seed=3)
    text = slate.messages[0][-1].content
    assert candidate.alignment.proof_operation not in text
    assert all(step not in text for step in candidate.transfer.reasoning_steps)
    assert candidate.transfer.predicted_next_subgoal not in text


def test_collection_checkpoint_resumes_completed_cells_without_model_work(tmp_path):
    data = setup()
    collection = collect(data)
    with report.ActivationArtifactWriter.create(tmp_path / "run", collection) as writer:
        writer.append_collection(collection.cells[0])
        saved = writer.load_collection()
        before = len(data[5].calls)
        resumed = runner.collect_prelean(data[0], data[1], data[3], data[5],
            proof_structures=data[6], completed_cells=saved.cells, cell_sink=writer.append_collection)
        assert resumed.cells[0] == collection.cells[0]
        assert len(data[5].calls) - before == before // 2
        assert runner.freeze_selections(resumed).cells[0].winner == runner.freeze_selections(collection).cells[0].winner


def test_trial_commit_survives_interruption_inside_eight_row_cell(tmp_path, monkeypatch):
    run, _ = completed_run()
    with report.ActivationArtifactWriter.create(tmp_path / "run", run.collection) as writer:
        writer.write_frozen(run.selections)
        original = report.os.replace
        def interrupted(*args):
            raise OSError("interrupted before commit")
        monkeypatch.setattr(report.os, "replace", interrupted)
        with pytest.raises(OSError, match="interrupted"):
            writer.append_trial(run.trials[0])
        assert writer.load_trials() == ()
        monkeypatch.setattr(report.os, "replace", original)
        writer.append_trial(run.trials[0])
        assert report._encode(writer.load_trials()) == report._encode((run.trials[0],))


def test_new_submitted_tactic_gets_post_freeze_diagnostic_and_cost():
    from test_activation_runner import Provider
    class NewFinal(Provider):
        final = False
        def generate(self, messages, *, seed):
            text = super().generate(messages, seed=seed)
            return tactics("new_final") if self.final and '"selected"' in text else text
    data = list(setup())
    data[5] = NewFinal()
    collection = collect(data)
    frozen = runner.freeze_selections(collection)
    original = frozen.cells[0].winner
    data[5].final = True
    data[4].allowed = True
    trial = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], max_topups=0)[0]
    selected = next(o for o in trial.outcomes if o.condition is Arm.SELECTED)
    assert selected.verified_tactic_causal_effects == (("new_final", 1.0),)
    assert sum(c.processed_token_positions for c in selected.charges if c.operation.startswith("verified_tactic")) == 60
    assert trial.compute_ceiling == 252
    assert frozen.cells[0].winner == original


def test_default_serialization_hashes_patch_vectors_and_full_flag_retains_them():
    run, _ = completed_run()
    winner = run.selections.cells[0].winner
    summarized = report._encode(winner)
    assert summarized["patch_deltas"] == []
    assert summarized["patch_delta_summaries"][0]["dimensions"] == 2
    assert report._encode(winner, vectors=True)["patch_deltas"] == [[1.0, 0.0], [1.0, 0.0]]


@pytest.mark.parametrize("pair", ["ab", b"ab"])
def test_role_mapping_rejects_strings_masquerading_as_pairs(pair):
    from analogy.experiments.activation_records import AnalogyCandidateRecord
    with pytest.raises(ValueError, match="role_mapping"):
        AnalogyCandidateRecord("id", "domain", "analogy", (pair,), "limit", ("intro h",))


def test_interrupted_collection_replays_durable_model_calls_in_partial_cell(tmp_path):
    data = setup()
    baseline = collect(data)
    with report.ActivationArtifactWriter.create(tmp_path / "calls", baseline) as writer:
        provider = writer.collection_provider(data[5], data[3][0])
        messages = baseline.cells[0].neutral.messages[0]
        first = provider.generate_with_usage(messages, seed=100)
        count = len(data[5].calls)
        # Fresh adapter simulates process restart halfway through a cell.
        resumed = writer.collection_provider(data[5], data[3][0])
        assert resumed.generate_with_usage(messages, seed=100) == first
        assert len(data[5].calls) == count
        with pytest.raises(ValueError, match="request"):
            writer.collection_provider(data[5], data[3][0]).generate_with_usage(messages, seed=101)


def test_shuffled_donor_replaces_source_alignment_and_transfer_together():
    candidate = generated()
    donor = replace(candidate,
        source=replace(candidate.source, domain="gardening"),
        alignment=replace(candidate.alignment, proof_operation="Combine independent resources."),
        transfer=replace(candidate.transfer, reasoning_steps=("Combine the two resources.",), predicted_next_subgoal="Both requirements."))
    slate = generate_shuffled_slate(ScriptedProvider([tactics()]), CONTEXT, candidate, seed=3, donor=donor)
    text = slate.messages[0][-1].content
    assert "gardening" in text and "Combine independent resources." in text and "Both requirements." in text
    assert candidate.alignment.proof_operation not in text
    assert candidate.transfer.reasoning_steps[0] not in text


def test_progress_per_call_uses_only_eligible_immediate_rescue_calls():
    run, _ = completed_run()
    trial = run.trials[0]
    prompted = next(o for o in trial.outcomes if o.condition is Arm.PROMPTED)
    prompted = replace(prompted, rescue_lean_calls=2, continuation_lean_calls=9)
    eligible = replace(trial, outcomes=(prompted,))
    ineligible = replace(trial, seed=20, rescue_eligible=False, status="invalid_usage", outcomes=(replace(prompted, rescue_lean_calls=99),))
    summary = report.summarize_activation_run(replace(run, trials=(eligible, ineligible)))
    row = next(r for r in summary["rows"] if r["problem_id"] == "p" and r["condition"] == Arm.PROMPTED.value and r["budget_view"] == "equal_tactic")
    assert row["progress_per_lean_call"] == 0.5
    assert row["eligible_rescue_lean_calls"] == 2


def test_verified_tactic_causal_effect_is_separate_from_union_score():
    run, _ = completed_run()
    selected = next(o for o in run.trials[0].outcomes if o.condition is Arm.SELECTED)
    assert selected.verified_tactic_causal_effects == (("selected", 1.0),)
    summary = report.summarize_activation_run(run)
    row = next(r for r in summary["rows"] if r["problem_id"] == "p" and r["condition"] == Arm.SELECTED.value and r["budget_view"] == "equal_tactic")
    assert row["verified_tactic_causal_coverage"] == 1.0
    assert row["verified_tactic_causal_effect"] == 1.0
