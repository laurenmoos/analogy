"""Deterministic propositional validation for displayed Lean proof states."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from itertools import product
import re
from typing import Protocol, TypeAlias

from ..model.prompts import prompt_catalog
from ..records import SymbolicState, TacticResult
from .proof_inventory import InventoryError, ProofGoal, proof_goals, proposition_root


class SemanticCheckError(ValueError):
    """The displayed proof state could not be checked safely."""


class SemanticResult(str, Enum):
    VALID = "valid"
    COUNTERMODEL = "countermodel"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SemanticAssessment:
    state_id: str
    result: SemanticResult
    countermodel: tuple[tuple[str, bool], ...] = ()
    invalid_goal_index: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state_id, str) or not self.state_id.strip():
            raise ValueError("state_id must be nonblank text")
        if not isinstance(self.result, SemanticResult):
            raise ValueError("result must be a SemanticResult")
        if self.result in {SemanticResult.VALID, SemanticResult.UNKNOWN}:
            if self.countermodel or self.invalid_goal_index is not None:
                raise ValueError("valid assessments cannot include a countermodel")
        elif self.invalid_goal_index is None or self.invalid_goal_index < 0:
            raise ValueError("countermodels require a non-negative goal index")


@dataclass(frozen=True, slots=True)
class SemanticPrune:
    parent_state_id: str
    successor_state_id: str
    tactic: str
    countermodel: tuple[tuple[str, bool], ...]
    invalid_goal_index: int

    def __post_init__(self) -> None:
        if not self.parent_state_id.strip() or not self.successor_state_id.strip():
            raise ValueError("semantic prune state IDs must be nonblank")
        if not self.tactic.strip():
            raise ValueError("semantic prune tactic must be nonblank")
        if self.invalid_goal_index < 0:
            raise ValueError("semantic prune goal index must be non-negative")


@dataclass(frozen=True, slots=True)
class CountermodelRow:
    tactic: str
    countermodel: tuple[tuple[str, bool], ...]
    hypothesis_sides: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CountermodelTable:
    rescue_state_id: str
    rows: tuple[CountermodelRow, ...]


@dataclass(frozen=True, slots=True)
class ForcedMoveEvent:
    rescue_state_id: str
    hypothesis: str
    tactic: str
    result: TacticResult
    outcome: str


@dataclass(frozen=True, slots=True)
class SemanticRecoveryEvent:
    event_id: str
    trigger_state_id: str
    result: SemanticResult | None
    countermodel: tuple[tuple[str, bool], ...] = ()
    rescue_state_id: str | None = None
    abandoned_tactics: tuple[str, ...] = ()
    error: str | None = None
    countermodel_table: CountermodelTable | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.trigger_state_id.strip():
            raise ValueError("semantic event and trigger IDs must be nonblank")
        if self.result is None:
            if self.error is None or not self.error.strip():
                raise ValueError("failed semantic events require an error")
        elif not isinstance(self.result, SemanticResult):
            raise ValueError("result must be a SemanticResult or None")
        elif self.error is not None:
            raise ValueError("successful semantic assessments cannot include an error")
        if self.rescue_state_id is not None and not self.rescue_state_id.strip():
            raise ValueError("rescue_state_id must be nonblank or None")
        if any(not item.strip() for item in self.abandoned_tactics):
            raise ValueError("abandoned tactics must be nonblank")
        if self.countermodel_table is not None and not isinstance(
            self.countermodel_table, CountermodelTable
        ):
            raise ValueError("countermodel_table must be a CountermodelTable or None")


class SemanticChecker(Protocol):
    def assess(self, state: SymbolicState) -> SemanticAssessment: ...


def render_semantic_guidance(event: SemanticRecoveryEvent) -> str:
    """Render deterministic SAT guidance in at most 240 characters."""
    if event.result is SemanticResult.VALID:
        return _bounded_guidance(prompt_catalog().text("semantic_valid_guidance"))
    if event.result is not SemanticResult.COUNTERMODEL or event.rescue_state_id is None:
        raise SemanticCheckError("semantic guidance requires a validated rescue state")
    edge = " ".join(event.abandoned_tactics[0].split())
    if len(edge) > 72:
        edge = edge[:71] + "…"
    catalog = prompt_catalog()
    empty = catalog.text(
        "semantic_countermodel_guidance",
        edge=edge,
        assignments="",
    )
    available = max(0, 240 - len(empty))
    assignments: list[str] = []
    for atom, value in event.countermodel:
        item = f"{atom}={'true' if value else 'false'}"
        candidate = ", ".join(assignments + [item])
        if len(candidate) > available:
            if available >= 1:
                clipped = ", ".join(assignments)
                if len(clipped) + (2 if clipped else 0) + 1 <= available:
                    assignments.append("…")
            break
        assignments.append(item)
    body = ", ".join(assignments) if assignments else "no variable assignments"
    if len(body) > available:
        body = body[: max(0, available - 1)] + "…"
    return _bounded_guidance(
        catalog.text(
            "semantic_countermodel_guidance",
            edge=edge,
            assignments=body,
        )
    )


def _bounded_guidance(text: str) -> str:
    return text if len(text) <= 240 else text[:239] + "…"


Expr: TypeAlias = tuple


class _UnsupportedExpression(ValueError):
    pass


_PROPOSITION_ATOM = re.compile(r"(?:[A-Za-z_][\w'✝]*|«[^»]+»)")


def _expression(text: str, allowed_atoms: set[str] | None = None) -> Expr:
    root = proposition_root(text)
    if root.text == "True":
        return ("constant", True)
    if root.text == "False":
        return ("constant", False)
    if root.operator == "¬" and root.parts is not None:
        return ("not", _expression(root.parts[0], allowed_atoms))
    if root.operator in {"∧", "∨", "→", "↔"} and root.parts is not None:
        return (
            root.operator,
            _expression(root.parts[0], allowed_atoms),
            _expression(root.parts[1], allowed_atoms),
        )
    if _PROPOSITION_ATOM.fullmatch(root.text) is None:
        raise _UnsupportedExpression(root.text)
    if allowed_atoms is not None and root.text not in allowed_atoms:
        raise _UnsupportedExpression(root.text)
    return ("atom", root.text)


def _atoms(expression: Expr) -> set[str]:
    kind = expression[0]
    if kind == "atom":
        return {expression[1]}
    if kind == "constant":
        return set()
    if kind == "not":
        return _atoms(expression[1])
    return _atoms(expression[1]) | _atoms(expression[2])


def _evaluate(expression: Expr, valuation: dict[str, bool]) -> bool:
    kind = expression[0]
    if kind == "atom":
        return valuation[expression[1]]
    if kind == "constant":
        return expression[1]
    if kind == "not":
        return not _evaluate(expression[1], valuation)
    left = _evaluate(expression[1], valuation)
    right = _evaluate(expression[2], valuation)
    if kind == "∧":
        return left and right
    if kind == "∨":
        return left or right
    if kind == "→":
        return not left or right
    if kind == "↔":
        return left == right
    raise SemanticCheckError(f"unsupported semantic operator {kind!r}")


def build_countermodel_table(
    state: SymbolicState,
    prunes: tuple[SemanticPrune, ...] | list[SemanticPrune],
) -> CountermodelTable:
    """Summarize which side of each local disjunction each sibling model uses."""

    try:
        goal = proof_goals(state.expression)[0]
    except InventoryError as error:
        raise SemanticCheckError(str(error)) from error
    declared_atoms = {
        name for name, proposition in goal.declarations
        if proposition_root(proposition).text == "Prop"
    }
    disjunctions: list[tuple[str, tuple[Expr, ...]]] = []

    def flattened_or(text: str) -> tuple[str, ...]:
        root = proposition_root(text)
        if root.operator != "∨" or root.parts is None:
            return (text,)
        return (root.parts[0],) + flattened_or(root.parts[1])
    for name, proposition in goal.declarations:
        root = proposition_root(proposition)
        if root.operator == "∨" and root.parts is not None:
            try:
                disjunctions.append((
                    name,
                    tuple(
                        _expression(part, declared_atoms)
                        for part in flattened_or(proposition)
                    ),
                ))
            except _UnsupportedExpression:
                continue
    rows: list[CountermodelRow] = []
    for prune in prunes:
        valuation = dict(prune.countermodel)
        sides: list[tuple[str, str]] = []
        for name, parts in disjunctions:
            try:
                truths = tuple(_evaluate(part, valuation) for part in parts)
            except KeyError:
                label = "unknown"
            else:
                if len(parts) == 3 and sum(truths) == 1:
                    label = ("first", "second", "third")[truths.index(True)]
                elif len(parts) == 2:
                    label = (
                        "both" if all(truths)
                        else "left" if truths[0]
                        else "right" if truths[1]
                        else "neither"
                    )
                else:
                    label = "multiple" if any(truths) else "neither"
            sides.append((name, label))
        rows.append(CountermodelRow(prune.tactic, prune.countermodel, tuple(sides)))
    return CountermodelTable(state.id, tuple(rows))


def forced_disjunction_tactic(
    state: SymbolicState,
    table: CountermodelTable,
) -> tuple[str, str] | None:
    """Return the first disjunction split required by opposite sibling models."""

    if table.rescue_state_id != state.id:
        raise SemanticCheckError("countermodel table belongs to another rescue state")
    labels: dict[str, set[str]] = {}
    order: list[str] = []
    for row in table.rows:
        for hypothesis, side in row.hypothesis_sides:
            if hypothesis not in labels:
                labels[hypothesis] = set()
                order.append(hypothesis)
            labels[hypothesis].add(side)
    for hypothesis in order:
        if {"first", "second", "third"}.issubset(labels[hypothesis]):
            try:
                goal = proof_goals(state.expression)[0]
                proposition = dict(goal.declarations)[hypothesis]
                parts: list[str] = []
                current = proposition
                while True:
                    root = proposition_root(current)
                    if root.operator != "∨" or root.parts is None:
                        parts.append(current)
                        break
                    parts.append(root.parts[0])
                    current = root.parts[1]
                if len(parts) != 3:
                    continue
                occupied = {name for name, _ in goal.declarations}

                def fresh(part: str, fallback: str) -> str:
                    atoms = re.findall(r"[A-Za-z_][A-Za-z0-9_']*", part)
                    base = "h" + ("".join(atoms)[:18] or fallback)
                    candidate = base
                    index = 2
                    while candidate in occupied:
                        candidate = f"{base}_{index}"
                        index += 1
                    occupied.add(candidate)
                    return candidate

                names = tuple(
                    fresh(part, fallback)
                    for part, fallback in zip(parts, ("First", "Second", "Third"))
                )
                return hypothesis, f"rcases {hypothesis} with {' | '.join(names)}"
            except (InventoryError, KeyError):
                continue
        if {"left", "right"}.issubset(labels[hypothesis]):
            from ..model.parser import normalize_lean4_tactic

            tactic = normalize_lean4_tactic(
                f"rcases {hypothesis} with left | right",
                state.expression,
            )
            return hypothesis, tactic
    return None


def render_countermodel_table(table: CountermodelTable | None) -> str:
    if table is None or not table.rows:
        return "(none)"
    lines: list[str] = []
    for row in table.rows:
        assignments = ", ".join(
            f"{atom}={'true' if value else 'false'}"
            for atom, value in row.countermodel
        ) or "no assignments"
        sides = ", ".join(f"{name}={side}" for name, side in row.hypothesis_sides)
        suffix = f"; {sides}" if sides else ""
        lines.append(f"{row.tactic}: {assignments}{suffix}")
    return "\n".join(lines)


def _goal_countermodel(goal: ProofGoal) -> tuple[tuple[str, bool], ...] | None:
    assumptions: list[Expr] = []
    grouped = _grouped_declarations(goal.declarations)
    declared_atoms = {
        name
        for names, proposition in grouped
        if proposition_root(proposition).text == "Prop"
        for name in names
    }
    for names, proposition in grouped:
        if proposition_root(proposition).text == "Prop":
            continue
        else:
            assumptions.append(_expression(proposition, declared_atoms))
    target = _expression(goal.target, declared_atoms)
    atoms = sorted(
        declared_atoms
        | _atoms(target)
        | set().union(*(_atoms(item) for item in assumptions), set())
    )
    for values in product((False, True), repeat=len(atoms)):
        valuation = dict(zip(atoms, values))
        if all(_evaluate(item, valuation) for item in assumptions) and not _evaluate(
            target, valuation
        ):
            return tuple(zip(atoms, values))
    return None


def _grouped_declarations(
    declarations: tuple[tuple[str, str], ...],
) -> tuple[tuple[tuple[str, ...], str], ...]:
    groups: list[tuple[list[str], str]] = []
    for name, proposition in declarations:
        if groups and groups[-1][1] == proposition:
            groups[-1][0].append(name)
        else:
            groups.append(([name], proposition))
    return tuple((tuple(names), proposition) for names, proposition in groups)


class ExhaustiveSemanticChecker:
    """Decide small propositional goals by exhaustive Boolean valuation."""

    def assess(self, state: SymbolicState) -> SemanticAssessment:
        if not isinstance(state, SymbolicState):
            raise SemanticCheckError("semantic checker requires a SymbolicState")
        try:
            if any(
                ":=" in line
                for line in state.expression.splitlines()
                if not line.lstrip().startswith("⊢")
            ):
                return SemanticAssessment(state.id, SemanticResult.UNKNOWN)
            goals = proof_goals(state.expression)
            if any(_goal_has_quantifier(goal) for goal in goals):
                return SemanticAssessment(state.id, SemanticResult.UNKNOWN)
            for index, goal in enumerate(goals):
                countermodel = _goal_countermodel(goal)
                if countermodel is not None:
                    return SemanticAssessment(
                        state.id,
                        SemanticResult.COUNTERMODEL,
                        countermodel,
                        index,
                    )
        except _UnsupportedExpression:
            return SemanticAssessment(state.id, SemanticResult.UNKNOWN)
        except (InventoryError, RecursionError, ValueError) as error:
            if isinstance(error, SemanticCheckError):
                raise
            raise SemanticCheckError(str(error)) from error
        return SemanticAssessment(state.id, SemanticResult.VALID)


_QUANTIFIER = re.compile(r"(?:^|[^\w])(?:∀|∃|forall\b|exists\b)")


def _goal_has_quantifier(goal: ProofGoal) -> bool:
    return _QUANTIFIER.search(goal.target) is not None or any(
        _QUANTIFIER.search(proposition) is not None
        for _, proposition in goal.declarations
    )


__all__ = [
    "ExhaustiveSemanticChecker",
    "CountermodelRow",
    "CountermodelTable",
    "ForcedMoveEvent",
    "SemanticAssessment",
    "SemanticCheckError",
    "SemanticChecker",
    "SemanticRecoveryEvent",
    "SemanticPrune",
    "SemanticResult",
    "build_countermodel_table",
    "forced_disjunction_tactic",
    "render_countermodel_table",
    "render_semantic_guidance",
]
