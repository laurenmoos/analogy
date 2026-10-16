import json

from analogy.grounding import GroundingOperator, enumerate_applications
from analogy.proof_inventory import InventoryEntry, InventoryKind, LocalProofInventory
from analogy.tactic_compiler import GroundedTacticCompiler


def _entry(index, identifier, proposition):
    return InventoryEntry(f"local:{index}", InventoryKind.LOCAL, identifier, proposition,
                          json.dumps({"root": "opaque", "children": []}))


def _inventory(goal, *locals_):
    return LocalProofInventory(
        "fingerprint", goal, goal, tuple(locals_),
        (InventoryEntry("active_subgoal", InventoryKind.ACTIVE_SUBGOAL, None, goal,
                        json.dumps({"root": "opaque", "children": []})),),
    )


def _texts(operator, inventory):
    result = GroundedTacticCompiler().compile_all(enumerate_applications(operator, inventory), inventory)
    assert result.diagnostics == ()
    return [candidate.text for candidate in result.candidates]


def test_compiles_every_operator_family():
    assert _texts(GroundingOperator.EXACT_LOCAL, _inventory("P", _entry(0, "«λ local»", "P"))) == ["exact «λ local»"]
    assert _texts(GroundingOperator.INTRODUCE, _inventory("P → Q", _entry(0, "analogy_h", "R"))) == ["intro analogy_h1"]
    assert _texts(GroundingOperator.APPLY_IMPLICATION, _inventory("Q", _entry(0, "f", "P → Q"), _entry(1, "hp", "P"))) == ["apply f", "exact f hp"]
    assert _texts(GroundingOperator.IFF_FORWARD, _inventory("Q", _entry(0, "h", "P ↔ Q"), _entry(1, "hp", "P"))) == ["exact h.mp hp"]
    assert _texts(GroundingOperator.IFF_BACKWARD, _inventory("P", _entry(0, "h", "P ↔ Q"), _entry(1, "hq", "Q"))) == ["exact h.mpr hq"]
    assert _texts(GroundingOperator.REWRITE_FORWARD, _inventory("P", _entry(0, "h", "P = Q"))) == ["rw [h]"]
    assert _texts(GroundingOperator.REWRITE_BACKWARD, _inventory("Q", _entry(0, "h", "P = Q"))) == ["rw [← h]"]
    assert _texts(GroundingOperator.SPLIT, _inventory("P ∧ Q")) == ["constructor"]
    assert _texts(GroundingOperator.SELECT_LEFT, _inventory("P", _entry(0, "h", "P ∧ Q"))) == ["exact h.left"]
    assert _texts(GroundingOperator.SELECT_RIGHT, _inventory("Q", _entry(0, "h", "P ∧ Q"))) == ["exact h.right"]
    assert _texts(GroundingOperator.CHOOSE_LEFT, _inventory("P ∨ Q")) == ["left"]
    assert _texts(GroundingOperator.CHOOSE_RIGHT, _inventory("P ∨ Q")) == ["right"]
    assert _texts(GroundingOperator.CASES, _inventory("R", _entry(0, "h", "P ∨ Q"))) == ["cases h"]
    assert _texts(GroundingOperator.CONTRADICTION, _inventory("Q", _entry(0, "hp", "P"), _entry(1, "hn", "¬ P"))) == ["exact False.elim (hn hp)"]


def test_compile_all_deduplicates_globally_without_a_candidate_limit():
    inventory = _inventory("P", _entry(0, "h", "P"))
    applications = enumerate_applications(GroundingOperator.EXACT_LOCAL, inventory)
    result = GroundedTacticCompiler().compile_all(applications + applications, inventory)
    assert [item.text for item in result.candidates] == ["exact h"]


def test_stale_fingerprint_and_unsafe_identifier_fail_closed():
    inventory = _inventory("P", _entry(0, "h✝", "P"))
    application = enumerate_applications(GroundingOperator.EXACT_LOCAL, inventory)[0]
    stale = type(application)(
        application.application_id, application.operator, "stale", application.inventory_ids,
        application.input_propositions, application.output_proposition,
        application.expected_next_subgoal, application.completion_expected,
    )
    result = GroundedTacticCompiler().compile_all((application, stale), inventory)
    assert result.candidates == ()
    assert {item.code for item in result.diagnostics} == {"unsupported_identifier", "stale_state"}
