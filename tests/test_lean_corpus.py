from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from analogy.lean_gym.corpus import (
    CorpusValidationError,
    load_problems,
    load_reference_proofs,
    validate_reference_coverage,
)
from analogy.lean_gym.models import VerificationStatus
from analogy.lean_gym.resources import CORPUS_ROOT, LEAN_PROJECT_ROOT
from analogy.lean_gym.verifier import LeanSubprocessVerifier


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def valid_problem(problem_id: str = "identity") -> dict[str, object]:
    return {
        "id": problem_id,
        "title": "Identity",
        "binders": "(P : Prop)",
        "target": "P → P",
        "source_path": "Game/Levels/ImpIntro/L02.lean",
        "source_level": "ImpIntro/2",
        "connective": "implication",
        "difficulty": 1,
    }


def test_load_problems_sorts_and_validates_exact_schema(tmp_path: Path) -> None:
    second = valid_problem("second")
    second["difficulty"] = 2
    path = write_json(
        tmp_path / "problems.json",
        {"schema_version": 1, "source": {"repository": "https://example.test", "license": "MIT"}, "problems": [second, valid_problem()]},
    )

    problems = load_problems(path)

    assert [problem.id for problem in problems] == ["identity", "second"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda root: root["problems"].append(valid_problem()), "duplicate problem id"),
        (lambda root: root["problems"][0].pop("target"), "problems[0]"),
        (lambda root: root["problems"][0].update({"extra": "bad"}), "unknown keys"),
        (lambda root: root["problems"][0].update({"difficulty": 0}), "difficulty"),
        (lambda root: root["problems"][0].update({"target": ""}), "target"),
    ],
)
def test_load_problems_rejects_corrupt_records(tmp_path: Path, mutate, message: str) -> None:
    root = {"schema_version": 1, "source": {"repository": "https://example.test", "license": "MIT"}, "problems": [valid_problem()]}
    mutate(root)
    path = write_json(tmp_path / "bad.json", root)

    with pytest.raises(CorpusValidationError, match=re.escape(message)):
        load_problems(path)


def test_reference_proofs_validate_tactics_and_coverage(tmp_path: Path) -> None:
    proof_path = write_json(
        tmp_path / "proofs.json",
        {"schema_version": 1, "proofs": [{"problem_id": "identity", "tactics": ["intro h", "exact h"]}]},
    )
    problems_path = write_json(
        tmp_path / "problems.json",
        {"schema_version": 1, "source": {"repository": "x", "license": "MIT"}, "problems": [valid_problem()]},
    )
    proofs = load_reference_proofs(proof_path)
    validate_reference_coverage(load_problems(problems_path), proofs)
    assert proofs["identity"].tactics == ("intro h", "exact h")

    bad_path = write_json(tmp_path / "bad-proofs.json", {"schema_version": 1, "proofs": [{"problem_id": "other", "tactics": [""]}]})
    with pytest.raises(CorpusValidationError, match="tactics"):
        load_reference_proofs(bad_path)


def test_production_corpus_has_25_attributed_problems_without_broken_or_level() -> None:
    problems = load_problems(CORPUS_ROOT / "problems.json")
    ids = {item.id for item in problems}
    assert len(problems) == 25
    assert "or_intro_02" not in ids  # Upstream L02 refers to undeclared K.
    for family in ("and", "imp", "or", "iff", "not"):
        assert sum(item.id.startswith(f"{family}_intro_") for item in problems) == 5
    assert all(item.source_path.startswith("Game/Levels/") for item in problems)


def test_every_production_reference_proof_is_cleanly_verified_by_lean() -> None:
    problems = load_problems(CORPUS_ROOT / "problems.json")
    proofs = load_reference_proofs(CORPUS_ROOT / "reference_proofs.json")
    validate_reference_coverage(problems, proofs)
    verifier = LeanSubprocessVerifier(LEAN_PROJECT_ROOT)
    failures: list[str] = []
    for item in problems:
        result = verifier.verify_complete(item, proofs[item.id].tactics)
        if result.status is not VerificationStatus.COMPLETE:
            failures.append(f"{item.id}: {result.status.value}: {[d.message for d in result.diagnostics]}")
    assert failures == []
