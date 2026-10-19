from __future__ import annotations

import json
from pathlib import Path

from analogy.main import load_config
from analogy.lean_gym.corpus import load_problems, load_reference_proofs, validate_reference_coverage
from analogy.lean_gym.models import VerificationStatus
from analogy.lean_gym.verifier import LeanSubprocessVerifier


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "src/analogy/lean_gym/corpus"
REVISION = "62a13de410999c20f60ec2f3be93457dcaac0d62"


def _assets():
    problems = load_problems(CORPUS / "problems.json")
    proofs = load_reference_proofs(CORPUS / "reference_proofs.json")
    metadata = json.loads((CORPUS / "benchmark_metadata.json").read_text(encoding="utf-8"))
    return problems, proofs, metadata


def test_active_corpus_is_thirty_sourced_propl_ood_problems() -> None:
    problems, proofs, metadata = _assets()

    assert len(problems) == 30
    assert len({problem.id for problem in problems}) == 30
    assert all(problem.id.startswith("propl_ood_") for problem in problems)
    assert all(problem.binders == "(p1 p2 p3 p4 p5 : Prop)" for problem in problems)
    assert all(problem.connective == "mixed" for problem in problems)
    assert all(all(symbol in problem.target for symbol in ("∧", "∨", "→")) for problem in problems)
    assert all(len(proofs[problem.id].tactics) >= 13 for problem in problems)
    assert not any(problem.id.startswith(("and_intro_", "or_intro_", "imp_intro_")) for problem in problems)
    validate_reference_coverage(problems, proofs)

    assert metadata["source"]["dataset"] == "KomeijiForce/PropL"
    assert metadata["source"]["revision"] == REVISION
    assert metadata["source"]["split"] == "test_ood"
    assert len(metadata["records"]) == 30
    assert {record["problem_id"] for record in metadata["records"]} == {problem.id for problem in problems}
    assert all(record["lean_status"] == "complete" for record in metadata["records"])
    assert all(record["one_step_simp_status"] != "complete" for record in metadata["records"])


def test_default_config_runs_the_whole_corpus_with_sufficient_depth() -> None:
    problems, proofs, _ = _assets()
    config = load_config(ROOT / "config.yaml")

    assert config.benchmark.selection == "exhaustive"
    assert config.search.max_steps >= max(len(proofs[problem.id].tactics) for problem in problems)


def test_every_propl_reference_proof_completes_in_local_lean() -> None:
    problems, proofs, _ = _assets()
    verifier = LeanSubprocessVerifier(ROOT, timeout_seconds=20)

    failures: list[str] = []
    for problem in problems:
        result = verifier.verify_complete(problem, proofs[problem.id].tactics)
        if result.status is not VerificationStatus.COMPLETE:
            messages = "; ".join(item.message for item in result.diagnostics)
            failures.append(f"{problem.id}: {result.status.value}: {messages}")

    assert failures == []


def test_no_propl_problem_completes_with_one_simp() -> None:
    problems, _, _ = _assets()
    verifier = LeanSubprocessVerifier(ROOT, timeout_seconds=20)

    trivial = [
        problem.id
        for problem in problems
        if verifier.try_tactic(problem, (), "simp").status
        is VerificationStatus.COMPLETE
    ]

    assert trivial == []
