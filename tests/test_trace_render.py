from __future__ import annotations

import json
from pathlib import Path

import pytest

from analogy.trace_render import render_theorem_trace
from analogy.training import RescueRunWriter


FIXTURES = Path(__file__).parent / "fixtures" / "generated_analogy_traces"


def state(number, goal, context=()):
    return {"id": str(number), "expression": f"h : R\n⊢ {goal}", "context": context}


def attempt(tactic, status, successor=None, discarded=None, stage="analogy"):
    result = {"candidate": {"source": tactic}, "status": status,
              "state": successor, "diagnostics": ["type mismatch"] if status == "error" else []}
    return {"result": result, "discarded": discarded, "stage": stage}


def complex_trace():
    source = {"domain": "navigation", "source_before": "East bank blocked",
              "source_after": "West bank reached", "relation_chain": ["Bridge links banks"],
              "rationale": "Reverse a crossing", "transformation": {"domain_neutral_summary": "Reverse direction"}}
    return {"problem": {"id": "complex", "target": "R → Q"}, "initial_state": state(0, "R → Q"),
            "ordinary_expansions": [
                {"node": {"state": state(0, "R → Q")}, "attempts": [attempt("intro hR", "accepted", state(1, "Q"))["result"]]},
                {"node": {"state": state(1, "Q")}, "attempts": [attempt("apply hPQ", "accepted", state(2, "P"))["result"]]},
            ], "rollouts": [{"state_id": "2", "analogy_trace": {"iterations": [
                {"iteration_index": 27, "state_before": state(2, "P"), "source": source,
                 "alignment": {"mapping": [["East", "R"], ["West", "P"]], "invariant": "A reversible connection",
                               "proof_operation": "Use reverse equivalence", "limitation": "Orientation matters"},
                 "transfer": {"active_subgoal": "P", "reasoning_steps": ["Reduce P to the available R"], "predicted_next_subgoal": "R"},
                 "finisher_state": state(3, "R"), "attempts": [
                     attempt("bad", "error"),
                     attempt("loop", "accepted", state(9, "P"), {"reason": "repeated_state"}),
                     attempt("apply hPR.mpr", "accepted", state(3, "R")),
                     attempt("assumption", "complete", stage="conventional_finisher"),
                 ]}], "stop_reason": "complete"}}], "termination": "complete"}


def test_trace_renderer_connects_failures_repeats_and_completion():
    rendered = render_theorem_trace(complex_trace())
    assert "THEOREM TARGET (fixed): R → Q" in rendered
    assert "[State 2] ACTIVE SUBGOAL: P" in rendered
    assert "[State 2 unchanged]" in rendered
    assert "[DISCARDED: repeated_state" in rendered
    assert "[COMPLETE: theorem target proved]" in rendered
    assert "Analogy 1" in rendered
    assert "Analogy 27" not in rendered
    assert "iteration_index" not in rendered
    assert rendered.index("SOURCE ANALOGY") < rendered.index("ALIGNMENT") < rendered.index("TRANSFERRED PROOF REASONING")
    assert "TACTIC [State 2]: bad" in rendered
    assert "TACTIC [State 3]: assumption" in rendered
    assert "no remaining subgoals" in rendered


@pytest.mark.parametrize("problem_id", ["imp_intro_04", "imp_intro_05", "not_intro_05", "iff_intro_04", "iff_intro_05"])
def test_readability_fixture(problem_id):
    record = json.loads((FIXTURES / f"{problem_id}.json").read_text())
    assert render_theorem_trace(record) == (FIXTURES / f"{problem_id}.md").read_text()


def test_multiple_subgoals_and_legacy_iteration_are_explicit():
    record = complex_trace()
    iteration = record["rollouts"][0]["analogy_trace"]["iterations"][0]
    iteration["state_before"]["expression"] = "case left\n⊢ P\n\ncase right\n⊢ Q"
    iteration.pop("source")
    iteration.pop("alignment")
    iteration.pop("transfer")
    iteration["hop"] = {"domain": "legacy", "situation": "Legacy card", "mapping": [], "continuation": "Old reasoning"}
    rendered = render_theorem_trace(record)
    assert "ACTIVE SUBGOALS:" in rendered
    assert "1. P" in rendered and "2. Q" in rendered
    assert "LEGACY ANALOGY" in rendered


def test_renderer_does_not_invent_intermediate_states_for_replayed_prefix():
    record = {"problem": {"id": "replay", "target": "R → Q"},
              "initial_state": state(4, "P", ("intro; apply hPQ",)), "rollouts": []}
    rendered = render_theorem_trace(record)
    assert "intro; apply hPQ" in rendered
    assert "intermediate states not recorded" in rendered
    assert "ACTIVE SUBGOAL: P" in rendered


def test_failed_generated_stage_keeps_stage_boundaries_and_no_invented_lean_edge():
    record = complex_trace()
    iteration = record["rollouts"][0]["analogy_trace"]["iterations"][0]
    iteration.update(source=None, alignment=None, transfer=None, attempts=[],
                     failed_stage="source", failure_diagnostic="invalid source")
    rendered = render_theorem_trace(record)
    for label in ("SOURCE ANALOGY", "ALIGNMENT", "TRANSFERRED PROOF REASONING", "TACTIC", "LEAN RESULT"):
        assert label in rendered
    assert "LEAN RESULT: not attempted" in rendered
    assert "[FAILED STAGE: source] invalid source" in rendered


def test_finalization_joins_rollouts_by_problem_phase_and_epoch(tmp_path):
    writer = RescueRunWriter.create(tmp_path, "run", {})
    record = complex_trace()
    rollout = record.pop("rollouts")[0]
    writer.append_jsonl("problems.jsonl", {"problem_id": "complex", "phase": "test", "epoch": 1,
                                           "proof_trace": record})
    for pid, epoch, phase, tactic in (("complex", 1, "test", "correct"),
                                     ("other", 1, "test", "wrong problem"),
                                     ("complex", 0, "test", "wrong epoch"),
                                     ("complex", 1, "train", "wrong phase")):
        row = json.loads(json.dumps(rollout))
        row.update(problem_id=pid, epoch=epoch, phase=phase)
        row["analogy_trace"]["iterations"][0]["attempts"][0]["result"]["candidate"]["source"] = tactic
        writer.append_jsonl("rollouts.jsonl", row)
    writer.finalize_readable_traces()
    rendered = (writer.path / "readable_traces.md").read_text()
    assert "correct" in rendered
    assert "wrong problem" not in rendered
    assert "wrong epoch" not in rendered
    assert "wrong phase" not in rendered


def test_replayed_proof_paths_do_not_share_rollouts_with_other_invocations(tmp_path):
    writer = RescueRunWriter.create(tmp_path, "run", {})
    record = complex_trace()
    rollout = record.pop("rollouts")[0]
    record["proof_path_id"] = "chosen"
    writer.append_jsonl("problems.jsonl", {"problem_id": "complex", "phase": "train", "epoch": 1,
                                           "proof_trace": record})
    for identity, tactic in (("chosen", "chosen tactic"), ("other", "wrong proof path")):
        row = json.loads(json.dumps(rollout))
        row.update(problem_id="complex", phase="train", epoch=1,
                   rollout_id="same-state:same-rollout", proof_path_id=identity)
        row["analogy_trace"]["iterations"][0]["attempts"][0]["result"]["candidate"]["source"] = tactic
        writer.append_jsonl("rollouts.jsonl", row)
    writer.finalize_readable_traces()
    rendered = (writer.path / "readable_traces.md").read_text()
    assert "chosen tactic" in rendered
    assert "wrong proof path" not in rendered


def test_model_fences_cannot_escape_the_trace():
    record = complex_trace()
    record["rollouts"][0]["analogy_trace"]["iterations"][0]["source"]["source_before"] = "```\nmisleading target"
    rendered = render_theorem_trace(record)
    assert "````text" in rendered
    assert rendered.rstrip().endswith("````")
    assert rendered.count("THEOREM TARGET (fixed):") == 1


def test_direct_rescue_and_ordinary_rejection_keep_their_source_state():
    record = complex_trace()
    record["rollouts"] = [{"state_id": "2", "attempts": [attempt("direct", "error")]}]
    record["ordinary_expansions"].append({"node": {"state": state(2, "P")},
                                           "attempts": [attempt("ordinary_bad", "error")["result"]]})
    rendered = render_theorem_trace(record)
    assert "TACTIC [State 2]: direct [direct rescue]" in rendered
    assert "TACTIC [State 2]: ordinary_bad [ordinary search]" in rendered
    assert rendered.count("[State 2 unchanged]") == 2


def test_finalization_groups_by_theorem_and_keeps_machine_artifacts(tmp_path):
    writer = RescueRunWriter.create(tmp_path, "run", {})
    record = complex_trace()
    writer.append_jsonl("problems.jsonl", {"problem_id": "complex", "phase": "test", "epoch": 0,
                                           "proof_trace": record})
    writer.write_json("summary.json", {"solved": 1})
    before = {path.name: path.read_bytes() for path in writer.path.glob("*.json*")}
    writer.finalize_readable_traces()
    assert "THEOREM TARGET (fixed): R → Q" in (writer.path / "readable_traces.md").read_text()
    assert before == {path.name: path.read_bytes() for path in writer.path.glob("*.json*")}


@pytest.mark.parametrize("failure", ["render", "replace", "flush"])
def test_interrupted_render_preserves_prior_markdown_and_machine_artifacts(tmp_path, monkeypatch, failure):
    import analogy.trace_render as renderer

    writer = RescueRunWriter.create(tmp_path, "run", {})
    writer.append_jsonl("problems.jsonl", {"problem_id": "complex", "proof_trace": complex_trace()})
    writer.write_json("summary.json", {"solved": 1})
    writer.finalize_readable_traces()
    before = {path.name: path.read_bytes() for path in writer.path.iterdir() if path.is_file()}

    def fail(*args, **kwargs):
        raise OSError("interrupted")

    if failure == "flush":
        monkeypatch.setattr(renderer.os, "fsync", fail)
    else:
        monkeypatch.setattr(renderer, "render_theorem_trace" if failure == "render" else "_replace", fail)
    with pytest.warns(RuntimeWarning, match="readable trace"):
        writer.finalize_readable_traces()
    assert before == {path.name: path.read_bytes() for path in writer.path.iterdir() if path.is_file()}
