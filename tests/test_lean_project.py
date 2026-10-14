from __future__ import annotations

import subprocess
import os
import sys
from pathlib import Path

from analogy.lean_gym.resources import LEAN_PROJECT_ROOT


ROOT = Path(__file__).parents[1]
LEAN_ENV = {**os.environ, "ELAN_TOOLCHAIN": "leanprover/lean4:v4.29.1"}


def test_pinned_lean_project_builds_with_expected_version() -> None:
    version = subprocess.run(
        ["lake", "env", "lean", "--version"],
        cwd=LEAN_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=LEAN_ENV,
    )
    assert version.returncode == 0, version.stderr
    assert "Lean (version 4.29.1" in version.stdout

    build = subprocess.run(
        ["lake", "env", "lean", "LeanGym/Prelude.lean"],
        cwd=LEAN_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=LEAN_ENV,
    )
    assert build.returncode == 0, build.stderr


def test_module_entry_point_displays_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "analogy.lean_gym", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONPATH": str(ROOT / "src")},
    )
    assert result.returncode == 0, result.stderr
    assert "Lean logic proof REPL" in result.stdout
