from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from analogy.inference.prompts import (
    PromptCatalog,
    PromptError,
    lean_tactic_card,
    focused_lean_goal,
    render_lean_goal_for_model,
)


ROOT = Path(__file__).parents[1]


def write_catalog(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_prompt_catalog_renders_named_values_and_preserves_literal_json(tmp_path):
    path = write_catalog(tmp_path / "prompts.yaml", """prompts:
  story:
    version: story-v1
    system: 'Return JSON: {"operator": "exact_local"}'
    user: 'Need: {{need}}'
""")
    catalog = PromptCatalog.load(path)
    messages = catalog.messages("story", need="one item")
    assert messages[0].content == 'Return JSON: {"operator": "exact_local"}'
    assert messages[1].content == "Need: one item"
    assert catalog.version("story") == "story-v1"
    assert catalog.content_hash == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("text, diagnostic", [
    ("story: {}", "only prompts"),
    ("prompts: {}", "nonempty mapping"),
    ("prompts:\n  story:\n    version: v1\n    system: s\n    user: u\n    extra: x\n", "invalid prompt"),
])
def test_prompt_catalog_rejects_unknown_or_malformed_structure(tmp_path, text, diagnostic):
    path = write_catalog(tmp_path / "prompts.yaml", text)
    with pytest.raises(PromptError, match=diagnostic):
        PromptCatalog.load(path)


def test_prompt_catalog_rejects_missing_extra_and_unknown_prompt_values(tmp_path):
    path = write_catalog(tmp_path / "prompts.yaml", """prompts:
  story:
    version: story-v1
    system: 'Choose {{operator}}.'
    user: 'Need {{need}}.'
""")
    catalog = PromptCatalog.load(path)
    with pytest.raises(PromptError, match="required=.*need.*operator"):
        catalog.messages("story", need="x")
    with pytest.raises(PromptError, match="supplied=.*extra"):
        catalog.messages("story", need="x", operator="y", extra="z")
    with pytest.raises(PromptError, match="unknown prompt"):
        catalog.messages("missing")


@pytest.mark.parametrize(
    "source, expected",
    [
        ("¬P", "¬P  [definitionally: P → False]"),
        ("P : Prop\n⊢ ¬P", "P : Prop\n⊢ ¬P  [definitionally: P → False]"),
        ("P : Prop\n⊢ P", "P : Prop\n⊢ P"),
    ],
)
def test_negation_goals_are_also_shown_as_implications(source, expected):
    assert render_lean_goal_for_model(source) == expected


def test_analogy_fallback_requests_exactly_one_tactic():
    catalog = PromptCatalog.load(ROOT / "prompts.yaml")
    values = {
        "overall_target": "P",
        "current_goals": "P",
        "accepted": "(none)",
        "open_tactics": "(none)",
        "rejected_tactics": "(none)",
        "tactic_card": "Lean card: one line.",
        "semantic_guidance": "SAT: no countermodel; continue from this state.",
        "dependency_table": "(none)",
    }
    prompt = "\n".join(message.content for message in catalog.messages("analogy_rescue", **values))

    assert 'exactly "analogy", "predicted_state", and "tactic"' in prompt
    assert "one complete Lean 4 line" in prompt
    assert "SAT: no countermodel; continue from this state." in prompt
    assert "OPEN tactics" in prompt
    assert "REJECTED tactics" in prompt


def test_tactic_card_is_generic_and_bans_automation():
    state = """B S : Prop
h1 : B → S
h2 : ¬S
⊢ ¬B"""

    card = lean_tactic_card(state)

    assert "modus tollens" not in card
    assert "target=" not in card
    assert "rcases h with a | b" not in card
    assert "obtain ⟨a,b⟩ := h" not in card
    assert "Or.inl" in card and "Or.inr" in card and "⟨_,_⟩" in card
    assert "no tauto/itauto" in card
    assert len(card) <= 240


def test_tactic_card_selects_only_relevant_connective_recipes():
    state = """G H U : Prop
h : G ∨ H ∧ U
⊢ (G ∨ H) ∧ (G ∨ U)"""

    card = lean_tactic_card(state)

    assert "Build goals" in card
    assert "rcases h with hG | hHU" in card
    assert "analogy_left" not in card
    assert "analogy_right" not in card
    assert len(card) <= 240


def test_tactic_card_has_a_hard_size_ceiling_for_long_identifiers():
    name = "hypothesis_" + ("x" * 180)
    state = f"P Q : Prop\n{name} : P → Q\nnot_q : ¬Q\n⊢ ¬P"

    assert len(lean_tactic_card(state)) <= 200


def test_tactic_card_uses_only_root_connectives():
    state = """P Q R : Prop
h : P → (Q ∨ R)
⊢ P → (Q ∧ R)"""

    card = lean_tactic_card(state)

    assert "target=" not in card
    assert "modus tollens" not in card


def test_tactic_card_prefers_root_connective_over_prefix_negation():
    card = lean_tactic_card("P Q : Prop\n⊢ ¬P ∧ Q")

    assert "target=" not in card
    assert "modus tollens" not in card


def test_tactic_card_reads_only_the_first_active_goal():
    state = """case first
P Q : Prop
h : P
⊢ P

case second
later : Q ∨ P
⊢ Q"""

    card = lean_tactic_card(state)

    assert "later" not in card
    assert "bad" not in card


def test_tactic_card_does_not_embed_problem_specific_exact_hint():
    state = """A C L P : Prop
h1 : L ↔ P
h2 : A ∧ C ∧ ¬P
⊢ A ∧ C ∧ ¬P"""

    card = lean_tactic_card(state)

    assert "target=h2" not in card


def test_tactic_card_uses_visible_hypothesis_names():
    state = """G H U : Prop
hA : G
hB : H ∧ U
⊢ H"""

    card = lean_tactic_card(state)

    assert "obtain ⟨hH,hU⟩ := hB" in card
    assert "⟨hA,hB⟩" not in card
    assert "analogy_" not in card


def test_tactic_card_shows_three_way_rcases_and_universal_intro():
    state = "α : Type\nP Q R : α → Prop\nhCases : P x ∨ Q x ∨ R x\n⊢ ∀ y, P y"

    card = lean_tactic_card(state)

    assert "rcases hCases with hPx | hQx | hRx" in card
    assert "intro y" in card
    assert len(card) <= 240


def test_tactic_card_does_not_merge_tokens_when_matching_a_hypothesis():
    card = lean_tactic_card("f x : Prop\nh : f x\nfx : Prop\n⊢ fx")

    assert "target=h: exact h" not in card


def test_tactic_card_freshens_destructured_names_against_grouped_declarations():
    state = """G H U : Prop
hG hH : G
h : G ∧ H
⊢ U"""

    card = lean_tactic_card(state)

    assert "obtain ⟨hG_2,hH_2⟩ := h" in card


def test_tactic_card_requires_operand_tactics_to_be_complete():
    card = lean_tactic_card("P : Prop\n⊢ P")

    assert "exact <hyp>" in card
    assert "apply <hyp>" in card
    assert "rw [<hyp>]" in card
    assert len(card) <= 240


def test_focused_lean_goal_drops_later_cases_but_keeps_first_context():
    state = """case left
P Q : Prop
h : P
⊢ P

case right
bad : Q
⊢ Q"""

    focused = focused_lean_goal(state)

    assert "h : P" in focused and "⊢ P" in focused
    assert "case right" not in focused and "bad : Q" not in focused
