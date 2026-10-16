import json

import pytest

from analogy.model import normalize_lean4_tactic, parse_tactic_candidates, parse_tactic_sequence
from analogy.mlx_model import _mlx_import_diagnostic


@pytest.mark.parametrize("source,expected", [
    ("cases h with hP hQ", "cases h"),
    (" cases   h   with   hP   hQ ", "cases h"),
    ("cases «h with x»", "cases «h with x»"),
    ("cases h with | inl hp => exact hp", "cases h with | inl hp => exact hp"),
])
def test_normalize_lean3_cases_only_when_standalone(source, expected):
    assert normalize_lean4_tactic(source) == expected


def test_candidate_normalization_precedes_deduplication_and_weighting():
    candidates = parse_tactic_candidates(json.dumps([
        {"tactic": "cases h with hp hq", "score": 1.0},
        {"tactic": "cases h", "score": 3.0},
        {"tactic": "exact hp", "score": 1.0},
    ]), 3)
    assert [(item.source, item.score) for item in candidates] == [
        ("cases h", 0.75), ("exact hp", 0.25),
    ]


def test_capability_sequence_is_normalized_too():
    assert parse_tactic_sequence('["cases h with hp hq"]') == ("cases h",)


def test_mlx_import_diagnostic_distinguishes_missing_extra_from_unavailable_metal():
    missing = ImportError("No module named 'mlx_lm'", name="mlx_lm")
    metal = ImportError("[metal::load_device] No Metal device available")

    assert _mlx_import_diagnostic(missing) == (
        "the MLX extra is not installed; install analogy[mlx]"
    )
    assert _mlx_import_diagnostic(metal) == (
        "failed to initialize MLX: [metal::load_device] No Metal device available"
    )
