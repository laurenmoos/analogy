"""Compiler boundary tests: only inventory names and package templates execute."""

from dataclasses import replace

import pytest

from analogy.grounded_plan import (
    GroundedInferenceStep, GroundedRoleBinding, GroundedTacticPlan, GroundingOperator,
)
from analogy.proof_inventory import build_local_inventory
from analogy.proof_views import ProofStructure
from analogy.records import SymbolicState
from analogy.tactic_compiler import CompiledTactic, GroundedTacticCompiler


def evaluate_with_contract(inv, compiled, accepted_texts):
    """Exercise real adapter/record compatibility with a bounded verifier double."""
    from analogy.lean_gym.models import Problem, ProofState, VerificationResult, VerificationStatus
    from analogy.lean_gym.verifier import LeanGymAdapter
    from analogy.records import CandidateOrigin, TacticCandidate, TacticStatus

    class TemplateVerifier:
        def try_tactic(self, problem, prefix, text):
            if text not in accepted_texts or prefix:
                return VerificationResult(VerificationStatus.REJECTED, False, None, (), 1, 0)
            return VerificationResult(VerificationStatus.OPEN, True,
                                      ProofState.from_text("⊢ successor"), (), 0, 0)

    adapter = LeanGymAdapter(Problem("contract", "Contract", "", inv.fixed_target,
                                    "test", "test", "test", 1), TemplateVerifier())
    state = adapter.register_replay_state(SymbolicState("contract", "⊢ " + inv.active_subgoal, "lean4"))
    for item in compiled.candidates:
        result = adapter.run_tactic(state, TacticCandidate(item.text, 1.0, CandidateOrigin.ANALOGICAL))
        assert result.status is TacticStatus.ACCEPTED
        assert result.state.context == (item.text,)


def inventory(context="h : P", target="P", root="atom"):
    children = ',"children":[{"root":"atom"},{"root":"atom"}]' if root != "atom" else ""
    return build_local_inventory(
        SymbolicState("compiler", context + "\n⊢ " + target, "lean4"),
        ProofStructure(1, '{"root":"' + root + '"' + children + '}', ()),
        fixed_target=target,
    )


def plan(inv, operator="exact_local", inputs=("b0",), output=None, *, complete=True,
         next_goal="No remaining goals.", extra_steps=(), hints=()):
    bindings = tuple(GroundedRoleBinding(
        f"b{i}", f"role-{i}", entry.inventory_id, entry.proposition, None, "Grounded premise."
    ) for i, entry in enumerate(inv.locals))
    refs = {binding.binding_id: binding.proposition for binding in bindings}
    refs.update((step.step_id, step.output_proposition) for step in extra_steps)
    step = GroundedInferenceStep("final", GroundingOperator(operator), inputs,
                                tuple(refs[ref] for ref in inputs),
                                output or (inv.active_subgoal if complete else next_goal), 0, complete)
    return GroundedTacticPlan("plan-1", "transfer-1", inv.state_fingerprint, bindings,
                             (*extra_steps, step), next_goal, complete, hints)


def texts(result):
    assert result.diagnostics == ()
    return [candidate.text for candidate in result.candidates]


@pytest.mark.parametrize("context,target,root,operator,inputs,complete,next_goal,want", [
    ("h : P", "P", "atom", "exact_local", ("b0",), True, "done", ["exact h"]),
    ("h : P → Q\np : P", "Q", "atom", "apply_implication", ("b0", "b1"), True, "done", ["exact h p", "apply h"]),
    ("h : P → Q", "Q", "atom", "apply_implication", ("b0",), False, "P", ["apply h"]),
    ("h : P ↔ Q\np : P", "Q", "atom", "iff_forward", ("b0", "b1"), True, "done", ["exact h.mp p"]),
    ("h : P ↔ Q\nq : Q", "P", "atom", "iff_backward", ("b0", "b1"), True, "done", ["exact h.mpr q"]),
    ("h : a = b", "a = b", "atom", "rewrite_forward", ("b0",), True, "done", ["rw [h]"]),
    ("h : a = b", "b = a", "atom", "rewrite_backward", ("b0",), True, "done", ["rw [← h]"]),
    ("h : P ↔ Q", "Q ↔ P", "iff", "rewrite_backward", ("b0",), True, "done", ["rw [← h]"]),
    ("P : Prop", "P → P", "implication", "introduce", (), False, "P", ["intro analogy_h"]),
    ("P : Prop", "¬ P", "atom", "introduce", (), False, "False", ["intro analogy_h"]),
    ("h : P ∧ Q", "P", "atom", "select_left", ("b0",), True, "done", ["exact h.left"]),
    ("h : P ∧ Q", "Q", "atom", "select_right", ("b0",), True, "done", ["exact h.right"]),
    ("P : Prop", "P ∧ Q", "and", "split", (), False, "P", ["constructor"]),
    ("P : Prop", "P ↔ Q", "iff", "split", (), False, "P → Q", ["constructor"]),
    ("P : Prop", "P ∨ Q", "or", "choose_left", (), False, "P", ["left"]),
    ("P : Prop", "P ∨ Q", "or", "choose_right", (), False, "Q", ["right"]),
    ("h : P ∨ Q", "R", "atom", "cases", ("b0",), False, "R", ["cases h"]),
    ("h : False", "P", "atom", "contradiction", ("b0",), True, "done", ["exact False.elim h"]),
    ("hn : ¬ P\np : P", "Q", "atom", "contradiction", ("b0", "b1"), True, "done", ["exact False.elim (hn p)"]),
])
def test_operator_templates(context, target, root, operator, inputs, complete, next_goal, want):
    inv = inventory(context, target, root)
    result = GroundedTacticCompiler().compile(
        plan(inv, operator, inputs, complete=complete, next_goal=next_goal), inv, 4)
    assert texts(result) == want
    evaluate_with_contract(inv, result, want)


def test_typed_two_step_term_has_stable_dependency_provenance_and_partial_metadata():
    inv = inventory("h1 : P ↔ R\nh2 : P → Q\nr : R", "Q")
    first = GroundedInferenceStep("step-1", GroundingOperator.IFF_BACKWARD,
                                 ("b0", "b2"), ("P ↔ R", "R"), "P", 0, False)
    grounded = plan(inv, "apply_implication", ("b1", "step-1"), extra_steps=(first,))
    result = GroundedTacticCompiler().compile(grounded, inv, 4)
    assert texts(result) == ["exact h2 (h1.mpr r)", "apply h2"]
    assert result.candidates[0].step_ids == ("step-1", "final")
    assert result.candidates[0].binding_ids == ("b0", "b1", "b2")
    assert result.candidates[0].predicted_next_subgoal == "No remaining goals."
    assert result.candidates[1].step_ids == ("final",)
    assert result.candidates[1].binding_ids == ("b1",)
    assert result.candidates[1].predicted_next_subgoal == "P"


def test_three_step_inline_term_and_compose_alias_deduplicate_before_limit():
    inv = inventory("h : P ↔ Q\np : P\nf : Q → R", "R")
    one = GroundedInferenceStep("one", GroundingOperator.IFF_FORWARD, ("b0", "b1"),
                                ("P ↔ Q", "P"), "Q", 0, False)
    two = GroundedInferenceStep("two", GroundingOperator.APPLY_IMPLICATION, ("b2", "one"),
                                ("Q → R", "Q"), "R", 0, False)
    grounded = plan(inv, "compose", ("two",), extra_steps=(one, two))
    assert texts(GroundedTacticCompiler().compile(grounded, inv, 8)) == ["exact f (h.mp p)"]
    assert texts(GroundedTacticCompiler().compile(grounded, inv, 1)) == ["exact f (h.mp p)"]
    assert texts(GroundedTacticCompiler().compile(grounded, inv, 0)) == []


def test_canonical_deduplication_precedes_limit_and_preserves_quoted_name_spacing():
    class RepeatedEmitter(GroundedTacticCompiler):
        def _compile(self, plan, inventory):
            return [CompiledTactic(text, ("final",), ("b0",), "done") for text in
                    (" exact   h ", "exact h", "exact «λ  local»")]

    inv = inventory()
    assert texts(RepeatedEmitter().compile(plan(inv), inv, 2)) == ["exact h", "exact «λ  local»"]


@pytest.mark.parametrize("emitted", ["exact\nh", "exact h\n", "exact h\r", "exact h\u2028"])
def test_multiline_emission_is_rejected_before_whitespace_canonicalization(emitted):
    class MultilineEmitter(GroundedTacticCompiler):
        def _compile(self, plan, inventory):
            return [CompiledTactic(emitted, ("final",), ("b0",), "done")]

    inv = inventory()
    result = MultilineEmitter().compile(plan(inv), inv, 2)
    assert result.candidates == ()
    assert result.diagnostics[0].code == "unsupported_composition"


def test_intermediate_terms_are_bounded_before_later_composition():
    inv = inventory("h" * 4097 + " : P\nf : P → Q", "Q")
    first = GroundedInferenceStep("one", GroundingOperator.EXACT_LOCAL, ("b0",), ("P",), "P", 0, False)
    grounded = plan(inv, "apply_implication", ("b1", "one"), extra_steps=(first,))
    result = GroundedTacticCompiler().compile(grounded, inv, 4)
    assert result.candidates == ()
    assert result.diagnostics[0].code == "unsupported_composition"
    assert result.diagnostics[0].step_id == "one"


def test_invalid_plan_returns_diagnostic_without_any_lean_process(monkeypatch):
    import subprocess

    def forbidden(*args, **kwargs):
        pytest.fail("compiler validation must never invoke Lean")

    monkeypatch.setattr(subprocess, "run", forbidden)
    inv = inventory()
    invalid = replace(plan(inv), bindings=(replace(plan(inv).bindings[0], inventory_id="local:404"),))
    result = GroundedTacticCompiler().compile(invalid, inv, 4)
    assert result.candidates == ()
    assert result.diagnostics[0].code == "invalid_plan"


@pytest.mark.parametrize("name", ["α₁", "«λ  local»", "h'", "«h✝»"])
def test_exact_inventory_spelling_survives_shadowing_and_whitespace(name):
    inv = inventory(f"h : Q\n{name} : P", "P")
    grounded = plan(inv, inputs=("b1",), hints=("exact missing\nend",))
    candidate = GroundedTacticCompiler().compile(grounded, inv, 4).candidates[0]
    assert candidate.text == f"exact {name}"
    assert candidate.binding_ids == ("b1",)


def test_inaccessible_shadow_name_is_diagnostic_instead_of_malformed_lean():
    inv = inventory("h✝ : P\nh : Q", "P")
    result = GroundedTacticCompiler().compile(plan(inv), inv, 4)
    assert result.candidates == ()
    assert result.diagnostics[0].code == "unsupported_identifier"


@pytest.mark.parametrize("binders,target,operator,input_names,complete,next_goal", [
    ("(P Q : Prop) (h : P ↔ Q) (p : P)", "Q", "iff_forward", ("h", "p"), True, "done"),
    ("(P Q : Prop) (h : P ↔ Q) (q : Q)", "P", "iff_backward", ("h", "q"), True, "done"),
    ("(a b : Nat) (h : a = b)", "a = b", "rewrite_forward", ("h",), True, "done"),
    ("(a b : Nat) (h : a = b)", "b = a", "rewrite_backward", ("h",), True, "done"),
    ("(P Q : Prop) (h : P ↔ Q)", "Q ↔ P", "rewrite_backward", ("h",), True, "done"),
    ("(P Q : Prop) (h : P ↔ Q)", "P ↔ Q", "rewrite_forward", ("h",), True, "done"),
    ("(P : Prop) (α₁ : P)", "P", "exact_local", ("α₁",), True, "done"),
    ("(P : Prop) («λ  local» : P)", "P", "exact_local", ("«λ  local»",), True, "done"),
    ("(P Q : Prop) (h : Q) (h : P)", "P", "exact_local", ("h",), True, "done"),
    ("(P Q : Prop) (h : P → Q) (p : P)", "Q", "apply_implication", ("h", "p"), True, "done"),
    ("(P : Prop) (analogy_h : Prop)", "P → P", "introduce", (), False, "P"),
    ("(P Q : Prop)", "P ∧ Q", "split", (), False, "P"),
    ("(P Q : Prop)", "P ↔ Q", "split", (), False, "P → Q"),
    ("(P Q : Prop)", "P ∨ Q", "choose_right", (), False, "Q"),
    ("(P Q : Prop) (h : P ∧ Q)", "Q", "select_right", ("h",), True, "done"),
    ("(P Q R : Prop) (h : P ∨ Q)", "R", "cases", ("h",), False, "R"),
    ("(P Q : Prop) (hn : ¬ P) (p : P)", "Q", "contradiction", ("hn", "p"), True, "done"),
])
def test_compiled_candidates_with_pinned_lean(binders, target, operator, input_names, complete, next_goal):
    from analogy.lean_gym.models import Problem
    from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
    from analogy.lean_gym.verifier import LeanSubprocessVerifier

    verifier = LeanSubprocessVerifier(LEAN_PROJECT_ROOT)
    problem = Problem("compiled", "Compiled", binders, target, "test", "test", "test", 1)
    initial = verifier.initial_state(problem)
    inv = build_local_inventory(
        SymbolicState("compiled", initial.proof_state.text, "lean4"),
        verifier.proof_structure_after(problem, ()), fixed_target=target)
    inputs = tuple(f"b{next(i for i, entry in enumerate(inv.locals) if entry.identifier == name)}"
                   for name in input_names)
    grounded = plan(inv, operator, inputs, complete=complete, next_goal=next_goal)
    compiled = GroundedTacticCompiler().compile(grounded, inv, 4)
    assert compiled.diagnostics == ()
    assert compiled.candidates
    for candidate in compiled.candidates:
        result = verifier.try_tactic(problem, (), candidate.text)
        assert result.accepted, (candidate, result.diagnostics)


def test_typed_multistep_term_with_pinned_lean():
    from analogy.lean_gym.models import Problem
    from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
    from analogy.lean_gym.verifier import LeanSubprocessVerifier

    verifier = LeanSubprocessVerifier(LEAN_PROJECT_ROOT)
    problem = Problem("chain", "Chain", "(P R Q : Prop) (h1 : P ↔ R) (h2 : P → Q) (r : R)",
                      "Q", "test", "test", "test", 1)
    initial = verifier.initial_state(problem)
    inv = build_local_inventory(SymbolicState("chain", initial.proof_state.text, "lean4"),
                                verifier.proof_structure_after(problem, ()), fixed_target="Q")
    first = GroundedInferenceStep("step-1", GroundingOperator.IFF_BACKWARD,
                                 ("b3", "b5"), ("P ↔ R", "R"), "P", 0, False)
    compiled = GroundedTacticCompiler().compile(
        plan(inv, "apply_implication", ("b4", "step-1"), extra_steps=(first,)), inv, 4)
    assert texts(compiled) == ["exact h2 (h1.mpr r)", "apply h2"]
    for candidate in compiled.candidates:
        assert verifier.try_tactic(problem, (), candidate.text).accepted


@pytest.mark.parametrize("mutation", ["stale", "proposition", "future", "duplicate", "final", "arity", "type", "raw_identifier", "nonlocal"])
def test_direct_constructed_invalid_records_return_counted_diagnostics(mutation):
    inv = inventory()
    grounded = plan(inv)
    if mutation == "stale":
        inv = replace(inv, state_fingerprint="new-state")
    elif mutation == "proposition":
        grounded = replace(grounded, bindings=(replace(grounded.bindings[0], proposition="Q"),))
    elif mutation == "future":
        grounded = replace(grounded, steps=(replace(grounded.steps[0], inputs=("later",)),))
    elif mutation == "duplicate":
        grounded = replace(grounded, bindings=grounded.bindings * 2)
    elif mutation == "final":
        grounded = replace(grounded, steps=(replace(grounded.steps[0], output_proposition="Q"),))
    elif mutation == "arity":
        grounded = replace(grounded, steps=(replace(grounded.steps[0], inputs=(), input_propositions=()),))
    elif mutation == "type":
        grounded = replace(grounded, steps=(replace(grounded.steps[0], operator=GroundingOperator.IFF_FORWARD),))
    elif mutation == "raw_identifier":
        inv = replace(inv, locals=(replace(inv.locals[0], identifier="h; exact missing"),))
    else:
        grounded = replace(grounded, bindings=(replace(grounded.bindings[0], inventory_id="active_subgoal"),))
    result = GroundedTacticCompiler().compile(grounded, inv, 4)
    assert result.candidates == ()
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code in {"invalid_plan", "unsupported_operator"}


def test_unknown_composition_and_structural_intermediate_never_emit_partial_candidates():
    inv = inventory("h : P\nf : P → Q", "Q")
    grounded = plan(inv, "compose", ("b0", "b1"))
    result = GroundedTacticCompiler().compile(grounded, inv, 4)
    assert result.candidates == ()
    assert result.diagnostics[0].code == "unsupported_composition"
    intro = GroundedInferenceStep("intro", GroundingOperator.INTRODUCE, (), (), "P", 0, False)
    grounded = plan(inv, "apply_implication", ("b1", "intro"), extra_steps=(intro,))
    result = GroundedTacticCompiler().compile(grounded, inv, 4)
    assert result.candidates == ()
    assert result.diagnostics[0].code == "unsupported_composition"


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_invalid_candidate_limit_is_diagnostic(limit):
    inv = inventory()
    result = GroundedTacticCompiler().compile(plan(inv), inv, limit)
    assert result.candidates == ()
    assert result.diagnostics[0].code == "invalid_limit"
