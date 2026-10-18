from __future__ import annotations

import pytest

from analogy.model import prompts as prompts_module
from analogy.model.prompts import PromptCatalog
from analogy.solver.analogy.protocol import (
    extract_mental_model_block,
    extract_analogy_block,
    record_model_history,
    render_analogy_state,
    render_mental_model,
    render_scratchpad_context,
)
from analogy.solver.analogy.state import (
    AnalogyScratchpad,
    AnalogyState,
    AnalogyStateError,
    AnalogyUpdateAction,
    MentalModel,
    MentalModelCorrespondence,
)


LOCK = AnalogyState(
    domain="locksmithing",
    mapping=("P -> locked door", "Q -> matching key"),
    steps=("Inspect the lock.", "Use the matching key."),
)


MENTAL_MODEL = MentalModel(
    domain="island treasure hunt",
    correspondences=(
        MentalModelCorrespondence(
            domain_before="standing on the shore",
            domain_transition="sail to the island",
            domain_after="arrive at the island",
            proof_before="h : P\n⊢ P",
            proof_transition="exact h",
            proof_after="SOLVED",
        ),
        MentalModelCorrespondence(
            domain_before="arrive at the island",
            domain_transition="follow the map",
            domain_after="find the treasure",
        ),
    ),
)


def test_mental_model_round_trips_ordered_complete_and_partial_correspondences():
    rendered = render_mental_model(MENTAL_MODEL)
    restored, remainder = extract_mental_model_block(rendered)

    assert rendered.startswith("<mental_model>\n{")
    assert restored == MENTAL_MODEL
    assert [item.domain_transition for item in restored.correspondences] == [
        "sail to the island",
        "follow the map",
    ]
    assert restored.correspondences[1].proof_transition is None
    assert remainder == ""


@pytest.mark.parametrize(
    "payload,message",
    [
        ('{"domain":"x","correspondences":[],"abandoned_domains":[]}', "nonempty"),
        ('{"domain":"x","correspondences":[{"domain_before":"a"}],"abandoned_domains":[]}', "exactly"),
        ('{"domain":"x","correspondences":[{"domain_before":"a","domain_transition":"go","domain_after":"b","proof_before":null,"proof_transition":null,"proof_after":null}],"abandoned_domains":[],"extra":1}', "exactly"),
    ],
)
def test_mental_model_json_shape_is_strict(payload, message):
    with pytest.raises(AnalogyStateError, match=message):
        extract_mental_model_block(
            f"<mental_model>\n{payload}\n</mental_model>"
        )


def test_mental_model_revision_can_change_correspondences_but_not_domain():
    scratchpad = AnalogyScratchpad(MENTAL_MODEL)
    revised = MentalModel(
        MENTAL_MODEL.domain,
        (
            MentalModelCorrespondence(
                "shore", "build a raft", "open water", proof_transition="apply h"
            ),
        ),
    )

    scratchpad.apply(AnalogyUpdateAction.REVISE_MODEL, revised)
    assert scratchpad.current == revised

    with pytest.raises(AnalogyStateError, match="preserve domain"):
        scratchpad.apply(
            AnalogyUpdateAction.REVISE_MODEL,
            MentalModel(
                "naive physics",
                (MentalModelCorrespondence("rest", "push", "motion"),),
            ),
        )


def test_protocol_renders_one_canonical_tagged_block() -> None:
    assert render_analogy_state(LOCK) == """<analogy>
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
    text = f"preface\n{render_analogy_state(LOCK)}\n[{{\"tactic\": \"exact h\", \"score\": 1}}]"

    state, payload = extract_analogy_block(text)

    assert state == LOCK
    assert payload == 'preface\n\n[{"tactic": "exact h", "score": 1}]'
    assert "<analogy>" not in payload


@pytest.mark.parametrize(
    "text, message",
    [
        ("no block", "exactly one"),
        (
            f"{render_analogy_state(LOCK)}\n{render_analogy_state(LOCK)}",
            "exactly one",
        ),
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
    assert "- locksmithing" in render_scratchpad_context(scratchpad)
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
    record_model_history(scratchpad, f"first\n{render_analogy_state(LOCK)}")
    record_model_history(scratchpad, "second: Lean rejected")
    record_model_history(scratchpad, "third: Lean accepted")

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

    assert render_scratchpad_context(AnalogyScratchpad()) == "CUSTOM - (none)"
