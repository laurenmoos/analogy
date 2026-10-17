from __future__ import annotations

import subprocess
import os

from analogy.lean_gym.resources import LEAN_PROJECT_ROOT


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
