from dataclasses import replace
from pathlib import Path

import pytest

from analogy.config import load_config
from analogy.model import ModelConfigurationError, QwenGenerator


ROOT = Path(__file__).parents[1]


def test_factory_selects_transformers(monkeypatch) -> None:
    from analogy.model_backend import load_model_backend

    config = load_config(ROOT / "config.yaml").model
    monkeypatch.setattr(
        QwenGenerator, "from_config", lambda value: ("hf", value)
    )

    assert load_model_backend(config) == ("hf", config)


def test_factory_selects_mlx_lazily(monkeypatch) -> None:
    import analogy.model_backend as module

    config = replace(
        load_config(ROOT / "config.yaml").model, backend="mlx"
    )

    class MLXGenerator:
        @classmethod
        def from_config(cls, value):
            return "mlx", value

    imported = type("Imported", (), {"MLXQwenGenerator": MLXGenerator})
    monkeypatch.setattr(module, "import_module", lambda name: imported)

    assert module.load_model_backend(config) == ("mlx", config)


def test_factory_reports_missing_mlx_extra(monkeypatch) -> None:
    import analogy.model_backend as module

    config = replace(
        load_config(ROOT / "config.yaml").model, backend="mlx"
    )

    def missing(name):
        raise ImportError(name)

    monkeypatch.setattr(module, "import_module", missing)

    with pytest.raises(ModelConfigurationError, match=r"analogy\[mlx\]"):
        module.load_model_backend(config)
