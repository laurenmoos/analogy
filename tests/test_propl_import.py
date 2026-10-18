from __future__ import annotations

import csv
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/import_propl_ood.py"


def _load_importer():
    spec = importlib.util.spec_from_file_location("propl_importer", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("data_id", "input", "output"))
        writer.writeheader()
        writer.writerows(rows)


def _trace(*tactics: tuple[int, int, str]) -> str:
    chunks = []
    for state, attempt, tactic in tactics:
        chunks.append(f"state_{state}_tactic_{attempt}:\n{tactic}\nstate_{state + 1}:\n⊢ next")
    return "\n".join(chunks)


def test_parse_records_preserves_multiline_target_and_tactics(tmp_path: Path) -> None:
    source = tmp_path / "normal.csv"
    _write_csv(
        source,
        [
            {
                "data_id": "42",
                "input": "state_0:\np1 p2 : Prop\n⊢ (p1 →\n  p2) ∨ True\n",
                "output": _trace((0, 0, "intro h1"), (1, 0, "exact Or.inr True.intro")),
            }
        ],
    )

    record = _load_importer().parse_records(source)["42"]

    assert record.target == "(p1 →\n  p2) ∨ True"
    assert record.tactics == ("intro h1", "exact Or.inr True.intro")


def test_pair_records_counts_alternative_attempts_and_rejects_nested_traces(tmp_path: Path) -> None:
    normal = tmp_path / "normal.csv"
    backtrack = tmp_path / "backtrack.csv"
    base_input = "state_0:\np1 p2 p3 : Prop\n⊢ (p1 ∧ p2) ∨ (p2 → p3)\n"
    _write_csv(
        normal,
        [
            {"data_id": "flat", "input": base_input, "output": _trace((0, 0, "apply Or.inl"))},
            {"data_id": "case", "input": base_input, "output": _trace((0, 0, "case inl h =>"))},
            {"data_id": "have", "input": base_input, "output": _trace((0, 0, "have h : p1 := by"))},
        ],
    )
    _write_csv(
        backtrack,
        [
            {
                "data_id": "flat",
                "input": base_input,
                "output": _trace((0, 0, "apply Or.inr"), (0, 1, "apply Or.inl"), (1, 0, "exact h")),
            },
            {"data_id": "case", "input": base_input, "output": _trace((0, 0, "case inl h =>"))},
            {"data_id": "have", "input": base_input, "output": _trace((0, 0, "have h : p1 := by"))},
        ],
    )

    pairs = _load_importer().pair_records(normal, backtrack)

    assert pairs["flat"].failed_attempts == 1
    assert pairs["flat"].flat_trace is True
    assert pairs["case"].flat_trace is False
    assert pairs["have"].flat_trace is False


def test_rank_candidates_is_deterministic_and_prefers_backtracking() -> None:
    importer = _load_importer()
    candidates = [
        importer.Candidate("long", "p1 ∧ p2 ∨ (p2 → p3)", ("x",) * 20, 0, True),
        importer.Candidate("back", "p1 ∧ p2 ∨ (p2 → p3)", ("x",) * 15, 2, True),
        importer.Candidate("more", "p1 ∧ p2 ∨ (p2 → p3)", ("x",) * 16, 2, True),
    ]

    ranked = importer.rank_candidates(candidates)

    assert [item.data_id for item in ranked] == ["more", "back", "long"]


def test_candidate_filter_requires_mixed_connectives_length_and_flat_trace() -> None:
    importer = _load_importer()
    eligible = importer.Candidate("ok", "p1 ∧ p2 ∨ (p2 → p3)", ("x",) * 15, 0, True)

    assert importer.is_static_candidate(eligible)
    assert not importer.is_static_candidate(
        importer.Candidate("short", eligible.target, ("x",) * 14, 0, True)
    )
    assert not importer.is_static_candidate(
        importer.Candidate("simple", "p1 ∧ p2 → p3", ("x",) * 15, 0, True)
    )
    assert not importer.is_static_candidate(
        importer.Candidate("nested", eligible.target, ("x",) * 15, 0, False)
    )
