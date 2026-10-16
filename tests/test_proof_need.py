from dataclasses import asdict
import json

from analogy.analogy_transfer import Arity, InformationFlow, derive_need_analysis
from analogy.grounding import GroundingOperator
from analogy.proof_inventory import InventoryEntry, InventoryKind, LocalProofInventory
from analogy.proof_views import ProofStructure


def _shape(root, *children):
    return json.dumps({"root": root, "children": list(children)}, sort_keys=True)


ATOM = {"root": "atom", "children": []}
TARGET = {"root": "target", "children": []}


def _inventory(target="P", locals_=()):
    return LocalProofInventory(
        "state-hash", "P", target, tuple(locals_),
        (InventoryEntry("active_subgoal", InventoryKind.ACTIVE_SUBGOAL, None,
                        target, _shape("target")),),
    )


def test_conjunction_component_need_is_neutral_and_logs_expected_operator():
    structured = InventoryEntry(
        "local:0", InventoryKind.LOCAL, "h", "P ∧ Q", _shape("and", TARGET, ATOM)
    )
    structure = ProofStructure(1, _shape("target"), (structured.structural_shape,))
    analysis = derive_need_analysis(structure, _inventory(locals_=(structured,)))

    assert asdict(analysis.description) == {
        "input_arity": Arity.TWO,
        "output_arity": Arity.ONE,
        "information_flow": (InformationFlow.SELECT, InformationFlow.DISCARD),
        "focus_before": "one available item made of two parts",
        "desired_focus_after": "the part matching the goal",
        "proof_roles": ("active_subgoal", "structured_hypothesis"),
        "summary": "There is one item made of two parts, and the goal matches one part.",
    }
    assert analysis.expected_operator is GroundingOperator.SELECT_LEFT
    assert "operator" not in json.dumps(asdict(analysis.description))


def test_need_analysis_covers_core_structural_operations():
    cases = (
        (ProofStructure(1, _shape("target"), (_shape("target"),)), _inventory(), GroundingOperator.EXACT_LOCAL),
        (ProofStructure(1, _shape("implication", ATOM, TARGET), ()), _inventory("P → Q"), GroundingOperator.INTRODUCE),
        (ProofStructure(1, _shape("and", ATOM, ATOM), ()), _inventory("P ∧ Q"), GroundingOperator.SPLIT),
        (ProofStructure(1, _shape("or", TARGET, ATOM), ()), _inventory("P ∨ Q"), GroundingOperator.CHOOSE_LEFT),
        (ProofStructure(1, _shape("target"), (_shape("implication", ATOM, TARGET),)),
         _inventory(locals_=(InventoryEntry("local:0", InventoryKind.LOCAL, "f", "Q → P", _shape("implication", ATOM, TARGET)),)),
         GroundingOperator.APPLY_IMPLICATION),
        (ProofStructure(1, _shape("target"), (_shape("or", ATOM, ATOM),)),
         _inventory(locals_=(InventoryEntry("local:0", InventoryKind.LOCAL, "h", "Q ∨ R", _shape("or", ATOM, ATOM)),)),
         GroundingOperator.CASES),
    )
    for structure, inventory, expected in cases:
        analysis = derive_need_analysis(structure, inventory)
        assert analysis.expected_operator is expected
        assert expected.value not in analysis.description.summary.lower()


def test_need_analysis_distinguishes_directions_and_conflict():
    directional = (
        (_shape("and", ATOM, TARGET), GroundingOperator.SELECT_RIGHT),
        (_shape("iff", ATOM, TARGET), GroundingOperator.IFF_FORWARD),
        (_shape("iff", TARGET, ATOM), GroundingOperator.IFF_BACKWARD),
        (_shape("equality", ATOM, TARGET), GroundingOperator.REWRITE_FORWARD),
        (_shape("equality", TARGET, ATOM), GroundingOperator.REWRITE_BACKWARD),
    )
    for index, (hypothesis_shape, expected) in enumerate(directional):
        entry = InventoryEntry(
            f"local:{index}", InventoryKind.LOCAL, f"h{index}", "Q",
            hypothesis_shape,
        )
        analysis = derive_need_analysis(
            ProofStructure(1, _shape("target"), (hypothesis_shape,)),
            _inventory(locals_=(entry,)),
        )
        assert analysis.expected_operator is expected

    positive = InventoryEntry("local:0", InventoryKind.LOCAL, "hp", "P", _shape("atom"))
    negative = InventoryEntry("local:1", InventoryKind.LOCAL, "hn", "¬ P", _shape("not", ATOM))
    conflict = derive_need_analysis(
        ProofStructure(1, _shape("atom"), (positive.structural_shape, negative.structural_shape)),
        _inventory("Q", (positive, negative)),
    )
    assert conflict.expected_operator is GroundingOperator.CONTRADICTION

