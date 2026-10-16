"""A model repository label alone must never masquerade as a frozen revision."""
from types import SimpleNamespace

import pytest

from analogy.inference import mlx_model


@pytest.mark.parametrize("tokenizer", [
    SimpleNamespace(_commit_hash="a" * 40),
    SimpleNamespace(init_kwargs={"_commit_hash": "a" * 40}),
    SimpleNamespace(name_or_path="/cache/models--mlx-community--Qwen3-8B-4bit/snapshots/" + "a" * 40),
])
def test_loaded_tokenizer_metadata_resolves_immutable_snapshot(tokenizer):
    assert mlx_model.resolve_loaded_revision("mlx-community/Qwen3-8B-4bit", tokenizer) == "mlx-community/Qwen3-8B-4bit@" + "a" * 40


def test_mutable_repository_and_branch_labels_are_explicitly_unresolved():
    tokenizer = SimpleNamespace(name_or_path="mlx-community/Qwen3-8B-4bit", init_kwargs={"revision": "main"})
    assert mlx_model.resolve_loaded_revision("mlx-community/Qwen3-8B-4bit", tokenizer) == "unresolved:mlx-community/Qwen3-8B-4bit"


def test_report_flags_unresolved_real_model_provenance():
    from dataclasses import replace
    from test_activation_report import completed_run
    from analogy.experiments.activation_report import summarize_activation_run, render_activation_report
    run, _ = completed_run()
    run = replace(run, collection=replace(run.collection, model_revision="unresolved:mlx-community/Qwen3-8B-4bit"))
    assert summarize_activation_run(run)["model_revision_resolved"] is False
    assert "UNRESOLVED MODEL REVISION" in render_activation_report(run)
