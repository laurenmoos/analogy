from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from analogy.config import load_config
from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
from analogy.lean_gym.verifier import LeanSubprocessVerifier
from analogy.model import run_capability


ROOT = Path(__file__).parents[1]


class FakeGenerator:
    def __init__(self, output: str | BaseException) -> None:
        self.output = output
        self.calls = []

    def generate(self, messages, *, seed: int) -> str:
        self.calls.append((messages, seed))
        if isinstance(self.output, BaseException):
            raise self.output
        return self.output


def write_identity_corpus(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"repository": "secret-repository", "license": "MIT"},
                "problems": [
                    {
                        "id": "identity",
                        "title": "secret-title",
                        "binders": "(P : Prop)",
                        "target": "P → P",
                        "source_path": "secret/source.lean",
                        "source_level": "secret-level",
                        "connective": "implication",
                        "difficulty": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def capability_config(tmp_path: Path):
    config = load_config(ROOT / "config.yaml")
    return replace(
        config,
        benchmark=replace(
            config.benchmark,
            corpus=write_identity_corpus(tmp_path / "problems.json"),
        ),
        run=replace(config.run, output_dir=tmp_path / "runs"),
    )


def test_capability_run_lean_verifies_generated_proof_and_never_overwrites(
    tmp_path: Path,
) -> None:
    config = capability_config(tmp_path)
    generator = FakeGenerator('["intro h", "exact h"]')
    verifier = LeanSubprocessVerifier(LEAN_PROJECT_ROOT)

    first = run_capability(config, generator, verifier)
    second = run_capability(config, generator, verifier)

    assert first[0].problem_id == "identity"
    assert first[0].tactics == ("intro h", "exact h")
    assert first[0].solved is True
    assert first[0].failure is None
    assert second[0].solved is True
    assert second[0].tactics == first[0].tactics
    run_directories = sorted((tmp_path / "runs/capability").iterdir())
    assert len(run_directories) == 2
    assert run_directories[0].name != run_directories[1].name
    for directory in run_directories:
        record_lines = (directory / "identity.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        assert len(record_lines) == 1
        assert json.loads(record_lines[0])["solved"] is True
        summary = json.loads((directory / "run.json").read_text(encoding="utf-8"))
        assert summary["attempted_count"] == 1
        assert summary["solved_count"] == 1
        assert summary["seed"] == config.run.seed
        assert summary["decoding"] == {
            "do_sample": config.model.do_sample,
            "temperature": config.model.temperature,
            "top_p": config.model.top_p,
        }

    messages, seed = generator.calls[0]
    prompt = "\n".join(item.content for item in messages)
    assert seed == config.run.seed
    assert "(P : Prop)" in prompt
    assert "P → P" in prompt
    assert "secret-title" not in prompt
    assert "secret-repository" not in prompt
    assert "secret/source.lean" not in prompt


@pytest.mark.parametrize(
    ("output", "failure"),
    [
        ("not json", "model_output"),
        ("[]", "model_output"),
        ('["intro h", 2]', "model_output"),
        (TimeoutError("generation timed out"), "generation_timeout"),
    ],
)
def test_capability_run_records_generation_failures_and_remains_resumable(
    tmp_path: Path, output: str | BaseException, failure: str
) -> None:
    config = capability_config(tmp_path)

    results = run_capability(
        config,
        FakeGenerator(output),
        LeanSubprocessVerifier(LEAN_PROJECT_ROOT),
    )

    assert results[0].solved is False
    assert results[0].failure is not None
    assert results[0].failure.startswith(failure)
    record = next((tmp_path / "runs/capability").glob("*/identity.jsonl"))
    assert json.loads(record.read_text(encoding="utf-8"))["failure"].startswith(
        failure
    )


def test_capability_run_does_not_count_rejected_tactics_as_solved(
    tmp_path: Path,
) -> None:
    results = run_capability(
        capability_config(tmp_path),
        FakeGenerator('["intro h", "exact missing"]'),
        LeanSubprocessVerifier(LEAN_PROJECT_ROOT),
    )

    assert results[0].solved is False
    assert results[0].failure is not None
    assert results[0].failure.startswith("lean_rejected")
