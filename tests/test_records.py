from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from analogy.records import (
    AnalogicalTransfer,
    Embedding,
    OperatorComposition,
    OperatorRelation,
    OperatorTransformation,
    PseudoOperator,
    SymbolicState,
)
from analogy import (
    AnalogicalTransfer as PublicAnalogicalTransfer,
    Embedding as PublicEmbedding,
    OperatorComposition as PublicOperatorComposition,
    OperatorLibraryEntry,
    OperatorRelation as PublicOperatorRelation,
    OperatorTransformation as PublicOperatorTransformation,
    ProgramCandidate,
    PseudoOperator as PublicPseudoOperator,
    SymbolicState as PublicSymbolicState,
    VerificationEvidence,
    VerificationStatus,
    VerifiedProgram,
)


def test_embedding_normalizes_values_and_reports_dimension() -> None:
    embedding = Embedding("encoder-v1", [1, 2.5, -3])  # type: ignore[arg-type]

    assert embedding.values == (1.0, 2.5, -3.0)
    assert embedding.dimension == 3

    with pytest.raises(FrozenInstanceError):
        embedding.model_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("model_id", ["", "   "])
def test_embedding_rejects_blank_model_ids(model_id: str) -> None:
    with pytest.raises(ValueError, match="model_id"):
        Embedding(model_id, (1.0,))


@pytest.mark.parametrize(
    "values",
    [(), (True,), ("1",), (math.nan,), (math.inf,), (-math.inf,), (10**400,)],
)
def test_embedding_rejects_invalid_values(values: tuple[object, ...]) -> None:
    with pytest.raises(ValueError, match="values"):
        Embedding("encoder-v1", values)  # type: ignore[arg-type]


def test_embedding_consumes_a_generator_once() -> None:
    embedding = Embedding("encoder-v1", (value for value in range(3)))  # type: ignore[arg-type]

    assert embedding.values == (0.0, 1.0, 2.0)


def test_symbolic_state_normalizes_ordered_context() -> None:
    state = SymbolicState("state-1", "P → P", "lean4", ["P : Prop", "h : P"])  # type: ignore[arg-type]

    assert state.context == ("P : Prop", "h : P")


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("id", {"id": " "}),
        ("expression", {"expression": ""}),
        ("language", {"language": "\t"}),
        ("context", {"context": ("P : Prop", " ")}),
        ("context", {"context": "P : Prop"}),
    ],
)
def test_symbolic_state_rejects_blank_required_text(field: str, kwargs: dict[str, object]) -> None:
    arguments: dict[str, object] = {
        "id": "state-1",
        "expression": "P → P",
        "language": "lean4",
        "context": (),
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError, match=field):
        SymbolicState(**arguments)  # type: ignore[arg-type]


def test_operator_geometry_records_directed_references() -> None:
    operator_embedding = Embedding("operator-v1", (0.1, 0.2))
    relation_embedding = Embedding("relation-v1", (0.3,))

    operator = PseudoOperator("op-1", "state-a", "state-b", operator_embedding)
    relation = OperatorRelation("rel-1", "op-1", "op-2", relation_embedding)
    composition = OperatorComposition("op-1", "op-2", "op-3")
    transformation = OperatorTransformation("op-1", "rel-1", "op-2")
    analogy = AnalogicalTransfer("op-1", "op-2", "op-3", "op-4", 0.875)

    assert operator.source_state_id == "state-a"
    assert relation.target_operator_id == "op-2"
    assert composition.result_operator_id == "op-3"
    assert transformation.relation_id == "rel-1"
    assert analogy.similarity == 0.875


def test_identity_transition_is_valid() -> None:
    embedding = Embedding("operator-v1", (1.0,))

    assert PseudoOperator("identity", "state-a", "state-a", embedding).target_state_id == "state-a"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: PseudoOperator(" ", "state-a", "state-b", Embedding("m", (1.0,))),
        lambda: OperatorRelation("rel", "", "op-2", Embedding("m", (1.0,))),
        lambda: OperatorComposition("op-1", " ", "op-3"),
        lambda: OperatorTransformation("op-1", "", "op-2"),
        lambda: AnalogicalTransfer("op-1", "op-2", "op-3", "", 0.5),
    ],
)
def test_operator_records_reject_blank_references(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize("similarity", [True, math.nan, math.inf, -1.01, 1.01, 10**400])
def test_analogy_rejects_invalid_similarity(similarity: object) -> None:
    with pytest.raises(ValueError, match="similarity"):
        AnalogicalTransfer("op-1", "op-2", "op-3", "op-4", similarity)  # type: ignore[arg-type]


def test_analogy_accepts_integer_cosine_boundaries_as_floats() -> None:
    low = AnalogicalTransfer("op-1", "op-2", "op-3", "op-4", -1)
    high = AnalogicalTransfer("op-1", "op-2", "op-3", "op-4", 1)

    assert low.similarity == -1.0
    assert high.similarity == 1.0


def test_program_candidate_normalizes_a_generator_of_operator_ids() -> None:
    candidate = ProgramCandidate(
        "candidate-1",
        "state-a",
        "state-c",
        (operator_id for operator_id in ["op-1", "op-2"]),  # type: ignore[arg-type]
        "lean4",
        "theorem answer : P → P := by\n  intro h\n  exact h\n",
    )

    assert candidate.operator_ids == ("op-1", "op-2")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", " "),
        ("initial_state_id", ""),
        ("final_state_id", "\t"),
        ("operator_ids", ()),
        ("operator_ids", ("op-1", " ")),
        ("operator_ids", "op-1"),
        ("language", ""),
        ("source", " "),
    ],
)
def test_program_candidate_rejects_missing_required_data(field: str, value: object) -> None:
    arguments: dict[str, object] = {
        "id": "candidate-1",
        "initial_state_id": "state-a",
        "final_state_id": "state-b",
        "operator_ids": ("op-1",),
        "language": "lean4",
        "source": "exact h",
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=field):
        ProgramCandidate(**arguments)  # type: ignore[arg-type]


def test_verification_evidence_normalizes_diagnostics() -> None:
    evidence = VerificationEvidence(
        "lean4",
        VerificationStatus.ACCEPTED,
        "trace://session/1",
        ["kernel checked"],  # type: ignore[arg-type]
    )

    assert evidence.diagnostics == ("kernel checked",)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"verifier": " "},
        {"status": "accepted"},
        {"artifact_ref": ""},
        {"diagnostics": (" ",)},
    ],
)
def test_verification_evidence_rejects_invalid_fields(kwargs: dict[str, object]) -> None:
    arguments: dict[str, object] = {
        "verifier": "lean4",
        "status": VerificationStatus.ACCEPTED,
        "artifact_ref": None,
        "diagnostics": (),
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError):
        VerificationEvidence(**arguments)  # type: ignore[arg-type]


def test_verified_program_requires_accepted_evidence() -> None:
    candidate = ProgramCandidate("candidate-1", "state-a", "state-b", ("op-1",), "lean4", "exact h")
    rejected = VerificationEvidence("lean4", VerificationStatus.REJECTED)

    with pytest.raises(ValueError, match="accepted"):
        VerifiedProgram(candidate, rejected)


def test_library_entry_preserves_verified_provenance_and_normalizes_tags() -> None:
    embedding = Embedding("operator-v1", (1.0,))
    operator = PseudoOperator("op-1", "state-a", "state-b", embedding)
    candidate = ProgramCandidate("candidate-1", "state-a", "state-b", ("op-1",), "lean4", "exact h")
    evidence = VerificationEvidence("lean4", VerificationStatus.ACCEPTED)
    verified = VerifiedProgram(candidate, evidence)

    entry = OperatorLibraryEntry(operator, verified, "identity", "Identity proof", ["logic", "verified"])  # type: ignore[arg-type]

    assert entry.tags == ("logic", "verified")
    assert entry.program.candidate.id == "candidate-1"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": " "},
        {"description": None},
        {"tags": ("logic", "")},
    ],
)
def test_library_entry_rejects_invalid_metadata(kwargs: dict[str, object]) -> None:
    operator = PseudoOperator("op-1", "state-a", "state-b", Embedding("m", (1.0,)))
    candidate = ProgramCandidate("candidate-1", "state-a", "state-b", ("op-1",), "lean4", "exact h")
    verified = VerifiedProgram(candidate, VerificationEvidence("lean4", VerificationStatus.ACCEPTED))
    arguments: dict[str, object] = {
        "operator": operator,
        "program": verified,
        "name": "identity",
        "description": "",
        "tags": (),
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError):
        OperatorLibraryEntry(**arguments)  # type: ignore[arg-type]


def test_package_root_exports_the_complete_public_model() -> None:
    assert PublicEmbedding is Embedding
    assert PublicSymbolicState is SymbolicState
    assert PublicPseudoOperator is PseudoOperator
    assert PublicOperatorRelation is OperatorRelation
    assert PublicOperatorComposition is OperatorComposition
    assert PublicOperatorTransformation is OperatorTransformation
    assert PublicAnalogicalTransfer is AnalogicalTransfer


def test_daydream_does_not_import_lean_gym() -> None:
    import sys

    assert "lean_gym" not in sys.modules
