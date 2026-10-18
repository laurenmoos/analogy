from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, asdict

import pytest

from analogy.solver.proof_inventory import InventoryError, InventoryKind, build_local_inventory
from analogy.solver.proof_views import ProofStructure
from analogy.utils.records import SymbolicState
from analogy.solver.replay import proof_state_fingerprint


def shape(root: str, *children: str) -> str:
    return json.dumps({"root": root, "children": [json.loads(child) for child in children]})


ATOM = shape("atom")


def structure_for_iff_application() -> ProofStructure:
    return ProofStructure(1, ATOM, (shape("iff", ATOM, ATOM), shape("implication", ATOM, shape("target")), ATOM))


def structure_with_two_locals() -> ProofStructure:
    return ProofStructure(1, ATOM, (ATOM, shape("target")))


def test_inventory_preserves_exact_local_identifiers_and_types() -> None:
    inventory = build_local_inventory(
        SymbolicState("s", "h1 : P ↔ R\nh2 : P → Q\nr : R\n⊢ Q", "lean4", ()),
        structure_for_iff_application(),
        fixed_target="R → Q",
    )
    assert [(item.inventory_id, item.identifier, item.proposition) for item in inventory.locals] == [
        ("local:0", "h1", "P ↔ R"),
        ("local:1", "h2", "P → Q"),
        ("local:2", "r", "R"),
    ]
    assert inventory.active_subgoal == "Q"
    assert inventory.fixed_target == "R → Q"
    assert inventory.locals[0].structural_shape == structure_for_iff_application().hypothesis_shapes[0]


def test_shadowed_names_have_distinct_stable_inventory_ids() -> None:
    inventory = build_local_inventory(
        SymbolicState("s", "x : P\nx✝ : Q\n⊢ Q", "lean4", ()),
        structure_with_two_locals(),
        fixed_target="Q",
    )
    assert [item.inventory_id for item in inventory.locals] == ["local:0", "local:1"]
    assert inventory.resolve("local:1").identifier == "x✝"


def test_inventory_fingerprint_detects_stale_state_and_keeps_full_state() -> None:
    state = SymbolicState("s", "case left\nh : P\n⊢ P\n\ncase right\nh : P\n⊢ Q", "lean4", ())
    structure = ProofStructure(2, ATOM, (shape("target"),))
    inventory = build_local_inventory(state, structure, fixed_target="P ∧ Q")
    assert inventory.state_fingerprint == proof_state_fingerprint(state.expression)
    assert inventory.state_fingerprint != proof_state_fingerprint(state.expression.replace("⊢ Q", "⊢ R"))
    assert [(item.identifier, item.proposition) for item in inventory.locals] == [("h", "P")]
    assert inventory.active_subgoal == "P"


def test_inventory_expands_grouped_declarations_without_renaming_or_type_normalization() -> None:
    state = SymbolicState("s", "P Q : Prop\n«a b» h✝ : (P  → Q)\n⊢ Q", "lean4", ())
    implication = shape("implication", ATOM, shape("target"))
    inventory = build_local_inventory(state, ProofStructure(1, ATOM, (implication, implication)), fixed_target="Q")
    assert [(item.inventory_id, item.identifier, item.proposition) for item in inventory.locals] == [
        ("local:0", "P", "Prop"), ("local:1", "Q", "Prop"),
        ("local:2", "«a b»", "(P  → Q)"), ("local:3", "h✝", "(P  → Q)"),
    ]
    assert inventory.resolve("local:2").structural_shape == implication


def test_inventory_preserves_quoted_identifiers_containing_declaration_punctuation() -> None:
    state = SymbolicState("s", "«h : exact» : P\n⊢ P", "lean4", ())
    inventory = build_local_inventory(state, ProofStructure(1, ATOM, (shape("target"),)), fixed_target="P")
    assert inventory.resolve("local:0").identifier == "«h : exact»"
    assert inventory.resolve("local:0").proposition == "P"


def test_inventory_preserves_multiline_types_and_active_target() -> None:
    state = SymbolicState("s", "h : P →\n  (Q ∧ R)\n⊢ P →\n  (Q ∧ R)", "lean4", ())
    inventory = build_local_inventory(state, ProofStructure(1, shape("implication", ATOM, shape("and", ATOM, ATOM)), (shape("target"),)), fixed_target="P → (Q ∧ R)")
    assert inventory.locals[0].proposition == "P →\n  (Q ∧ R)"
    assert inventory.active_subgoal == "P →\n  (Q ∧ R)"


def test_inventory_uses_local_definition_type_without_initializer() -> None:
    state = SymbolicState("s", "h : P\nt : P := h\n⊢ P", "lean4", ())
    inventory = build_local_inventory(state, ProofStructure(1, ATOM, (shape("target"), shape("target"))), fixed_target="P")
    assert inventory.resolve("local:1").proposition == "P"


def test_inventory_keeps_unmatched_shape_metadata_opaque() -> None:
    state = SymbolicState("s", "n : Nat\nh : P\n⊢ Q", "lean4", ())
    inventory = build_local_inventory(state, ProofStructure(1, ATOM, (ATOM,)), fixed_target="Q")
    assert [json.loads(entry.structural_shape)["root"] for entry in inventory.locals] == ["opaque", "opaque"]


@pytest.mark.parametrize("root,expression,constructors", [
    ("and", "(P → Q) ∧ R", ("And.intro",)),
    ("or", "(P → Q) ∨ R", ("Or.inl", "Or.inr")),
    ("iff", "(P → Q) ↔ R", ("Iff.intro",)),
    ("implication", "(P → Q) → R", ()),
])
def test_inventory_derives_exact_target_components_and_constructors(root, expression, constructors) -> None:
    structure = ProofStructure(1, shape(root, shape("implication", ATOM, ATOM), ATOM), ())
    inventory = build_local_inventory(SymbolicState("s", f"⊢ {expression}", "lean4", ()), structure, fixed_target=expression)
    assert inventory.resolve("active_subgoal").kind is InventoryKind.ACTIVE_SUBGOAL
    components = [entry for entry in inventory.target_entries if entry.kind is InventoryKind.TARGET_COMPONENT]
    assert [(entry.inventory_id, entry.proposition) for entry in components] == [("target:0", "(P → Q)"), ("target:1", "R")]
    assert tuple(entry.identifier for entry in inventory.target_entries if entry.kind is InventoryKind.CONSTRUCTOR) == constructors


def test_inventory_is_immutable_serializable_and_rejects_unknown_ids() -> None:
    inventory = build_local_inventory(SymbolicState("s", "⊢ Q", "lean4", ()), ProofStructure(1, ATOM, ()), fixed_target="Q")
    assert json.loads(json.dumps(asdict(inventory)))["active_subgoal"] == "Q"
    with pytest.raises(FrozenInstanceError):
        inventory.active_subgoal = "R"
    with pytest.raises(InventoryError, match="unknown inventory id"):
        inventory.resolve("local:999")


@pytest.mark.parametrize("expression", ["h : P", "unparsed declaration\n⊢ P"])
def test_inventory_rejects_unparseable_state_instead_of_inventing_identifiers(expression) -> None:
    with pytest.raises(InventoryError):
        build_local_inventory(SymbolicState("s", expression, "lean4", ()), ProofStructure(1, ATOM, ()), fixed_target="P")
