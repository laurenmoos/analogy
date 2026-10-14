from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from analogy.lean_gym.resources import LEAN_PROJECT_ROOT


ROOT = Path(__file__).parents[1]


def test_module_cli_completes_saves_and_replays_a_proof(tmp_path: Path) -> None:
    trace = tmp_path / "session.jsonl"
    proof = tmp_path / "answer.lean"
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "ELAN_TOOLCHAIN": "leanprover/lean4:v4.29.1",
    }
    interaction = f'imp_intro_02\nintro h\nexact missing\nexact h\n:save "{proof}"\n:quit\n'
    run = subprocess.run(
        [sys.executable, "-m", "analogy.lean_gym", "--trace", str(trace)],
        cwd=ROOT,
        env=environment,
        input=interaction,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert "Proof complete" in run.stdout
    assert proof.exists()
    assert any(json.loads(line)["accepted"] is False for line in trace.read_text(encoding="utf-8").splitlines() if '"event_type":"tactic_attempt"' in line)

    replay = subprocess.run(
        [sys.executable, "-m", "analogy.lean_gym", "replay", str(trace)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert "Replay matched" in replay.stdout

    lean = subprocess.run(["lake", "env", "lean", str(proof)], cwd=LEAN_PROJECT_ROOT, env=environment, capture_output=True, text=True)
    assert lean.returncode == 0, lean.stderr


def test_module_cli_refuses_to_append_a_second_session_to_explicit_trace(tmp_path: Path) -> None:
    trace = tmp_path / "existing.jsonl"
    trace.write_text("existing session\n", encoding="utf-8")
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    run = subprocess.run(
        [sys.executable, "-m", "analogy.lean_gym", "--trace", str(trace)],
        cwd=ROOT,
        env=environment,
        input="",
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 2
    assert "already exists" in run.stderr
    assert trace.read_text(encoding="utf-8") == "existing session\n"
