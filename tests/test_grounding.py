import json

import pytest

from analogy.grounding import GroundingOperator, enumerate_applications
from analogy.proof_inventory import InventoryEntry, InventoryKind, LocalProofInventory


def _entry(index, identifier, proposition):
    return InventoryEntry(f"local:{index}", InventoryKind.LOCAL, identifier, proposition,
                          json.dumps({"root": "opaque", "children": []}))


def _inventory(goal, *locals_):
    return LocalProofInventory(
        "fingerprint", goal, goal, tuple(locals_),
        (InventoryEntry("active_subgoal", InventoryKind.ACTIVE_SUBGOAL, None, goal,
                        json.dumps({"root": "opaque", "children": []})),),
    )


def test_exact_local_is_exhaustive_ordered_and_owns_state_data():
    inventory = _inventory("(P)", _entry(2, "h2", "P"), _entry(0, "h0", "(P)"), _entry(1, "hq", "Q"))
    applications = enumerate_applications(GroundingOperator.EXACT_LOCAL, inventory)
    assert [item.inventory_ids for item in applications] == [("local:0",), ("local:2",)]
    assert [item.application_id for item in applications] == ["application:0", "application:1"]
    assert all(item.state_fingerprint == "fingerprint" for item in applications)
    assert all(item.output_proposition == inventory.active_subgoal for item in applications)
    assert all(item.completion_expected for item in applications)


@pytest.mark.parametrize("operator,goal,locals_,expected_ids,output,complete", [
    (GroundingOperator.INTRODUCE, "P → Q", (), (), "Q", False),
    (GroundingOperator.INTRODUCE, "¬ P", (), (), "False", False),
    (GroundingOperator.APPLY_IMPLICATION, "Q", (_entry(0, "f", "P → Q"),), ("local:0",), "P", False),
    (GroundingOperator.APPLY_IMPLICATION, "Q", (_entry(0, "f", "P → Q"), _entry(1, "hp", "P")), ("local:0", "local:1"), "Q", True),
    (GroundingOperator.IFF_FORWARD, "Q", (_entry(0, "h", "P ↔ Q"), _entry(1, "hp", "P")), ("local:0", "local:1"), "Q", True),
    (GroundingOperator.IFF_BACKWARD, "P", (_entry(0, "h", "P ↔ Q"), _entry(1, "hq", "Q")), ("local:0", "local:1"), "P", True),
    (GroundingOperator.REWRITE_FORWARD, "P", (_entry(0, "h", "P = Q"),), ("local:0",), "Q", False),
    (GroundingOperator.REWRITE_BACKWARD, "Q", (_entry(0, "h", "P = Q"),), ("local:0",), "P", False),
    (GroundingOperator.SPLIT, "P ∧ Q", (), (), "P", False),
    (GroundingOperator.SPLIT, "P ↔ Q", (), (), "P → Q", False),
    (GroundingOperator.SELECT_LEFT, "P", (_entry(0, "h", "P ∧ Q"),), ("local:0",), "P", True),
    (GroundingOperator.SELECT_RIGHT, "Q", (_entry(0, "h", "P ∧ Q"),), ("local:0",), "Q", True),
    (GroundingOperator.CHOOSE_LEFT, "P ∨ Q", (), (), "P", False),
    (GroundingOperator.CHOOSE_RIGHT, "P ∨ Q", (), (), "Q", False),
    (GroundingOperator.CASES, "R", (_entry(0, "h", "P ∨ Q"),), ("local:0",), "R", False),
    (GroundingOperator.CONTRADICTION, "Q", (_entry(0, "hf", "False"),), ("local:0",), "Q", True),
    (GroundingOperator.CONTRADICTION, "Q", (_entry(0, "hp", "P"), _entry(1, "hn", "¬ P")), ("local:0", "local:1"), "Q", True),
])
def test_enumerates_each_operator(operator, goal, locals_, expected_ids, output, complete):
    applications = enumerate_applications(operator, _inventory(goal, *locals_))
    assert any(
        item.inventory_ids == expected_ids
        and item.output_proposition == output
        and item.completion_expected is complete
        for item in applications
    )


def test_opaque_or_nested_syntax_fails_closed():
    inventory = _inventory("R", _entry(0, "h", "P → Q ∧ R"))
    assert enumerate_applications(GroundingOperator.SELECT_RIGHT, inventory) == ()
    assert enumerate_applications(GroundingOperator.COMPOSE, inventory) == ()

