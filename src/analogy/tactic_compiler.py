"""Compile deterministic grounded applications into bounded Lean 4 syntax."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from .grounding import GroundedApplication, GroundingOperator as Op
from .proof_inventory import (
    InventoryKind, LocalProofInventory, implication_parts, same_proposition,
)


TACTIC_COMPILER_VERSION = "grounded-compiler-v2"


@dataclass(frozen=True, slots=True)
class CompiledTactic:
    text: str
    application_id: str
    inventory_ids: tuple[str, ...]
    predicted_next_subgoal: str


@dataclass(frozen=True, slots=True)
class CompilationDiagnostic:
    code: str
    application_id: str | None
    message: str


@dataclass(frozen=True, slots=True)
class CompilationResult:
    candidates: tuple[CompiledTactic, ...]
    diagnostics: tuple[CompilationDiagnostic, ...]


class _CannotCompile(ValueError):
    def __init__(self, code: str, application_id: str | None, message: str):
        super().__init__(message)
        self.diagnostic = CompilationDiagnostic(code, application_id, message)


def _safe_identifier(name: str | None) -> bool:
    if not isinstance(name, str) or not name or any(unicodedata.category(c).startswith("C") for c in name):
        return False
    if name.startswith("«") and name.endswith("»"):
        return len(name) > 2 and "»" not in name[1:-1] and "«" not in name[1:-1]
    return ((name[0].isalpha() or name[0] == "_")
            and all(c.isalnum() or c in "_'✝" or unicodedata.category(c).startswith("M") for c in name))


def _canonical(text: str) -> str:
    return " ".join(re.findall(r"«[^»]*»|[^\s«]+", text))


class GroundedTacticCompiler:
    def compile_all(
        self, applications: tuple[GroundedApplication, ...], inventory: LocalProofInventory,
    ) -> CompilationResult:
        emitted: list[CompiledTactic] = []
        diagnostics: list[CompilationDiagnostic] = []
        for application in applications:
            try:
                emitted.extend(self._compile(application, inventory))
            except _CannotCompile as error:
                diagnostics.append(error.diagnostic)
            except (TypeError, ValueError, AttributeError) as error:
                diagnostics.append(CompilationDiagnostic(
                    "invalid_application", getattr(application, "application_id", None), str(error),
                ))
        seen: set[str] = set()
        unique: list[CompiledTactic] = []
        for candidate in emitted:
            text = _canonical(candidate.text)
            if not text or len(text) > 4096 or any(char in text for char in "\n\r\x00"):
                diagnostics.append(CompilationDiagnostic(
                    "invalid_tactic", candidate.application_id,
                    "compiled tactic must be one bounded line",
                ))
            elif text not in seen:
                seen.add(text)
                unique.append(replace(candidate, text=text))
        return CompilationResult(tuple(unique), tuple(diagnostics))

    def _compile(
        self, application: GroundedApplication, inventory: LocalProofInventory,
    ) -> tuple[CompiledTactic, ...]:
        if not isinstance(application, GroundedApplication):
            raise _CannotCompile("invalid_application", None, "expected GroundedApplication")
        if application.state_fingerprint != inventory.state_fingerprint:
            raise _CannotCompile("stale_state", application.application_id,
                                 "application does not cite the current state")
        entries = tuple(inventory.resolve(item) for item in application.inventory_ids)
        if any(entry.kind is not InventoryKind.LOCAL for entry in entries):
            raise _CannotCompile("invalid_inventory", application.application_id,
                                 "operators consume only local inventory entries")
        if tuple(item.proposition for item in entries) != application.input_propositions:
            raise _CannotCompile("stale_state", application.application_id,
                                 "application propositions do not match current inventory")
        if any(not _safe_identifier(item.identifier) or (
            "✝" in item.identifier and not item.identifier.startswith("«")
        ) for item in entries):
            raise _CannotCompile("unsupported_identifier", application.application_id,
                                 "application uses an inaccessible local identifier")
        names = tuple(item.identifier for item in entries)
        op = application.operator
        if op is Op.EXACT_LOCAL:
            texts = [f"exact {names[0]}"]
        elif op is Op.INTRODUCE:
            existing = {item.identifier.strip("«»") for item in inventory.locals if item.identifier}
            fresh, suffix = "analogy_h", 0
            while fresh in existing:
                suffix += 1
                fresh = f"analogy_h{suffix}"
            texts = [f"intro {fresh}"]
        elif op is Op.APPLY_IMPLICATION:
            texts = [f"apply {names[0]}"]
            if len(names) == 2:
                texts.append(f"exact {names[0]} {names[1]}")
        elif op in (Op.IFF_FORWARD, Op.IFF_BACKWARD):
            projection = "mp" if op is Op.IFF_FORWARD else "mpr"
            texts = [f"exact {names[0]}.{projection} {names[1]}"]
        elif op in (Op.REWRITE_FORWARD, Op.REWRITE_BACKWARD):
            arrow = "" if op is Op.REWRITE_FORWARD else "← "
            texts = [f"rw [{arrow}{names[0]}]"]
        elif op is Op.SPLIT:
            texts = ["constructor"]
        elif op in (Op.SELECT_LEFT, Op.SELECT_RIGHT):
            projection = "left" if op is Op.SELECT_LEFT else "right"
            texts = [f"exact {names[0]}.{projection}"]
        elif op in (Op.CHOOSE_LEFT, Op.CHOOSE_RIGHT):
            texts = ["left" if op is Op.CHOOSE_LEFT else "right"]
        elif op is Op.CASES:
            texts = [f"cases {names[0]}"]
        elif op is Op.CONTRADICTION:
            if len(entries) == 1:
                false_term = names[0]
            else:
                proof, negation = entries
                relation = implication_parts(negation.proposition)
                if relation is None or not same_proposition(relation[0], proof.proposition):
                    raise _CannotCompile("invalid_application", application.application_id,
                                         "contradiction operands are not complementary")
                false_term = f"{names[1]} {names[0]}"
            texts = [f"exact False.elim ({false_term})"]
        else:
            raise _CannotCompile("unsupported_operator", application.application_id,
                                 "operator has no direct tactic template")
        return tuple(CompiledTactic(
            text, application.application_id, application.inventory_ids,
            application.expected_next_subgoal,
        ) for text in texts)


__all__ = [
    "TACTIC_COMPILER_VERSION", "CompiledTactic", "CompilationDiagnostic",
    "CompilationResult", "GroundedTacticCompiler",
]
