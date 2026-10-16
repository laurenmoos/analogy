from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from analogy.lean_gym.models import Diagnostic, Problem, VerificationStatus
from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
from analogy.lean_gym.verifier import LeanGymAdapter, LeanRun, LeanSubprocessVerifier
from analogy.proof_inventory import build_local_inventory
from analogy.records import SymbolicState


def problem(binders: str = "(P : Prop)", target: str = "P → P") -> Problem:
    return Problem("identity", "Identity", binders, target, "test", "test", "implication", 1)


@pytest.fixture
def verifier() -> LeanSubprocessVerifier:
    return LeanSubprocessVerifier(LEAN_PROJECT_ROOT)


def test_real_lean_tracks_open_rejected_and_complete_steps(verifier: LeanSubprocessVerifier) -> None:
    initial = verifier.initial_state(problem())
    assert initial.status is VerificationStatus.OPEN
    assert initial.proof_state is not None
    assert "⊢ P → P" in initial.proof_state.text

    opened = verifier.try_tactic(problem(), (), "intro h")
    assert opened.status is VerificationStatus.OPEN
    assert opened.accepted is True
    assert opened.proof_state is not None
    assert "h : P" in opened.proof_state.text
    assert "⊢ P" in opened.proof_state.text

    rejected = verifier.try_tactic(problem(), ("intro h",), "exact missing")
    assert rejected.status is VerificationStatus.REJECTED
    assert rejected.accepted is False
    assert any("Unknown identifier" in diagnostic.message for diagnostic in rejected.diagnostics)

    complete = verifier.try_tactic(problem(), ("intro h",), "exact h")
    assert complete.status is VerificationStatus.COMPLETE
    assert complete.accepted is True
    assert complete.proof_state is None


def test_real_lean_preserves_multiple_goals(verifier: LeanSubprocessVerifier) -> None:
    two = problem("(P Q : Prop)", "P → Q → P ∧ Q")
    prefix = ("intro p", "intro q")
    split = verifier.try_tactic(two, prefix, "constructor")
    assert split.status is VerificationStatus.OPEN
    assert split.proof_state is not None
    assert split.proof_state.text.count("⊢") == 2

    first = verifier.try_tactic(two, prefix + ("constructor",), "exact p")
    assert first.status is VerificationStatus.OPEN
    assert first.proof_state is not None
    assert first.proof_state.text.count("⊢") == 1

    final = verifier.try_tactic(two, prefix + ("constructor", "exact p"), "exact q")
    assert final.status is VerificationStatus.COMPLETE


def test_real_lean_extracts_structural_proof_signature(
    verifier: LeanSubprocessVerifier,
) -> None:
    item = problem("(P Q : Prop) (h : P ∧ Q)", "Q ∧ P")

    structure = verifier.proof_structure_after(item, ())

    assert structure.goal_count == 1
    assert '"root":"and"' in structure.target_shape
    assert structure.hypothesis_shapes
    assert "hidden" not in structure.target_shape
    assert "/tmp/" not in structure.target_shape


def test_real_lean_local_inventory_reuses_one_structure_extraction(
    verifier: LeanSubprocessVerifier, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LeanGymAdapter(problem("(P Q : Prop) (h : P ∧ Q)", "Q ∧ P"), verifier)
    state = adapter.initial_state()
    original = verifier._run_lean
    calls = []

    def counted(source: str) -> LeanRun:
        calls.append(source)
        return original(source)

    monkeypatch.setattr(verifier, "_run_lean", counted)
    inventory = adapter.local_inventory_for(state)
    assert [(entry.identifier, entry.proposition) for entry in inventory.locals] == [
        ("P", "Prop"), ("Q", "Prop"), ("h", "P ∧ Q"),
    ]
    assert inventory.fixed_target == "Q ∧ P"
    assert len(calls) == 1
    # A caller that already has the structure needs no further Lean run.
    structure = adapter.proof_structure_for(state)
    before = len(calls)
    assert build_local_inventory(state, structure, fixed_target=adapter.problem.target) == inventory
    assert len(calls) == before


@pytest.mark.parametrize("binders,prefix,expected_context,expected_locals", [
    (
        "(h : ∀ x : Nat, let y := x; y = x)", (),
        "h :\n  ∀ (x : Nat),\n    let y := x;\n    y = x",
        (("h", "∀ (x : Nat),\n    let y := x;\n    y = x"),),
    ),
    (
        "(" + " ".join(f"VeryLongPropositionName{i}" for i in range(8)) + " : Prop)", (),
        " ".join(f"VeryLongPropositionName{i}" for i in range(8)) + " :\n  Prop",
        tuple((f"VeryLongPropositionName{i}", "Prop") for i in range(8)),
    ),
    (
        "(P : Prop) (h : P)", ("let t : (let x := P; x) := h",),
        "P : Prop\nh : P\nt : let x := P;\nx :=\n  h",
        (("P", "Prop"), ("h", "P"), ("t", "let x := P;\nx")),
    ),
])
def test_real_lean_inventory_preserves_pretty_printer_declaration_layouts(
    verifier: LeanSubprocessVerifier, binders, prefix, expected_context, expected_locals,
) -> None:
    item = problem(binders, "True")
    result = verifier.state_after(item, prefix)
    assert result.proof_state is not None
    assert result.proof_state.text == expected_context + "\n⊢ True"
    state = SymbolicState("layout", result.proof_state.text, "lean4", prefix)
    inventory = build_local_inventory(state, verifier.proof_structure_after(item, prefix), fixed_target=item.target)
    assert tuple((entry.identifier, entry.proposition) for entry in inventory.locals) == expected_locals


@pytest.mark.parametrize("binders,target,hypothesis,left_component", [
    (
        '(s : String) (h : "a:=b" = s)', '"∧" = s ∧ True',
        '"a:=b" = s', '"∧" = s',
    ),
    (
        r'(s : String) (h : "a\"b:=c" = s)', r'"a\"∧b" = s ∧ True',
        r'"a\"b:=c" = s', r'"a\"∧b" = s',
    ),
    (
        "(c : Char) (h : '\\'' = c)", "'∧' = c ∧ True",
        "'\\'' = c", "'∧' = c",
    ),
    (
        "(c : Char) (h : '(' = c)", "'(' = c ∧ True",
        "'(' = c", "'(' = c",
    ),
])
def test_real_lean_inventory_preserves_literals_while_splitting_syntax(
    verifier: LeanSubprocessVerifier, binders, target, hypothesis, left_component,
) -> None:
    item = problem(binders, target)
    result = verifier.initial_state(item)
    assert result.proof_state is not None
    state = SymbolicState("literals", result.proof_state.text, "lean4", ())
    inventory = build_local_inventory(state, verifier.proof_structure_after(item, ()), fixed_target=target)
    assert inventory.resolve("local:1").proposition == hypothesis
    assert inventory.active_subgoal == target
    assert inventory.resolve("target:0").proposition == left_component
    assert inventory.resolve("target:1").proposition == "True"


@pytest.mark.parametrize("candidate", ["", "   ", "intro h\nend", "intro h\rend", "intro\x00h", "x" * 4097])
def test_invalid_tactic_is_rejected_without_starting_lean(monkeypatch: pytest.MonkeyPatch, candidate: str) -> None:
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess must not start")

    monkeypatch.setattr(subprocess, "run", forbidden)
    result = LeanSubprocessVerifier(LEAN_PROJECT_ROOT).try_tactic(problem(), (), candidate)
    assert result.status is VerificationStatus.REJECTED
    assert result.diagnostics[0].kind == "input_validation"
    assert called is False


def test_verify_complete_rejects_unsolved_proof(verifier: LeanSubprocessVerifier) -> None:
    result = verifier.verify_complete(problem(), ("intro h",))
    assert result.status is VerificationStatus.REJECTED
    assert result.accepted is False


def test_empty_controlled_trace_is_not_complete_when_clean_pass_fails() -> None:
    class ScriptedVerifier(LeanSubprocessVerifier):
        def __init__(self) -> None:
            super().__init__(LEAN_PROJECT_ROOT)
            self.calls = 0

        def _run_lean(self, source: str) -> LeanRun:
            self.calls += 1
            if self.calls == 1:
                return LeanRun(0, (Diagnostic("information", "trace", "", 3, 2),), 1)
            return LeanRun(1, (Diagnostic("error", "type", "clean proof failed", 2, 2),), 1)

    result = ScriptedVerifier().try_tactic(problem(), (), "exact fun h => h")
    assert result.status is VerificationStatus.REJECTED
    assert result.accepted is False


def test_empty_trace_at_uncontrolled_line_is_rejected_without_clean_pass() -> None:
    class SpoofedVerifier(LeanSubprocessVerifier):
        def __init__(self) -> None:
            super().__init__(LEAN_PROJECT_ROOT)
            self.calls = 0

        def _run_lean(self, source: str) -> LeanRun:
            self.calls += 1
            return LeanRun(0, (Diagnostic("information", "trace", "", 99, 2),), 1)

    verifier = SpoofedVerifier()
    result = verifier.try_tactic(problem(), (), "exact fun h => h")
    assert result.status is VerificationStatus.REJECTED
    assert verifier.calls == 1
