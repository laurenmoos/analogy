"""Offline verification and theorem splits are the curriculum trust boundary."""

import json
from dataclasses import fields, replace

import pytest

from analogy.config import CurriculumConfig
from analogy.grounded_curriculum import (
    CurriculumError, OnlineCurriculumState, build_grounded_curriculum,
    load_online_curriculum_states,
)
from analogy.lean_gym.models import ProofState, VerificationResult, VerificationStatus as Status
from analogy.proof_views import ProofStructure


def result(text=None, status=Status.OPEN):
    return VerificationResult(status, status in (Status.OPEN, Status.COMPLETE),
                              ProofState.from_text(text) if text else None, (), 0, 0)


class PrefixVerifier:
    """External Lean boundary; derivation, inventory, compiler and storage stay real."""
    states = ("h : P\n⊢ P → P", "h : P\nx : P\n⊢ P", "h : P\nx : P\ny : P\n⊢ P")

    def __init__(self, project):
        self.project_root = project
        self.calls = []

    def state_after(self, problem, prefix):
        self.calls.append((problem.id, prefix, None))
        return result(self.states[min(len(prefix), 2)])

    def try_tactic(self, problem, prefix, candidate):
        self.calls.append((problem.id, prefix, candidate))
        if candidate == "exact h":
            return result(status=Status.COMPLETE)
        if candidate.startswith("intro "):
            # Reference naming is intentionally the compiler's deterministic name.
            return result("h : P\nanalogy_h : P\n⊢ P")
        if candidate == "rw [e]":
            return result(self.states[2])
        return result(status=Status.REJECTED)

    def proof_structure_after(self, problem, prefix):
        shape = '{"root":"implication","children":[{"root":"atom"},{"root":"atom"}]}' if not prefix else '{"root":"atom"}'
        return ProofStructure(1, shape, ())


def config(tmp_path, tactics=("intro analogy_h", "exact h"), ids=("train_a",)):
    problems = [{"id": name, "title": name, "binders": "(P : Prop) (h : P)",
                 "target": "P → P", "source_path": "Example.lean", "source_level": "1",
                 "connective": "implication", "difficulty": 1} for name in ids]
    (tmp_path / "problems.json").write_text(json.dumps({"schema_version": 1,
        "source": {"repository": "fixture", "license": "MIT"}, "problems": problems}))
    (tmp_path / "proofs.json").write_text(json.dumps({"schema_version": 1,
        "proofs": [{"problem_id": name, "tactics": tactics} for name in ids]}))
    (tmp_path / "splits.json").write_text(json.dumps({"schema_version": 1,
        "train": list(ids), "validation": [], "test": []}))
    project = tmp_path / "lean"
    project.mkdir()
    (project / "lean-toolchain").write_text("leanprover/lean4:v4.29.1\n")
    return CurriculumConfig(tmp_path / "problems.json", tmp_path / "proofs.json",
                            tmp_path / "splits.json", tmp_path / "out", project,
                            7, "grounded-curriculum-v1", 0)


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_replays_every_prefix_and_keeps_nonterminal_and_completion(tmp_path):
    cfg = config(tmp_path)
    verifier = PrefixVerifier(cfg.lean_project)
    summary = build_grounded_curriculum(cfg, verifier)
    records = rows(summary.transitions_path)
    assert [(r["problem_id"], r["prefix_index"], r["outcome"]) for r in records] == [
        ("train_a", 0, "novel_successor"), ("train_a", 1, "complete")]
    assert records[0]["sampling_weight"] > records[1]["sampling_weight"]
    assert summary.nonterminal == 1 and summary.completions == 1
    assert ("train_a", (), None) in verifier.calls
    assert ("train_a", ("intro analogy_h",), None) in verifier.calls
    assert all(r["matching_candidate"]["text"] for r in records)


@pytest.mark.parametrize("splits", [
    {"train": ["train_a"], "validation": ["train_a"], "test": []},
    {"train": [], "validation": [], "test": []},
    {"train": ["other"], "validation": [], "test": []},
])
def test_rejects_overlap_missing_and_unknown_theorems_before_any_replay(tmp_path, splits):
    cfg = config(tmp_path)
    cfg.split_manifest.write_text(json.dumps({"schema_version": 1, **splits}))
    verifier = PrefixVerifier(cfg.lean_project)
    with pytest.raises(CurriculumError, match="split"):
        build_grounded_curriculum(cfg, verifier)
    assert verifier.calls == []
    assert not cfg.output_dir.exists()


def test_online_view_is_exact_projection_and_never_leaks_heldout_or_answer(tmp_path):
    cfg = config(tmp_path, ids=("train_a", "heldout"))
    cfg.split_manifest.write_text(json.dumps({"schema_version": 1,
        "train": ["train_a"], "validation": [], "test": ["heldout"]}))
    summary = build_grounded_curriculum(cfg, PrefixVerifier(cfg.lean_project))
    online = load_online_curriculum_states(summary.manifest_path, "train")
    assert len(online) == 2
    assert all(item.problem_id == "train_a" for item in online)
    assert {f.name for f in fields(OnlineCurriculumState)} == {
        "problem_id", "accepted_prefix", "current_state_hash", "split", "transition_id"}
    assert online[0].accepted_prefix == ()
    assert online[1].accepted_prefix == ("intro analogy_h",)
    assert len({item.transition_id for item in online}) == 2
    with pytest.raises(ValueError):
        load_online_curriculum_states(summary.manifest_path, "unknown")


def test_equal_before_text_from_distinct_prefixes_keeps_distinct_identity(tmp_path):
    cfg = config(tmp_path, tactics=("rw [e]", "rw [e]", "exact h"))
    class V(PrefixVerifier):
        states = ("h : P\ne : P ↔ Q\n⊢ P", "h : P\ne : P ↔ Q\n⊢ Q", "h : P\ne : P ↔ Q\n⊢ P")
        def try_tactic(self, problem, prefix, candidate):
            if candidate == "rw [e]":
                return result(self.states[(len(prefix) + 1) % 2])
            return super().try_tactic(problem, prefix, candidate)
    summary = build_grounded_curriculum(cfg, V(cfg.lean_project))
    records = rows(summary.transitions_path)
    assert [r["prefix_index"] for r in records] == [0, 2]
    assert records[0]["before_state_hash"] == records[1]["before_state_hash"]
    assert records[0]["transition_id"] != records[1]["transition_id"]
    assert summary.repeated_state == 1


@pytest.mark.parametrize("kind", ["unsupported_tactic", "compile_mismatch", "verifier_error", "structure_failure"])
def test_failure_categories_are_separate_and_later_prefixes_are_replayed(tmp_path, kind):
    first = "aesop" if kind == "unsupported_tactic" else "intro analogy_h"
    cfg = config(tmp_path, tactics=(first, "exact h"))
    class V(PrefixVerifier):
        def try_tactic(self, problem, prefix, candidate):
            if not prefix:
                if kind == "verifier_error":
                    return result(status=Status.TIMEOUT)
                if kind == "compile_mismatch" and candidate.startswith("intro ") and candidate != first:
                    return result("⊢ Q")
                if first == "aesop":
                    return result(self.states[1])
            return super().try_tactic(problem, prefix, candidate)
        def proof_structure_after(self, problem, prefix):
            if not prefix and kind == "structure_failure":
                raise ValueError("broken structure")
            return super().proof_structure_after(problem, prefix)
    # Candidate and reference must differ to exercise successor mismatch.
    if kind == "compile_mismatch":
        data = json.loads(cfg.reference_proofs.read_text())
        data["proofs"][0]["tactics"][0] = "intro x"
        cfg.reference_proofs.write_text(json.dumps(data))
        first = "intro x"
    summary = build_grounded_curriculum(cfg, V(cfg.lean_project))
    assert getattr(summary, kind) == 1
    assert summary.attempted == 2
    assert [r["prefix_index"] for r in rows(summary.transitions_path)] == [1]


def test_resume_recovers_partial_rows_without_reverification_or_duplicate_ids(tmp_path):
    cfg = config(tmp_path)
    verifier = PrefixVerifier(cfg.lean_project)
    summary = build_grounded_curriculum(cfg, verifier)
    expected = summary.transitions_path.read_bytes()
    with summary.transitions_path.open("ab") as handle:
        handle.write(b'{"partial":')
    with (cfg.output_dir / "journal.jsonl").open("ab") as handle:
        handle.write(b'{"partial":')
    resumed = PrefixVerifier(cfg.lean_project)
    again = build_grounded_curriculum(cfg, resumed)
    assert again.transitions_path.read_bytes() == expected
    assert resumed.calls == []
    assert again == summary


@pytest.mark.parametrize("change", ["seed", "source", "project", "model"])
def test_resume_requires_identical_source_config_project_and_model_hashes(tmp_path, change):
    cfg = config(tmp_path)
    build_grounded_curriculum(cfg, PrefixVerifier(cfg.lean_project))
    if change == "seed":
        cfg = replace(cfg, seed=8)
    elif change == "source":
        cfg.reference_proofs.write_text(cfg.reference_proofs.read_text() + " ")
    elif change == "project":
        (cfg.lean_project / "Extra.lean").write_text("-- changed\n")
    else:
        cfg = replace(cfg, model_revision="different")
    verifier = PrefixVerifier(cfg.lean_project)
    with pytest.raises(CurriculumError, match="identical|hash"):
        build_grounded_curriculum(cfg, verifier)
    assert verifier.calls == []


def test_online_loader_rejects_tampered_final_artifact(tmp_path):
    cfg = config(tmp_path)
    summary = build_grounded_curriculum(cfg, PrefixVerifier(cfg.lean_project))
    summary.transitions_path.write_text(summary.transitions_path.read_text().replace('"train_a"', '"leak"'))
    with pytest.raises(CurriculumError, match="hash"):
        load_online_curriculum_states(summary.manifest_path, "train")


@pytest.mark.parametrize("corruption", ["content", "hash", "missing", "outside_directory"])
def test_online_loader_authenticates_summary_before_returning_states(tmp_path, corruption):
    cfg = config(tmp_path)
    summary = build_grounded_curriculum(cfg, PrefixVerifier(cfg.lean_project))
    summary_path = cfg.output_dir / "summary.json"
    manifest = json.loads(summary.manifest_path.read_text())
    if corruption == "content":
        summary_path.write_text(summary_path.read_text().replace('"retained":2', '"retained":999'))
    elif corruption == "hash":
        manifest["summary_hash"] = "0" * 64
    elif corruption == "missing":
        manifest["summary"] = "missing-summary.json"
    else:
        # A byte-identical file outside the published artifact directory is invalid.
        (tmp_path / "outside-summary.json").write_bytes(summary_path.read_bytes())
        manifest["summary"] = "../outside-summary.json"
    summary.manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(CurriculumError, match="summary artifact"):
        load_online_curriculum_states(summary.manifest_path, "train")


def test_refuses_floating_lean_toolchain_before_replay(tmp_path):
    cfg = config(tmp_path)
    (cfg.lean_project / "lean-toolchain").write_text("leanprover/lean4:nightly\n")
    verifier = PrefixVerifier(cfg.lean_project)
    with pytest.raises(CurriculumError, match="pinned"):
        build_grounded_curriculum(cfg, verifier)
    assert verifier.calls == []


def test_completed_journal_corruption_is_rejected_without_discarding_prior_rows(tmp_path):
    cfg = config(tmp_path)
    build_grounded_curriculum(cfg, PrefixVerifier(cfg.lean_project))
    journal = cfg.output_dir / "journal.jsonl"
    journal.write_text(journal.read_text().replace('"train_a"', '"tampered"'))
    original = journal.read_bytes()
    with pytest.raises(CurriculumError, match="journal"):
        build_grounded_curriculum(cfg, PrefixVerifier(cfg.lean_project))
    assert journal.read_bytes() == original


def test_online_state_freezes_prefix_and_validates_split():
    prefix = ["intro h"]
    state = OnlineCurriculumState("problem", prefix, "state-hash", "train", "opaque-id")
    prefix.append("exact h")
    assert state.accepted_prefix == ("intro h",)
    with pytest.raises(ValueError, match="split"):
        OnlineCurriculumState("problem", (), "state-hash", "unknown", "opaque-id")


def test_interruption_resumes_after_committed_prefix_and_preserves_failure_counts(tmp_path):
    cfg = config(tmp_path, tactics=("aesop", "exact h"))
    class Interrupted(PrefixVerifier):
        def state_after(self, problem, prefix):
            if prefix:
                raise KeyboardInterrupt()
            return super().state_after(problem, prefix)
        def try_tactic(self, problem, prefix, candidate):
            return result(self.states[1]) if candidate == "aesop" else super().try_tactic(problem, prefix, candidate)
    with pytest.raises(KeyboardInterrupt):
        build_grounded_curriculum(cfg, Interrupted(cfg.lean_project))
    assert not (cfg.output_dir / "manifest.json").exists()
    assert len(rows(cfg.output_dir / "journal.jsonl")) == 1
    verifier = PrefixVerifier(cfg.lean_project)
    summary = build_grounded_curriculum(cfg, verifier)
    assert summary.unsupported_tactic == 1 and summary.completions == 1
    assert all(prefix == ("aesop",) for _, prefix, _ in verifier.calls)


@pytest.mark.parametrize("operator,expected_variants", [("exact_local", 2), ("rewrite_forward", 0)])
def test_offline_augmentations_use_zero_action_and_cannot_escape_theorem_split(tmp_path, operator, expected_variants):
    from test_analogy_transfer_generation import RecordingBackend, SOURCE, ALIGNMENT, TRANSFER
    from test_analogy_transfer_loop import grounded_payload
    cfg = config(tmp_path, tactics=("exact h",), ids=("train_a", "heldout"))
    problems = json.loads(cfg.problems.read_text())
    for problem in problems["problems"]:
        problem.update(target="C", binders="(C : Prop) (h : C)")
    cfg.problems.write_text(json.dumps(problems))
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text('{}')
    from analogy.config import ModelConfig
    cfg = replace(cfg, analogies_per_transition=1, model_revision="fixture-v1",
        model=ModelConfig("transformers", str(model_dir), "cpu", -1, 512, 30, False, 0.3, 0.9))
    cfg.split_manifest.write_text(json.dumps({"schema_version": 1,
        "train": ["train_a"], "validation": [], "test": ["heldout"]}))
    class V(PrefixVerifier):
        states = ("h : C\n⊢ C",)
        def proof_structure_after(self, problem, prefix):
            return ProofStructure(1, '{"root":"atom"}', ('{"root":"target"}',))
    class Backend(RecordingBackend):
        hidden_size = 2
    payloads = []
    for index in range(2):
        payloads.extend([SOURCE, ALIGNMENT, TRANSFER, json.dumps(grounded_payload(index=index, operator=operator))])
    backend = Backend(payloads)
    summary = build_grounded_curriculum(cfg, V(cfg.lean_project), generator=backend)
    assert summary.variants == expected_variants
    assert summary.augmentation_failure == 2 - expected_variants
    records = rows(summary.transitions_path)
    for name, split in (("train_a", "train"), ("heldout", "test")):
        group = [row for row in records if row["problem_id"] == name]
        assert len(group) == (2 if expected_variants else 1)
        assert {row["split"] for row in group} == {split}
        if expected_variants:
            assert group[1]["parent_transition_id"] == group[0]["transition_id"]
            assert group[1]["latent_action"] == {"kind": "unsteered_zero", "delta": [0.0, 0.0]}
    assert len(load_online_curriculum_states(summary.manifest_path, "train")) == 1
    assert len(backend.calls) == 8
    assert backend.calls[0][2].tolist() == [0.0, 0.0]
    assert backend.calls[4][2].tolist() == [0.0, 0.0]
    assert all(delta is None for index, (_, _, delta) in enumerate(backend.calls) if index % 4)


def test_pinned_lean_replays_and_compiles_real_nonterminal_transition(tmp_path):
    from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
    from analogy.lean_gym.verifier import LeanSubprocessVerifier
    cfg = config(tmp_path, tactics=("intro analogy_h", "exact h"))
    cfg = replace(cfg, lean_project=LEAN_PROJECT_ROOT)
    summary = build_grounded_curriculum(cfg, LeanSubprocessVerifier(LEAN_PROJECT_ROOT))
    assert summary.nonterminal == 1 and summary.completions == 1
    assert summary.retained == 2


def test_local_model_content_change_blocks_resume_even_with_same_revision(tmp_path):
    from analogy.config import ModelConfig
    cfg = config(tmp_path)
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    weights = model_dir / "weights.safetensors"
    weights.write_bytes(b"original")
    cfg = replace(cfg, model=ModelConfig("transformers", str(model_dir), "cpu", -1, 512, 30, False, 0.3, 0.9),
                  model_revision="immutable-revision")
    build_grounded_curriculum(cfg, PrefixVerifier(cfg.lean_project))
    weights.write_bytes(b"changed")
    verifier = PrefixVerifier(cfg.lean_project)
    with pytest.raises(CurriculumError, match="identical"):
        build_grounded_curriculum(cfg, verifier)
    assert verifier.calls == []


def test_manifest_is_published_last_and_finalization_failure_can_resume(tmp_path, monkeypatch):
    import analogy.grounded_curriculum as curriculum
    cfg = config(tmp_path)
    atomic = curriculum._atomic
    def interrupted_publish(path, content):
        if path.name == "summary.json":
            raise OSError("interrupted finalization")
        atomic(path, content)
    monkeypatch.setattr(curriculum, "_atomic", interrupted_publish)
    with pytest.raises(OSError, match="finalization"):
        build_grounded_curriculum(cfg, PrefixVerifier(cfg.lean_project))
    assert not (cfg.output_dir / "manifest.json").exists()
    monkeypatch.setattr(curriculum, "_atomic", atomic)
    verifier = PrefixVerifier(cfg.lean_project)
    summary = build_grounded_curriculum(cfg, verifier)
    assert len(load_online_curriculum_states(summary.manifest_path, "train")) == 2
    assert verifier.calls == []
