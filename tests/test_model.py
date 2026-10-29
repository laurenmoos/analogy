import json

import pytest

from analogy.config import ModelConfig
from analogy.model.model_provider import QwenGenerator
from analogy.model.parser import (
    FilteredCandidatePool,
    IncompleteCandidatePool,
    candidate_rejection_reason,
    normalize_lean4_tactic,
)
from analogy.solver.scoring import parse_scored_tactic_candidates
from analogy.model.mlx_model import _load_exact_model, _mlx_import_diagnostic


@pytest.mark.parametrize("source,expected", [
    ("cases h with hP hQ", "cases h with hP hQ"),
    (" cases   h   with   hP   hQ ", "cases h with hP hQ"),
    ("cases «h with x»", "cases «h with x»"),
    ("cases h with | inl hp => exact hp", "cases h with | inl hp => exact hp"),
    ("apply and.intro", "apply And.intro"),
    ("exact iff.intro hsj hjs", "exact Iff.intro hsj hjs"),
    ("apply or.introL", "apply Or.inl"),
    ("by intro h; exact h", "intro h; exact h"),
    ("exact And.intro hp hq", "exact And.intro hp hq"),
])
def test_normalize_lean3_cases_only_when_standalone(source, expected):
    assert normalize_lean4_tactic(source) == expected


@pytest.mark.parametrize("source,expected", [
    ("rw h", "rw [h]"),
    ("split", "constructor"),
    ("exact not.intro h", "exact Not.intro h"),
])
def test_normalize_common_model_syntax_before_lean(source, expected):
    assert normalize_lean4_tactic(source) == expected


def test_mlx_loader_resolves_an_immutable_model_revision_before_loading():
    calls = []

    def snapshot_download(**kwargs):
        calls.append(("download", kwargs))
        return "/cache/snapshots/abc123"

    def load(path):
        calls.append(("load", path))
        return "model", "tokenizer"

    loaded = _load_exact_model(
        load,
        "example/model",
        revision="abc123",
        snapshot_download_fn=snapshot_download,
    )

    assert loaded == ("model", "tokenizer")
    assert calls == [
        (
            "download",
            {"repo_id": "example/model", "revision": "abc123"},
        ),
        ("load", "/cache/snapshots/abc123"),
    ]


def test_transformers_loader_uses_the_configured_immutable_revision():
    calls = []

    class Loader:
        def __init__(self, kind):
            self.kind = kind

        def from_pretrained(self, name, **kwargs):
            calls.append((self.kind, name, kwargs))
            if self.kind == "tokenizer":
                return object()
            return type("Model", (), {
                "config": type("Config", (), {
                    "_commit_hash": "abc123",
                    "hidden_size": 8,
                    "name_or_path": name,
                })(),
                "device": "cuda",
                "parameters": lambda self: (),
                "eval": lambda self: None,
            })()

    transformers = type("Transformers", (), {
        "AutoTokenizer": Loader("tokenizer"),
        "AutoModelForCausalLM": Loader("model"),
    })()
    torch = type("Torch", (), {
        "cuda": type("Cuda", (), {"is_available": lambda self: True})(),
        "backends": type("Backends", (), {
            "mps": type("Mps", (), {"is_available": lambda self: False})()
        })(),
    })()
    config = ModelConfig(
        backend="transformers",
        name="example/model",
        revision="abc123",
        device="cuda",
        hidden_layer=-1,
        max_new_tokens=8,
        timeout_seconds=10.0,
        do_sample=False,
        temperature=1.0,
        top_p=1.0,
    )

    QwenGenerator.from_config(
        config,
        torch_module=torch,
        transformers_module=transformers,
    )

    assert calls[0] == (
        "tokenizer", "example/model", {"revision": "abc123"}
    )
    assert calls[1][2]["revision"] == "abc123"


@pytest.mark.parametrize("source", ["tauto", "itauto", "modus_tollens h1 h2"])
def test_banned_and_unknown_pseudo_tactics_are_filtered(source):
    with pytest.raises(FilteredCandidatePool):
        parse_scored_tactic_candidates(
            json.dumps([{"tactic": source, "score": 1.0}]),
            1,
            proof_state="P : Prop\n⊢ P",
        )


@pytest.mark.parametrize(
    "source",
    ("simp; tauto", "first | tauto | assumption", "left; exact", "foobar"),
)
def test_compound_banned_incomplete_and_unknown_tactics_are_filtered(source):
    with pytest.raises((FilteredCandidatePool, IncompleteCandidatePool)):
        parse_scored_tactic_candidates(
            json.dumps([{"tactic": source, "score": 1.0}]),
            1,
            proof_state="P Q : Prop\n⊢ P ∨ Q",
        )


@pytest.mark.parametrize(
    "source",
    ("exact (by bogus)", "exact (by exact)", "simp only []; left"),
)
def test_nested_or_shape_unsafe_continuation_tactics_are_filtered(source):
    with pytest.raises((FilteredCandidatePool, IncompleteCandidatePool)):
        parse_scored_tactic_candidates(
            json.dumps([{"tactic": source, "score": 1.0}]),
            1,
            proof_state="P : Prop\n⊢ P",
        )


@pytest.mark.parametrize(
    "source",
    (
        "sorry",
        "exact sorry",
        "exact (sorry)",
        "refine (sorry)",
        "exact (by sorry)",
        "admit",
        "exact (by admit)",
    ),
)
def test_admission_constructs_are_filtered_in_every_supported_shape(source):
    assert candidate_rejection_reason(source, "P : Prop\n⊢ P") is not None


@pytest.mark.parametrize(
    "state,tactic,expected",
    [
        ("P Q : Prop\n⊢ P", "left", "requires a disjunction goal"),
        ("P Q : Prop\n⊢ P", "right", "requires a disjunction goal"),
        ("P Q : Prop\n⊢ P", "constructor", "requires a conjunction, equivalence, or disjunction goal"),
        ("P Q : Prop\n⊢ P", "intro h", "requires an implication, negation, or universal goal"),
        ("P Q : Prop\n⊢ P ∨ Q", "left", None),
        ("P Q : Prop\n⊢ P → Q", "intro h", None),
        ("α : Type\nP : α → Prop\n⊢ ∀ x, P x", "intro x", None),
        ("α : Type\nP : α → Prop\n⊢ forall x, P x", "intros", None),
        ("P : Prop\n⊢ P", "intros", "requires an implication, negation, or universal goal"),
        ("P : Prop\n⊢ P", "exact P", "proposition name is not evidence"),
        ("P : Prop\n⊢ P", "exact ghost", "identifier is not visible in the proof state"),
        ("P : Prop\n⊢ P", "apply ghost", "identifier is not visible in the proof state"),
        ("P : Prop\n⊢ P", "cases ghost", "identifier is not visible in the proof state"),
    ],
)
def test_candidate_goal_shape_filter(state, tactic, expected):
    assert candidate_rejection_reason(tactic, state) == expected


def test_candidates_already_tried_at_this_state_are_filtered():
    discarded = []
    candidates = parse_scored_tactic_candidates(
        json.dumps([
            {"tactic": "rw h", "score": 3.0},
            {"tactic": "exact h", "score": 1.0},
        ]),
        2,
        proof_state="P Q : Prop\nh : P ↔ Q\n⊢ Q",
        excluded_tactics={"rw [h]"},
        discarded_candidates=discarded,
    )

    assert [item.source for item in candidates] == ["exact h"]
    assert discarded == ["rw [h]: already tried at this state"]


def test_candidate_normalization_precedes_deduplication_and_weighting():
    candidates = parse_scored_tactic_candidates(json.dumps([
        {"tactic": "cases h with hp hq", "score": 1.0},
        {"tactic": "cases h", "score": 3.0},
        {"tactic": "exact hp", "score": 1.0},
    ]), 3)
    assert [(item.source, item.score) for item in candidates] == [
        ("cases h with hp hq", 1 / 3), ("cases h", 1 / 3), ("exact hp", 1 / 3),
    ]


def test_conjunction_destructuring_uses_semantic_fresh_names_and_rewrites_suffix():
    state = """G H U : Prop
hA : G
hB : H ∧ U
⊢ H"""

    assert normalize_lean4_tactic(
        "rcases hB with ⟨hA,hB⟩; exact hA",
        state,
    ) == "rcases hB with ⟨hH,hU⟩; exact hH"


def test_conjunction_suffix_renaming_is_simultaneous_for_overlapping_names():
    state = "H U : Prop\nh : H ∧ U\n⊢ H"

    assert normalize_lean4_tactic(
        "rcases h with ⟨x,hH⟩; exact x",
        state,
    ) == "rcases h with ⟨hH,hU⟩; exact hH"
    assert normalize_lean4_tactic(
        "rcases h with ⟨hU,hH⟩; exact hU",
        state,
    ) == "rcases h with ⟨hH,hU⟩; exact hH"


def test_conjunction_suffix_does_not_rewrite_qualified_identifier_components():
    state = "H U : Prop\nh : H ∧ U\n⊢ H"

    assert normalize_lean4_tactic(
        "rcases h with ⟨left,right⟩; exact And.left (And.intro left right)",
        state,
    ) == (
        "rcases h with ⟨hH,hU⟩; exact And.left (And.intro hH hU)"
    )


def test_conjunction_suffix_does_not_rewrite_a_tactic_head_named_like_a_binder():
    state = "H U : Prop\nh : H ∧ U\n⊢ H ∨ U"

    assert normalize_lean4_tactic(
        "rcases h with ⟨left,right⟩; left; exact left",
        state,
    ) == "rcases h with ⟨hH,hU⟩; left; exact hH"


def test_conjunction_suffix_rewrites_local_projections_but_not_namespace_members():
    state = "P Q U : Prop\nh : (P ∧ Q) ∧ U\n⊢ P"

    assert normalize_lean4_tactic(
        "rcases h with ⟨x,y⟩; exact x.left",
        state,
    ) == "rcases h with ⟨hPQ,hU⟩; exact hPQ.left"


def test_conjunction_destructuring_with_a_binder_introducing_suffix_is_unchanged():
    state = "H U : Prop\nh : H ∧ U\n⊢ H"
    tactic = "rcases h with ⟨x,y⟩; have hH : U := y; exact x"

    assert normalize_lean4_tactic(tactic, state) == tactic


def test_semantic_destructuring_names_are_freshened_against_the_first_goal():
    state = """H U : Prop
hH : H
hB : H ∧ U
⊢ U"""

    assert normalize_lean4_tactic("rcases hB with ⟨x,y⟩", state) == (
        "rcases hB with ⟨hH_2,hU⟩"
    )


def test_bare_disjunction_split_is_semantic_but_a_branch_continuation_is_unchanged():
    state = """G H U : Prop
h : G ∨ H ∧ U
⊢ (G ∨ H) ∧ (G ∨ U)"""

    assert normalize_lean4_tactic("rcases h with (h1 | h2)", state) == (
        "rcases h with hG | hHU"
    )
    assert normalize_lean4_tactic(
        "rcases h with (h1 | h2); left; exact h1",
        state,
    ) == "rcases h with (h1 | h2); left; exact h1"


def test_destructuring_uses_only_first_goal_and_falls_back_for_complex_operands():
    state = """case first
α P : Prop
h : (List α) ∧ ¬P
⊢ P

case second
h : P ∧ P
⊢ P"""

    assert normalize_lean4_tactic("rcases h with ⟨x,y⟩", state) == (
        "rcases h with ⟨hLeft,hRight⟩"
    )


def test_state_aware_normalization_precedes_candidate_deduplication():
    state = "H U : Prop\nhB : H ∧ U\n⊢ H"
    candidates = parse_scored_tactic_candidates(json.dumps([
        {"tactic": "rcases hB with ⟨x,y⟩", "score": 1.0},
        {"tactic": "rcases hB with ⟨hH,hU⟩", "score": 3.0},
    ]), 2, proof_state=state)

    assert [(item.source, item.score) for item in candidates] == [
        ("rcases hB with ⟨hH,hU⟩", 1.0),
    ]


def test_incomplete_candidates_are_removed_before_top_k_and_scores_are_renormalized():
    candidates = parse_scored_tactic_candidates(json.dumps([
        {"tactic": "exact", "score": 100.0},
        {"tactic": "rw", "score": 90.0},
        {"tactic": "exact h2", "score": 3.0},
        {"tactic": "assumption", "score": 1.0},
    ]), 2)

    assert [(item.source, item.score) for item in candidates] == [
        ("exact h2", 0.5),
        ("assumption", 0.5),
    ]


def test_model_written_zero_scores_are_schema_valid_but_do_not_rank_candidates():
    discarded = []
    candidates = parse_scored_tactic_candidates(json.dumps([
        {"tactic": "simp", "score": 0},
        {"tactic": "simp", "score": 99},
        {"tactic": "assumption", "score": 0},
    ]), None, discarded_candidates=discarded)

    assert [(item.source, item.score) for item in candidates] == [
        ("simp", 0.5),
        ("assumption", 0.5),
    ]
    assert discarded == ["simp: duplicate proposal"]


def test_tactic_only_candidates_are_schema_valid_without_model_written_scores():
    candidates = parse_scored_tactic_candidates(json.dumps([
        {"tactic": "exact h"},
        {"tactic": "assumption"},
    ]), None)

    assert [(item.source, item.score) for item in candidates] == [
        ("exact h", 0.5),
        ("assumption", 0.5),
    ]


def test_all_incomplete_candidates_raise_a_typed_repairable_error():
    with pytest.raises(IncompleteCandidatePool, match="exact, apply, rw"):
        parse_scored_tactic_candidates(json.dumps([
            {"tactic": "exact", "score": 3.0},
            {"tactic": "apply", "score": 2.0},
            {"tactic": "rw", "score": 1.0},
        ]), 3)


@pytest.mark.parametrize("tactic", [
    "intro", "constructor", "left", "right", "simp", "assumption", "rfl",
    "cases h",
])
def test_complete_standalone_and_suggestion_tactics_remain_eligible(tactic):
    candidates = parse_scored_tactic_candidates(
        json.dumps([{"tactic": tactic, "score": 1.0}]),
        1,
    )

    assert [item.source for item in candidates] == [tactic]


def test_mlx_import_diagnostic_distinguishes_missing_extra_from_unavailable_metal():
    missing = ImportError("No module named 'mlx_lm'", name="mlx_lm")
    metal = ImportError("[metal::load_device] No Metal device available")

    assert _mlx_import_diagnostic(missing) == (
        "the MLX extra is not installed; install analogy[mlx]"
    )
    assert _mlx_import_diagnostic(metal) == (
        "failed to initialize MLX: [metal::load_device] No Metal device available"
    )
