#!/usr/bin/env python3
"""Standalone poetry/physics retrieval experiment for Lean tactic recovery.

This file intentionally does not import the ``analogy`` package.  It owns
its data, model, retrieval, verification, and reporting boundaries so the
experiment can be inspected and run independently of the numbered plans.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import heapq
import inspect
import json
import math
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol


class ExperimentError(RuntimeError):
    """Raised when experiment inputs or runtime behavior are invalid."""


class ModelOutputError(ExperimentError):
    """Raised when generated or embedded model output violates its schema."""


class ModelConfigurationError(ExperimentError):
    """Raised when the configured model cannot satisfy the experiment."""


class ExperimentRunIncomplete(ExperimentError):
    """Raised after preserving artifacts for a run with operational failures."""


def _nonblank(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        qualifier = "string" if allow_empty else "nonblank string"
        raise ExperimentError(f"{field} must be a {qualifier}")
    return value


@dataclass(frozen=True, slots=True)
class Problem:
    id: str
    binders: str
    target: str
    title: str = ""
    difficulty: int = 1

    def __post_init__(self) -> None:
        _nonblank(self.id, "problem.id")
        _nonblank(self.binders, "problem.binders", allow_empty=True)
        _nonblank(self.target, "problem.target")
        if isinstance(self.difficulty, bool) or not isinstance(self.difficulty, int) or self.difficulty <= 0:
            raise ExperimentError("problem.difficulty must be a positive integer")

    def theorem_header(self, name: str = "current_problem") -> str:
        binder_segment = f" {self.binders}" if self.binders else ""
        return f"theorem {name}{binder_segment} : {self.target} := by"


@dataclass(frozen=True, slots=True)
class ReferenceProof:
    problem_id: str
    tactics: tuple[str, ...]

    def __post_init__(self) -> None:
        _nonblank(self.problem_id, "reference_proof.problem_id")
        normalized = tuple(self.tactics)
        if not normalized or any(not isinstance(item, str) or not item.strip() for item in normalized):
            raise ExperimentError("reference_proof.tactics must be nonempty strings")
        object.__setattr__(self, "tactics", normalized)


@dataclass(frozen=True, slots=True)
class Transition:
    id: str
    domain: str
    before: str
    after: str
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        _nonblank(self.id, "transition.id")
        if self.domain not in {"poetry", "physics"}:
            raise ExperimentError("transition.domain must be poetry or physics")
        _nonblank(self.before, "transition.before")
        _nonblank(self.after, "transition.after")
        if not isinstance(self.provenance, Mapping):
            raise ExperimentError("transition.provenance must be a mapping")
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


class Condition(str, Enum):
    NONE = "none"
    RANDOM = "random"
    ANALOGY = "analogy"


# Proof structure


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


def _require_canonical_enum_tuple(
    values: tuple[Enum, ...], enum_type: type[Enum], field_name: str, *, nonempty: bool = False
) -> None:
    if nonempty and not values:
        raise ExperimentError(f"{field_name} must be nonempty")
    if any(not isinstance(item, enum_type) for item in values):
        raise ExperimentError(f"{field_name} contains an invalid value")
    if len(set(values)) != len(values):
        raise ExperimentError(f"{field_name} must contain unique values")
    enum_order = {item: index for index, item in enumerate(enum_type)}
    if tuple(sorted(values, key=enum_order.__getitem__)) != values:
        raise ExperimentError(f"{field_name} must use canonical enum order")


@dataclass(frozen=True, slots=True)
class ExpressionShape:
    root: str
    children: tuple["ExpressionShape", ...] = ()

    def __post_init__(self) -> None:
        _nonblank(self.root, "expression_shape.root")
        children = tuple(self.children)
        if any(not isinstance(child, ExpressionShape) for child in children):
            raise ExperimentError("expression_shape.children must contain ExpressionShape values")
        object.__setattr__(self, "children", children)


@dataclass(frozen=True, slots=True)
class ProofSignature:
    target_root: str
    target_shape: ExpressionShape
    hypothesis_roots: tuple[str, ...]
    hypothesis_shapes: tuple[ExpressionShape, ...]
    target_occurrence_paths: tuple[str, ...]
    candidate_operations: tuple[OperationClass, ...]
    input_arity: Arity
    output_arity: Arity
    permutation: Permutation
    information_flow: tuple[InformationFlow, ...]
    failed_operation_classes: tuple[OperationClass, ...]
    goal_count: int
    version: str = "proof-signature-v1"

    def __post_init__(self) -> None:
        _nonblank(self.target_root, "proof_signature.target_root")
        _nonblank(self.version, "proof_signature.version")
        if not isinstance(self.target_shape, ExpressionShape):
            raise ExperimentError("proof_signature.target_shape must be an ExpressionShape")
        hypothesis_roots = tuple(self.hypothesis_roots)
        hypothesis_shapes = tuple(self.hypothesis_shapes)
        occurrence_paths = tuple(self.target_occurrence_paths)
        candidate_operations = tuple(self.candidate_operations)
        information_flow = tuple(self.information_flow)
        failed_operations = tuple(self.failed_operation_classes)
        if len(hypothesis_roots) != len(hypothesis_shapes):
            raise ExperimentError("proof signature hypothesis roots and shapes must align")
        if hypothesis_roots != tuple(sorted(hypothesis_roots)):
            raise ExperimentError("proof signature hypothesis roots must be sorted")
        if occurrence_paths != tuple(sorted(set(occurrence_paths))):
            raise ExperimentError("proof signature occurrence paths must be sorted and unique")
        _require_canonical_enum_tuple(
            candidate_operations, OperationClass, "proof_signature.candidate_operations", nonempty=True
        )
        _require_canonical_enum_tuple(
            information_flow, InformationFlow, "proof_signature.information_flow", nonempty=True
        )
        _require_canonical_enum_tuple(
            failed_operations, OperationClass, "proof_signature.failed_operation_classes"
        )
        if isinstance(self.goal_count, bool) or not isinstance(self.goal_count, int) or self.goal_count < 0:
            raise ExperimentError("proof_signature.goal_count must be a nonnegative integer")
        object.__setattr__(self, "hypothesis_roots", hypothesis_roots)
        object.__setattr__(self, "hypothesis_shapes", hypothesis_shapes)
        object.__setattr__(self, "target_occurrence_paths", occurrence_paths)
        object.__setattr__(self, "candidate_operations", candidate_operations)
        object.__setattr__(self, "information_flow", information_flow)
        object.__setattr__(self, "failed_operation_classes", failed_operations)


# Transformation schema


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
        if not isinstance(self.operation, OperationClass):
            raise ExperimentError("transformation.operation is invalid")
        if not isinstance(self.input_arity, Arity) or not isinstance(self.output_arity, Arity):
            raise ExperimentError("transformation arity is invalid")
        if not isinstance(self.permutation, Permutation):
            raise ExperimentError("transformation.permutation is invalid")
        information_flow = tuple(self.information_flow)
        _require_canonical_enum_tuple(
            information_flow, InformationFlow, "transformation.information_flow", nonempty=True
        )
        focus_before = " ".join(_nonblank(self.focus_before, "transformation.focus_before").split())
        focus_after = " ".join(_nonblank(self.focus_after, "transformation.focus_after").split())
        summary = " ".join(
            _nonblank(self.domain_neutral_summary, "transformation.domain_neutral_summary").split()
        )
        if len(summary) > 240:
            raise ExperimentError("transformation.domain_neutral_summary exceeds 240 characters")
        if _TACTIC_WORDS.search(summary) or _TACTIC_WORDS.search(focus_before) or _TACTIC_WORDS.search(focus_after):
            raise ExperimentError("transformation text must not contain Lean tactic names")
        object.__setattr__(self, "information_flow", information_flow)
        object.__setattr__(self, "focus_before", focus_before)
        object.__setattr__(self, "focus_after", focus_after)
        object.__setattr__(self, "domain_neutral_summary", summary)


_EXPRESSION_ROOTS = frozenset(
    {
        "target",
        "implication",
        "forall",
        "and",
        "or",
        "iff",
        "equality",
        "not",
        "exists",
        "false",
        "application",
        "atom",
        "opaque",
    }
)


def _parse_expression_shape(value: Any, *, depth: int = 0) -> ExpressionShape:
    if depth > 8 or not isinstance(value, Mapping) or set(value) != {"root", "children"}:
        raise ExperimentError("expression shape must use exactly root and children")
    root = value["root"]
    children = value["children"]
    if not isinstance(root, str) or root not in _EXPRESSION_ROOTS:
        raise ExperimentError("expression shape root is invalid")
    if not isinstance(children, list):
        raise ExperimentError("expression shape children must be an array")
    return ExpressionShape(
        root,
        tuple(_parse_expression_shape(child, depth=depth + 1) for child in children),
    )


def _shape_payload(shape: ExpressionShape) -> dict[str, Any]:
    return {
        "root": shape.root,
        "children": [_shape_payload(child) for child in shape.children],
    }


def _shape_key(shape: ExpressionShape) -> str:
    return json.dumps(_shape_payload(shape), sort_keys=True, separators=(",", ":"))


def _target_paths(shape: ExpressionShape, prefix: str) -> tuple[str, ...]:
    paths: list[str] = []
    if shape.root == "target":
        paths.append(prefix)
    labels = ("first", "second")
    for index, child in enumerate(shape.children):
        label = labels[index] if index < len(labels) else f"child_{index + 1}"
        paths.extend(_target_paths(child, f"{prefix}.{label}"))
    return tuple(paths)


def _enum_tuple(enum_type: type[Enum], values: set[Enum]) -> tuple[Any, ...]:
    return tuple(item for item in enum_type if item in values)


def parse_proof_signature(payload: Mapping[str, Any]) -> ProofSignature:
    """Validate Lean's name-free shape payload and derive retrieval features."""
    if not isinstance(payload, Mapping) or set(payload) != {"goal_count", "target", "hypotheses"}:
        raise ModelOutputError("proof structure must contain exactly goal_count, target, and hypotheses")
    goal_count = payload["goal_count"]
    hypotheses_raw = payload["hypotheses"]
    if isinstance(goal_count, bool) or not isinstance(goal_count, int) or goal_count <= 0:
        raise ModelOutputError("proof structure goal_count must be a positive integer")
    if not isinstance(hypotheses_raw, list):
        raise ModelOutputError("proof structure hypotheses must be an array")
    try:
        target = _parse_expression_shape(payload["target"])
        hypotheses = tuple(sorted(
            (_parse_expression_shape(item) for item in hypotheses_raw),
            key=lambda shape: (shape.root, _shape_key(shape)),
        ))
    except (ExperimentError, TypeError, ValueError) as error:
        raise ModelOutputError(f"invalid proof structure: {error}") from error

    operations: set[OperationClass] = set()
    flow: set[InformationFlow] = set()
    input_arity = Arity.ONE
    output_arity = Arity.ONE
    permutation = Permutation.NONE

    if target.root in {"implication", "forall"}:
        operations.add(OperationClass.INTRODUCE)
        flow.add(InformationFlow.ADD)
    if target.root == "and":
        operations.add(OperationClass.COMBINE)
        input_arity = Arity.TWO
        flow.update({InformationFlow.ADD, InformationFlow.RESTRUCTURE})
    if target.root == "or":
        operations.add(OperationClass.SELECT_BRANCH)
        flow.add(InformationFlow.SELECT)

    occurrence_paths: list[str] = []
    for index, hypothesis in enumerate(hypotheses):
        occurrence_paths.extend(_target_paths(hypothesis, f"hypothesis_{index + 1}"))
        if hypothesis.root == "target":
            operations.add(OperationClass.REUSE)
            flow.add(InformationFlow.PRESERVE)
        if hypothesis.root in {"and", "or", "iff", "exists"} and _target_paths(
            hypothesis, f"hypothesis_{index + 1}"
        ):
            operations.add(OperationClass.DECOMPOSE)
            if hypothesis.root == "and":
                operations.add(OperationClass.SELECT)
                input_arity = Arity.TWO
                output_arity = Arity.ONE
                flow.update({InformationFlow.SELECT, InformationFlow.DISCARD})
            else:
                flow.add(InformationFlow.RESTRUCTURE)
        if hypothesis.root == "equality":
            operations.add(OperationClass.REWRITE)
            flow.add(InformationFlow.RESTRUCTURE)
        if (
            target.root == "and"
            and hypothesis.root == "and"
            and len(target.children) == len(hypothesis.children) == 2
            and hypothesis.children == tuple(reversed(target.children))
            and hypothesis.children != target.children
        ):
            operations.add(OperationClass.REORDER)
            permutation = Permutation.SWAP
            flow.add(InformationFlow.RESTRUCTURE)
        if (
            hypothesis.root in {"implication", "forall"}
            and len(hypothesis.children) == 2
            and hypothesis.children[1].root == "target"
        ):
            operations.add(OperationClass.SPECIALIZE)
            flow.update({InformationFlow.SELECT, InformationFlow.PRESERVE})

    if not operations:
        operations.add(OperationClass.UNKNOWN)
    if not flow:
        flow.add(InformationFlow.PRESERVE)

    return ProofSignature(
        target_root=target.root,
        target_shape=target,
        hypothesis_roots=tuple(shape.root for shape in hypotheses),
        hypothesis_shapes=hypotheses,
        target_occurrence_paths=tuple(sorted(set(occurrence_paths))),
        candidate_operations=_enum_tuple(OperationClass, operations),
        input_arity=input_arity,
        output_arity=output_arity,
        permutation=permutation,
        information_flow=_enum_tuple(InformationFlow, flow),
        failed_operation_classes=(),
        goal_count=goal_count,
    )


_ROOT_DESCRIPTIONS = {
    "atom": "atomic",
    "application": "an application",
    "implication": "an implication",
    "forall": "universally quantified",
    "and": "a conjunction",
    "or": "a disjunction",
    "iff": "a biconditional",
    "equality": "an equality",
    "not": "a negation",
    "exists": "existential",
    "false": "falsehood",
    "opaque": "structurally opaque",
    "target": "identical to the target",
}


def render_proof_signature(signature: ProofSignature) -> str:
    if not isinstance(signature, ProofSignature):
        raise ExperimentError("render_proof_signature requires a ProofSignature")
    clauses = [f"The target is {_ROOT_DESCRIPTIONS.get(signature.target_root, signature.target_root)}."]
    if len(signature.hypothesis_shapes) == 1:
        hypothesis = signature.hypothesis_shapes[0]
        paths = signature.target_occurrence_paths
        if hypothesis.root == "and" and paths == ("hypothesis_1.second",):
            clauses.append("A conjunction hypothesis contains the target as its second component.")
    operation_words = [item.value.replace("_", " ") for item in signature.candidate_operations]
    if operation_words == ["decompose", "select"] and signature.information_flow == (
        InformationFlow.SELECT,
        InformationFlow.DISCARD,
    ):
        clauses.append(
            "Plausible operations decompose or select a component, selecting information "
            "and discarding the remainder."
        )
    else:
        joined = ", ".join(operation_words[:-1]) + (
            f" or {operation_words[-1]}" if len(operation_words) > 1 else operation_words[0]
        )
        clauses.append(f"Plausible operations: {joined}.")
    if signature.goal_count > 1:
        clauses.append(f"There are {signature.goal_count} open goals.")
    return " ".join(clauses)


def proof_signature_fingerprint(signature: ProofSignature) -> str:
    payload = json.dumps(
        _jsonable(signature), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256_text(payload)


def classify_failed_tactic(tactic: str) -> OperationClass | None:
    normalized = " ".join(tactic.strip().split()).casefold()
    rules = (
        (r"^(intro|rintro)\b", OperationClass.INTRODUCE),
        (r"^(cases|rcases|obtain)\b", OperationClass.DECOMPOSE),
        (r"^(exact|assumption)\b.*\.(left|right|1|2)\b", OperationClass.SELECT),
        (r"^(constructor|refine\s+⟨)", OperationClass.COMBINE),
        (r"^(left|right)\b", OperationClass.SELECT_BRANCH),
        (r"^(rw|rewrite|simp)\b", OperationClass.REWRITE),
        (r"^(exact|assumption)\b", OperationClass.REUSE),
        (r"^(apply|specialize|have)\b", OperationClass.SPECIALIZE),
    )
    for pattern, operation in rules:
        if re.search(pattern, normalized):
            return operation
    return None


@dataclass(frozen=True, slots=True)
class Message:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise ExperimentError("message.role is invalid")
        _nonblank(self.content, "message.content")


@dataclass(frozen=True, slots=True)
class Embedding:
    values: tuple[float, ...]
    layer: int
    token_index: int
    model_revision: str
    text_hash: str

    def __post_init__(self) -> None:
        values = tuple(float(item) for item in self.values)
        if not values or not all(math.isfinite(item) for item in values):
            raise ModelOutputError("embedding values must be nonempty and finite")
        norm = math.sqrt(sum(item * item for item in values))
        if norm == 0.0:
            raise ModelOutputError("embedding vector must not be zero")
        object.__setattr__(self, "values", values)
        if self.token_index < 0:
            raise ModelOutputError("embedding token index must be nonnegative")
        _nonblank(self.model_revision, "embedding.model_revision")
        _nonblank(self.text_hash, "embedding.text_hash")

    @property
    def dimension(self) -> int:
        return len(self.values)


@dataclass(frozen=True, slots=True)
class CompletionScore:
    text: str
    sum_log_likelihood: float
    mean_log_likelihood: float
    token_count: int

    def __post_init__(self) -> None:
        _nonblank(self.text, "completion_score.text")
        if not math.isfinite(self.sum_log_likelihood) or not math.isfinite(
            self.mean_log_likelihood
        ):
            raise ModelOutputError("completion likelihoods must be finite")
        if isinstance(self.token_count, bool) or not isinstance(self.token_count, int) or self.token_count <= 0:
            raise ModelOutputError("completion token count must be positive")


@dataclass(frozen=True, slots=True)
class PairScore:
    transition_id: str
    relevant: CompletionScore
    irrelevant: CompletionScore
    margin: float

    def __post_init__(self) -> None:
        _nonblank(self.transition_id, "pair_score.transition_id")
        if not isinstance(self.relevant, CompletionScore) or not isinstance(
            self.irrelevant, CompletionScore
        ):
            raise ExperimentError("pair score requires completion scores")
        if not math.isfinite(self.margin):
            raise ModelOutputError("pair score margin must be finite")


class TextModel(Protocol):
    model_revision: str

    def generate(self, messages: tuple[Message, ...], *, seed: int) -> str: ...

    def embed(self, text: str, layer: int) -> Embedding: ...

    def score_completions(
        self,
        messages: tuple[Message, ...],
        completions: tuple[str, ...],
        *,
        max_batch_tokens: int | None = None,
    ) -> tuple[CompletionScore, ...]: ...


CANDIDATE_PROMPT_VERSION = "candidate-v1"
PROOF_NEED_PROMPT_VERSION = "proof-need-v1"
TRANSFORMATION_PROMPT_VERSION = "transformation-v1"
RANKING_PROMPT_VERSION = "ranking-v1"
PAIR_SCORING_PROMPT_VERSION = "pair-scoring-v1"
PROMPT_VERSIONS = MappingProxyType(
    {
        "candidate": CANDIDATE_PROMPT_VERSION,
        "proof_need": PROOF_NEED_PROMPT_VERSION,
        "transformation": TRANSFORMATION_PROMPT_VERSION,
        "ranking": RANKING_PROMPT_VERSION,
        "pair_scoring": PAIR_SCORING_PROMPT_VERSION,
    }
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _context_block(values: tuple[str, ...]) -> str:
    return "\n".join(values) if values else "(none)"


def candidate_prompt(
    problem: Problem,
    state: str,
    accepted: tuple[str, ...],
    failed: tuple[str, ...],
    top_k: int,
) -> tuple[Message, ...]:
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ExperimentError("top_k must be a positive integer")
    payload = masked_problem_payload(problem)
    return (
        Message(
            "system",
            f"Prompt {CANDIDATE_PROMPT_VERSION}. Propose ranked next Lean 4 tactics. "
            "Return only a nonempty JSON array of unique, complete, single-line "
            "tactic strings. Use only visible identifiers and do not repeat failures.",
        ),
        Message(
            "user",
            f"Binders: {payload['binders'] or '(none)'}\n"
            f"Overall target: {payload['target']}\n"
            f"Current goals:\n{state}\n"
            f"Accepted prefix:\n{_context_block(accepted)}\n"
            f"Failed tactics:\n{_context_block(failed)}\n"
            f"Return at most {top_k} tactics.",
        ),
    )


def proof_need_prompt(
    problem: Problem,
    state: str,
    accepted: tuple[str, ...],
    failed: tuple[str, ...],
) -> tuple[Message, ...]:
    payload = masked_problem_payload(problem)
    return (
        Message(
            "system",
            f"Prompt {PROOF_NEED_PROMPT_VERSION}. Describe the abstract structural "
            "change needed next. Do not name Lean tactics or copy identifiers. "
            "Return one concise sentence only.",
        ),
        Message(
            "user",
            f"Overall target: {payload['target']}\nCurrent goals:\n{state}\n"
            f"Accepted progress:\n{_context_block(accepted)}\n"
            f"Failed attempts:\n{_context_block(failed)}",
        ),
    )


def render_transformation_prompt(transition: Transition) -> tuple[Message, ...]:
    operation_values = ", ".join(item.value for item in OperationClass)
    arity_values = ", ".join(item.value for item in Arity)
    permutation_values = ", ".join(item.value for item in Permutation)
    flow_values = ", ".join(item.value for item in InformationFlow)
    return (
        Message(
            "system",
            f"Prompt {TRANSFORMATION_PROMPT_VERSION}. Extract the domain-neutral "
            "structural change from before to after. Return exactly one JSON object "
            "with exactly these keys: \"operation\", \"input_arity\", \"output_arity\", "
            "\"permutation\", \"information_flow\", \"focus_before\", \"focus_after\", "
            "and \"domain_neutral_summary\". "
            f"operation must be one of: {operation_values}. "
            f"input_arity and output_arity must be one of: {arity_values}. "
            f"permutation must be one of: {permutation_values}. "
            f"information_flow must be a nonempty unique list in canonical order from: {flow_values}. "
            "Focus values must be short noun-role phrases. The summary must be one concise "
            "sentence of at most 240 characters. Do not use Lean syntax, Lean tactic names, "
            "or source-specific proper nouns.",
        ),
        Message(
            "user",
            f"Domain: {transition.domain}\nBefore: {transition.before}\nAfter: {transition.after}",
        ),
    )


def transformation_prompt(transition: Transition) -> tuple[Message, ...]:
    """Compatibility alias for the versioned strict extraction prompt."""
    return render_transformation_prompt(transition)


def ranking_prompt(
    problem: Problem,
    state: str,
    candidates: tuple[str, ...],
    transformations: tuple[str, ...],
    proof_need: str | None = None,
) -> tuple[Message, ...]:
    payload = masked_problem_payload(problem)
    numbered = "\n".join(f"{index}: {item}" for index, item in enumerate(candidates))
    analogies = _context_block(transformations)
    return (
        Message(
            "system",
            f"Prompt {RANKING_PROMPT_VERSION}. Rank only the supplied Lean tactic "
            "indices. Return a JSON array of unique integer indices. Do not write tactics.",
        ),
        Message(
            "user",
            f"Overall target: {payload['target']}\nCurrent goals:\n{state}\n"
            f"Structural proof need:\n{proof_need or '(none)'}\n"
            f"Structural transformations:\n{analogies}\nCandidates:\n{numbered}",
        ),
    )


def tactic_scoring_prompt(
    problem: Problem,
    state: str,
    signature: ProofSignature,
    hits: tuple["RetrievalHit", ...],
) -> tuple[Message, ...]:
    payload = masked_problem_payload(problem)
    transformations = [
        {
            "id": hit.item.transition.id,
            "domain": hit.item.transition.domain,
            "record": _jsonable(hit.item.record),
        }
        for hit in hits
    ]
    return (
        Message(
            "system",
            "Score the supplied completion as the next Lean 4 tactic. The proof state "
            "and any retrieved cross-domain transformations are fixed context.",
        ),
        Message(
            "user",
            f"Overall target: {payload['target']}\nCurrent goals:\n{state}\n"
            f"Proof structure: {render_proof_signature(signature)}\n"
            f"Retrieved transformations: {json.dumps(transformations, sort_keys=True, separators=(',', ':'))}\n"
            "Next tactic:",
        ),
    )


def _parse_bare_candidate_array(text: str) -> list[str] | None:
    """Recover Qwen's unquoted ``[tactic, tactic]`` serialization safely.

    This is deliberately not a general Python/Lean parser: it only separates
    commas at the outer list level and never evaluates model output.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if len(stripped) < 3 or stripped[0] != "[" or stripped[-1] != "]":
        return None
    body = stripped[1:-1]
    pairs = {"(": ")", "[": "]", "{": "}", "⟨": "⟩"}
    closers = set(pairs.values())
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    start = 0
    items: list[str] = []
    for index, character in enumerate(body):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character in pairs:
            stack.append(pairs[character])
        elif character in closers:
            if not stack or stack.pop() != character:
                return None
        elif character == "," and not stack:
            items.append(body[start:index].strip())
            start = index + 1
    if quote is not None or stack:
        return None
    items.append(body[start:].strip())
    if not items or any(not item for item in items):
        return None
    return items


def parse_candidates(text: str, top_k: int) -> tuple[str, ...]:
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ExperimentError("top_k must be a positive integer")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        value = _parse_bare_candidate_array(text)
        if value is None:
            raise ModelOutputError("candidate output must be a JSON or bare tactic array") from error
    if not isinstance(value, list) or not value:
        raise ModelOutputError("candidate output must be a nonempty array")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip() or any(character in item for character in "\r\n\x00"):
            raise ModelOutputError("candidate tactics must be nonblank single lines")
        canonical = " ".join(item.split())
        if canonical in seen:
            raise ModelOutputError("candidate tactics must be unique")
        seen.add(canonical)
        result.append(canonical)
    return tuple(result[:top_k])


_TACTIC_WORDS = re.compile(r"\b(?:intro|exact|apply|cases|constructor|simp|aesop|assumption|rfl)\b", re.IGNORECASE)


def parse_transformation_record(text: str) -> TransformationRecord:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ModelOutputError("transformation output must be valid JSON") from error
    expected = {
        "operation",
        "input_arity",
        "output_arity",
        "permutation",
        "information_flow",
        "focus_before",
        "focus_after",
        "domain_neutral_summary",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ModelOutputError("transformation output must contain exactly the documented schema")
    flows = value["information_flow"]
    if not isinstance(flows, list):
        raise ModelOutputError("transformation information_flow must be an array")
    try:
        return TransformationRecord(
            operation=OperationClass(value["operation"]),
            input_arity=Arity(value["input_arity"]),
            output_arity=Arity(value["output_arity"]),
            permutation=Permutation(value["permutation"]),
            information_flow=tuple(InformationFlow(item) for item in flows),
            focus_before=value["focus_before"],
            focus_after=value["focus_after"],
            domain_neutral_summary=value["domain_neutral_summary"],
        )
    except (ExperimentError, TypeError, ValueError) as error:
        raise ModelOutputError(f"invalid transformation record: {error}") from error


def parse_transformation(text: str) -> TransformationRecord:
    """Compatibility alias for callers migrating from the free-form schema."""
    return parse_transformation_record(text)


def parse_ranking(text: str, candidate_count: int) -> tuple[int, ...]:
    if isinstance(candidate_count, bool) or not isinstance(candidate_count, int) or candidate_count <= 0:
        raise ExperimentError("candidate_count must be a positive integer")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as direct_error:
        fenced = (
            re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", text, re.DOTALL | re.IGNORECASE)
            if isinstance(text, str)
            else None
        )
        if fenced is None:
            raise ModelOutputError("ranking output must be valid JSON") from direct_error
        try:
            value = json.loads(fenced.group(1))
        except json.JSONDecodeError as fenced_error:
            raise ModelOutputError("ranking output must be valid JSON") from fenced_error
    if not isinstance(value, list):
        raise ModelOutputError("ranking output must be an array")
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item < candidate_count:
            raise ModelOutputError("ranking indices are out of range")
        if item in seen:
            raise ModelOutputError("ranking indices must be unique")
        seen.add(item)
        result.append(item)
    result.extend(index for index in range(candidate_count) if index not in seen)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class GenerationPolicy:
    thinking: bool
    reasoning_max_tokens: int
    reasoning_timeout_seconds: float
    answer_max_tokens: int
    answer_timeout_seconds: float


def generation_policy(
    difficulty: int,
    *,
    base_max_new_tokens: int,
    base_timeout_seconds: float,
) -> GenerationPolicy:
    if isinstance(difficulty, bool) or not isinstance(difficulty, int) or difficulty <= 0:
        raise ExperimentError("generation difficulty must be a positive integer")
    if base_max_new_tokens <= 0 or base_timeout_seconds <= 0:
        raise ExperimentError("generation base budgets must be positive")
    if difficulty <= 2:
        return GenerationPolicy(
            False, 0, 0.0, base_max_new_tokens, float(base_timeout_seconds)
        )
    if difficulty <= 4:
        return GenerationPolicy(
            True,
            max(1, base_max_new_tokens // 2),
            float(base_timeout_seconds) * 2,
            base_max_new_tokens,
            float(base_timeout_seconds) * 2,
        )
    return GenerationPolicy(
        True,
        base_max_new_tokens,
        float(base_timeout_seconds) * 3,
        base_max_new_tokens,
        float(base_timeout_seconds) * 2,
    )


def extract_complete_json(
    text: str,
    expected: str,
    *,
    thinking: bool,
) -> str | None:
    if expected not in {"array", "object"}:
        raise ExperimentError("expected JSON container must be array or object")
    if not isinstance(text, str):
        return None
    if thinking:
        marker = text.find("</think>")
        if marker < 0 and "<think>" in text:
            return None
        if marker >= 0:
            text = text[marker + len("</think>") :]
    opener = "[" if expected == "array" else "{"
    expected_type = list if expected == "array" else dict
    decoder = json.JSONDecoder()
    for offset, character in enumerate(text):
        if character != opener:
            continue
        try:
            value, end = decoder.raw_decode(text[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, expected_type):
            return text[offset : offset + end]
    return None


class _CompleteJsonStoppingCriteria:
    def __init__(
        self,
        tokenizer: Any,
        input_length: int,
        expected: str,
        thinking: bool,
    ) -> None:
        self.tokenizer = tokenizer
        self.input_length = input_length
        self.expected = expected
        self.thinking = thinking

    def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> bool:
        generated = input_ids[0][self.input_length :]
        text = self.tokenizer.decode(generated, skip_special_tokens=True)
        return extract_complete_json(text, self.expected, thinking=self.thinking) is not None


class _ThinkingCompleteStoppingCriteria:
    def __init__(self, tokenizer: Any, input_length: int) -> None:
        self.tokenizer = tokenizer
        self.input_length = input_length

    def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> bool:
        generated = input_ids[0][self.input_length :]
        text = self.tokenizer.decode(generated, skip_special_tokens=True)
        return "</think>" in text


class QwenModel:
    """Frozen local Qwen boundary for deterministic generation and embeddings."""

    def __init__(
        self,
        *,
        tokenizer: Any,
        model: Any,
        torch_module: Any,
        device: str,
        max_new_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self._tokenizer = tokenizer
        self._model = model
        self._torch = torch_module
        self._device = device
        self._max_new_tokens = max_new_tokens
        self._timeout_seconds = timeout_seconds
        self._generation_sink: Callable[[Mapping[str, Any]], Any] | None = None
        self._scoring_sink: Callable[[Mapping[str, Any]], Any] | None = None
        self.model_revision = str(
            getattr(getattr(model, "config", None), "_commit_hash", None)
            or getattr(getattr(model, "config", None), "name_or_path", None)
            or "unknown"
        )
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        model.eval()

    @classmethod
    def load(
        cls,
        name: str,
        device: str,
        max_new_tokens: int,
        timeout_seconds: float,
    ) -> QwenModel:
        try:
            import torch
            import transformers
        except ImportError as error:
            raise ModelConfigurationError("torch and transformers are required") from error
        normalized = device.casefold()
        if normalized == "auto":
            if torch.cuda.is_available():
                normalized = "cuda"
            elif torch.backends.mps.is_available():
                normalized = "mps"
            else:
                normalized = "cpu"
        if normalized not in {"cpu", "cuda", "mps"}:
            raise ModelConfigurationError(f"unknown model device {device!r}")
        if normalized == "cuda" and not torch.cuda.is_available():
            raise ModelConfigurationError("configured cuda device is unavailable")
        if normalized == "mps" and not torch.backends.mps.is_available():
            raise ModelConfigurationError("configured mps device is unavailable")
        tokenizer = transformers.AutoTokenizer.from_pretrained(name)
        model = transformers.AutoModelForCausalLM.from_pretrained(name, torch_dtype="auto")
        model.to(normalized)
        return cls(
            tokenizer=tokenizer,
            model=model,
            torch_module=torch,
            device=normalized,
            max_new_tokens=max_new_tokens,
            timeout_seconds=timeout_seconds,
        )

    def set_generation_sink(
        self, sink: Callable[[Mapping[str, Any]], Any] | None
    ) -> None:
        self._generation_sink = sink

    def set_scoring_sink(
        self, sink: Callable[[Mapping[str, Any]], Any] | None
    ) -> None:
        self._scoring_sink = sink

    def _render(self, messages: tuple[Message, ...], *, thinking: bool) -> str:
        return self._tokenizer.apply_chat_template(
            [{"role": item.role, "content": item.content} for item in messages],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=thinking,
        )

    def score_completions(
        self,
        messages: tuple[Message, ...],
        completions: tuple[str, ...],
        *,
        max_batch_tokens: int | None = None,
    ) -> tuple[CompletionScore, ...]:
        rendered = self._render(tuple(messages), thinking=False)
        return self._score_rendered_completions(
            ((rendered, tuple(completions)),), max_batch_tokens=max_batch_tokens
        )[0]

    def _score_rendered_completions(
        self,
        requests: tuple[tuple[str, tuple[str, ...]], ...],
        *,
        max_batch_tokens: int | None,
    ) -> tuple[tuple[CompletionScore, ...], ...]:
        if not requests:
            return ()
        pad_token_id = getattr(self._tokenizer, "pad_token_id", None)
        if pad_token_id is None:
            pad_token_id = getattr(self._tokenizer, "eos_token_id", None)
        if pad_token_id is None:
            raise ModelConfigurationError("tokenizer must define a pad or EOS token ID")
        if max_batch_tokens is not None and (
            isinstance(max_batch_tokens, bool)
            or not isinstance(max_batch_tokens, int)
            or max_batch_tokens <= 0
        ):
            raise ExperimentError("max_batch_tokens must be a positive integer")

        entries: list[tuple[int, int, str, tuple[int, ...], tuple[int, ...]]] = []
        group_sizes: list[int] = []
        for request_index, (rendered, completions) in enumerate(requests):
            _nonblank(rendered, "scoring prompt")
            if not completions:
                raise ExperimentError("scoring completions must be nonempty")
            prompt_ids = tuple(
                int(item)
                for item in self._tokenizer.encode(rendered, add_special_tokens=False)
            )
            if not prompt_ids:
                raise ModelConfigurationError("scoring prompt tokenization must be nonempty")
            group_sizes.append(len(completions))
            for completion_index, completion in enumerate(completions):
                _nonblank(completion, "scoring completion")
                completion_ids = tuple(
                    int(item)
                    for item in self._tokenizer.encode(completion, add_special_tokens=False)
                )
                if not completion_ids:
                    raise ModelConfigurationError("completion tokenization must be nonempty")
                entries.append(
                    (request_index, completion_index, completion, prompt_ids, completion_ids)
                )

        longest = max(len(prompt) + len(completion) for _, _, _, prompt, completion in entries)
        if max_batch_tokens is not None and max_batch_tokens < longest:
            raise ExperimentError("max_batch_tokens is below the longest individual sequence")

        batches: list[list[tuple[int, int, str, tuple[int, ...], tuple[int, ...]]]] = []
        current: list[tuple[int, int, str, tuple[int, ...], tuple[int, ...]]] = []
        current_width = 0
        for entry in entries:
            sequence_length = len(entry[3]) + len(entry[4])
            proposed_width = max(current_width, sequence_length)
            proposed_tokens = proposed_width * (len(current) + 1)
            if current and max_batch_tokens is not None and proposed_tokens > max_batch_tokens:
                batches.append(current)
                current = []
                current_width = 0
            current.append(entry)
            current_width = max(current_width, sequence_length)
        if current:
            batches.append(current)

        grouped: list[list[CompletionScore | None]] = [
            [None] * size for size in group_sizes
        ]
        for batch_index, batch in enumerate(batches):
            width = max(len(entry[3]) + len(entry[4]) for entry in batch)
            input_rows: list[list[int]] = []
            attention_rows: list[list[int]] = []
            for _, _, _, prompt_ids, completion_ids in batch:
                sequence = list(prompt_ids + completion_ids)
                padding = width - len(sequence)
                input_rows.append(sequence + [int(pad_token_id)] * padding)
                attention_rows.append([1] * len(sequence) + [0] * padding)
            input_ids = self._torch.tensor(
                input_rows, dtype=self._torch.long, device=self._device
            )
            attention_mask = self._torch.tensor(
                attention_rows, dtype=self._torch.long, device=self._device
            )
            started = time.monotonic()
            with self._torch.inference_mode():
                output = self._model(
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                    use_cache=False,
                )
            if self._device == "mps":
                self._torch.mps.synchronize()
            elapsed = time.monotonic() - started
            logits = getattr(output, "logits", None)
            if logits is None or logits.ndim != 3:
                raise ModelOutputError("completion scorer requires rank-3 logits")
            if not bool(self._torch.isfinite(logits).all().item()):
                raise ModelOutputError("completion scorer logits must be finite")
            log_probabilities = self._torch.log_softmax(
                logits.to(dtype=self._torch.float32), dim=-1
            )
            for row, (request_index, completion_index, text, prompt_ids, completion_ids) in enumerate(batch):
                token_scores: list[float] = []
                for offset, token_id in enumerate(completion_ids):
                    logit_position = len(prompt_ids) + offset - 1
                    if not 0 <= token_id < int(log_probabilities.shape[-1]):
                        raise ModelOutputError("completion token ID is outside model vocabulary")
                    token_scores.append(
                        float(log_probabilities[row, logit_position, token_id].item())
                    )
                total = sum(token_scores)
                grouped[request_index][completion_index] = CompletionScore(
                    text,
                    total,
                    total / len(token_scores),
                    len(token_scores),
                )
            if self._scoring_sink is not None:
                self._scoring_sink(
                    {
                        "operation": "completion_scoring",
                        "batch_index": batch_index,
                        "sequence_count": len(batch),
                        "padded_token_count": len(batch) * width,
                        "elapsed_seconds": elapsed,
                    }
                )
        if any(score is None for group in grouped for score in group):
            raise ModelOutputError("completion scorer did not fill every request")
        return tuple(tuple(group) for group in grouped)  # type: ignore[arg-type]

    def generate(
        self,
        messages: tuple[Message, ...],
        *,
        seed: int,
        timeout_seconds: float | None = None,
        operation: str = "unspecified",
        difficulty: int = 1,
        expected_json: str | None = None,
    ) -> str:
        policy = generation_policy(
            difficulty,
            base_max_new_tokens=self._max_new_tokens,
            base_timeout_seconds=self._timeout_seconds,
        )
        overall_deadline = (
            None if timeout_seconds is None else time.monotonic() + timeout_seconds
        )

        def remaining_timeout(configured: float) -> float:
            if overall_deadline is None:
                return configured
            remaining = overall_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("theorem timeout")
            return min(configured, max(remaining, 0.001))

        def run_phase(
            phase_messages: tuple[Message, ...],
            *,
            phase: str,
            thinking: bool,
            phase_seed: int,
            max_new_tokens: int,
            configured_timeout: float,
            json_contract: str | None = None,
            stop_after_thinking: bool = False,
        ) -> tuple[str, Any, float, float, str]:
            prompt = self._render(phase_messages, thinking=thinking)
            inputs = self._tokenizer([prompt], return_tensors="pt").to(self._device)
            input_length = int(inputs["input_ids"].shape[-1])
            criteria: list[Any] = []
            if json_contract is not None:
                criteria.append(
                    _CompleteJsonStoppingCriteria(
                        self._tokenizer, input_length, json_contract, False
                    )
                )
            if stop_after_thinking:
                criteria.append(
                    _ThinkingCompleteStoppingCriteria(self._tokenizer, input_length)
                )
            if criteria:
                try:
                    from transformers import StoppingCriteriaList
                except ImportError as error:
                    raise ModelConfigurationError("transformers is required") from error
                stopping_criteria: Any = StoppingCriteriaList(criteria)
            else:
                stopping_criteria = []
            effective_timeout = remaining_timeout(configured_timeout)
            self._torch.manual_seed(phase_seed)
            started = time.monotonic()
            with self._torch.inference_mode():
                generated = self._model.generate(
                    **inputs,
                    do_sample=False,
                    max_new_tokens=max_new_tokens,
                    max_time=effective_timeout,
                    stopping_criteria=stopping_criteria,
                )
            elapsed = time.monotonic() - started
            output_ids = generated[0][input_length:]
            raw = self._tokenizer.decode(output_ids, skip_special_tokens=True)
            generated_tokens = int(output_ids.shape[-1])
            if phase == "reasoning":
                if "</think>" in raw:
                    stop_reason = "thinking_complete"
                elif elapsed >= effective_timeout:
                    stop_reason = "timeout"
                elif generated_tokens >= max_new_tokens:
                    stop_reason = "reasoning_ceiling"
                else:
                    stop_reason = "eos"
                reasoning_tokens = generated_tokens
                answer_tokens = 0
            else:
                structured = (
                    None
                    if json_contract is None
                    else extract_complete_json(raw, json_contract, thinking=False)
                )
                if structured is not None:
                    stop_reason = "complete_json"
                elif elapsed >= effective_timeout:
                    stop_reason = "timeout"
                elif generated_tokens >= max_new_tokens:
                    stop_reason = "answer_ceiling"
                else:
                    stop_reason = "eos"
                reasoning_tokens = 0
                answer_tokens = generated_tokens
            event = {
                "operation": operation,
                "phase": phase,
                "difficulty": difficulty,
                "thinking": thinking,
                "max_new_tokens": max_new_tokens,
                "timeout_seconds": effective_timeout,
                "generated_tokens": generated_tokens,
                "reasoning_tokens": reasoning_tokens,
                "answer_tokens": answer_tokens,
                "elapsed_seconds": elapsed,
                "stop_reason": stop_reason,
                "seed": phase_seed,
                "prompt_hash": sha256_text(prompt),
                "output_hash": sha256_text(raw),
            }
            if phase == "answer":
                event["output_text"] = raw
            if self._generation_sink is not None:
                self._generation_sink(event)
            return raw, output_ids, elapsed, effective_timeout, stop_reason

        answer_messages = messages
        if policy.thinking:
            reasoning_raw, _, _, _, _ = run_phase(
                messages,
                phase="reasoning",
                thinking=True,
                phase_seed=seed,
                max_new_tokens=policy.reasoning_max_tokens,
                configured_timeout=policy.reasoning_timeout_seconds,
                stop_after_thinking=True,
            )
            reasoning_notes = reasoning_raw
            if "<think>" in reasoning_notes:
                reasoning_notes = reasoning_notes.split("<think>", 1)[1]
            if "</think>" in reasoning_notes:
                reasoning_notes = reasoning_notes.split("</think>", 1)[0]
            reasoning_notes = reasoning_notes.strip()
            answer_messages = messages + (
                Message("assistant", f"Reasoning notes:\n{reasoning_notes or '(none)'}"),
                Message(
                    "user",
                    "Using those notes, return only the exact final response requested "
                    "by the system message. Do not continue reasoning or add explanation.",
                ),
            )

        answer_seed = derived_seed(seed, operation, "answer") if policy.thinking else seed
        raw, _, elapsed, effective_timeout, _ = run_phase(
            answer_messages,
            phase="answer",
            thinking=False,
            phase_seed=answer_seed,
            max_new_tokens=policy.answer_max_tokens,
            configured_timeout=policy.answer_timeout_seconds,
            json_contract=expected_json,
        )
        structured = (
            None
            if expected_json is None
            else extract_complete_json(raw, expected_json, thinking=False)
        )
        if elapsed >= effective_timeout and structured is None:
            raise TimeoutError(f"answer generation exceeded {effective_timeout:g} seconds")
        return structured if structured is not None else raw.strip()

    def embed(self, text: str, layer: int) -> Embedding:
        _nonblank(text, "embedding text")
        inputs = self._tokenizer([text], return_tensors="pt").to(self._device)
        with self._torch.inference_mode():
            output = self._model(**inputs, output_hidden_states=True, use_cache=False)
        hidden_states = tuple(output.hidden_states)
        resolved = layer if layer >= 0 else len(hidden_states) + layer
        if not 0 <= resolved < len(hidden_states):
            raise ModelConfigurationError(
                f"embedding layer {layer} is outside [-{len(hidden_states)}, {len(hidden_states) - 1}]"
            )
        attention = inputs["attention_mask"]
        token_index = int(attention[0].sum().item()) - 1
        vector = hidden_states[resolved][0, token_index].to(dtype=self._torch.float32)
        if not bool(self._torch.isfinite(vector).all().item()):
            raise ModelOutputError("embedding vector must be finite")
        norm = self._torch.linalg.vector_norm(vector)
        if not math.isfinite(float(norm.item())) or float(norm.item()) == 0.0:
            raise ModelOutputError("embedding vector must be finite and nonzero")
        normalized = vector / norm
        values = tuple(float(item) for item in normalized.detach().cpu().tolist())
        return Embedding(
            values=values,
            layer=resolved,
            token_index=token_index,
            model_revision=self.model_revision,
            text_hash=sha256_text(text),
        )


TRANSFORMATION_SCHEMA_VERSION = "transformation-record-v1"
DIAGNOSTIC_POOLING_RULE = "final-nonpadding-l2-float32-v1"


@dataclass(frozen=True, slots=True)
class IndexedTransformation:
    transition: Transition
    record: TransformationRecord
    diagnostic_embedding: Embedding | None
    cache_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.transition, Transition):
            raise ExperimentError("indexed transformation requires a Transition")
        if not isinstance(self.record, TransformationRecord):
            raise ExperimentError("indexed transformation requires a TransformationRecord")
        if self.diagnostic_embedding is not None and not isinstance(
            self.diagnostic_embedding, Embedding
        ):
            raise ExperimentError("diagnostic embedding must be an Embedding or None")
        _nonblank(self.cache_key, "indexed_transformation.cache_key")

    @property
    def transformation(self) -> str:
        """Compatibility view used by the legacy ranker until Task 4 removes it."""
        return self.record.domain_neutral_summary

    @property
    def embedding(self) -> Embedding:
        """Compatibility view used only by the legacy cosine ablation."""
        if self.diagnostic_embedding is None:
            raise ExperimentError("diagnostic embedding was not captured")
        return self.diagnostic_embedding


def relevance_pair_prompt(
    signature: ProofSignature, item: IndexedTransformation
) -> tuple[Message, ...]:
    return (
        Message(
            "system",
            f"Prompt {PAIR_SCORING_PROMPT_VERSION}. Judge whether the candidate "
            "cross-domain transformation is structurally relevant to the proof need. "
            "The completion must be exactly relevant or irrelevant.",
        ),
        Message(
            "user",
            f"Transition provenance: {json.dumps({'id': item.transition.id, 'domain': item.transition.domain}, sort_keys=True, separators=(',', ':'))}\n"
            f"Proof structure: {render_proof_signature(signature)}\n"
            f"Proof signature: {json.dumps(_jsonable(signature), sort_keys=True, separators=(',', ':'))}\n"
            f"Transformation schema: {json.dumps(_jsonable(item.record), sort_keys=True, separators=(',', ':'))}\n"
            f"Transformation summary: {item.record.domain_neutral_summary}\n"
            "Judgment:",
        ),
    )


def score_relevance_pairs(
    model: TextModel,
    pairs: tuple[tuple[ProofSignature, IndexedTransformation], ...],
    *,
    max_batch_tokens: int | None = None,
) -> tuple[PairScore, ...]:
    normalized = tuple(pairs)
    if not normalized:
        return ()
    completions = ("relevant", "irrelevant")
    internal = getattr(model, "_score_rendered_completions", None)
    render = getattr(model, "_render", None)
    if callable(internal) and callable(render):
        rendered_requests = tuple(
            (render(relevance_pair_prompt(signature, item), thinking=False), completions)
            for signature, item in normalized
        )
        grouped_scores = internal(
            rendered_requests, max_batch_tokens=max_batch_tokens
        )
    else:
        grouped_scores = tuple(
            model.score_completions(
                relevance_pair_prompt(signature, item),
                completions,
                max_batch_tokens=max_batch_tokens,
            )
            for signature, item in normalized
        )
    results: list[PairScore] = []
    for (_, item), scores in zip(normalized, grouped_scores):
        if len(scores) != 2:
            raise ModelOutputError("pair scorer must return relevant and irrelevant scores")
        relevant, irrelevant = scores
        results.append(
            PairScore(
                item.transition.id,
                relevant,
                irrelevant,
                relevant.mean_log_likelihood - irrelevant.mean_log_likelihood,
            )
        )
    return tuple(results)


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    item: IndexedTransformation
    structural: CompatibilityBreakdown
    pair_score: PairScore | None
    selection: str

    def __post_init__(self) -> None:
        if not isinstance(self.item, IndexedTransformation):
            raise ExperimentError("retrieval hit requires an IndexedTransformation")
        if not isinstance(self.structural, CompatibilityBreakdown):
            raise ExperimentError("retrieval hit requires structural compatibility")
        if self.pair_score is not None and not isinstance(self.pair_score, PairScore):
            raise ExperimentError("retrieval hit pair score is invalid")
        if self.selection not in {"matched_random", "analogy"}:
            raise ExperimentError("retrieval hit selection is invalid")


@dataclass(frozen=True, slots=True)
class RankingDecision:
    candidates: tuple[str, ...]
    indices: tuple[int, ...]
    signature: ProofSignature
    proof_rendering: str
    structural_shortlist_ids: tuple[str, ...]
    hits: tuple[RetrievalHit, ...]
    tactic_scores: tuple[CompletionScore, ...]
    pair_scores: tuple[PairScore, ...]
    fallback_reason: str | None

    def __post_init__(self) -> None:
        candidates = tuple(self.candidates)
        indices = tuple(self.indices)
        hits = tuple(self.hits)
        if not candidates or len(indices) != len(candidates) or set(indices) != set(range(len(candidates))):
            raise ExperimentError("ranking decision must be a permutation of candidates")
        if not all(isinstance(item, RetrievalHit) for item in hits):
            raise ExperimentError("ranking hits must contain RetrievalHit values")
        if not isinstance(self.signature, ProofSignature):
            raise ExperimentError("ranking decision requires a proof signature")
        _nonblank(self.proof_rendering, "ranking_decision.proof_rendering")
        if len(self.tactic_scores) != len(candidates):
            raise ExperimentError("ranking decision requires one tactic score per candidate")
        if tuple(score.text for score in self.tactic_scores) != candidates:
            raise ExperimentError("ranking tactic scores must preserve candidate order")
        if not all(isinstance(item, PairScore) for item in self.pair_scores):
            raise ExperimentError("ranking pair scores are invalid")
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "indices", indices)
        object.__setattr__(self, "hits", hits)

    @property
    def ordered_candidates(self) -> tuple[str, ...]:
        return tuple(self.candidates[index] for index in self.indices)


def derived_seed(seed: int, *parts: str) -> int:
    material = "\x1f".join((str(seed), *parts))
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")


def transformation_cache_key(
    transition: Transition,
    model_revision: str,
    layer: int,
    prompt_version: str,
    *,
    schema_version: str = TRANSFORMATION_SCHEMA_VERSION,
    pooling_rule: str = DIAGNOSTIC_POOLING_RULE,
) -> str:
    payload = {
        "transition": {
            "id": transition.id,
            "domain": transition.domain,
            "before": transition.before,
            "after": transition.after,
            "provenance": dict(transition.provenance),
        },
        "model_revision": model_revision,
        "layer": layer,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "pooling": pooling_rule,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(encoded)


def _record_from_payload(value: Any) -> TransformationRecord:
    if not isinstance(value, Mapping):
        raise ModelOutputError("cached transformation record must be an object")
    return parse_transformation_record(json.dumps(value, ensure_ascii=False))


def _embedding_from_payload(value: Any) -> Embedding | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "values", "layer", "token_index", "model_revision", "text_hash"
    }:
        raise ModelOutputError("cached diagnostic embedding is invalid")
    embedding = Embedding(
        tuple(value["values"]),
        value["layer"],
        value["token_index"],
        value["model_revision"],
        value["text_hash"],
    )
    norm = math.sqrt(sum(item * item for item in embedding.values))
    if not math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-6):
        raise ModelOutputError("cached diagnostic embedding is not normalized")
    return embedding


def _cached_index_item(
    path: Path, transition: Transition, expected_key: str
) -> IndexedTransformation:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExperimentError(f"invalid transition cache {path}: {error}") from error
    if not isinstance(raw, dict) or raw.get("cache_key") != expected_key or raw.get("transition_id") != transition.id:
        raise ExperimentError(f"transition cache identity mismatch: {path}")
    try:
        if raw.get("schema_version") != TRANSFORMATION_SCHEMA_VERSION:
            raise ModelOutputError("cached transformation schema version is invalid")
        record = _record_from_payload(raw["record"])
        embedding = _embedding_from_payload(raw.get("diagnostic_embedding"))
    except (KeyError, ExperimentError, TypeError, ValueError) as error:
        raise ExperimentError(f"transition cache content is invalid: {path}: {error}") from error
    return IndexedTransformation(transition, record, embedding, expected_key)


def _model_generate(
    model: TextModel,
    messages: tuple[Message, ...],
    *,
    seed: int,
    timeout_seconds: float | None = None,
    operation: str,
    difficulty: int,
    expected_json: str | None,
) -> str:
    parameters = inspect.signature(model.generate).parameters
    accepts_keywords = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    optional = {
        "operation": operation,
        "difficulty": difficulty,
        "expected_json": expected_json,
    }
    if timeout_seconds is not None:
        optional["timeout_seconds"] = timeout_seconds
    kwargs = {
        key: value
        for key, value in optional.items()
        if accepts_keywords or key in parameters
    }
    return model.generate(messages, seed=seed, **kwargs)


def build_transformation_index(
    transitions: Iterable[Transition],
    model: TextModel,
    cache_dir: Path | None,
    hidden_layer: int,
    *,
    capture_diagnostic_embeddings: bool = False,
) -> tuple[tuple[IndexedTransformation, ...], Mapping[str, int]]:
    if cache_dir is not None:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
    indexed: list[IndexedTransformation] = []
    exclusions: dict[str, int] = {}
    seen_records: set[str] = set()

    def exclude(category: str) -> None:
        exclusions[category] = exclusions.get(category, 0) + 1

    for transition in transitions:
        key = transformation_cache_key(
            transition,
            model.model_revision,
            hidden_layer,
            TRANSFORMATION_PROMPT_VERSION,
        )
        cache_path = None if cache_dir is None else Path(cache_dir) / f"{key}.json"
        if cache_path is not None and cache_path.exists():
            try:
                item = _cached_index_item(cache_path, transition, key)
                if capture_diagnostic_embeddings and item.diagnostic_embedding is None:
                    embedding = model.embed(item.record.domain_neutral_summary, hidden_layer)
                    item = replace(item, diagnostic_embedding=embedding)
            except ModelOutputError:
                exclude("invalid_embedding")
                continue
            except ExperimentError as error:
                category = "invalid_embedding" if "embedding" in str(error).casefold() else "invalid_schema"
                exclude(category)
                continue
        else:
            try:
                generated = _model_generate(
                    model,
                    render_transformation_prompt(transition),
                    seed=derived_seed(0, transition.id, TRANSFORMATION_PROMPT_VERSION),
                    operation="transformation",
                    difficulty=1,
                    expected_json="object",
                )
            except Exception:
                exclude("model_failure")
                continue
            try:
                record = parse_transformation_record(generated)
            except ModelOutputError:
                exclude("invalid_schema")
                continue
            embedding = None
            if capture_diagnostic_embeddings:
                try:
                    embedding = model.embed(record.domain_neutral_summary, hidden_layer)
                except Exception:
                    exclude("invalid_embedding")
                    continue
            item = IndexedTransformation(transition, record, embedding, key)

        record_key = json.dumps(
            _jsonable(item.record), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if record_key in seen_records:
            exclude("duplicate_record")
            continue
        seen_records.add(record_key)
        if cache_path is not None:
            payload = {
                "cache_key": key,
                "transition_id": transition.id,
                "schema_version": TRANSFORMATION_SCHEMA_VERSION,
                "record": _jsonable(item.record),
                "diagnostic_embedding": (
                    None
                    if item.diagnostic_embedding is None
                    else _jsonable(item.diagnostic_embedding)
                ),
            }
            temporary = cache_path.with_name(f".{cache_path.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(cache_path)
        indexed.append(item)
    return tuple(indexed), MappingProxyType(dict(sorted(exclusions.items())))


def build_index(
    transitions: Iterable[Transition],
    model: TextModel,
    cache_dir: Path | None,
    layer: int,
) -> tuple[IndexedTransformation, ...]:
    """Compatibility wrapper for the legacy cosine ranker removed in Task 4."""
    index, exclusions = build_transformation_index(
        transitions,
        model,
        cache_dir,
        layer,
        capture_diagnostic_embeddings=True,
    )
    if exclusions:
        raise ExperimentError(f"transition index exclusions: {dict(exclusions)}")
    return index


# Structural retrieval


STRUCTURAL_COMPATIBILITY_VERSION = "structural-compatibility-v1"
STRUCTURAL_WEIGHTS = MappingProxyType(
    {
        "operation": 0.40,
        "information_flow": 0.20,
        "arity": 0.15,
        "permutation": 0.15,
        "shape_role": 0.10,
    }
)
_COMPATIBLE_OPERATION_PAIRS = frozenset(
    {
        frozenset({OperationClass.DECOMPOSE, OperationClass.SELECT}),
        frozenset({OperationClass.COMBINE, OperationClass.RESTRUCTURE}),
        frozenset({OperationClass.REWRITE, OperationClass.REPLACE}),
        frozenset({OperationClass.REORDER, OperationClass.RESTRUCTURE}),
        frozenset({OperationClass.REUSE, OperationClass.SELECT}),
    }
)


@dataclass(frozen=True, slots=True)
class CompatibilityBreakdown:
    operation: float
    information_flow: float
    arity: float
    permutation: float
    shape_role: float
    matches: tuple[str, ...]
    conflicts: tuple[str, ...]

    def __post_init__(self) -> None:
        components = (
            self.operation,
            self.information_flow,
            self.arity,
            self.permutation,
            self.shape_role,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in components):
            raise ExperimentError("compatibility components must be finite and nonnegative")
        if not 0.0 <= sum(components) <= 1.0 + 1e-12:
            raise ExperimentError("compatibility total must be in [0, 1]")

    @property
    def total(self) -> float:
        return (
            self.operation
            + self.information_flow
            + self.arity
            + self.permutation
            + self.shape_role
        )


@dataclass(frozen=True, slots=True)
class StructuralCandidate:
    item: IndexedTransformation
    breakdown: CompatibilityBreakdown

    def __post_init__(self) -> None:
        if not isinstance(self.item, IndexedTransformation):
            raise ExperimentError("structural candidate requires an indexed transformation")
        if not isinstance(self.breakdown, CompatibilityBreakdown):
            raise ExperimentError("structural candidate requires a compatibility breakdown")


def _operations_compatible(
    candidates: tuple[OperationClass, ...], operation: OperationClass
) -> bool:
    if operation is OperationClass.UNKNOWN:
        return True
    return any(
        operation is candidate
        or frozenset({candidate, operation}) in _COMPATIBLE_OPERATION_PAIRS
        for candidate in candidates
    )


def structural_compatibility(
    signature: ProofSignature, item: IndexedTransformation
) -> CompatibilityBreakdown | None:
    if not isinstance(signature, ProofSignature) or not isinstance(item, IndexedTransformation):
        raise ExperimentError("structural compatibility requires canonical records")
    record = item.record
    if not _operations_compatible(signature.candidate_operations, record.operation):
        return None

    matches: list[str] = []
    conflicts: list[str] = []
    operation_score = 0.0
    if record.operation in signature.candidate_operations:
        operation_score = STRUCTURAL_WEIGHTS["operation"]
        matches.append("operation:exact")
    elif record.operation is not OperationClass.UNKNOWN:
        operation_score = STRUCTURAL_WEIGHTS["operation"] / 2
        matches.append("operation:compatible")

    proof_flow = set(signature.information_flow)
    record_flow = set(record.information_flow)
    if record_flow == proof_flow:
        flow_score = STRUCTURAL_WEIGHTS["information_flow"]
        matches.append("information_flow:exact")
    elif record_flow & proof_flow:
        flow_score = STRUCTURAL_WEIGHTS["information_flow"] / 2
        matches.append("information_flow:overlap")
    else:
        conflicts.append("information_flow")
        return None

    arity_score = 0.0
    known_arity_fields = 0
    matching_arity_fields = 0
    for field_name, proof_value, record_value in (
        ("input_arity", signature.input_arity, record.input_arity),
        ("output_arity", signature.output_arity, record.output_arity),
    ):
        if record_value is Arity.UNKNOWN or proof_value is Arity.UNKNOWN:
            continue
        known_arity_fields += 1
        if record_value is not proof_value:
            conflicts.append(field_name)
            return None
        matching_arity_fields += 1
        matches.append(f"{field_name}:exact")
    if known_arity_fields:
        arity_score = STRUCTURAL_WEIGHTS["arity"] * (
            matching_arity_fields / known_arity_fields
        )

    permutation_score = 0.0
    if record.permutation is not Permutation.UNKNOWN and signature.permutation is not Permutation.UNKNOWN:
        if record.permutation is not signature.permutation:
            conflicts.append("permutation")
            return None
        permutation_score = STRUCTURAL_WEIGHTS["permutation"]
        matches.append("permutation:exact")

    shape_role_score = 0.0
    if operation_score == STRUCTURAL_WEIGHTS["operation"]:
        shape_role_score = STRUCTURAL_WEIGHTS["shape_role"]
        matches.append("shape_role:operation-aligned")

    return CompatibilityBreakdown(
        operation_score,
        flow_score,
        arity_score,
        permutation_score,
        shape_role_score,
        tuple(matches),
        tuple(conflicts),
    )


def structural_shortlist(
    signature: ProofSignature,
    index: tuple[IndexedTransformation, ...],
    limit: int,
) -> tuple[StructuralCandidate, ...]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ExperimentError("structural shortlist limit must be positive")
    identifiers = [item.transition.id for item in index]
    if len(set(identifiers)) != len(identifiers):
        raise ExperimentError("structural shortlist contains duplicate transition IDs")
    candidates = tuple(
        StructuralCandidate(item, breakdown)
        for item in index
        if (breakdown := structural_compatibility(signature, item)) is not None
    )
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (-candidate.breakdown.total, candidate.item.transition.id),
        )[:limit]
    )


def matched_random_retrieve(
    shortlist: tuple[StructuralCandidate, ...], count: int, seed: int
) -> tuple[StructuralCandidate, ...]:
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ExperimentError("matched-random count must be positive")
    if not shortlist:
        return ()
    selected = random.Random(seed).sample(list(shortlist), min(count, len(shortlist)))
    return tuple(sorted(selected, key=lambda candidate: candidate.item.transition.id))


def _generate_before_deadline(
    model: TextModel,
    messages: tuple[Message, ...],
    *,
    seed: int,
    deadline: float | None,
    operation: str,
    difficulty: int,
    expected_json: str | None,
) -> str:
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        raise TimeoutError("theorem timeout")
    result = _model_generate(
        model,
        messages,
        seed=seed,
        timeout_seconds=remaining,
        operation=operation,
        difficulty=difficulty,
        expected_json=expected_json,
    )
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("theorem timeout")
    return result


class HybridConditionRanker:
    def __init__(
        self,
        *,
        model: TextModel,
        index: tuple[IndexedTransformation, ...],
        retrieval_count: int,
        structural_shortlist_size: int,
        pair_margin_threshold: float,
        max_batch_tokens: int | None = None,
    ) -> None:
        if retrieval_count <= 0 or structural_shortlist_size <= 0:
            raise ExperimentError("retrieval and shortlist sizes must be positive")
        if not math.isfinite(pair_margin_threshold):
            raise ExperimentError("pair margin threshold must be finite")
        self.model = model
        self.index = tuple(index)
        self.retrieval_count = retrieval_count
        self.structural_shortlist_size = structural_shortlist_size
        self.pair_margin_threshold = float(pair_margin_threshold)
        self.max_batch_tokens = max_batch_tokens
        self._shortlist_cache: dict[str, tuple[StructuralCandidate, ...]] = {}

    def rank(
        self,
        condition: Condition,
        problem: Problem,
        state: str,
        signature: ProofSignature,
        accepted: tuple[str, ...],
        failed: tuple[str, ...],
        candidates: tuple[str, ...],
        seed: int,
        deadline: float | None = None,
    ) -> RankingDecision:
        if not isinstance(condition, Condition):
            raise ExperimentError("condition must be a Condition")
        candidates = tuple(candidates)
        if not candidates:
            raise ExperimentError("candidate pool must not be empty")
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("theorem timeout")
        failed_operations = {
            operation
            for tactic in failed
            if (operation := classify_failed_tactic(tactic)) is not None
        }
        merged_signature = replace(
            signature,
            failed_operation_classes=_enum_tuple(OperationClass, failed_operations),
        )
        proof_rendering = render_proof_signature(merged_signature)
        signature_key = proof_signature_fingerprint(merged_signature)
        if signature_key not in self._shortlist_cache:
            self._shortlist_cache[signature_key] = structural_shortlist(
                merged_signature, self.index, self.structural_shortlist_size
            )
        shortlist = self._shortlist_cache[signature_key]
        shortlist_ids = tuple(item.item.transition.id for item in shortlist)
        pair_scores: tuple[PairScore, ...] = ()
        hits: tuple[RetrievalHit, ...] = ()
        fallback_reason: str | None = None

        if condition is Condition.RANDOM:
            selected = matched_random_retrieve(
                shortlist,
                self.retrieval_count,
                derived_seed(seed, problem.id, sha256_text(state), "random-retrieval"),
            )
            hits = tuple(
                RetrievalHit(item.item, item.breakdown, None, "matched_random")
                for item in selected
            )
            if not shortlist:
                fallback_reason = "empty_structural_shortlist"
        elif condition is Condition.ANALOGY:
            if shortlist:
                pair_scores = score_relevance_pairs(
                    self.model,
                    tuple((merged_signature, candidate.item) for candidate in shortlist),
                    max_batch_tokens=self.max_batch_tokens,
                )
                by_id = {score.transition_id: score for score in pair_scores}
                eligible = [
                    candidate
                    for candidate in shortlist
                    if by_id[candidate.item.transition.id].margin
                    >= self.pair_margin_threshold
                ]
                eligible.sort(
                    key=lambda candidate: (
                        -by_id[candidate.item.transition.id].margin,
                        candidate.item.transition.id,
                    )
                )
                selected = eligible[: self.retrieval_count]
                hits = tuple(
                    RetrievalHit(
                        candidate.item,
                        candidate.breakdown,
                        by_id[candidate.item.transition.id],
                        "analogy",
                    )
                    for candidate in selected
                )
                if not selected:
                    fallback_reason = "below_pair_margin_threshold"
            else:
                fallback_reason = "empty_structural_shortlist"

        tactic_scores = self.model.score_completions(
            tactic_scoring_prompt(problem, state, merged_signature, hits),
            candidates,
            max_batch_tokens=self.max_batch_tokens,
        )
        if tuple(score.text for score in tactic_scores) != candidates:
            raise ModelOutputError("tactic scorer changed candidate identity or order")
        indices = tuple(
            sorted(
                range(len(candidates)),
                key=lambda index: (-tactic_scores[index].mean_log_likelihood, index),
            )
        )
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("theorem timeout")
        return RankingDecision(
            candidates,
            indices,
            merged_signature,
            proof_rendering,
            shortlist_ids,
            hits,
            tactic_scores,
            pair_scores,
            fallback_reason,
        )


def rank_for_condition(
    condition: Condition,
    problem: Problem,
    state: str,
    candidates: tuple[str, ...],
    proof_need: Any,
    index: tuple[IndexedTransformation, ...],
    model: TextModel,
    retrieval_count: int,
    seed: int,
    *,
    layer: int = -1,
    accepted: tuple[str, ...] = (),
    failed: tuple[str, ...] = (),
    deadline: float | None = None,
    proof_signature: ProofSignature | None = None,
) -> RankingDecision:
    del proof_need, layer
    if proof_signature is None:
        raise ExperimentError("all conditions require a deterministic proof signature")
    return HybridConditionRanker(
        model=model,
        index=index,
        retrieval_count=retrieval_count,
        structural_shortlist_size=max(len(index), 1),
        pair_margin_threshold=0.0,
    ).rank(
        condition,
        problem,
        state,
        proof_signature,
        accepted,
        failed,
        candidates,
        seed,
        deadline,
    )


class CandidatePoolStore:
    """Generate each conventional tactic pool once per normalized proof state."""

    def __init__(self, model: TextModel) -> None:
        self._model = model
        self._pools: dict[tuple[str, str, int, str, int], tuple[str, ...]] = {}

    def get(
        self,
        problem: Problem,
        state: str,
        accepted: tuple[str, ...],
        failed: tuple[str, ...],
        *,
        seed: int,
        top_k: int,
    ) -> tuple[str, ...]:
        state_hash = sha256_text(state)
        key = (problem.id, state_hash, seed, CANDIDATE_PROMPT_VERSION, top_k)
        if key not in self._pools:
            generated = _model_generate(
                self._model,
                candidate_prompt(problem, state, accepted, failed, top_k),
                seed=derived_seed(seed, problem.id, state_hash, "candidates"),
                operation="candidate",
                difficulty=problem.difficulty,
                expected_json="array",
            )
            self._pools[key] = parse_candidates(generated, top_k)
        return self._pools[key]


class LeanStatus(str, Enum):
    OPEN = "open"
    COMPLETE = "complete"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class LeanResult:
    status: LeanStatus
    accepted: bool
    state: str | None
    diagnostics: tuple[str, ...]
    exit_code: int | None
    duration_ms: int
    signature: ProofSignature | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, LeanStatus) or not isinstance(self.accepted, bool):
            raise ExperimentError("Lean result status or acceptance is invalid")
        diagnostics = tuple(str(item) for item in self.diagnostics)
        object.__setattr__(self, "diagnostics", diagnostics)
        if self.status is LeanStatus.OPEN and (
            not self.accepted or not self.state or not isinstance(self.signature, ProofSignature)
        ):
            raise ExperimentError("open Lean results require an accepted state and proof signature")
        if self.status is LeanStatus.COMPLETE and (
            not self.accepted or self.state is not None or self.signature is not None
        ):
            raise ExperimentError("complete Lean results require acceptance, no state, and no signature")
        if self.status not in {LeanStatus.OPEN, LeanStatus.COMPLETE} and self.accepted:
            raise ExperimentError("failed Lean results cannot be accepted")
        if self.status is not LeanStatus.OPEN and self.signature is not None:
            raise ExperimentError("only open Lean results may carry proof signatures")


@dataclass(frozen=True, slots=True)
class _LeanDiagnostic:
    severity: str
    kind: str | None
    message: str
    line: int | None


@dataclass(frozen=True, slots=True)
class _LeanRun:
    exit_code: int | None
    diagnostics: tuple[_LeanDiagnostic, ...]
    duration_ms: int
    timed_out: bool = False
    internal_error: bool = False


_TEMP_LEAN_PATH = re.compile(r"(?:/[A-Za-z0-9_.-]+)+/(?:Current|current_problem)\.lean(?::\d+:\d+)?")
_PROOF_STRUCTURE_MARKER = "ANALOGY_PROOF_STRUCTURE:"


LEAN_STRUCTURE_PRELUDE = r'''import Lean
open Lean Meta Elab Tactic

partial def analogyExprShape (needle : Option Expr) (expression : Expr)
    (depth : Nat := 0) : MetaM Json := do
  if depth >= 8 then
    return Json.mkObj [("root", Json.str "opaque"), ("children", Json.arr #[])]
  let expression ← instantiateMVars expression
  if let some target := needle then
    if expression == target then
      return Json.mkObj [("root", Json.str "target"), ("children", Json.arr #[])]
  match expression with
  | .forallE _ domain body _ =>
      let root := if body.hasLooseBVar 0 then "forall" else "implication"
      let left ← analogyExprShape needle domain (depth + 1)
      let right ← analogyExprShape needle body (depth + 1)
      return Json.mkObj [("root", Json.str root), ("children", Json.arr #[left, right])]
  | _ =>
      let root := match expression.getAppFn.constName? with
        | some ``And => "and"
        | some ``Or => "or"
        | some ``Iff => "iff"
        | some ``Eq => "equality"
        | some ``Not => "not"
        | some ``Exists => "exists"
        | some ``False => "false"
        | _ => if expression.isApp then "application" else "atom"
      let children ← expression.getAppArgs.mapM fun child =>
        analogyExprShape needle child (depth + 1)
      return Json.mkObj [("root", Json.str root), ("children", Json.arr children)]

elab "trace_proof_structure" : tactic => do
  let goals ← getGoals
  match goals[0]? with
  | none => pure ()
  | some goal => goal.withContext do
      let target ← instantiateMVars (← goal.getType)
      let targetJson ← analogyExprShape none target
      let mut hypotheses : Array Json := #[]
      for declaration in (← getLCtx) do
        if !declaration.isImplementationDetail && (← isProp declaration.type) then
          let shape ← analogyExprShape (some target) declaration.type
          hypotheses := hypotheses.push shape
      let payload := Json.mkObj [
        ("goal_count", toJson goals.length),
        ("target", targetJson),
        ("hypotheses", Json.arr hypotheses)
      ]
      logInfo m!"ANALOGY_PROOF_STRUCTURE:{payload.compress}"
'''


def normalize_lean_text(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized = "\n".join(line.rstrip() for line in lines).strip("\n")
    return _TEMP_LEAN_PATH.sub("<temp>", normalized)


class LeanVerifier:
    """Independent subprocess verifier for the pinned Lean project."""

    def __init__(
        self,
        project_root: Path,
        *,
        timeout_seconds: float = 10.0,
        max_output_bytes: int = 1_048_576,
        lake_executable: str = "lake",
    ) -> None:
        self.project_root = Path(project_root)
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_bytes = max_output_bytes
        self.lake_executable = lake_executable

    def initial_state(
        self, problem: Problem, *, timeout_seconds: float | None = None
    ) -> LeanResult:
        return self._state_after(problem, (), timeout_seconds=timeout_seconds)

    def try_tactic(
        self,
        problem: Problem,
        prefix: tuple[str, ...],
        candidate: str,
        *,
        timeout_seconds: float | None = None,
    ) -> LeanResult:
        invalid = self._validate_tactics(prefix + (candidate,))
        if invalid is not None:
            return invalid
        return self._state_after(
            problem, prefix + (candidate,), timeout_seconds=timeout_seconds
        )

    def _state_after(
        self,
        problem: Problem,
        tactics: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> LeanResult:
        invalid = self._validate_tactics(tactics)
        if invalid is not None:
            return invalid
        source, trace_line = self._render_probe(problem, tactics)
        local_deadline = (
            None if timeout_seconds is None else time.monotonic() + timeout_seconds
        )
        run = self._run_lean(source, timeout_seconds=timeout_seconds)
        if run.timed_out:
            return self._result(LeanStatus.TIMEOUT, False, None, run)
        if run.internal_error:
            return self._result(LeanStatus.INTERNAL_ERROR, False, None, run)
        errors = [item for item in run.diagnostics if item.severity == "error"]
        traces = [
            item
            for item in run.diagnostics
            if item.kind == "trace" and item.line == trace_line
        ]
        structure_diagnostics = [
            item for item in run.diagnostics if _PROOF_STRUCTURE_MARKER in item.message
        ]
        if not errors and run.exit_code == 0 and len(traces) == 1 and not traces[0].message.strip():
            remaining = (
                None if local_deadline is None else local_deadline - time.monotonic()
            )
            if remaining is not None and remaining <= 0:
                return LeanResult(
                    LeanStatus.TIMEOUT, False, None, ("theorem timeout",), None, run.duration_ms
                )
            return self._verify_complete(problem, tactics, timeout_seconds=remaining)
        if errors or run.exit_code != 0 or len(traces) != 1 or not traces[0].message.strip():
            return self._result(LeanStatus.REJECTED, False, None, run)
        if not any(item.kind == "hasSorry" for item in run.diagnostics):
            altered = _LeanRun(
                run.exit_code,
                run.diagnostics
                + (_LeanDiagnostic("error", "protocol", "probe did not report generated sorry", None),),
                run.duration_ms,
                internal_error=True,
            )
            return self._result(LeanStatus.INTERNAL_ERROR, False, None, altered)
        if len(structure_diagnostics) != 1:
            altered = _LeanRun(
                run.exit_code,
                run.diagnostics + (
                    _LeanDiagnostic(
                        "error",
                        "protocol",
                        f"protocol: expected one {_PROOF_STRUCTURE_MARKER} marker",
                        None,
                    ),
                ),
                run.duration_ms,
                internal_error=True,
            )
            return self._result(LeanStatus.INTERNAL_ERROR, False, None, altered)
        marker_text = structure_diagnostics[0].message
        encoded = marker_text.split(_PROOF_STRUCTURE_MARKER, 1)[1].strip()
        try:
            payload = json.loads(encoded)
            signature = parse_proof_signature(payload)
        except (json.JSONDecodeError, ModelOutputError, TypeError, ValueError) as error:
            altered = _LeanRun(
                run.exit_code,
                run.diagnostics + (
                    _LeanDiagnostic(
                        "error", "protocol", f"protocol: invalid proof structure marker: {error}", None
                    ),
                ),
                run.duration_ms,
                internal_error=True,
            )
            return self._result(LeanStatus.INTERNAL_ERROR, False, None, altered)
        return self._result(
            LeanStatus.OPEN,
            True,
            normalize_lean_text(traces[0].message),
            run,
            signature=signature,
        )

    def _verify_complete(
        self,
        problem: Problem,
        tactics: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> LeanResult:
        run = self._run_lean(
            self._render_final(problem, tactics), timeout_seconds=timeout_seconds
        )
        if run.timed_out:
            return self._result(LeanStatus.TIMEOUT, False, None, run)
        if run.internal_error:
            return self._result(LeanStatus.INTERNAL_ERROR, False, None, run)
        has_error = any(item.severity == "error" for item in run.diagnostics)
        has_sorry = any(item.kind == "hasSorry" for item in run.diagnostics)
        if run.exit_code == 0 and not has_error and not has_sorry:
            return self._result(LeanStatus.COMPLETE, True, None, run)
        return self._result(LeanStatus.REJECTED, False, None, run)

    @staticmethod
    def _validate_tactics(tactics: tuple[str, ...]) -> LeanResult | None:
        for tactic in tactics:
            if (
                not isinstance(tactic, str)
                or not tactic.strip()
                or any(character in tactic for character in "\r\n\x00")
                or len(tactic) > 4096
            ):
                return LeanResult(
                    LeanStatus.REJECTED,
                    False,
                    None,
                    ("tactic must be one nonblank line of at most 4096 characters",),
                    None,
                    0,
                )
        return None

    @staticmethod
    def _render_probe(problem: Problem, tactics: tuple[str, ...]) -> tuple[str, int]:
        lines = LEAN_STRUCTURE_PRELUDE.rstrip().splitlines() + ["", problem.theorem_header()]
        lines.extend(f"  {tactic}" for tactic in tactics)
        lines.append("  trace_proof_structure")
        trace_line = len(lines) + 1
        lines.append("  trace_state")
        lines.append("  all_goals sorry")
        return "\n".join(lines) + "\n", trace_line

    @staticmethod
    def _render_final(problem: Problem, tactics: tuple[str, ...]) -> str:
        lines = [problem.theorem_header()]
        lines.extend(f"  {tactic}" for tactic in tactics)
        return "\n".join(lines) + "\n"

    def _run_lean(
        self, source: str, *, timeout_seconds: float | None = None
    ) -> _LeanRun:
        started = time.monotonic_ns()
        effective_timeout = self.timeout_seconds
        if timeout_seconds is not None:
            effective_timeout = min(effective_timeout, max(float(timeout_seconds), 0.001))
        source_path: Path | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="standalone-lean-") as directory:
                source_path = Path(directory) / "Current.lean"
                source_path.write_text(source, encoding="utf-8")
                environment = {
                    **os.environ,
                    "ELAN_TOOLCHAIN": "leanprover/lean4:v4.29.1",
                }
                completed = subprocess.run(
                    [self.lake_executable, "env", "lean", "--json", str(source_path)],
                    cwd=self.project_root,
                    capture_output=True,
                    text=False,
                    timeout=effective_timeout,
                    check=False,
                    env=environment,
                )
        except subprocess.TimeoutExpired:
            duration = (time.monotonic_ns() - started) // 1_000_000
            return _LeanRun(
                None,
                (_LeanDiagnostic("error", "timeout", f"Lean exceeded {effective_timeout:g} seconds", None),),
                duration,
                timed_out=True,
            )
        except (OSError, ValueError) as error:
            duration = (time.monotonic_ns() - started) // 1_000_000
            return _LeanRun(
                None,
                (_LeanDiagnostic("error", "internal", normalize_lean_text(str(error)), None),),
                duration,
                internal_error=True,
            )
        duration = (time.monotonic_ns() - started) // 1_000_000
        raw = completed.stdout + completed.stderr
        if len(raw) > self.max_output_bytes:
            return _LeanRun(
                completed.returncode,
                (_LeanDiagnostic("error", "output_limit", "Lean output exceeded configured bytes", None),),
                duration,
                internal_error=True,
            )
        diagnostics = self._parse_diagnostics(raw, source_path)
        return _LeanRun(completed.returncode, diagnostics, duration)

    @staticmethod
    def _parse_diagnostics(raw: bytes, source_path: Path | None) -> tuple[_LeanDiagnostic, ...]:
        diagnostics: list[_LeanDiagnostic] = []
        source_text = str(source_path) if source_path is not None else None
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise TypeError("diagnostic is not an object")
                position = item.get("pos") if isinstance(item.get("pos"), dict) else {}
                data = item.get("data", "")
                message = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, sort_keys=True)
                if source_text:
                    message = message.replace(source_text, "<temp>")
                diagnostics.append(
                    _LeanDiagnostic(
                        str(item.get("severity", "error")),
                        str(item["kind"]) if item.get("kind") is not None else None,
                        normalize_lean_text(message),
                        position.get("line") if isinstance(position.get("line"), int) else None,
                    )
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                message = line.replace(source_text, "<temp>") if source_text else line
                diagnostics.append(_LeanDiagnostic("error", "protocol", normalize_lean_text(message), None))
        return tuple(diagnostics)

    @staticmethod
    def _result(
        status: LeanStatus,
        accepted: bool,
        state: str | None,
        run: _LeanRun,
        *,
        signature: ProofSignature | None = None,
    ) -> LeanResult:
        messages = tuple(item.message for item in run.diagnostics if item.message.strip())
        return LeanResult(
            status, accepted, state, messages, run.exit_code, run.duration_ms, signature
        )


@dataclass(frozen=True, slots=True)
class SearchLimits:
    max_expansions: int
    max_verifier_calls: int
    theorem_timeout_seconds: float
    top_k: int = 8

    def __post_init__(self) -> None:
        for value, field in (
            (self.max_expansions, "max_expansions"),
            (self.max_verifier_calls, "max_verifier_calls"),
            (self.top_k, "top_k"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ExperimentError(f"{field} must be a positive integer")
        if self.theorem_timeout_seconds <= 0:
            raise ExperimentError("theorem_timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    problem_id: str
    condition: str
    seed: int
    state_fingerprint: str
    prefix: tuple[str, ...]
    candidates: tuple[str, ...]
    ordered_indices: tuple[int, ...]
    candidate: str
    disposition: str
    successor_fingerprint: str | None
    diagnostics: tuple[str, ...]
    retrieval_hits: tuple[str, ...] = ()
    retrieval_domains: tuple[str, ...] = ()
    signature_fingerprint: str = ""
    signature_version: str = ""
    proof_rendering_hash: str = ""
    structural_shortlist_ids: tuple[str, ...] = ()
    retrieval_compatibility: tuple[Mapping[str, Any], ...] = ()
    pair_scores: tuple[Mapping[str, Any], ...] = ()
    tactic_scores: tuple[Mapping[str, Any], ...] = ()
    fallback_reason: str | None = None
    operation_seeds: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TrialResult:
    problem_id: str
    condition: str
    seed: int
    status: str
    solved: bool
    tactics: tuple[str, ...]
    expansion_count: int
    verifier_call_count: int
    rejected_count: int
    repeated_state_count: int
    novel_state_count: int
    failure: str | None
    elapsed_seconds: float
    reference_coverage: bool = False
    exact_reference_overlap: int = 0


def state_fingerprint(problem: Problem, state: str) -> str:
    return sha256_text(f"{problem.id}\x1f{normalize_lean_text(state)}")[:24]


class SearchRanker(Protocol):
    def rank(
        self,
        condition: Condition,
        problem: Problem,
        state: str,
        signature: ProofSignature,
        accepted: tuple[str, ...],
        failed: tuple[str, ...],
        candidates: tuple[str, ...],
        seed: int,
        deadline: float | None = None,
    ) -> RankingDecision: ...


def _call_with_remaining(call: Callable[..., Any], *args: Any, deadline: float) -> Any:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("theorem timeout")
    parameters = inspect.signature(call).parameters
    if "timeout_seconds" in parameters:
        result = call(*args, timeout_seconds=remaining)
    else:
        result = call(*args)
    if time.monotonic() >= deadline:
        raise TimeoutError("theorem timeout")
    return result


def _attempt_metadata(
    decision: RankingDecision,
    problem: Problem,
    state: str,
    seed: int,
    condition: Condition,
) -> tuple[Mapping[str, Any], Mapping[str, int]]:
    metadata = {
        "signature_fingerprint": proof_signature_fingerprint(decision.signature),
        "signature_version": decision.signature.version,
        "proof_rendering_hash": sha256_text(decision.proof_rendering),
        "structural_shortlist_ids": decision.structural_shortlist_ids,
        "retrieval_compatibility": tuple(
            {
                "transition_id": hit.item.transition.id,
                **_jsonable(hit.structural),
            }
            for hit in decision.hits
        ),
        "pair_scores": tuple(_jsonable(score) for score in decision.pair_scores),
        "tactic_scores": tuple(_jsonable(score) for score in decision.tactic_scores),
        "fallback_reason": decision.fallback_reason,
    }
    state_hash = sha256_text(state)
    return metadata, {
        "candidate": derived_seed(seed, problem.id, state_hash, "candidates"),
        "random_retrieval": derived_seed(seed, problem.id, state_hash, "random-retrieval"),
    }


def search_problem(
    problem: Problem,
    condition: Condition,
    seed: int,
    pool_store: Any,
    ranker: SearchRanker,
    verifier: Any,
    limits: SearchLimits,
    attempt_sink: Callable[[AttemptRecord], Any],
) -> TrialResult:
    started = time.monotonic()
    deadline = started + limits.theorem_timeout_seconds
    try:
        initial = _call_with_remaining(verifier.initial_state, problem, deadline=deadline)
    except TimeoutError:
        return TrialResult(
            problem.id, condition.value, seed, "timeout", False, (), 0, 0, 0, 0, 0,
            "theorem timeout", time.monotonic() - started,
        )
    if initial.status is not LeanStatus.OPEN or not initial.state:
        return TrialResult(
            problem.id,
            condition.value,
            seed,
            "initialization_failed",
            False,
            (),
            0,
            0,
            0,
            0,
            0,
            "; ".join(initial.diagnostics),
            time.monotonic() - started,
        )

    insertion_order = 0
    initial_state = normalize_lean_text(initial.state)
    initial_fingerprint = state_fingerprint(problem, initial_state)
    frontier: list[tuple[int, int, str, ProofSignature, tuple[str, ...]]] = [
        (0, insertion_order, initial_state, initial.signature, ())
    ]
    seen = {initial_fingerprint}
    failed_by_state: dict[str, list[str]] = {}
    expansions = 0
    verifier_calls = 0
    rejected = 0
    repeated = 0
    novel = 0
    status = ""

    while frontier:
        if time.monotonic() >= deadline:
            status = "timeout"
            failure = "theorem timeout"
            break
        if expansions >= limits.max_expansions or verifier_calls >= limits.max_verifier_calls:
            status = "budget_exhausted"
            failure = "search budget exhausted"
            break
        _, _, state, signature, prefix = heapq.heappop(frontier)
        fingerprint = state_fingerprint(problem, state)
        expansions += 1
        failed = tuple(failed_by_state.get(fingerprint, ()))
        try:
            pool_started = time.monotonic()
            candidates = pool_store.get(
                problem,
                state,
                prefix,
                failed,
                seed=seed,
                top_k=limits.top_k,
            )
            deadline += time.monotonic() - pool_started
            decision = ranker.rank(
                condition,
                problem,
                state,
                signature,
                prefix,
                failed,
                candidates,
                seed,
                deadline=deadline,
            )
        except TimeoutError as error:
            if time.monotonic() >= deadline or str(error) == "theorem timeout":
                status = "timeout"
                failure = "theorem timeout"
            else:
                status = "model_failure"
                failure = f"TimeoutError: {error}"
            break
        except Exception as error:
            status = "model_failure"
            failure = f"{type(error).__name__}: {error}"
            break
        for candidate in decision.ordered_candidates:
            if time.monotonic() >= deadline:
                status = "timeout"
                failure = "theorem timeout"
                break
            if verifier_calls >= limits.max_verifier_calls:
                status = "budget_exhausted"
                failure = "search budget exhausted"
                break
            verifier_calls += 1
            try:
                result = _call_with_remaining(
                    verifier.try_tactic,
                    problem,
                    prefix,
                    candidate,
                    deadline=deadline,
                )
            except TimeoutError:
                status = "timeout"
                failure = "theorem timeout"
                break
            successor_fingerprint: str | None = None
            ranking_metadata, operation_seeds = _attempt_metadata(
                decision, problem, state, seed, condition
            )
            if result.status is LeanStatus.COMPLETE and result.accepted:
                disposition = "complete"
                attempt_sink(
                    AttemptRecord(
                        problem_id=problem.id,
                        condition=condition.value,
                        seed=seed,
                        state_fingerprint=fingerprint,
                        prefix=prefix,
                        candidates=tuple(candidates),
                        ordered_indices=tuple(decision.indices),
                        candidate=candidate,
                        disposition=disposition,
                        successor_fingerprint=None,
                        diagnostics=result.diagnostics,
                        retrieval_hits=tuple(
                            hit.item.transition.id for hit in decision.hits
                        ),
                        retrieval_domains=tuple(
                            hit.item.transition.domain for hit in decision.hits
                        ),
                        **ranking_metadata,
                        operation_seeds=operation_seeds,
                    )
                )
                tactics = prefix + (candidate,)
                return TrialResult(
                    problem.id,
                    condition.value,
                    seed,
                    "complete",
                    True,
                    tactics,
                    expansions,
                    verifier_calls,
                    rejected,
                    repeated,
                    novel,
                    None,
                    time.monotonic() - started,
                )
            if result.status is LeanStatus.OPEN and result.accepted and result.state:
                if result.signature is None:
                    status = "verifier_failure"
                    failure = "open Lean result omitted proof signature"
                    break
                successor = normalize_lean_text(result.state)
                successor_fingerprint = state_fingerprint(problem, successor)
                if successor_fingerprint in seen:
                    disposition = "repeated"
                    repeated += 1
                else:
                    disposition = "novel"
                    novel += 1
                    seen.add(successor_fingerprint)
                    insertion_order += 1
                    successor_prefix = prefix + (candidate,)
                    heapq.heappush(
                        frontier,
                        (
                            -len(successor_prefix),
                            insertion_order,
                            successor,
                            result.signature,
                            successor_prefix,
                        ),
                    )
            else:
                disposition = result.status.value
                rejected += 1
                failed_by_state.setdefault(fingerprint, []).append(candidate)
            attempt_sink(
                AttemptRecord(
                    problem_id=problem.id,
                    condition=condition.value,
                    seed=seed,
                    state_fingerprint=fingerprint,
                    prefix=prefix,
                    candidates=tuple(candidates),
                    ordered_indices=tuple(decision.indices),
                    candidate=candidate,
                    disposition=disposition,
                    successor_fingerprint=successor_fingerprint,
                    diagnostics=result.diagnostics,
                    retrieval_hits=tuple(
                        hit.item.transition.id for hit in decision.hits
                    ),
                    retrieval_domains=tuple(
                        hit.item.transition.domain for hit in decision.hits
                    ),
                    **ranking_metadata,
                    operation_seeds=operation_seeds,
                )
            )
            if result.status is LeanStatus.INTERNAL_ERROR:
                status = "verifier_failure"
                failure = "; ".join(result.diagnostics) or "Lean verifier internal error"
                break
            if result.status is LeanStatus.TIMEOUT:
                status = "timeout"
                failure = "theorem timeout"
                break
        if status in {"timeout", "budget_exhausted", "verifier_failure"}:
            break
    else:
        status = "exhausted"
        failure = "search frontier exhausted"

    return TrialResult(
        problem.id,
        condition.value,
        seed,
        status,
        False,
        (),
        expansions,
        verifier_calls,
        rejected,
        repeated,
        novel,
        failure,
        time.monotonic() - started,
    )


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    problems: tuple[Problem, ...]
    reference_proofs: Mapping[str, ReferenceProof]
    transitions: tuple[Transition, ...]
    conditions: tuple[Condition, ...]
    seeds: tuple[int, ...]
    output_dir: Path
    cache_dir: Path | None
    hidden_layer: int
    retrieval_count: int
    search_limits: SearchLimits
    model_name: str
    device: str = "auto"
    max_new_tokens: int = 256
    model_timeout_seconds: float = 60.0
    input_manifest: Mapping[str, Any] = field(default_factory=dict)
    lean_configuration: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        problems = tuple(self.problems)
        references = dict(self.reference_proofs)
        transitions = tuple(self.transitions)
        conditions = tuple(self.conditions)
        seeds = tuple(self.seeds)
        if not problems or len({item.id for item in problems}) != len(problems):
            raise ExperimentError("experiment problems must be nonempty and unique")
        if set(references) != {item.id for item in problems}:
            raise ExperimentError("experiment reference coverage must match problems")
        if not conditions or Condition.NONE not in conditions or len(set(conditions)) != len(conditions):
            raise ExperimentError("experiment conditions must be unique and include none")
        if not seeds or any(isinstance(item, bool) or not isinstance(item, int) for item in seeds):
            raise ExperimentError("experiment seeds must be nonempty integers")
        if any(condition is not Condition.NONE for condition in conditions) and not transitions:
            raise ExperimentError("retrieval conditions require transitions")
        if self.retrieval_count <= 0:
            raise ExperimentError("retrieval_count must be positive")
        if any(condition is not Condition.NONE for condition in conditions) and self.retrieval_count > len(transitions):
            raise ExperimentError("retrieval_count exceeds transition count")
        _nonblank(self.model_name, "model_name")
        object.__setattr__(self, "problems", problems)
        object.__setattr__(self, "reference_proofs", MappingProxyType(references))
        object.__setattr__(self, "transitions", transitions)
        object.__setattr__(self, "conditions", conditions)
        object.__setattr__(self, "seeds", seeds)
        object.__setattr__(self, "output_dir", Path(self.output_dir))
        object.__setattr__(self, "cache_dir", None if self.cache_dir is None else Path(self.cache_dir))
        object.__setattr__(self, "input_manifest", MappingProxyType(dict(self.input_manifest)))
        object.__setattr__(self, "lean_configuration", MappingProxyType(dict(self.lean_configuration)))


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


class JsonlSink:
    """Append-only JSONL writer that flushes every complete row."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle = self.path.open("x", encoding="utf-8", newline="\n")

    def write(self, value: Any) -> None:
        self._handle.write(json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True))
        self._handle.write("\n")
        self._handle.flush()

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> JsonlSink:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def summarize_trials(trials: Sequence[TrialResult]) -> dict[str, object]:
    normalized = tuple(trials)
    by_key: dict[tuple[str, int, str], TrialResult] = {}
    for item in normalized:
        key = (item.problem_id, item.seed, item.condition)
        if key in by_key:
            raise ExperimentError(f"duplicate trial result {key}")
        by_key[key] = item
    baseline = {
        (item.problem_id, item.seed): item
        for item in normalized
        if item.condition == Condition.NONE.value
    }
    if not baseline:
        raise ExperimentError("trial summary requires no-retrieval baselines")
    seeds = {seed for _, seed in baseline}

    def label(key: tuple[str, int]) -> str:
        problem_id, seed = key
        return problem_id if len(seeds) == 1 else f"{problem_id}@{seed}"

    baseline_solved = sorted(label(key) for key, item in baseline.items() if item.solved)
    baseline_unsolved = sorted(label(key) for key, item in baseline.items() if not item.solved)
    conditions = sorted({item.condition for item in normalized})
    aggregates: dict[str, object] = {}
    for condition in conditions:
        selected = [item for item in normalized if item.condition == condition]
        solved_count = sum(item.solved for item in selected)
        aggregates[condition] = {
            "trial_count": len(selected),
            "solved_count": solved_count,
            "completion_rate": solved_count / len(selected),
            "verifier_call_count": sum(item.verifier_call_count for item in selected),
            "expansion_count": sum(item.expansion_count for item in selected),
        }

    comparisons: dict[str, object] = {}
    for condition in (Condition.RANDOM.value, Condition.ANALOGY.value):
        target = {
            (item.problem_id, item.seed): item
            for item in normalized
            if item.condition == condition
        }
        if not target:
            continue
        if set(target) != set(baseline):
            raise ExperimentError(f"condition {condition} is not paired with every baseline")
        newly_solved = sorted(
            label(key)
            for key in baseline
            if not baseline[key].solved and target[key].solved
        )
        regressed = sorted(
            label(key)
            for key in baseline
            if baseline[key].solved and not target[key].solved
        )
        target_solved = sum(item.solved for item in target.values())
        baseline_count = sum(item.solved for item in baseline.values())
        comparisons[f"{condition}_vs_none"] = {
            "completion_delta": target_solved - baseline_count,
            "completion_rate_delta": (target_solved - baseline_count) / len(baseline),
            "newly_solved": newly_solved,
            "regressed": regressed,
            "verifier_call_delta": sum(item.verifier_call_count for item in target.values())
            - sum(item.verifier_call_count for item in baseline.values()),
            "expansion_delta": sum(item.expansion_count for item in target.values())
            - sum(item.expansion_count for item in baseline.values()),
            "baseline_solved_completion_delta": sum(
                target[key].solved - baseline[key].solved
                for key in baseline
                if baseline[key].solved
            ),
            "baseline_unsolved_completion_delta": sum(
                target[key].solved - baseline[key].solved
                for key in baseline
                if not baseline[key].solved
            ),
        }
    operational_statuses = {"initialization_failed", "model_failure", "verifier_failure"}
    operational_failures = [
        {
            "problem_id": item.problem_id,
            "condition": item.condition,
            "seed": item.seed,
            "status": item.status,
            "failure": item.failure,
        }
        for item in normalized
        if item.status in operational_statuses
    ]
    return {
        "run_complete": not operational_failures,
        "operational_failures": operational_failures,
        "trial_count": len(normalized),
        "subgroups": {
            "baseline_solved": baseline_solved,
            "baseline_unsolved": baseline_unsolved,
        },
        "conditions": aggregates,
        "comparisons": comparisons,
    }


class ConditionRanker(HybridConditionRanker):
    """Compatibility constructor for callers using the pre-hybrid ranker name."""

    def __init__(
        self,
        model: TextModel,
        index: tuple[IndexedTransformation, ...],
        retrieval_count: int,
        layer: int,
    ) -> None:
        del layer
        super().__init__(
            model=model,
            index=index,
            retrieval_count=retrieval_count,
            structural_shortlist_size=max(len(index), 1),
            pair_margin_threshold=0.0,
        )


def _run_config_payload(
    config: ExperimentConfig,
    *,
    complete: bool,
    model_revision: str | None = None,
    operational_failures: Sequence[Mapping[str, Any]] = (),
    transformation_index: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "run_complete": complete,
        "problems": [_jsonable(item) for item in config.problems],
        "conditions": [item.value for item in config.conditions],
        "seeds": list(config.seeds),
        "transition_count": len(config.transitions),
        "transition_counts": {
            domain: sum(item.domain == domain for item in config.transitions)
            for domain in ("poetry", "physics")
        },
        "inputs": _jsonable(config.input_manifest),
        "lean": _jsonable(config.lean_configuration),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
        },
        "model": {
            "name": config.model_name,
            "resolved_revision": model_revision,
            "device": config.device,
            "hidden_layer": config.hidden_layer,
            "max_new_tokens": config.max_new_tokens,
            "timeout_seconds": config.model_timeout_seconds,
            "adaptive_policy": {
                "difficulty_1_2": {"thinking": False},
                "difficulty_3_4": {
                    "thinking": True,
                    "reasoning_token_fraction": 0.5,
                    "reasoning_timeout_multiplier": 2,
                    "answer_timeout_multiplier": 2,
                },
                "difficulty_5_plus": {
                    "thinking": True,
                    "reasoning_token_fraction": 1.0,
                    "reasoning_timeout_multiplier": 3,
                    "answer_timeout_multiplier": 2,
                },
                "answer_token_multiplier": 1,
                "separate_reasoning_and_answer_phases": True,
                "structured_early_stop": True,
            },
        },
        "retrieval_count": config.retrieval_count,
        "transformation_index": dict(
            transformation_index
            or {
                "input_count": len(config.transitions),
                "valid_count": None,
                "exclusions": {},
            }
        ),
        "search_limits": _jsonable(config.search_limits),
        "prompt_versions": dict(PROMPT_VERSIONS),
        "operation_seed_scheme": "sha256(base_seed, problem_id, state_sha256, operation)-u64-v1",
        "operational_failures": list(operational_failures),
    }


def run_experiment(
    config: ExperimentConfig,
    model_factory: Callable[[ExperimentConfig], TextModel],
    verifier_factory: Callable[[ExperimentConfig], Any],
) -> Path:
    output = config.output_dir
    if output.exists():
        raise ExperimentError(f"output directory already exists: {output}")
    output.mkdir(parents=True, exist_ok=False)
    _atomic_json(output / "config.json", _run_config_payload(config, complete=False))
    model = model_factory(config)
    verifier = verifier_factory(config)
    trials: list[TrialResult] = []
    index_stats: dict[str, Any] = {
        "input_count": len(config.transitions),
        "valid_count": 0,
        "exclusions": {},
    }
    condition_order = [Condition.NONE] + [
        condition for condition in config.conditions if condition is not Condition.NONE
    ]

    with (
        JsonlSink(output / "transitions.jsonl") as transition_sink,
        JsonlSink(output / "attempts.jsonl") as attempt_sink,
        JsonlSink(output / "trials.jsonl") as trial_sink,
        JsonlSink(output / "generations.jsonl") as generation_sink,
    ):
        set_generation_sink = getattr(model, "set_generation_sink", None)
        if callable(set_generation_sink):
            set_generation_sink(generation_sink.write)
        set_scoring_sink = getattr(model, "set_scoring_sink", None)
        if callable(set_scoring_sink):
            set_scoring_sink(generation_sink.write)
        needs_index = any(condition is not Condition.NONE for condition in config.conditions)
        if needs_index:
            index, exclusions = build_transformation_index(
                config.transitions,
                model,
                config.cache_dir,
                config.hidden_layer,
                capture_diagnostic_embeddings=False,
            )
            index_stats = {
                "input_count": len(config.transitions),
                "valid_count": len(index),
                "exclusions": dict(exclusions),
            }
        else:
            index = ()
        pool_store = CandidatePoolStore(model)
        ranker = ConditionRanker(model, index, config.retrieval_count, config.hidden_layer)
        for item in index:
            transition_sink.write(
                {
                    "id": item.transition.id,
                    "domain": item.transition.domain,
                    "before": item.transition.before,
                    "after": item.transition.after,
                    "provenance": dict(item.transition.provenance),
                    "record": _jsonable(item.record),
                    "cache_key": item.cache_key,
                    "diagnostic_embedding": (
                        None if item.diagnostic_embedding is None else {
                            "used_for_retrieval": False,
                            "model_revision": item.diagnostic_embedding.model_revision,
                            "layer": item.diagnostic_embedding.layer,
                            "token_index": item.diagnostic_embedding.token_index,
                            "dimension": item.diagnostic_embedding.dimension,
                            "text_hash": item.diagnostic_embedding.text_hash,
                        }
                    ),
                }
            )
        for seed in config.seeds:
            for condition in condition_order:
                for problem in config.problems:
                    trial_attempts: list[AttemptRecord] = []

                    def record_attempt(record: AttemptRecord) -> None:
                        trial_attempts.append(record)
                        attempt_sink.write(record)

                    result = search_problem(
                        problem,
                        condition,
                        seed,
                        pool_store,
                        ranker,
                        verifier,
                        config.search_limits,
                        record_attempt,
                    )
                    reference = config.reference_proofs[problem.id]
                    candidate_coverage = any(
                        any(tactic in attempt.candidates for tactic in reference.tactics)
                        for attempt in trial_attempts
                    )
                    exact_overlap = sum(
                        index < len(reference.tactics) and tactic == reference.tactics[index]
                        for index, tactic in enumerate(result.tactics)
                    )
                    finalized = replace(
                        result,
                        reference_coverage=candidate_coverage,
                        exact_reference_overlap=exact_overlap,
                    )
                    trial_sink.write(finalized)
                    trials.append(finalized)
    expected = {
        (problem.id, seed, condition.value)
        for problem in config.problems
        for seed in config.seeds
        for condition in config.conditions
    }
    actual = {(item.problem_id, item.seed, item.condition) for item in trials}
    if actual != expected or len(trials) != len(expected):
        raise ExperimentError("run did not produce exactly one result per requested trial")
    summary = summarize_trials(trials)
    summary["transformation_index"] = index_stats
    _atomic_json(output / "summary.json", summary)
    failures = summary["operational_failures"]
    complete = bool(summary["run_complete"])
    _atomic_json(
        output / "config.json",
        _run_config_payload(
            config,
            complete=complete,
            model_revision=model.model_revision,
            operational_failures=failures,
            transformation_index=index_stats,
        ),
    )
    if not complete:
        raise ExperimentRunIncomplete(
            f"experiment preserved {len(failures)} operational failure(s) in {output}"
        )
    return output


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone poetry/physics retrieval experiment for Lean tactic recovery"
    )
    parser.add_argument("--dry-run", action="store_true", help="validate inputs without loading Qwen")
    parser.add_argument("--problems", type=Path, default=REPOSITORY_ROOT / "src/analogy/lean_gym/corpus/problems.json")
    parser.add_argument(
        "--reference-proofs",
        type=Path,
        default=REPOSITORY_ROOT / "src/analogy/lean_gym/corpus/reference_proofs.json",
    )
    parser.add_argument(
        "--lean-project",
        type=Path,
        default=REPOSITORY_ROOT / "src/analogy/lean_gym/lean_project",
    )
    parser.add_argument(
        "--poetry",
        type=Path,
        default=Path("/tmp/analogy-data/gutenberg-poetry-v001.ndjson.gz"),
    )
    parser.add_argument(
        "--physics",
        type=Path,
        default=Path("/tmp/analogy-data/trip-ClozeTrain.jsonl"),
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path("/tmp/analogy-data/standalone-cache"))
    parser.add_argument("--problem", action="append", dest="problem_ids")
    parser.add_argument("--condition", action="append", choices=[item.value for item in Condition])
    parser.add_argument("--seed", action="append", type=int)
    parser.add_argument("--poetry-limit", type=int, default=64)
    parser.add_argument("--physics-limit", type=int, default=64)
    parser.add_argument("--retrieval-count", type=int, default=4)
    parser.add_argument("--candidate-count", type=int, default=8)
    parser.add_argument("--max-expansions", type=int, default=32)
    parser.add_argument("--max-verifier-calls", type=int, default=256)
    parser.add_argument("--theorem-timeout", type=float, default=300.0)
    parser.add_argument("--lean-timeout", type=float, default=10.0)
    parser.add_argument("--lake-executable", default="lake")
    parser.add_argument("--model-name", default="Qwen/Qwen3-8B")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--hidden-layer", type=int, default=-1)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--model-timeout", type=float, default=60.0)
    return parser


def _require_positive(value: int | float, field: str) -> None:
    if isinstance(value, bool) or value <= 0:
        raise ExperimentError(f"{field} must be positive")


def _file_manifest(path: Path) -> dict[str, str]:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(Path(path).resolve()), "sha256": digest.hexdigest()}


def _probe_lean(project: Path, lake_path: str, timeout_seconds: float) -> dict[str, Any]:
    environment = {**os.environ, "ELAN_TOOLCHAIN": "leanprover/lean4:v4.29.1"}
    try:
        completed = subprocess.run(
            [lake_path, "env", "lean", "--version"],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ExperimentError(f"Lean probe failed: {error}") from error
    version = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0 or not version:
        raise ExperimentError(
            f"Lean probe failed with exit code {completed.returncode}: {version or '(no output)'}"
        )
    return {
        "project": str(Path(project).resolve()),
        "lake_executable": str(Path(lake_path).resolve()),
        "toolchain": environment["ELAN_TOOLCHAIN"],
        "version": version,
        "per_call_timeout_seconds": timeout_seconds,
    }


def _resolve_cli(args: argparse.Namespace) -> tuple[ExperimentConfig, Path, str, dict[str, int]]:
    for value, field in (
        (args.poetry_limit, "poetry-limit"),
        (args.physics_limit, "physics-limit"),
        (args.retrieval_count, "retrieval-count"),
        (args.candidate_count, "candidate-count"),
        (args.max_expansions, "max-expansions"),
        (args.max_verifier_calls, "max-verifier-calls"),
        (args.theorem_timeout, "theorem-timeout"),
        (args.lean_timeout, "lean-timeout"),
        (args.max_new_tokens, "max-new-tokens"),
        (args.model_timeout, "model-timeout"),
    ):
        _require_positive(value, field)
    if args.problem_ids and len(set(args.problem_ids)) != len(args.problem_ids):
        raise ExperimentError("problem selections must be unique")
    requested_conditions = args.condition or [item.value for item in Condition]
    if len(set(requested_conditions)) != len(requested_conditions):
        raise ExperimentError("condition selections must be unique")
    conditions = tuple(Condition(item) for item in requested_conditions)
    if Condition.NONE not in conditions:
        raise ExperimentError("conditions must include none for paired evaluation")
    if (
        any(condition is not Condition.NONE for condition in conditions)
        and args.retrieval_count > args.poetry_limit + args.physics_limit
    ):
        raise ExperimentError("retrieval-count exceeds the configured transition index")
    seeds = tuple(args.seed or [0])
    if len(set(seeds)) != len(seeds):
        raise ExperimentError("seed selections must be unique")
    if args.output_dir is not None and Path(args.output_dir).exists():
        raise ExperimentError(f"output directory already exists: {args.output_dir}")
    for path, field in (
        (args.problems, "problems"),
        (args.reference_proofs, "reference-proofs"),
    ):
        if not Path(path).is_file():
            raise ExperimentError(f"{field} file does not exist: {path}")
    needs_retrieval = any(condition is not Condition.NONE for condition in conditions)
    if needs_retrieval:
        for path, field in ((args.poetry, "poetry"), (args.physics, "physics")):
            if not Path(path).is_file():
                raise ExperimentError(f"{field} file does not exist: {path}")
    if not Path(args.lean_project).is_dir():
        raise ExperimentError(f"Lean project does not exist: {args.lean_project}")
    lake_path = shutil.which(args.lake_executable)
    if lake_path is None:
        raise ExperimentError(f"lake executable is unavailable: {args.lake_executable}")
    lean_configuration = _probe_lean(Path(args.lean_project), lake_path, args.lean_timeout)
    problems, references = load_benchmark(args.problems, args.reference_proofs)
    by_id = {item.id: item for item in problems}
    if args.problem_ids:
        unknown = sorted(set(args.problem_ids) - by_id.keys())
        if unknown:
            raise ExperimentError(f"unknown problem IDs: {unknown}")
        problems = tuple(by_id[item] for item in args.problem_ids)
        references = {item.id: references[item.id] for item in problems}
    poetry = (
        _reservoir_sample(
            iter_poetry_transitions(args.poetry),
            args.poetry_limit,
            random.Random(f"{seeds[0]}:poetry"),
        )
        if needs_retrieval
        else ()
    )
    physics = (
        _reservoir_sample(
            iter_physics_transitions(args.physics),
            args.physics_limit,
            random.Random(f"{seeds[0]}:physics"),
        )
        if needs_retrieval
        else ()
    )
    transitions = poetry + physics
    if any(condition is not Condition.NONE for condition in conditions) and args.retrieval_count > len(transitions):
        raise ExperimentError("retrieval-count exceeds the eligible transition index")
    output = args.output_dir or (
        REPOSITORY_ROOT / "results" / f"standalone-{uuid.uuid4().hex}"
    )
    if Path(output).exists():
        raise ExperimentError(f"output directory already exists: {output}")
    limits = SearchLimits(
        args.max_expansions,
        args.max_verifier_calls,
        args.theorem_timeout,
        top_k=args.candidate_count,
    )
    config = ExperimentConfig(
        problems=problems,
        reference_proofs=references,
        transitions=transitions,
        conditions=conditions,
        seeds=seeds,
        output_dir=output,
        cache_dir=args.cache_dir,
        hidden_layer=args.hidden_layer,
        retrieval_count=args.retrieval_count,
        search_limits=limits,
        model_name=args.model_name,
        device=args.device,
        max_new_tokens=args.max_new_tokens,
        model_timeout_seconds=args.model_timeout,
        input_manifest={
            "problems": _file_manifest(args.problems),
            "reference_proofs": _file_manifest(args.reference_proofs),
            **(
                {
                    "poetry": _file_manifest(args.poetry),
                    "physics": _file_manifest(args.physics),
                }
                if needs_retrieval
                else {}
            ),
        },
        lean_configuration=lean_configuration,
    )
    counts = {"poetry": len(poetry), "physics": len(physics)}
    return config, Path(args.lean_project), lake_path, counts


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = False
    try:
        config, lean_project, lake_path, transition_counts = _resolve_cli(args)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "problem_count": len(config.problems),
                        "condition_count": len(config.conditions),
                        "seed_count": len(config.seeds),
                        "transition_counts": transition_counts,
                        "lean_available": True,
                        "model_loaded": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        started = True
        output = run_experiment(
            config,
            model_factory=lambda resolved: QwenModel.load(
                resolved.model_name,
                resolved.device,
                resolved.max_new_tokens,
                resolved.model_timeout_seconds,
            ),
            verifier_factory=lambda _: LeanVerifier(
                lean_project,
                timeout_seconds=args.lean_timeout,
                lake_executable=lake_path,
            ),
        )
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "output_dir": str(output),
                    "trial_count": summary["trial_count"],
                    "conditions": summary["conditions"],
                },
                sort_keys=True,
            )
        )
        return 0
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except (ExperimentError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1 if started else 2


def masked_problem_payload(problem: Problem) -> dict[str, str]:
    """Return the only benchmark fields allowed to enter inference prompts."""

    if not isinstance(problem, Problem):
        raise ExperimentError("problem must be a Problem")
    return {
        "problem_id": problem.id,
        "binders": problem.binders,
        "target": problem.target,
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExperimentError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise ExperimentError(f"{path} must contain a JSON object")
    return value


def load_benchmark(
    problem_path: Path,
    proof_path: Path,
) -> tuple[tuple[Problem, ...], dict[str, ReferenceProof]]:
    """Load the raw benchmark while keeping reference proofs separate."""

    problem_root = _read_json_object(Path(problem_path))
    proof_root = _read_json_object(Path(proof_path))
    if problem_root.get("schema_version") != 1 or proof_root.get("schema_version") != 1:
        raise ExperimentError("benchmark schema_version must be 1")
    raw_problems = problem_root.get("problems")
    raw_proofs = proof_root.get("proofs")
    if not isinstance(raw_problems, list) or not isinstance(raw_proofs, list):
        raise ExperimentError("benchmark problems and proofs must be arrays")

    problems: list[Problem] = []
    seen_problem_ids: set[str] = set()
    for index, raw in enumerate(raw_problems):
        if not isinstance(raw, dict):
            raise ExperimentError(f"problems[{index}] must be an object")
        try:
            problem = Problem(
                id=raw["id"],
                binders=raw["binders"],
                target=raw["target"],
                title=raw.get("title", ""),
                difficulty=raw["difficulty"],
            )
        except KeyError as error:
            raise ExperimentError(f"problems[{index}] is missing {error.args[0]}") from error
        if problem.id in seen_problem_ids:
            raise ExperimentError(f"duplicate problem id {problem.id!r}")
        seen_problem_ids.add(problem.id)
        problems.append(problem)

    proofs: dict[str, ReferenceProof] = {}
    for index, raw in enumerate(raw_proofs):
        if not isinstance(raw, dict):
            raise ExperimentError(f"proofs[{index}] must be an object")
        try:
            proof = ReferenceProof(raw["problem_id"], tuple(raw["tactics"]))
        except (KeyError, TypeError) as error:
            raise ExperimentError(f"proofs[{index}] is malformed") from error
        if proof.problem_id in proofs:
            raise ExperimentError(f"duplicate proof id {proof.problem_id!r}")
        proofs[proof.problem_id] = proof

    proof_ids = set(proofs)
    if seen_problem_ids != proof_ids:
        missing = sorted(seen_problem_ids - proof_ids)
        extra = sorted(proof_ids - seen_problem_ids)
        raise ExperimentError(f"reference coverage mismatch: missing={missing}, extra={extra}")
    return tuple(sorted(problems, key=lambda item: (item.difficulty, item.id))), proofs


def _transition_id(domain: str, source_id: str, before: str, after: str) -> str:
    content = json.dumps(
        [domain, source_id, before, after],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]


def iter_poetry_transitions(path: Path) -> Iterator[Transition]:
    """Yield adjacent nonblank lines without crossing Gutenberg works."""

    previous_gid: str | None = None
    previous_line: str | None = None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    previous_gid = None
                    previous_line = None
                    continue
                if not isinstance(raw, dict):
                    previous_gid = None
                    previous_line = None
                    continue
                gid = raw.get("gid")
                text = raw.get("s")
                if not isinstance(gid, str) or not gid.strip() or not isinstance(text, str) or not text.strip():
                    previous_gid = None
                    previous_line = None
                    continue
                text = " ".join(text.split())
                if gid == previous_gid and previous_line is not None and previous_line != text:
                    source_id = f"gutenberg:{gid}:{line_number - 1}-{line_number}"
                    yield Transition(
                        _transition_id("poetry", source_id, previous_line, text),
                        "poetry",
                        previous_line,
                        text,
                        {"gid": gid, "line_end": line_number, "source_id": source_id},
                    )
                previous_gid = gid
                previous_line = text
    except OSError as error:
        raise ExperimentError(f"cannot read poetry corpus {path}: {error}") from error


def iter_physics_transitions(path: Path) -> Iterator[Transition]:
    """Yield adjacent sentence transitions from every valid TRIP story."""

    try:
        handle = Path(path).open(encoding="utf-8")
    except OSError as error:
        raise ExperimentError(f"cannot read physics corpus {path}: {error}") from error
    with handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or not isinstance(record.get("stories"), list):
                continue
            example_id = record.get("example_id")
            for story_position, story in enumerate(record["stories"]):
                if not isinstance(story, dict) or not isinstance(story.get("sentences"), list):
                    continue
                sentences = [
                    " ".join(item.split())
                    for item in story["sentences"]
                    if isinstance(item, str) and item.strip()
                ]
                story_id = story.get("story_id", story_position)
                for sentence_index, (before, after) in enumerate(zip(sentences, sentences[1:])):
                    if before == after:
                        continue
                    source_id = f"trip:{example_id}:{story_id}:{sentence_index}-{sentence_index + 1}"
                    provenance = {
                        "source_id": source_id,
                        "line": line_number,
                        "example_id": example_id,
                        "story_id": story_id,
                        "plausible": story.get("plausible"),
                        "breakpoint": story.get("breakpoint", record.get("breakpoint")),
                        "confl_sents": story.get("confl_sents", record.get("confl_sents", [])),
                        "confl_pairs": story.get("confl_pairs", record.get("confl_pairs", [])),
                    }
                    yield Transition(
                        _transition_id("physics", source_id, before, after),
                        "physics",
                        before,
                        after,
                        provenance,
                    )


def _reservoir_sample(
    values: Iterable[Transition],
    count: int,
    rng: random.Random,
) -> tuple[Transition, ...]:
    reservoir: list[Transition] = []
    seen_ids: set[str] = set()
    eligible_count = 0
    for item in values:
        if not isinstance(item, Transition):
            raise ExperimentError("transition streams must contain Transition values")
        if item.id in seen_ids:
            continue
        seen_ids.add(item.id)
        eligible_count += 1
        if len(reservoir) < count:
            reservoir.append(item)
            continue
        replacement = rng.randrange(eligible_count)
        if replacement < count:
            reservoir[replacement] = item
    return tuple(reservoir)


def sample_transitions(
    poetry: Iterable[Transition],
    physics: Iterable[Transition],
    per_domain: int,
    seed: int,
) -> tuple[Transition, ...]:
    """Reservoir-sample a deterministic balanced index without full materialization."""

    if isinstance(per_domain, bool) or not isinstance(per_domain, int) or per_domain <= 0:
        raise ExperimentError("per_domain must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ExperimentError("seed must be an integer")
    poetry_sample = _reservoir_sample(poetry, per_domain, random.Random(f"{seed}:poetry"))
    physics_sample = _reservoir_sample(physics, per_domain, random.Random(f"{seed}:physics"))
    return poetry_sample + physics_sample


if __name__ == "__main__":
    raise SystemExit(main())

