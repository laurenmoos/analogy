from pathlib import Path

from analogy.inference.prompts import configure_prompt_catalog


ROOT = Path(__file__).parents[1]


def test_prompt_catalog_is_loaded_once_per_path():
    first = configure_prompt_catalog(ROOT / "prompts.yaml")
    second = configure_prompt_catalog(ROOT / "prompts.yaml")

    assert first is second


def test_runtime_builds_shared_dependencies_once(monkeypatch):
    import analogy.runtime as runtime

    runtime.get_runtime.cache_clear()
    created = []
    config = object()
    model = object()
    verifier = object()
    problems = (object(),)

    monkeypatch.setattr(runtime, "load_config", lambda _path: created.append("config") or config)
    monkeypatch.setattr(runtime, "configure_prompt_catalog", lambda _path: created.append("prompts") or object())
    monkeypatch.setattr(runtime, "get_model_provider", lambda _config: created.append("model") or model)
    monkeypatch.setattr(runtime, "LeanSubprocessVerifier", lambda _root: created.append("lean") or verifier)
    monkeypatch.setattr(runtime, "load_problems", lambda _path: created.append("problems") or problems)
    monkeypatch.setattr(
        runtime,
        "runtime_config",
        lambda _config: type(
            "Config",
            (),
            {
                "prompts": type("Prompts", (), {"path": Path("prompts.yaml")})(),
                "model": object(),
                "benchmark": type("Benchmark", (), {"corpus": Path("problems.json")})(),
            },
        )(),
    )

    first = runtime.get_runtime(Path("config.yaml"))
    second = runtime.get_runtime(Path("config.yaml"))

    assert first is second
    assert (first.model, first.verifier, first.problems) == (
        model, verifier, problems
    )
    assert created == ["config", "prompts", "model", "lean", "problems"]
