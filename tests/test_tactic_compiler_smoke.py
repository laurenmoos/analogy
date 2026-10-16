"""Compact compiler boundary coverage retained by the pruned suite."""

from analogy.grounding import GroundingOperator, enumerate_applications
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
    return GroundedTacticCompiler().compile_all(
        enumerate_applications(operator, inventory), inventory
    )


def test_compiler_uses_exact_inventory_identifier_spelling() -> None:
    result = compile_one("«λ local» : P", "P", GroundingOperator.EXACT_LOCAL)
    assert [candidate.text for candidate in result.candidates] == ["exact «λ local»"]


def test_compiler_owns_backward_rewrite_direction_and_brackets() -> None:
    result = compile_one("h : a = b", "b", GroundingOperator.REWRITE_BACKWARD)
    assert [candidate.text for candidate in result.candidates] == ["rw [← h]"]


def test_nested_connective_cannot_masquerade_as_root() -> None:
    result = compile_one("h : P → Q ∧ R", "R", GroundingOperator.SELECT_RIGHT)
    assert result.candidates == ()
    assert result.diagnostics == ()
