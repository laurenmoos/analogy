from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from analogy.progress_judge import AuditIteration, AuditJudgment, AuditStage, JudgeAttempt
from analogy.run_audit import (
    AuditRunError,
    audit_analogy_run,
    load_audit_iterations,
)


def problem(identifier: str, target: str) -> dict[str, object]:
    return {
        "id": identifier,
        "title": identifier,
        "binders": "(P Q : Prop)",
        "target": target,
        "source_path": "Game/Test.lean",
        "source_level": "Test/1",
        "connective": "iff",
        "difficulty": 1,
    }


def iteration_row(
    *,
    problem_id: str = "p1",
    phase: str = "analogy_loop",
    index: int = 0,
    status: str = "accepted",
    actual: str | None = "h : P ↔ Q\n⊢ Q → P",
    discarded_reason: str | None = None,
) -> dict[str, object]:
    state = None if status == "error" else {
        "id": "next",
        "expression": actual or "no goals",
        "language": "lean4",
        "context": [],
    }
    return {
        "epoch": 0,
        "phase": phase,
        "problem_id": problem_id,
        "rollout_id": f"rollout-{problem_id}",
        "iteration_index": index,
        "state_before": {
            "id": "before",
            "expression": "h : P ↔ Q\n⊢ P → Q",
            "language": "lean4",
            "context": [],
        },
        "hop": {
            "hop_id": f"hop-{index}",
            "parent_id": None,
            "domain": "bridge",
            "situation": "cross directions",
            "relation_chain": ["two directions correspond"],
            "mapping": [["forward", "mp"]],
            "continuation": "take the matching direction",
            "predicted_consequence": "one implication remains",
            "rationale": "same relational structure",
            "stop": False,
        },
        "tactic_plan": {
            "predicted_state": "⊢ Q → P",
            "tactics": ["apply h.mp", "apply h.mpr"],
            "motivating_hop_id": f"hop-{index}",
        },
        "predicted_transition": "⊢ Q → P",
        "attempts": [
            {
                "proposal": {
                    "candidate": {
                        "source": "apply h.mp",
                        "score": 0.5,
                        "origin": "analogical",
                    },
                    "predicted_state": "⊢ Q → P",
                },
                "result": {
                    "candidate": {
                        "source": "apply h.mp",
                        "score": 0.5,
                        "origin": "analogical",
                    },
                    "status": status,
                    "state": state,
                    "proof": "by exact h.mp" if status == "complete" else None,
                    "diagnostics": ["type mismatch"] if status == "error" else [],
                },
                "discarded": (
                    None
                    if discarded_reason is None
                    else {"reason": discarded_reason, "result": {"status": status}}
                ),
                "prediction_distance": None,
            }
        ],
        "actual_transition": actual if status == "accepted" else None,
        "comparison": {"surprise": 0.3} if status == "accepted" else None,
        "decision": {"continue_loop": False, "reason": "no_successor", "state": {}},
        "surprise_delta": None,
    }


def write_source(
    root: Path,
    rows: list[dict[str, object]],
    *,
    include_config: bool = True,
    include_summary: bool = True,
) -> tuple[Path, Path]:
    root.mkdir()
    corpus = root.parent / f"{root.name}-problems.json"
    corpus.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"repository": "test", "license": "MIT"},
                "problems": [problem("p1", "(P ↔ Q) → (P → Q)")],
            }
        ),
        encoding="utf-8",
    )
    if include_config:
        (root / "config.json").write_text(json.dumps({"model": "fake", "seed": 3}), encoding="utf-8")
    if include_summary:
        (root / "summary.json").write_text(
            json.dumps({"run_id": "source-run", "condition_summaries": []}),
            encoding="utf-8",
        )
    (root / "analogy_iterations.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return root, corpus


def test_load_normalizes_accepted_rejected_and_completed_analogy_rows(
    tmp_path: Path,
) -> None:
    rows = [
        iteration_row(index=0, status="accepted"),
        iteration_row(index=1, status="error", actual=None),
        iteration_row(index=2, status="complete", actual=None),
        iteration_row(
            index=3,
            status="accepted",
            actual=None,
            discarded_reason="repeated_state",
        ),
        iteration_row(index=4, phase="conventional", status="error", actual=None),
    ]
    source, corpus = write_source(tmp_path / "source", rows)

    loaded = load_audit_iterations(source, corpus)

    assert [item.iteration_index for item in loaded] == [0, 1, 2, 3]
    assert all(isinstance(item, AuditIteration) for item in loaded)
    assert loaded[0].target == "(P ↔ Q) → (P → Q)"
    assert loaded[0].actual_transition == "h : P ↔ Q\n⊢ Q → P"
    assert loaded[0].surprise == 0.3
    assert loaded[1].lean_outcomes == ("error",)
    assert loaded[1].lean_diagnostics == ("type mismatch",)
    assert loaded[2].lean_outcomes == ("complete",)
    assert loaded[2].actual_transition is None
    assert loaded[3].discard_reasons == ("repeated_state",)
    assert json.loads(loaded[0].analogy_card) == rows[0]["hop"]


def test_load_preserves_each_executed_repair_with_its_result(tmp_path: Path) -> None:
    row = iteration_row(status="error", actual=None)
    repair = iteration_row(status="accepted")["attempts"][0]
    repair["proposal"]["candidate"]["source"] = "apply h2 at h1"
    repair["result"]["candidate"]["source"] = "apply h2 at h1"
    repair["result"]["state"]["expression"] = "h1 : Q\n⊢ P"
    row["attempts"].append(repair)
    source, corpus = write_source(tmp_path / "source", [row])

    loaded = load_audit_iterations(source, corpus)[0]

    assert loaded.tactics == ("apply h.mp", "apply h2 at h1")
    assert [attempt.source for attempt in loaded.lean_attempts] == [
        "apply h.mp",
        "apply h2 at h1",
    ]
    assert loaded.lean_attempts[0].diagnostics == ("type mismatch",)
    assert loaded.lean_attempts[1].status == "accepted"
    assert loaded.lean_attempts[1].successor == "h1 : Q\n⊢ P"


def generated_row(phase, *, failed_stage=None):
    from test_analogy_transfer_loop import runner, run, outputs, Evaluator
    raw = outputs(1)
    if failed_stage is not None:
        raw = raw[:failed_stage] + ["invalid", "invalid"]
    result, _, _ = runner(raw)
    rollout = run(result, Evaluator(finish=True)).rollouts[0]
    return json.loads(json.dumps(dict(asdict(rollout.analogy_trace.iterations[0]),
        epoch=1, phase=phase, problem_id="p1", rollout_id=rollout.rollout_id, proof_path_id="path-one")))


def grounded_row(*, failure=None, repair=False):
    from test_analogy_transfer_loop import (
        grounded_payload, grounded_runner, GroundedEvaluator, run, SymbolicState,
        state, NeverCancelled,
    )
    expression = "h : C ∧ C\n⊢ C" if repair else "h : C\n⊢ C"
    payload = grounded_payload(expression, operator="select_left" if repair else "exact_local",
                               proposition="C ∧ C" if repair else "C")
    extra = ()
    if failure == "grounding":
        payload["bindings"][0]["inventory_id"] = "local:999"
        extra = (json.dumps(payload),)
    elif failure == "compilation":
        payload["steps"][0]["operator"] = "rewrite_forward"
    elif repair:
        extra = (json.dumps(grounded_payload(expression, operator="select_right", proposition="C ∧ C")),)
    runner, _, _ = grounded_runner(payload, extra=extra)
    evaluator = GroundedEvaluator("exact h.right" if repair else "exact h")
    outcome = (runner.search(SymbolicState("C", expression, "lean4"), state(), (), (), evaluator, NeverCancelled())
               if repair else run(runner, evaluator))
    return json.loads(json.dumps(dict(asdict(outcome.rollouts[0].analogy_trace.iterations[0]),
        epoch=1, phase="train", problem_id="p1", rollout_id="grounded", proof_path_id="path-one")))


@pytest.mark.parametrize("failure", [None, "grounding", "compilation"])
def test_grounded_audit_preserves_stages_without_inventing_attempts(tmp_path, failure):
    row = grounded_row(failure=failure)
    source, corpus = write_source(tmp_path / "source", [row])
    item = load_audit_iterations(source, corpus)[0]
    assert item.record_kind == "grounded_generated"
    assert item.stage_payloads["inventory"]["payload"] == row["inventory"]
    assert item.stage_payloads["grounding"]["status"] == ("failed" if failure == "grounding" else "available")
    assert item.stage_payloads["compilation"]["status"] == ({None: "available", "grounding": "unavailable", "compilation": "failed"}[failure])
    assert item.tactics == (() if failure else ("exact h",))
    assert item.lean_outcomes == (() if failure else ("complete",))
    assert item.stage_payloads["lean"]["status"] == ("unavailable" if failure else "available")


def test_grounded_audit_retains_repair_provenance_and_is_observer_only(tmp_path):
    row = grounded_row(repair=True)
    source, corpus = write_source(tmp_path / "source", [row, dict(row, proof_path_id="path-two")])
    (source / "reward.json").write_text('{"reward": 7}')
    (source / "training.pt").write_bytes(b"training-artifact")
    before = tree_hashes(source)
    output = tmp_path / "audit"
    audit_analogy_run(source, output, SequenceGenerator((judgment("working", 1),) * 2), corpus, seed=2, max_calls=2)
    recorded = read_jsonl(output / "judgments.jsonl")
    assert len(recorded) == 2
    item = recorded[0]["input"]
    assert item["lean_outcomes"] == ["error", "complete"]
    assert item["stage_payloads"]["repair"]["payload"]["plan"] == row["repaired_grounded_plan"]
    assert item["stage_payloads"]["compilation"]["payload"]["candidates"][0]["binding_ids"] == ["binding-0"]
    assert tree_hashes(source) == before


@pytest.mark.parametrize("field,value", [("grounded_plan_schema_version", "grounded-plan-v99"), ("compiler_version", "grounded-compiler-v99")])
def test_grounded_audit_rejects_future_versions(tmp_path, field, value):
    row = dict(grounded_row(), **{field: value})
    source, corpus = write_source(tmp_path / "source", [row])
    with pytest.raises(AuditRunError, match="unsupported.*version"):
        load_audit_iterations(source, corpus)


@pytest.mark.parametrize("status", ["timeout", "internal_error"])
def test_grounded_audit_preserves_typed_infrastructure_failure(tmp_path, status):
    row = grounded_row()
    result = row["attempts"][0]["result"]
    result.update(status="error", proof=None, verification_status=status)
    source, corpus = write_source(tmp_path / "source", [row])
    evidence = load_audit_iterations(source, corpus)[0].lean_attempts[0]
    assert evidence.status == "error"
    assert evidence.verification_status == status
    assert evidence.infrastructure_failure == status


def test_grounded_audit_distinguishes_original_and_repaired_compilation(tmp_path):
    row = grounded_row(repair=True)
    row.update(failed_stage="compilation", failure_diagnostic="repair unsupported", repaired_compiled_tactics=[],
        repair_compilation_diagnostics=[{"code": "unsupported_operator", "step_id": "step-0", "message": "repair unsupported"}])
    source, corpus = write_source(tmp_path / "source", [row])
    stages = load_audit_iterations(source, corpus)[0].stage_payloads
    assert stages["compilation"]["status"] == "available"
    assert stages["repair"]["status"] == "failed"


def test_grounded_audit_tactics_come_from_executed_result_not_proposal(tmp_path):
    row = grounded_row()
    row["attempts"][0]["proposal"]["candidate"]["source"] = "unexecuted proposal"
    source, corpus = write_source(tmp_path / "source", [row])
    item = load_audit_iterations(source, corpus)[0]
    assert item.tactics == ("exact h",)
    assert item.lean_attempts[0].source == "exact h"


@pytest.mark.parametrize("phase", ["train", "validation", "generated_unsteered",
    "generated_random", "generated_learned", "generated_shuffled"])
@pytest.mark.parametrize("failed_stage", [None, 0, 1, 2, 3])
def test_generated_rows_normalize_without_legacy_hops_in_every_phase(tmp_path, phase, failed_stage):
    row = generated_row(phase, failed_stage=failed_stage)
    source, corpus = write_source(tmp_path / "source", [row])
    loaded = load_audit_iterations(source, corpus)
    assert len(loaded) == 1
    item = loaded[0]
    card = json.loads(item.analogy_card)
    assert card["kind"] == "generated_domain"
    assert card["source"] == row["source"]
    assert card["alignment"] == row["alignment"]
    assert card["transfer"] == row["transfer"]
    assert item.proof_path_id == "path-one"
    if failed_stage is None:
        assert item.lean_outcomes == ("accepted", "complete")
        assert item.actual_transition == "no goals"
    else:
        assert item.lean_outcomes == ()
        assert item.predicted_transition == "(not generated)"
        assert card["failed_stage"] == ("source", "alignment", "transfer", "tactic")[failed_stage]
        assert card["failure_diagnostic"]


@pytest.mark.parametrize("phase", ["train", "validation", "generated_unsteered",
    "generated_random", "generated_learned", "generated_shuffled"])
def test_generated_audit_preserves_distinct_paths_and_resumes_without_rescoring(tmp_path, phase):
    row = generated_row(phase)
    second = dict(row, proof_path_id="path-two")
    source, corpus = write_source(tmp_path / "source", [row, second])
    before = tree_hashes(source)
    generator = SequenceGenerator((judgment("working", 1.0),) * 2)
    output = tmp_path / "audit"
    summary = audit_analogy_run(source, output, generator, corpus, seed=2, max_calls=2)
    assert (summary.eligible, summary.judged) == (2, 2)
    assert {row["proof_path_id"] for row in read_jsonl(output / "judgments.jsonl")} == {"path-one", "path-two"}
    audit_analogy_run(source, output, SequenceGenerator(()), corpus, seed=2, max_calls=4)
    assert tree_hashes(source) == before
    assert len(read_jsonl(output / "judgments.jsonl")) == 2


@pytest.mark.parametrize("failed_stage", [0, 1, 2, 3])
def test_generated_partial_failure_reaches_auditor_with_stage_evidence(tmp_path, failed_stage):
    row = generated_row("train", failed_stage=failed_stage)
    source, corpus = write_source(tmp_path / "source", [row])
    before = tree_hashes(source)
    output = tmp_path / "audit"
    summary = audit_analogy_run(source, output, SequenceGenerator((judgment("analogy", 0.0),)),
                               corpus, seed=2, max_calls=1)
    assert (summary.eligible, summary.judged) == (1, 1)
    recorded = read_jsonl(output / "judgments.jsonl")[0]["input"]
    card = json.loads(recorded["analogy_card"])
    assert card["failed_stage"] == row["failed_stage"]
    assert card["generation_attempts"][row["failed_stage"]]
    assert recorded["lean_attempts"] == []
    assert tree_hashes(source) == before


@pytest.mark.parametrize("missing", ("config.json", "summary.json", "analogy_iterations.jsonl"))
def test_load_requires_each_source_artifact(tmp_path: Path, missing: str) -> None:
    source, corpus = write_source(tmp_path / "source", [iteration_row()])
    (source / missing).unlink()

    with pytest.raises(AuditRunError, match=missing):
        load_audit_iterations(source, corpus)


def test_load_names_malformed_jsonl_line_and_unknown_problem(tmp_path: Path) -> None:
    source, corpus = write_source(tmp_path / "source", [iteration_row()])
    (source / "analogy_iterations.jsonl").write_text("{bad json}\n", encoding="utf-8")
    with pytest.raises(AuditRunError, match="line 1"):
        load_audit_iterations(source, corpus)

    source2, corpus2 = write_source(
        tmp_path / "unknown", [iteration_row(problem_id="missing")]
    )
    with pytest.raises(AuditRunError, match="missing"):
        load_audit_iterations(source2, corpus2)


class SequenceGenerator:
    model_revision = "fake-judge-v1"

    def __init__(self, outputs: tuple[str, ...]) -> None:
        self.outputs = list(outputs)
        self.call_count = 0

    def generate(self, messages, *, seed: int) -> str:
        del messages, seed
        self.call_count += 1
        return self.outputs.pop(0)


def judgment(stage: str, progress: float) -> str:
    return json.dumps(
        {
            "stage": stage,
            "progress_score": progress,
            "analogy_quality": 0.5,
            "confidence": 0.8,
            "diagnosis": f"diagnosis for {stage}",
            "evidence": "Lean status and state transition",
            "suggested_fix": "change the corresponding stage",
        }
    )


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_audit_is_bounded_aggregated_and_source_immutable(tmp_path: Path) -> None:
    source, corpus = write_source(
        tmp_path / "source",
        [iteration_row(index=index, status="error", actual=None) for index in range(3)],
    )
    before = tree_hashes(source)
    generator = SequenceGenerator(
        (
            judgment("analogy", -0.8),
            judgment("tactic_translation", -0.4),
        )
    )

    summary = audit_analogy_run(
        source,
        tmp_path / "audit",
        generator,
        corpus,
        seed=9,
        max_calls=2,
    )

    assert generator.call_count == 2
    assert summary.eligible == 3
    assert summary.attempted == 2
    assert summary.judged == 2
    assert summary.failures == 0
    assert summary.coverage == pytest.approx(2 / 3)
    assert summary.stage_counts == (("analogy", 1), ("tactic_translation", 1))
    assert summary.mean_progress_score == pytest.approx(-0.6)
    assert tree_hashes(source) == before
    rows = read_jsonl(tmp_path / "audit" / "judgments.jsonl")
    assert [row["iteration_index"] for row in rows] == [0, 1]
    assert rows[0]["judgment"]["stage"] == "analogy"
    assert rows[0]["raw_outputs"] == [judgment("analogy", -0.8)]
    persisted = json.loads((tmp_path / "audit" / "summary.json").read_text())
    assert persisted["source_summary"]["run_id"] == "source-run"
    assert persisted["run_complete"] is True
    config = json.loads((tmp_path / "audit" / "config.json").read_text())
    assert config["source_run"] == str(source.resolve())
    assert config["source_hash"]
    assert config["run_complete"] is True


def test_audit_resume_appends_only_missing_keys(tmp_path: Path) -> None:
    source, corpus = write_source(
        tmp_path / "source",
        [iteration_row(index=index, status="error", actual=None) for index in range(3)],
    )
    output = tmp_path / "audit"
    audit_analogy_run(
        source,
        output,
        SequenceGenerator((judgment("lean_execution", -0.7),)),
        corpus,
        seed=2,
        max_calls=1,
    )
    resumed_generator = SequenceGenerator(
        (judgment("proof_progress", 0.1), judgment("working", 0.7))
    )

    summary = audit_analogy_run(
        source,
        output,
        resumed_generator,
        corpus,
        seed=2,
        max_calls=3,
    )

    rows = read_jsonl(output / "judgments.jsonl")
    keys = [
        (row["problem_id"], row["phase"], row["rollout_id"], row["iteration_index"])
        for row in rows
    ]
    assert len(keys) == len(set(keys)) == 3
    assert resumed_generator.call_count == 2
    assert summary.attempted == 3
    assert summary.judged == 3


def test_audit_preserves_failed_judge_output_and_counts_failure(tmp_path: Path) -> None:
    source, corpus = write_source(tmp_path / "source", [iteration_row()])
    generator = SequenceGenerator(("bad", "still bad"))

    summary = audit_analogy_run(
        source,
        tmp_path / "audit",
        generator,
        corpus,
        seed=0,
        max_calls=1,
        max_repair_calls=1,
    )

    assert summary.attempted == 1
    assert summary.judged == 0
    assert summary.failures == 1
    row = read_jsonl(tmp_path / "audit" / "judgments.jsonl")[0]
    assert row["raw_outputs"] == ["bad"]
    assert "JSON" in row["failure"]
    assert generator.call_count == 1


@pytest.mark.parametrize("tail_kind", ("truncated", "valid_without_newline"))
def test_audit_resume_recovers_unterminated_journal_tail(
    tmp_path: Path, tail_kind: str
) -> None:
    source, corpus = write_source(
        tmp_path / "source",
        [iteration_row(index=index, status="error", actual=None) for index in range(2)],
    )
    output = tmp_path / "audit"
    audit_analogy_run(
        source,
        output,
        SequenceGenerator((judgment("lean_execution", -0.7),)),
        corpus,
        seed=2,
        max_calls=1,
    )
    journal = output / "judgments.jsonl"
    if tail_kind == "truncated":
        with journal.open("a", encoding="utf-8") as handle:
            handle.write('{"problem_id":"p1"')
    else:
        journal.write_text(journal.read_text().rstrip("\n"), encoding="utf-8")

    summary = audit_analogy_run(
        source,
        output,
        SequenceGenerator((judgment("working", 0.7),)),
        corpus,
        seed=2,
        max_calls=2,
    )

    rows = read_jsonl(journal)
    assert len(rows) == 2
    assert journal.read_bytes().endswith(b"\n")
    assert summary.model_calls == 2
    if tail_kind == "truncated":
        assert (output / "judgments.jsonl.truncated").read_text() == (
            '{"problem_id":"p1"'
        )


def test_audit_rejects_resume_when_source_or_seed_changes(tmp_path: Path) -> None:
    source, corpus = write_source(tmp_path / "source", [iteration_row()])
    output = tmp_path / "audit"
    audit_analogy_run(
        source,
        output,
        SequenceGenerator((judgment("working", 0.8),)),
        corpus,
        seed=1,
        max_calls=1,
    )

    with pytest.raises(AuditRunError, match="seed"):
        audit_analogy_run(
            source,
            output,
            SequenceGenerator(()),
            corpus,
            seed=2,
            max_calls=1,
        )

    (source / "summary.json").write_text(json.dumps({"run_id": "changed"}))
    with pytest.raises(AuditRunError, match="source"):
        audit_analogy_run(
            source,
            output,
            SequenceGenerator(()),
            corpus,
            seed=1,
            max_calls=1,
        )
