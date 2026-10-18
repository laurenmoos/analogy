from dataclasses import replace
from pathlib import Path

from analogy.inference.prompts import configure_prompt_catalog
from analogy.config import load_config
from analogy.lean_gym.models import Problem


ROOT = Path(__file__).parents[1]


def test_prompt_catalog_is_loaded_once_per_path():
    first = configure_prompt_catalog(ROOT / "prompts.yaml")
    second = configure_prompt_catalog(ROOT / "prompts.yaml")

    assert first is second


def test_runtime_builds_shared_dependencies_once(monkeypatch, tmp_path):
    import analogy.runtime as runtime

    runtime.get_runtime.cache_clear()
    created = []
    config = load_config(ROOT / "config.yaml")
    config = replace(
        config,
        benchmark=replace(config.benchmark, selection="exhaustive"),
        run=replace(config.run, output_dir=tmp_path / "runs"),
    )
    model = object()
    verifier = object()
    problems = (Problem("p", "P", "", "P", "p.lean", "p", "test", 1),)

    monkeypatch.setattr(runtime, "load_config", lambda _path: created.append("config") or config)
    monkeypatch.setattr(runtime, "configure_prompt_catalog", lambda _path: created.append("prompts") or object())
    monkeypatch.setattr(runtime, "get_model_provider", lambda _config: created.append("model") or model)
    monkeypatch.setattr(runtime, "LeanSubprocessVerifier", lambda _root: created.append("lean") or verifier)
    monkeypatch.setattr(runtime, "load_problems", lambda _path: created.append("problems") or problems)
    monkeypatch.setattr(runtime, "runtime_config", lambda _config: config)

    first = runtime.get_runtime(Path("config.yaml"))
    second = runtime.get_runtime(Path("config.yaml"))

    assert first is second
    assert (first.model, first.verifier, first.problems) == (
        model, verifier, problems
    )
    assert created == ["config", "problems", "prompts", "model", "lean"]


def test_runtime_skips_mlx_and_lean_when_previous_failures_are_resolved(
    monkeypatch, tmp_path
):
    import analogy.runtime as runtime

    runtime.get_runtime.cache_clear()
    configured = load_config(ROOT / "config.yaml")
    configured = replace(
        configured,
        benchmark=replace(configured.benchmark, selection="previous_failures"),
        run=replace(configured.run, output_dir=tmp_path / "runs"),
    )
    problem = Problem("done", "Done", "", "P", "done.lean", "done", "test", 1)
    index = runtime.ProblemIndex.load(configured, (problem,))
    index.record("done", "complete")
    monkeypatch.setattr(runtime, "load_config", lambda _path: configured)
    monkeypatch.setattr(runtime, "load_problems", lambda _path: (problem,))
    monkeypatch.setattr(
        runtime,
        "configure_prompt_catalog",
        lambda _path: (_ for _ in ()).throw(AssertionError("prompts loaded")),
    )
    monkeypatch.setattr(
        runtime,
        "get_model_provider",
        lambda _config: (_ for _ in ()).throw(AssertionError("model loaded")),
    )
    monkeypatch.setattr(
        runtime,
        "LeanSubprocessVerifier",
        lambda _root: (_ for _ in ()).throw(AssertionError("Lean loaded")),
    )

    result = runtime.get_runtime(Path("config.yaml"))

    assert result.problems == ()
    assert result.model is None
    assert result.verifier is None
