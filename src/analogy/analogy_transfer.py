"""Strict, immutable records for generated source-domain analogy transfers.

These package-owned schemas deliberately do not depend on the standalone
experiment or replace the legacy analogy-stream schemas.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, fields
from enum import Enum
from typing import Callable

from .proof_views import ProofStructure


class TransferOutputError(ValueError):
    """A generated stage violated its schema or transfer boundary."""


class OperationClass(str, Enum):
    INTRODUCE = "introduce"
    DECOMPOSE = "decompose"
    SELECT = "select"
    COMBINE = "combine"
    SELECT_BRANCH = "select_branch"
    REORDER = "reorder"
    SPECIALIZE = "specialize"
    REWRITE = "rewrite"
    REUSE = "reuse"
    REPLACE = "replace"
    REMOVE = "remove"
    RESTRUCTURE = "restructure"
    UNKNOWN = "unknown"


class Arity(str, Enum):
    ZERO = "zero"
    ONE = "one"
    TWO = "two"
    MANY = "many"
    UNKNOWN = "unknown"


class Permutation(str, Enum):
    NONE = "none"
    SWAP = "swap"
    GENERAL = "general"
    UNKNOWN = "unknown"


class InformationFlow(str, Enum):
    PRESERVE = "preserve"
    SELECT = "select"
    ADD = "add"
    DISCARD = "discard"
    RESTRUCTURE = "restructure"


_TACTICS = re.compile(
    r"\b(?:intro|intros|rintro|exact|apply|cases|rcases|obtain|constructor|"
    r"simp|simpa|aesop|assumption|rfl|rw|rewrite|refine|specialize|"
    r"unfold|omega|tauto|sorry|admit)\b",
    re.IGNORECASE,
)
# These words also have ordinary domain meanings; reject their command form
# while retaining prose such as "a routing change" or "derive a contradiction".
_TACTIC_COMMANDS = re.compile(
    r"(?:^|[;\r\n]|\bby\s+)\s*(?:have|show|change|contradiction|trivial|decide)\b"
)
_LEAN_IDENTIFIERS = re.compile(
    r"\b(?:h[A-Z0-9_]\w*|(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*|[A-Za-z_]\w*_\d+)\b"
)
_PROOF_WORDS = re.compile(
    r"\b(?:proof|theorem|lemma|hypothesis|hypotheses|subgoal|proposition|"
    r"logical deduction|formal logic)\b", re.IGNORECASE,
)
_LEAN_SYMBOLS = re.compile(r"[⊢∀∃∧∨¬↔→]|:=|=>")


def _text(value: object, name: str, *,
          guard: Callable[[tuple[str, ...]], None] | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TransferOutputError(f"{name} must be nonblank text")
    if guard is not None:
        # Command boundaries are evidence: validate before collapsing newlines.
        guard((value,))
    return " ".join(value.split())


def _texts(value: object, name: str, *, minimum: int = 0,
           maximum: int | None = None, unique: bool = False,
           guard: Callable[[tuple[str, ...]], None] | None = None) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise TransferOutputError(f"{name} must be a text sequence")
    result = tuple(_text(item, name, guard=guard) for item in value)
    if len(result) < minimum or (maximum is not None and len(result) > maximum):
        raise TransferOutputError(f"{name} has an invalid number of entries")
    if unique and len(set(result)) != len(result):
        raise TransferOutputError(f"{name} must contain unique entries")
    return result


def _set_texts(record: object, *names: str,
               guard: Callable[[tuple[str, ...]], None] | None = None) -> None:
    for name in names:
        object.__setattr__(record, name, _text(getattr(record, name), name, guard=guard))


def _structural_fields(record: object) -> None:
    for name, enum in (("operation", OperationClass), ("input_arity", Arity),
                       ("output_arity", Arity), ("permutation", Permutation)):
        if not isinstance(getattr(record, name), enum):
            raise TransferOutputError(f"{name} must be a {enum.__name__}")
    flow = getattr(record, "information_flow")
    if not isinstance(flow, (tuple, list)) or not flow or any(
        not isinstance(item, InformationFlow) for item in flow
    ):
        raise TransferOutputError("information_flow must contain enum values")
    flow = tuple(flow)
    canonical = tuple(item for item in InformationFlow if item in flow)
    if flow != canonical:
        raise TransferOutputError("information_flow must be unique and use canonical enum order")
    object.__setattr__(record, "information_flow", flow)


def _tactic_free(texts: tuple[str, ...]) -> None:
    if any(_TACTICS.search(text) or _TACTIC_COMMANDS.search(text)
           or "⊢" in text or ":=" in text for text in texts):
        raise TransferOutputError("reasoning and transformation text must be tactic-free")


def _has_proposition(text: str, need: ProofNeed | None) -> bool:
    fragments = () if need is None else (need.fixed_target, need.active_subgoal)
    # Identifier boundaries keep a one-letter proposition from matching
    # the middle of an ordinary source-domain word.
    return any(re.search(r"(?<!\w)" + re.escape(fragment) + r"(?!\w)", text)
               for fragment in fragments)


def _neutral_guard(texts: tuple[str, ...], need: ProofNeed | None = None) -> None:
    _tactic_free(texts)
    if any(_LEAN_IDENTIFIERS.search(text) or _LEAN_SYMBOLS.search(text)
           or _has_proposition(text, need) for text in texts):
        raise TransferOutputError("domain-neutral transformation must not contain proof identifiers or syntax")


def _source_guard(texts: tuple[str, ...], need: ProofNeed | None = None) -> None:
    for text in texts:
        if (_TACTICS.search(text) or _TACTIC_COMMANDS.search(text) or _LEAN_IDENTIFIERS.search(text)
                or _LEAN_SYMBOLS.search(text) or _PROOF_WORDS.search(text)):
            raise TransferOutputError("source domain text must describe a concrete non-proof situation")
        if _has_proposition(text, need):
            raise TransferOutputError("source domain text repeats a proposition fragment")


@dataclass(frozen=True, slots=True)
class TransformationRecord:
    operation: OperationClass
    input_arity: Arity
    output_arity: Arity
    permutation: Permutation
    information_flow: tuple[InformationFlow, ...]
    focus_before: str
    focus_after: str
    domain_neutral_summary: str

    def __post_init__(self) -> None:
        _structural_fields(self)
        _set_texts(self, "focus_before", "focus_after", "domain_neutral_summary", guard=_neutral_guard)
        _neutral_guard((self.focus_before, self.focus_after, self.domain_neutral_summary))


@dataclass(frozen=True, slots=True)
class ProofNeed:
    fixed_target: str
    active_subgoal: str
    proof_roles: tuple[str, ...]
    operation: OperationClass
    input_arity: Arity
    output_arity: Arity
    permutation: Permutation
    information_flow: tuple[InformationFlow, ...]
    focus_before: str
    desired_focus_after: str
    summary: str

    def __post_init__(self) -> None:
        _structural_fields(self)
        _set_texts(self, "fixed_target", "active_subgoal")
        _set_texts(self, "focus_before", "desired_focus_after", "summary", guard=_tactic_free)
        roles = _texts(self.proof_roles, "proof_roles", minimum=1, unique=True)
        if "active_subgoal" not in roles:
            raise TransferOutputError("proof_roles must declare active_subgoal")
        object.__setattr__(self, "proof_roles", roles)
        _tactic_free((self.focus_before, self.desired_focus_after, self.summary))


@dataclass(frozen=True, slots=True)
class SourceAnalogy:
    analogy_id: str
    domain: str
    source_before: str
    source_after: str
    source_roles: tuple[str, ...]
    relation_chain: tuple[str, ...]
    transformation: TransformationRecord
    rationale: str

    def __post_init__(self) -> None:
        _set_texts(self, "analogy_id")
        _set_texts(self, "domain", "source_before", "source_after", "rationale", guard=_source_guard)
        object.__setattr__(self, "source_roles", _texts(
            self.source_roles, "source_roles", minimum=2, unique=True, guard=_source_guard))
        object.__setattr__(self, "relation_chain", _texts(
            self.relation_chain, "relation_chain", minimum=2, maximum=4, guard=_source_guard))
        if not isinstance(self.transformation, TransformationRecord):
            raise TransferOutputError("transformation must be a TransformationRecord")
        _source_guard(_source_texts(self))


def _source_texts(source: SourceAnalogy) -> tuple[str, ...]:
    return (source.domain, source.source_before, source.source_after,
            *source.source_roles, *source.relation_chain, source.rationale)


@dataclass(frozen=True, slots=True)
class AnalogyAlignment:
    alignment_id: str
    source_analogy_id: str
    mapping: tuple[tuple[str, str], ...]
    invariant: str
    source_operation: str
    proof_operation: str
    limitation: str
    confidence: float

    def __post_init__(self) -> None:
        _set_texts(self, "alignment_id", "source_analogy_id", "invariant", "source_operation", "proof_operation", "limitation")
        if not isinstance(self.mapping, (tuple, list)) or not 2 <= len(self.mapping) <= 6:
            raise TransferOutputError("mapping must contain two to six role pairs")
        pairs = tuple(_texts(pair, "mapping pair", minimum=2, maximum=2) for pair in self.mapping)
        if len({left for left, _ in pairs}) != len(pairs):
            raise TransferOutputError("mapping source roles must be unique")
        object.__setattr__(self, "mapping", pairs)
        confidence = self.confidence
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not 0 <= confidence <= 1 or not math.isfinite(confidence)):
            raise TransferOutputError("confidence must be finite and in [0, 1]")
        object.__setattr__(self, "confidence", float(confidence))


@dataclass(frozen=True, slots=True)
class ProofReasoningTransfer:
    transfer_id: str
    alignment_id: str
    active_subgoal: str
    premises: tuple[str, ...]
    reasoning_steps: tuple[str, ...]
    predicted_next_subgoal: str
    completion_expected: bool

    def __post_init__(self) -> None:
        _set_texts(self, "transfer_id", "alignment_id", "active_subgoal")
        _set_texts(self, "predicted_next_subgoal", guard=_tactic_free)
        object.__setattr__(self, "premises", _texts(
            self.premises, "premises", unique=True, guard=_tactic_free))
        object.__setattr__(self, "reasoning_steps", _texts(
            self.reasoning_steps, "reasoning_steps", minimum=1, guard=_tactic_free))
        if not isinstance(self.completion_expected, bool):
            raise TransferOutputError("completion_expected must be a boolean")
        _tactic_free((*self.premises, *self.reasoning_steps, self.predicted_next_subgoal))


@dataclass(frozen=True, slots=True)
class AnalogyObservation:
    """Readable trajectory snapshot available before one policy action."""

    fixed_target: str
    current_state: str
    previous_state: str | None
    last_tactic: str | None
    accepted_tactics: tuple[str, ...]
    rejected_tactics: tuple[tuple[str, tuple[str, ...]], ...]
    latest_source: SourceAnalogy | None
    latest_alignment: AnalogyAlignment | None
    latest_transfer: ProofReasoningTransfer | None
    predicted_transition: str | None
    actual_transition: str | None
    surprise: float | None
    surprise_delta: float | None
    repeated_state: bool
    iteration_index: int
    remaining_model_calls: int
    remaining_lean_calls: int


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransferOutputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise TransferOutputError(f"invalid JSON constant: {value}")


def _parse_object(raw: str, record_type: type, excluded: tuple[str, ...] = ()) -> dict:
    try:
        value = json.loads(raw, object_pairs_hook=_object_pairs, parse_constant=_invalid_constant)
    except (TypeError, ValueError) as error:
        raise TransferOutputError(f"{record_type.__name__} must be one strict JSON object: {error}") from error
    return _exact_object(value, record_type, excluded)


def _exact_object(value: object, record_type: type, excluded: tuple[str, ...] = ()) -> dict:
    expected = {field.name for field in fields(record_type)} - set(excluded)
    if not isinstance(value, dict) or set(value) != expected:
        raise TransferOutputError(f"{record_type.__name__} has missing or unknown keys")
    return value


def _array(value: object, name: str) -> list:
    if not isinstance(value, list):
        raise TransferOutputError(f"{name} must be a JSON array")
    return value


def _transformation(value: object) -> TransformationRecord:
    payload = dict(_exact_object(value, TransformationRecord))
    try:
        for name, enum in (("operation", OperationClass), ("input_arity", Arity),
                           ("output_arity", Arity), ("permutation", Permutation)):
            payload[name] = enum(payload[name])
        payload["information_flow"] = tuple(InformationFlow(item) for item in
            _array(payload["information_flow"], "information_flow"))
        return TransformationRecord(**payload)
    except (TypeError, ValueError) as error:
        raise TransferOutputError(f"invalid transformation record: {error}") from error


def parse_transformation_record(raw: str) -> TransformationRecord:
    return _transformation(_parse_object(raw, TransformationRecord))


def parse_source_analogy(raw: str, *, analogy_id: str,
                         need: ProofNeed | None = None) -> SourceAnalogy:
    """Parse a source situation; supplied proof expressions add leakage guards."""
    payload = dict(_parse_object(raw, SourceAnalogy, ("analogy_id",)))
    for name in ("source_roles", "relation_chain"):
        payload[name] = tuple(_array(payload[name], name))
    payload["transformation"] = _transformation(payload["transformation"])
    result = SourceAnalogy(analogy_id=analogy_id, **payload)
    _source_guard(_source_texts(result), need)
    transformation = result.transformation
    _neutral_guard((transformation.focus_before, transformation.focus_after,
                    transformation.domain_neutral_summary), need)
    return result


def _validate_proof_mapping(alignment: AnalogyAlignment, need: ProofNeed) -> None:
    roles = {right for _, right in alignment.mapping}
    if not roles <= set(need.proof_roles):
        raise TransferOutputError("mapping must reference declared roles on both sides")
    if "active_subgoal" not in roles or not roles - {"active_subgoal"}:
        raise TransferOutputError("mapping must cite active_subgoal and a relevant hypothesis or constructor")


def parse_alignment(raw: str, *, source: SourceAnalogy, need: ProofNeed,
                    alignment_id: str) -> AnalogyAlignment:
    payload = dict(_parse_object(raw, AnalogyAlignment, ("alignment_id", "source_analogy_id")))
    mapping = payload["mapping"]
    if not isinstance(mapping, dict):
        raise TransferOutputError("mapping must be a JSON object of declared roles")
    payload["mapping"] = tuple(mapping.items())
    result = AnalogyAlignment(alignment_id=alignment_id, source_analogy_id=source.analogy_id, **payload)
    if any(left not in source.source_roles for left, _ in result.mapping):
        raise TransferOutputError("mapping must reference declared roles on both sides")
    _validate_proof_mapping(result, need)
    return result


def parse_proof_reasoning_transfer(raw: str, *, need: ProofNeed,
                                  alignment: AnalogyAlignment,
                                  transfer_id: str) -> ProofReasoningTransfer:
    payload = dict(_parse_object(raw, ProofReasoningTransfer, ("transfer_id", "alignment_id")))
    for name in ("premises", "reasoning_steps"):
        payload[name] = tuple(_array(payload[name], name))
    result = ProofReasoningTransfer(transfer_id=transfer_id, alignment_id=alignment.alignment_id, **payload)
    if result.active_subgoal != need.active_subgoal:
        raise TransferOutputError("transfer must target the current active subgoal")
    _validate_proof_mapping(alignment, need)
    return result


def _shape(raw: str) -> dict:
    """Read only documented, name-free shape structure, including leaf shorthand."""
    roots = {"target", "atom", "opaque", "application", "implication", "forall",
             "and", "or", "iff", "equality", "not", "exists", "false"}

    def validate(value: object, depth: int = 0) -> dict:
        if (depth > 32 or not isinstance(value, dict) or set(value) - {"root", "children"}
                or not isinstance(value.get("root"), str) or value["root"] not in roots
                or not isinstance(value.get("children", []), list)):
            raise TransferOutputError("invalid name-free proof shape")
        return {"root": value["root"], "children": [validate(child, depth + 1)
                for child in value.get("children", [])]}

    return validate(json.loads(raw))


def _contains_target(shape: dict) -> bool:
    return shape["root"] == "target" or any(_contains_target(child) for child in shape["children"])


def derive_proof_need(structure: ProofStructure, active_subgoal: str, *,
                      fixed_target: str | None = None) -> ProofNeed:
    """Infer one conservative operation from Lean's name-free shapes.

    The required expression is the active subgoal. Without an explicit
    ``fixed_target`` the theorem target defaults to that same expression.
    A matching hypothesis takes precedence over every structural operation.
    """
    target = _shape(structure.target_shape)
    hypotheses = tuple(_shape(raw) for raw in structure.hypothesis_shapes)
    root = target["root"]
    operation = OperationClass.UNKNOWN
    role = None
    inputs = outputs = Arity.ONE
    permutation = Permutation.NONE
    flow = (InformationFlow.PRESERVE,)
    before, after = "unresolved requirement", "resolved requirement"
    summary = "The structural evidence does not identify a supported transformation."

    if any(hyp["root"] == "target" for hyp in hypotheses):
        operation, role = OperationClass.REUSE, "matching_hypothesis"
        before, after = "required item and matching available item", "requirement fulfilled"
        summary = "Use an available item identical to the required item."
    elif root in {"implication", "forall"}:
        operation, role = OperationClass.INTRODUCE, "introduced_assumption"
        flow = (InformationFlow.ADD,)
        before, after = "conditional or quantified requirement", "local item and remaining requirement"
        summary = "Expose a local input and continue with the remaining requirement."
    elif root == "equality" or any(hyp["root"] == "equality" for hyp in hypotheses):
        operation = OperationClass.REWRITE
        role = "equality_hypothesis" if any(hyp["root"] == "equality" for hyp in hypotheses) else "equality_constructor"
        flow = (InformationFlow.RESTRUCTURE,)
        before, after = "equivalent representations", "one shared representation"
        summary = "Replace an equivalent representation to meet the requirement."
    elif root == "and" and len(target["children"]) == 2 and any(
        hyp["root"] == "and" and hyp["children"] == list(reversed(target["children"]))
        and hyp["children"] != target["children"] for hyp in hypotheses
    ):
        operation, role = OperationClass.REORDER, "structured_hypothesis"
        inputs = outputs = Arity.TWO
        permutation = Permutation.SWAP
        flow = (InformationFlow.RESTRUCTURE,)
        before, after = "components in reversed positions", "components in required positions"
        summary = "Swap the positions of the available components."
    elif root == "and":
        operation, role = OperationClass.COMBINE, "conjunction_constructor"
        inputs = Arity.TWO
        flow = (InformationFlow.ADD, InformationFlow.RESTRUCTURE)
        before, after = "separate required components", "one combined item"
        summary = "Combine the required components into one structured item."
    elif any(hyp["root"] in {"and", "or", "iff", "exists"} and _contains_target(hyp)
             for hyp in hypotheses):
        selected = any(hyp["root"] == "and" and _contains_target(hyp) for hyp in hypotheses)
        operation = OperationClass.SELECT if selected else OperationClass.DECOMPOSE
        role = "structured_hypothesis"
        if selected:
            inputs = Arity.TWO
            flow = (InformationFlow.SELECT, InformationFlow.DISCARD)
        else:
            flow = (InformationFlow.RESTRUCTURE,)
        before, after = "available structured item", "required contained component"
        summary = "Extract the required component from an available structured item."
    elif root == "or":
        operation, role = OperationClass.SELECT_BRANCH, "disjunction_constructor"
        flow = (InformationFlow.SELECT,)
        before, after = "alternative requirements", "one chosen requirement"
        summary = "Choose one available branch of the requirement."
    elif any(hyp["root"] in {"implication", "forall"} and len(hyp["children"]) == 2
             and hyp["children"][1]["root"] == "target" for hyp in hypotheses):
        operation, role = OperationClass.SPECIALIZE, "conditional_hypothesis"
        flow = (InformationFlow.PRESERVE, InformationFlow.SELECT)
        before, after = "conditional resource", "resource for the required outcome"
        summary = "Supply the input needed by an available conditional resource."

    return ProofNeed(
        fixed_target=active_subgoal if fixed_target is None else fixed_target,
        active_subgoal=active_subgoal,
        proof_roles=("active_subgoal", role) if role else ("active_subgoal",),
        operation=operation, input_arity=inputs, output_arity=outputs,
        permutation=permutation, information_flow=flow, focus_before=before,
        desired_focus_after=after, summary=summary,
    )


__all__ = [
    "OperationClass", "Arity", "Permutation", "InformationFlow", "TransferOutputError",
    "TransformationRecord", "ProofNeed", "SourceAnalogy", "AnalogyAlignment",
    "ProofReasoningTransfer", "AnalogyObservation", "derive_proof_need", "parse_transformation_record",
    "parse_source_analogy", "parse_alignment", "parse_proof_reasoning_transfer",
]
