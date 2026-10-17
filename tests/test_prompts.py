from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from analogy.inference.prompts import (
    PromptCatalog,
    PromptError,
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
        "failed": "(none)",
    }
    prompt = "\n".join(message.content for message in catalog.messages("analogy_rescue", **values))

    assert 'singular "tactic"' in prompt
    assert "exactly one complete single-line Lean 4 command" in prompt
