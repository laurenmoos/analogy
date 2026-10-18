from __future__ import annotations

import pytest

from analogy.inference import prompts as prompts_module
from analogy.inference.prompts import PromptCatalog
from analogy.records import SymbolicState
from analogy.solver.semantic import (
    SemanticPrune,
    ExhaustiveSemanticChecker,
    SemanticCheckError,
    SemanticRecoveryEvent,
    SemanticResult,
    build_countermodel_table,
    forced_disjunction_tactic,
    render_countermodel_table,
    render_semantic_guidance,
)


def _state(expression: str) -> SymbolicState:
    return SymbolicState("state", expression, "lean4")


def test_semantic_guidance_wording_comes_from_prompt_catalog(tmp_path, monkeypatch):
    path = tmp_path / "prompts.yaml"
    path.write_text(
        """prompts:
  semantic_valid_guidance:
    version: semantic-valid-guidance-test-v1
    text: CUSTOM VALID
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        prompts_module,
        "_ACTIVE_CATALOG",
        PromptCatalog.load(path),
    )
    event = SemanticRecoveryEvent("event", "state", SemanticResult.VALID)

    assert render_semantic_guidance(event) == "CUSTOM VALID"


def test_semantic_guidance_bounds_oversized_catalog_templates(tmp_path, monkeypatch):
    path = tmp_path / "prompts.yaml"
    path.write_text(
        "prompts:\n"
        "  semantic_valid_guidance:\n"
        "    version: semantic-valid-guidance-test-v1\n"
        f"    text: '{'V' * 300}'\n"
        "  semantic_countermodel_guidance:\n"
        "    version: semantic-countermodel-guidance-test-v1\n"
        f"    text: '{'C' * 260} {{{{edge}}}} {{{{assignments}}}}'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        prompts_module,
        "_ACTIVE_CATALOG",
        PromptCatalog.load(path),
    )
    valid = SemanticRecoveryEvent("valid", "state", SemanticResult.VALID)
    countermodel = SemanticRecoveryEvent(
        "countermodel",
        "state",
        SemanticResult.COUNTERMODEL,
        countermodel=(("P", False),),
        rescue_state_id="state",
        abandoned_tactics=("left",),
    )

    for rendered in (
        render_semantic_guidance(valid),
        render_semantic_guidance(countermodel),
    ):
        assert len(rendered) == 240
        assert rendered.endswith("…")


def test_assesses_a_valid_goal_from_its_local_hypotheses() -> None:
    assessment = ExhaustiveSemanticChecker().assess(
        _state("P : Prop\nh : P\n⊢ P")
    )

    assert assessment.state_id == "state"
    assert assessment.result is SemanticResult.VALID
    assert assessment.countermodel == ()
    assert assessment.invalid_goal_index is None


def test_returns_the_first_countermodel_in_sorted_false_first_order() -> None:
    assessment = ExhaustiveSemanticChecker().assess(
        _state("P Q R : Prop\nh : P ∨ Q\n⊢ R")
    )

    assert assessment.result is SemanticResult.COUNTERMODEL
    assert assessment.countermodel == (("P", False), ("Q", True), ("R", False))
    assert assessment.invalid_goal_index == 0


@pytest.mark.parametrize(
    ("target", "expected"),
    (("True", SemanticResult.VALID), ("False", SemanticResult.COUNTERMODEL)),
)
def test_true_and_false_have_standard_semantics(
    target: str, expected: SemanticResult
) -> None:
    assessment = ExhaustiveSemanticChecker().assess(_state(f"⊢ {target}"))

    assert assessment.result is expected
    assert assessment.countermodel == ()


def test_grouped_prop_declarations_introduce_atoms_not_assumptions() -> None:
    assessment = ExhaustiveSemanticChecker().assess(_state("P Q : Prop\n⊢ P"))

    assert assessment.result is SemanticResult.COUNTERMODEL
    assert assessment.countermodel == (("P", False), ("Q", False))


def test_opaque_applications_are_unknown_even_when_repeated() -> None:
    assessment = ExhaustiveSemanticChecker().assess(
        _state("«P ∨ Q» : Prop\nh : f (P ∨ Q)\n⊢ f (P ∨ Q)")
    )

    assert assessment.result is SemanticResult.UNKNOWN


def test_multi_goal_state_reports_first_invalid_goal_in_display_order() -> None:
    assessment = ExhaustiveSemanticChecker().assess(
        _state(
            "case first\n"
            "P : Prop\n"
            "h : P\n"
            "⊢ P\n\n"
            "case second\n"
            "Q : Prop\n"
            "⊢ Q\n\n"
            "case third\n"
            "⊢ False"
        )
    )

    assert assessment.result is SemanticResult.COUNTERMODEL
    assert assessment.invalid_goal_index == 1
    assert assessment.countermodel == (("Q", False),)


@pytest.mark.parametrize(
    "expression",
    ("P : Prop", "unparsed declaration\n⊢ P", "case broken\n⊢"),
)
def test_malformed_proof_state_fails_closed(expression: str) -> None:
    with pytest.raises(SemanticCheckError):
        ExhaustiveSemanticChecker().assess(_state(expression))


@pytest.mark.parametrize(
    "expression",
    (
        "P : α → Prop\n⊢ ∀ x, P x",
        "P : α → Prop\nh : ∃ x, P x\n⊢ False",
    ),
)
def test_quantified_states_are_unknown_not_countermodels(expression: str) -> None:
    assessment = ExhaustiveSemanticChecker().assess(_state(expression))

    assert assessment.result is SemanticResult.UNKNOWN
    assert assessment.countermodel == ()
    assert assessment.invalid_goal_index is None


@pytest.mark.parametrize(
    "expression",
    (
        "n : Nat\n⊢ n = n",
        "P : Prop\nh : P\n⊢ id P",
        "P Q : Prop\nh : f (P ∨ Q)\n⊢ f (P ∨ Q)",
    ),
)
def test_unsupported_nonpropositional_states_are_unknown(expression: str) -> None:
    assessment = ExhaustiveSemanticChecker().assess(_state(expression))

    assert assessment.result is SemanticResult.UNKNOWN
    assert assessment.countermodel == ()
    assert assessment.invalid_goal_index is None


@pytest.mark.parametrize(
    "expression",
    (
        "P : Prop := True\n⊢ P",
        "P : Prop\nQ : Prop := P\nh : P\n⊢ Q",
    ),
)
def test_local_definitions_are_unknown_not_countermodels(expression: str) -> None:
    assert ExhaustiveSemanticChecker().assess(_state(expression)).result is SemanticResult.UNKNOWN


def test_countermodel_table_records_disjunction_side_dependencies_stably() -> None:
    state = _state("P Q R : Prop\nhPQ : P ∨ Q\nhR : R\n⊢ P")
    prunes = (
        SemanticPrune("state", "left", "left", (("P", True), ("Q", False), ("R", False)), 0),
        SemanticPrune("state", "right", "right", (("P", False), ("Q", True), ("R", False)), 0),
    )

    table = build_countermodel_table(state, prunes)

    assert table.rescue_state_id == "state"
    assert [row.tactic for row in table.rows] == ["left", "right"]
    assert [row.hypothesis_sides for row in table.rows] == [
        (("hPQ", "left"),),
        (("hPQ", "right"),),
    ]
    assert forced_disjunction_tactic(state, table) == (
        "hPQ",
        "rcases hPQ with hP | hQ",
    )
    assert render_countermodel_table(table) == (
        "left: P=true, Q=false, R=false; hPQ=left\n"
        "right: P=false, Q=true, R=false; hPQ=right"
    )


def test_forced_disjunction_tactic_flattens_three_way_hypothesis() -> None:
    state = _state("A B C : Prop\nh : A ∨ B ∨ C\n⊢ A")
    prunes = (
        SemanticPrune("state", "a", "left", (("A", True), ("B", False), ("C", False)), 0),
        SemanticPrune("state", "b", "right", (("A", False), ("B", True), ("C", False)), 0),
        SemanticPrune("state", "c", "right", (("A", False), ("B", False), ("C", True)), 0),
    )

    table = build_countermodel_table(state, prunes)

    assert [row.hypothesis_sides for row in table.rows] == [
        (("h", "first"),),
        (("h", "second"),),
        (("h", "third"),),
    ]
    assert forced_disjunction_tactic(state, table) == (
        "h",
        "rcases h with hA | hB | hC",
    )
