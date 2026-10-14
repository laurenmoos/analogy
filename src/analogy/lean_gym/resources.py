"""Filesystem locations for bundled Lean Gym resources."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


_PACKAGE = files("analogy.lean_gym")
CORPUS_ROOT = Path(str(_PACKAGE.joinpath("corpus"))).resolve()
LEAN_PROJECT_ROOT = Path(str(_PACKAGE.joinpath("lean_project"))).resolve()

__all__ = ["CORPUS_ROOT", "LEAN_PROJECT_ROOT"]

