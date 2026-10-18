from __future__ import annotations

import pytest

from analogy.model import prompts as prompts_module
from analogy.model.prompts import PromptCatalog
from analogy.solver.analogy.state import (
    AnalogyScratchpad,
    AnalogyState,
    AnalogyStateError,
    AnalogyUpdateAction,
    extract_analogy_block,
)


LOCK = AnalogyState(
    domain="locksmithing",
    mapping=("P -> locked door", "Q -> matching key"),
    steps=("Inspect the lock.", "Use the matching key."),
)


def test_state_renders_one_canonical_tagged_block() -> None:
    assert LOCK.render() == """<analogy>
domain: locksmithing
mapping:
- P -> locked door
- Q -> matching key
steps:
1. Inspect the lock.
2. Use the matching key.
abandoned domains:
- (none)
</analogy>"""


def test_extract_removes_exactly_one_block_and_leaves_tactic_payload() -> None:
    text = f"preface\n{LOCK.render()}\n[{{\"tactic\": \"exact h\", \"score\": 1}}]"

    state, payload = extract_analogy_block(text)

    assert state == LOCK
    assert payload == 'preface\n\n[{"tactic": "exact h", "score": 1}]'
    assert "<analogy>" not in payload


@pytest.mark.parametrize(
    "text, message",
    [
        ("no block", "exactly one"),
        (f"{LOCK.render()}\n{LOCK.render()}", "exactly one"),
        ("<analogy>\ndomain: x\n</analogy>", "mapping"),
        (
            """<analogy>
domain: x
mapping:
- P -> x
steps:
2. wrong number
abandoned domains:
- (none)
</analogy>""",
            "numbered",
        ),
    ],
)
def test_malformed_or_multiple_blocks_are_rejected_atomically(text: str, message: str) -> None:
    scratchpad = AnalogyScratchpad(LOCK)

    with pytest.raises(AnalogyStateError, match=message):
        candidate, _ = extract_analogy_block(text)
        scratchpad.initialize(candidate)

    assert scratchpad.current == LOCK


def test_revision_preserves_domain_mapping_and_abandoned_domains() -> None:
    scratchpad = AnalogyScratchpad(LOCK)
    revised = AnalogyState(
        domain=LOCK.domain,
        mapping=LOCK.mapping,
        steps=("Turn the key before pushing.",),
        abandoned_domains=LOCK.abandoned_domains,
    )

    scratchpad.apply(AnalogyUpdateAction.REVISE_STEPS, revised)

    assert scratchpad.current == revised
    with pytest.raises(AnalogyStateError, match="preserve domain and mapping"):
        scratchpad.apply(
            AnalogyUpdateAction.REVISE_STEPS,
            AnalogyState("shipping", ("P -> parcel",), ("Deliver it.",)),
        )


def test_domain_change_tracks_unique_recent_abandoned_domains() -> None:
    scratchpad = AnalogyScratchpad(LOCK, abandoned_limit=3)
    for index in range(5):
        scratchpad.apply(
            AnalogyUpdateAction.CHANGE_DOMAIN,
            AnalogyState(f"domain-{index}", (f"P -> thing-{index}",), ("Move.",)),
        )

    assert scratchpad.current is not None
    assert scratchpad.current.domain == "domain-4"
    assert scratchpad.current.abandoned_domains == (
        "domain-1",
        "domain-2",
        "domain-3",
    )
    with pytest.raises(AnalogyStateError, match="abandoned domain"):
        scratchpad.apply(
            AnalogyUpdateAction.CHANGE_DOMAIN,
            AnalogyState("domain-2", ("P -> reused",), ("Move.",)),
        )


def test_forced_abandonment_destroys_active_state_but_keeps_domain_ledger() -> None:
    scratchpad = AnalogyScratchpad(LOCK)

    scratchpad.abandon_current()

    assert scratchpad.current is None
    assert "- locksmithing" in scratchpad.context_block()
    assert scratchpad.pending_rebuild is True
    with pytest.raises(AnalogyStateError, match="active analogy"):
        scratchpad.apply(
            AnalogyUpdateAction.REVISE_STEPS,
            AnalogyState("locksmithing", LOCK.mapping, ("Try again.",)),
        )


def test_invalid_domain_change_is_validated_without_partial_mutation() -> None:
    scratchpad = AnalogyScratchpad(LOCK)

    with pytest.raises(AnalogyStateError, match="different from the current domain"):
        scratchpad.apply(
            AnalogyUpdateAction.CHANGE_DOMAIN,
            AnalogyState("locksmithing", ("P -> new lock",), ("Try again.",)),
        )

    assert scratchpad.current == LOCK
    assert scratchpad.current.abandoned_domains == ()


def test_pending_rebuild_blocks_initialization_until_change_domain_is_applied() -> None:
    scratchpad = AnalogyScratchpad(LOCK)
    scratchpad.abandon_current()

    with pytest.raises(AnalogyStateError, match="pending rebuild"):
        scratchpad.initialize(
            AnalogyState("shipping", ("P -> parcel",), ("Send it.",))
        )

    scratchpad.apply(
        AnalogyUpdateAction.CHANGE_DOMAIN,
        AnalogyState("shipping", ("P -> parcel",), ("Send it.",)),
    )
    assert scratchpad.pending_rebuild is False
    assert scratchpad.current is not None
    assert scratchpad.current.domain == "shipping"


def test_history_is_bounded_and_never_contains_analogy_blocks() -> None:
    scratchpad = AnalogyScratchpad(LOCK, history_limit=2)
    scratchpad.record_history(f"first\n{LOCK.render()}")
    scratchpad.record_history("second: Lean rejected")
    scratchpad.record_history("third: Lean accepted")

    assert scratchpad.history == (
        "second: Lean rejected",
        "third: Lean accepted",
    )
    assert all("<analogy>" not in item for item in scratchpad.history)


def test_pending_context_wording_comes_from_prompt_catalog(tmp_path, monkeypatch):
    path = tmp_path / "prompts.yaml"
    path.write_text(
        """prompts:
  analogy_pending_context:
    version: analogy-pending-context-test-v1
    text: 'CUSTOM {{abandoned_domains}}'
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        prompts_module,
        "_ACTIVE_CATALOG",
        PromptCatalog.load(path),
    )

    assert AnalogyScratchpad().context_block() == "CUSTOM - (none)"
