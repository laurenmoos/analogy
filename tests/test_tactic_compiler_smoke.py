"""Compact compiler boundary coverage retained by the pruned suite."""

from analogy.grounded_plan import (
    GroundedInferenceStep,
    GroundedRoleBinding,
    GroundedTacticPlan,
    GroundingOperator,
)
from analogy.proof_inventory import build_local_inventory
from analogy.proof_views import ProofStructure
from analogy.records import SymbolicState
from analogy.tactic_compiler import GroundedTacticCompiler


def compile_one(context: str, target: str, operator: GroundingOperator):
    inventory = build_local_inventory(
        SymbolicState("compiler", f"{context}\n⊢ {target}", "lean4"),
        ProofStructure(1, '{"root":"atom"}', ()),
        fixed_target=target,
    )
    binding = GroundedRoleBinding(
        "binding-0",
        "premise",
        "local:0",
        inventory.locals[0].proposition,
        None,
        "Exact current-state premise.",
    )
    step = GroundedInferenceStep(
        "step-0",
        operator,
        ("binding-0",),
        (binding.proposition,),
        target,
        0,
        True,
    )
    plan = GroundedTacticPlan(
        "plan-0",
        "transfer-0",
        inventory.state_fingerprint,
        (binding,),
        (step,),
        "No remaining goals.",
        True,
        (),
    )
    return GroundedTacticCompiler().compile(plan, inventory, 4)


def test_compiler_uses_exact_inventory_identifier_spelling() -> None:
    result = compile_one("«λ local» : P", "P", GroundingOperator.EXACT_LOCAL)
    assert [candidate.text for candidate in result.candidates] == ["exact «λ local»"]


def test_compiler_owns_backward_rewrite_direction_and_brackets() -> None:
    result = compile_one("h : a = b", "b = a", GroundingOperator.REWRITE_BACKWARD)
    assert [candidate.text for candidate in result.candidates] == ["rw [← h]"]


def test_nested_connective_cannot_masquerade_as_root() -> None:
    result = compile_one("h : P → Q ∧ R", "R", GroundingOperator.SELECT_RIGHT)
    assert result.candidates == ()
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["unsupported_operator"]
