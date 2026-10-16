"""Deterministic, inventory-only Lean syntax; successful compilation is not proof."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from .grounded_plan import GroundedPlanError, GroundedTacticPlan, GroundingOperator as Op
from .proof_inventory import InventoryError, InventoryKind, LocalProofInventory


TACTIC_COMPILER_VERSION = "grounded-compiler-v1"


@dataclass(frozen=True, slots=True)
class CompiledTactic:
    text: str
    step_ids: tuple[str, ...]
    binding_ids: tuple[str, ...]
    predicted_next_subgoal: str


@dataclass(frozen=True, slots=True)
class CompilationDiagnostic:
    code: str
    step_id: str | None
    message: str


@dataclass(frozen=True, slots=True)
class CompilationResult:
    candidates: tuple[CompiledTactic, ...]
    diagnostics: tuple[CompilationDiagnostic, ...]


class _CannotCompile(ValueError):
    def __init__(self, code: str, step_id: str | None, message: str):
        super().__init__(message)
        self.diagnostic = CompilationDiagnostic(code, step_id, message)


@dataclass(frozen=True, slots=True)
class _Term:
    text: str
    proposition: str
    steps: frozenset[str] = frozenset()
    bindings: frozenset[str] = frozenset()
    atomic: bool = True

    def argument(self) -> str:
        return self.text if self.atomic else f"({self.text})"


def _safe_identifier(name: str | None) -> bool:
    if not isinstance(name, str) or not name or any(unicodedata.category(c).startswith("C") for c in name):
        return False
    if name.startswith("«") and name.endswith("»"):
        return len(name) > 2 and "»" not in name[1:-1] and "«" not in name[1:-1]
    return ((name[0].isalpha() or name[0] == "_")
            and all(c.isalnum() or c in "_'✝" or unicodedata.category(c).startswith("M") for c in name))


def _canonical(text: str) -> str:
    # Generated syntax has no strings/types. Preserve spaces *inside* exact quoted names.
    return " ".join(re.findall(r"«[^»]*»|[^\s«]+", text))


@dataclass(frozen=True, slots=True)
class _PropositionRoot:
    text: str
    operator: str | None = None
    parts: tuple[str, str] | None = None


_WORD = re.compile(r"[\w']+")
_BINDERS = {"forall", "exists", "fun", "λ", "let", "if", "match", "by", "do", "have", "show", "calc"}


def _proposition_root(proposition: str) -> _PropositionRoot:
    """Recognize the supported root fragment using pinned Lean precedences.

    Iff=20, arrow=25, or=30, and=35, prefix not's operand=40, equality=50.
    A depth-zero occurrence alone is insufficient: lower-precedence operators
    outside parentheses own the root. Unparenthesized binders/control forms are
    deliberately opaque because their bodies may capture later connectives.
    This is conservative syntax decomposition, never Lean elaboration.
    """
    text = proposition.strip()
    pairs = {"(": ")", "[": "]", "{": "}", "⟨": "⟩"}
    for _ in range(64):
        opaque = _PropositionRoot(text)
        stack: list[str] = []
        operators: list[tuple[str, int]] = []
        wrapped = text.startswith("(")
        index = 0
        while index < len(text):
            char = text[index]
            prime = char == "'" and index > 0 and (text[index - 1].isalnum() or text[index - 1] in "_'✝")
            if char == '"' or (char == "'" and not prime):
                quote = char
                index += 1
                while index < len(text) and text[index] != quote:
                    index += 2 if text[index] == "\\" else 1
                if index >= len(text):
                    return opaque
                index += 1
                continue
            if char == "«":
                end = text.find("»", index + 1)
                if end < 0:
                    return opaque
                index = end + 1
                continue
            if text.startswith(("/-", "--"), index):
                return opaque
            if char in pairs:
                stack.append(pairs[char])
            elif char in pairs.values():
                if not stack or stack.pop() != char:
                    return opaque
                if not stack and index != len(text) - 1:
                    wrapped = False
            elif not stack:
                if char in "∀∃":
                    return opaque
                word = _WORD.match(text, index)
                if word:
                    if word[0] in _BINDERS:
                        return opaque
                    index = word.end()
                    continue
                if char in "↔→∨∧=":
                    # Equality notation is not ==, :=, =>, <=, >=, or !=.
                    if char == "=" and ((index and text[index - 1] in "=:<>!")
                                         or (index + 1 < len(text) and text[index + 1] in "=>")):
                        return opaque
                    operators.append((char, index))
            index += 1
        if stack:
            return opaque
        if wrapped:
            text = text[1:-1].strip()
            continue
        for operator in ("↔", "→", "∨", "∧", "¬", "="):
            if operator == "¬":
                if text.startswith("¬") and text[1:].strip():
                    return _PropositionRoot(text, "¬", (text[1:].strip(), "False"))
                continue
            positions = [position for symbol, position in operators if symbol == operator]
            if positions:
                if operator in ("↔", "=") and len(positions) != 1:
                    return opaque  # These infixes are non-associative in Lean.
                position = positions[0]  # arrow/or/and are right-associative.
                left, right = text[:position].strip(), text[position + 1:].strip()
                return _PropositionRoot(text, operator, (left, right)) if left and right else opaque
        return opaque
    return _PropositionRoot(text)  # Bound work for deeply wrapped unsupported text.


def _binary(proposition: str, operator: str) -> tuple[str, str] | None:
    root = _proposition_root(proposition)
    return root.parts if root.operator == operator else None


def _same(left: str, right: str) -> bool:
    # Ignore only complete outer parentheses; exact binding checks remain verbatim.
    return _proposition_root(left).text == _proposition_root(right).text


def _implication(proposition: str) -> tuple[str, str] | None:
    root = _proposition_root(proposition)
    return root.parts if root.operator in ("→", "¬") else None


class GroundedTacticCompiler:
    """Recheck current grounding, then emit only a bounded supported template set.

    The plan schema does not retain its transfer or a validation capability. This
    boundary therefore revalidates all information available in plan/inventory;
    transfer provenance must already have been checked by the strict parser.
    Intermediate proof terms are inlined. Tactic-valued intermediate steps and
    arbitrary COMPOSE combinations fail closed, never partially execute.
    """

    def compile(self, plan: GroundedTacticPlan, inventory: LocalProofInventory,
                candidate_limit: int) -> CompilationResult:
        if isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int) or candidate_limit < 0:
            return CompilationResult((), (CompilationDiagnostic("invalid_limit", None,
                                                                 "candidate_limit must be non-negative integer"),))
        try:
            self._validate(plan, inventory)
            candidates = self._compile(plan, inventory)
            seen: set[str] = set()
            unique = []
            for candidate in candidates:
                if candidate.text != "".join(candidate.text.splitlines()) or "\x00" in candidate.text:
                    raise _CannotCompile("unsupported_composition", None, "compiled tactic must be one line")
                text = _canonical(candidate.text)
                if any(c in text for c in "\n\r\x00") or not text or len(text) > 4096:
                    raise _CannotCompile("unsupported_composition", None, "compiled tactic exceeds single-line bounds")
                if text not in seen:
                    seen.add(text)
                    unique.append(replace(candidate, text=text))
            return CompilationResult(tuple(unique[:candidate_limit]), ())
        except _CannotCompile as error:
            return CompilationResult((), (error.diagnostic,))
        except (GroundedPlanError, InventoryError, TypeError, ValueError, AttributeError) as error:
            return CompilationResult((), (CompilationDiagnostic("invalid_plan", None, str(error)),))

    @staticmethod
    def _validate(plan: GroundedTacticPlan, inventory: LocalProofInventory) -> None:
        if not isinstance(plan, GroundedTacticPlan) or not isinstance(inventory, LocalProofInventory):
            raise GroundedPlanError("compiler requires a grounded plan and local inventory")
        replace(plan)  # Direct constructors (and subsequently tampered records) get the same schema checks.
        if plan.state_fingerprint != inventory.state_fingerprint:
            raise GroundedPlanError("plan must cite the current inventory fingerprint")
        entries = inventory.locals + inventory.target_entries
        if len({entry.inventory_id for entry in entries}) != len(entries):
            raise GroundedPlanError("inventory IDs must be unique")
        if any(entry.kind is not InventoryKind.LOCAL for entry in inventory.locals):
            raise GroundedPlanError("local inventory entries must have local kind")
        active = inventory.resolve("active_subgoal")
        if active.kind is not InventoryKind.ACTIVE_SUBGOAL or active.proposition != inventory.active_subgoal:
            raise GroundedPlanError("inventory active subgoal must agree with its entry")
        references: dict[str, str] = {}
        roles: set[str] = set()
        for binding in plan.bindings:
            replace(binding)
            if binding.binding_id in references or binding.proof_role in roles:
                raise GroundedPlanError("binding IDs and exclusive roles must be unique")
            entry = inventory.resolve(binding.inventory_id)
            if entry.proposition != binding.proposition:
                raise GroundedPlanError("binding proposition must exactly match inventory")
            references[binding.binding_id] = binding.proposition
            roles.add(binding.proof_role)
        for step in plan.steps:
            replace(step)
            if step.step_id in references:
                raise GroundedPlanError("step and binding IDs must be globally unique")
            for reference, proposition in zip(step.inputs, step.input_propositions):
                if reference not in references or references[reference] != proposition:
                    raise GroundedPlanError("input must cite an exact binding or prior-step proposition")
            references[step.step_id] = step.output_proposition
        final = plan.steps[-1]
        if final.closes_subgoal != plan.completion_expected:
            raise GroundedPlanError("final closure must agree with plan")
        output = inventory.active_subgoal if plan.completion_expected else plan.expected_next_subgoal
        if final.output_proposition != output:
            raise GroundedPlanError("final output must match active goal or expected successor")

    def _compile(self, plan: GroundedTacticPlan, inventory: LocalProofInventory) -> list[CompiledTactic]:
        bindings = {binding.binding_id: binding for binding in plan.bindings}
        steps = {step.step_id: step for step in plan.steps}
        needed = {plan.steps[-1].step_id}
        for step in reversed(plan.steps):
            if step.step_id in needed:
                needed.update(step.inputs)
        if any(step.step_id not in needed for step in plan.steps):
            raise _CannotCompile("unsupported_composition", None, "disconnected inference steps are unsupported")
        terms: dict[str, _Term] = {}

        def resolve(reference: str, step_id: str) -> _Term:
            if reference in terms:
                return terms[reference]
            if reference not in bindings:
                raise _CannotCompile("unsupported_composition", step_id, "intermediate step has no supported proof term")
            entry = inventory.resolve(bindings[reference].inventory_id)
            if entry.identifier and "✝" in entry.identifier and not entry.identifier.startswith("«"):
                raise _CannotCompile("unsupported_identifier", step_id,
                                     "inaccessible shadow-marked local requires a verified rename step")
            if entry.kind is not InventoryKind.LOCAL or not _safe_identifier(entry.identifier):
                raise GroundedPlanError("proof terms require a safe exact local inventory identifier")
            return _Term(entry.identifier, entry.proposition, bindings=frozenset((reference,)))

        def candidate(text: str, term: _Term, predicted: str) -> CompiledTactic:
            return CompiledTactic(text, tuple(step.step_id for step in plan.steps if step.step_id in term.steps),
                                  tuple(binding.binding_id for binding in plan.bindings if binding.binding_id in term.bindings),
                                  predicted)

        for step in plan.steps:
            final = step is plan.steps[-1]
            op = step.operator

            def unsupported(message="operator shape or proposition is unsupported"):
                raise _CannotCompile("unsupported_operator", step.step_id, message)

            def term(text: str, args: list[_Term], *, atomic=False) -> _Term:
                if len(text) > 4096:
                    raise _CannotCompile("unsupported_composition", step.step_id,
                                         "intermediate proof term exceeds tactic bounds")
                return _Term(text, step.output_proposition,
                             frozenset((step.step_id,)).union(*(arg.steps for arg in args)),
                             frozenset().union(*(arg.bindings for arg in args)), atomic)

            structural = {Op.INTRODUCE, Op.REWRITE_FORWARD, Op.REWRITE_BACKWARD,
                          Op.SPLIT, Op.CHOOSE_LEFT, Op.CHOOSE_RIGHT, Op.CASES}
            if not final and op in structural:
                raise _CannotCompile("unsupported_composition", step.step_id,
                                     "tactic-valued intermediate requires unsupported state transformation")
            args = [resolve(reference, step.step_id) for reference in step.inputs]
            fallback: tuple[str, _Term, str] | None = None
            result: _Term | None = None
            if op is Op.EXACT_LOCAL:
                if len(args) != 1 or step.inputs[0] not in bindings or not _same(args[0].proposition, step.output_proposition):
                    unsupported()
                result = term(args[0].text, args, atomic=args[0].atomic)
            elif op is Op.COMPOSE:
                if len(args) != 1 or step.inputs[0] not in steps or not _same(args[0].proposition, step.output_proposition):
                    raise _CannotCompile("unsupported_composition", step.step_id, "compose requires one typed prior result")
                result = term(args[0].text, args, atomic=args[0].atomic)
            elif op is Op.APPLY_IMPLICATION:
                relation = _implication(args[0].proposition) if args else None
                if relation is None or len(args) not in (1, 2):
                    unsupported()
                antecedent, consequent = relation
                partial = term(args[0].text, args[:1], atomic=args[0].atomic)
                if len(args) == 1:
                    if not final or plan.completion_expected or not _same(consequent, inventory.active_subgoal) or not _same(antecedent, step.output_proposition):
                        unsupported()
                    return [candidate(f"apply {args[0].argument()}", partial, antecedent)]
                if not _same(args[1].proposition, antecedent) or not _same(step.output_proposition, consequent):
                    unsupported()
                result = term(f"{args[0].argument()} {args[1].argument()}", args)
                fallback = (f"apply {args[0].argument()}", partial, antecedent)
            elif op in (Op.IFF_FORWARD, Op.IFF_BACKWARD):
                relation = _binary(args[0].proposition, "↔") if args else None
                if relation is None or len(args) != 2:
                    unsupported()
                left, right = relation if op is Op.IFF_FORWARD else relation[::-1]
                if not _same(left, args[1].proposition) or not _same(right, step.output_proposition):
                    unsupported()
                projection = "mp" if op is Op.IFF_FORWARD else "mpr"
                result = term(f"{args[0].argument()}.{projection} {args[1].argument()}", args)
            elif op in (Op.SELECT_LEFT, Op.SELECT_RIGHT):
                relation = _binary(args[0].proposition, "∧") if args else None
                index = 0 if op is Op.SELECT_LEFT else 1
                if len(args) != 1 or relation is None or not _same(relation[index], step.output_proposition):
                    unsupported()
                result = term(f"{args[0].argument()}.{'left' if index == 0 else 'right'}", args, atomic=True)
            elif op is Op.CONTRADICTION:
                if len(args) == 1 and args[0].proposition == "False":
                    false_term = args[0]
                elif len(args) == 2:
                    pair = next(((neg, proof) for neg, proof in (args, args[::-1])
                                 if (relation := _implication(neg.proposition)) is not None
                                 and relation[1] == "False" and _same(relation[0], proof.proposition)), None)
                    if pair is None:
                        unsupported()
                    neg, proof = pair
                    false_term = term(f"{neg.argument()} {proof.argument()}", args)
                else:
                    unsupported()
                result = term(false_term.text if step.output_proposition == "False"
                              else f"False.elim {false_term.argument()}", args)
            elif op in (Op.REWRITE_FORWARD, Op.REWRITE_BACKWARD):
                if len(args) != 1 or not (_binary(args[0].proposition, "=")
                                          or _binary(args[0].proposition, "↔")):
                    unsupported()
                arrow = "← " if op is Op.REWRITE_BACKWARD else ""
                return [candidate(f"rw [{arrow}{args[0].argument()}]", term("", args), plan.expected_next_subgoal)]
            elif op is Op.INTRODUCE:
                relation = _implication(inventory.active_subgoal)
                if args or plan.completion_expected or relation is None or not _same(relation[1], step.output_proposition):
                    unsupported()
                names = {entry.identifier.strip("«»") for entry in inventory.locals if entry.identifier}
                fresh, suffix = "analogy_h", 0
                while fresh in names:
                    suffix += 1
                    fresh = f"analogy_h{suffix}"
                return [candidate(f"intro {fresh}", term("", []), plan.expected_next_subgoal)]
            elif op in (Op.SPLIT, Op.CHOOSE_LEFT, Op.CHOOSE_RIGHT):
                if args or plan.completion_expected:
                    unsupported()
                if op is Op.SPLIT:
                    relation = _binary(inventory.active_subgoal, "∧")
                    if relation is not None:
                        successor = relation[0]
                    else:
                        relation = _binary(inventory.active_subgoal, "↔")
                        predicted = _binary(step.output_proposition, "→")
                        if relation is None or predicted is None or not all(
                            _same(actual, expected) for actual, expected in zip(predicted, relation)
                        ):
                            unsupported()
                        successor = step.output_proposition
                    syntax = "constructor"
                else:
                    relation = _binary(inventory.active_subgoal, "∨")
                    successor = relation[0 if op is Op.CHOOSE_LEFT else 1] if relation else None
                    syntax = "left" if op is Op.CHOOSE_LEFT else "right"
                if successor is None or not _same(successor, step.output_proposition):
                    unsupported()
                return [candidate(syntax, term("", []), plan.expected_next_subgoal)]
            elif op is Op.CASES:
                if len(args) != 1 or plan.completion_expected or _binary(args[0].proposition, "∨") is None or not _same(step.output_proposition, inventory.active_subgoal):
                    unsupported()
                return [candidate(f"cases {args[0].argument()}", term("", args), plan.expected_next_subgoal)]
            else:
                unsupported()
            terms[step.step_id] = result
            if final:
                if not plan.completion_expected:
                    unsupported("a proof term closes a goal; an open successor requires a tactic template")
                candidates = [candidate(f"exact {result.text}", result, plan.expected_next_subgoal)]
                if fallback is not None:
                    candidates.append(candidate(*fallback))
                return candidates
        raise _CannotCompile("unsupported_composition", None, "no final candidate")


__all__ = ["TACTIC_COMPILER_VERSION", "CompiledTactic", "CompilationDiagnostic",
           "CompilationResult", "GroundedTacticCompiler"]
