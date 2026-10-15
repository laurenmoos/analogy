from __future__ import annotations

import tomllib
from pathlib import Path


def test_mlx_lm_stays_on_transformers_4_compatible_release_line() -> None:
    pyproject = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert "mlx-lm>=0.29.1,<0.30" in pyproject["project"]["optional-dependencies"]["mlx"]
