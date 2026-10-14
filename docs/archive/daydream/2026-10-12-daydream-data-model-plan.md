# Daydream Data Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, immutable Python data model for analogical program synthesis while leaving the existing Lean proof gym unchanged.

**Architecture:** A new standard-library-only `daydream` distribution owns frozen, slotted value objects in one focused model module and re-exports its public API from the package root. References between states, operators, and relations use stable string IDs; verification is represented with neutral evidence records so the package has no dependency on `lean_gym`.

**Tech Stack:** Python 3.11+, `dataclasses`, `enum`, `math`, `numbers`, pytest 8.x for development tests.

**Spec:** [`docs/superpowers/specs/2026-10-12-daydream-data-model-design.md`](../specs/2026-10-12-daydream-data-model-design.md)

## Global Constraints

- Keep all runtime code and tests for this milestone under `daydream/`.
- Do not modify or import from `lean_gym`.
- Runtime dependencies are limited to the Python standard library.
- Every domain record is a frozen, slotted dataclass.
- Normalize collection fields to tuples during construction.
- Raise `ValueError` with a field-specific message for invalid domain values.
- Do not add encoders, relation learning, composition logic, transformation logic, analogy search, synthesis, persistence, Lean adapters, CLIs, or UIs.
- The workspace is not currently a Git repository. The plan names exact checkpoint commits, but execution must skip the `git` commands unless Git metadata becomes available.

## File Structure

- `daydream/pyproject.toml` — isolated package metadata, Python version floor, pytest configuration, and package discovery.
- `daydream/src/daydream/models.py` — validation helpers and all domain enums/dataclasses.
- `daydream/src/daydream/__init__.py` — explicit public API re-exports.
- `daydream/tests/test_models.py` — construction, immutability, normalization, invariants, edge cases, and package-isolation tests.

## Review Focus

- Whitespace-only identifiers and required source text must fail rather than produce unusable references; Task 1 and Task 3 pin this behavior.
- One-shot iterables such as generators must be consumed once and normalized to tuples; Task 1 and Task 3 cover this.
- Numeric validation must accept ordinary integers but reject Boolean, NaN, and infinity values; Task 1 and Task 2 cover this.
- Raw strings must not silently stand in for `VerificationStatus`, because that weakens the verified-program invariant; Task 3 covers this.
- Optional text and tuple elements must still have the declared type and non-blank tuple entries; Task 1 and Task 3 cover this.

---

### Task 1: Package Scaffold and Core Value Objects

**Files:**
- Create: `daydream/pyproject.toml`
- Create: `daydream/src/daydream/models.py`
- Create: `daydream/tests/test_models.py`

**Interfaces:**
- Consumes: Python standard-library `dataclass`, `math.isfinite`, and `numbers.Real`.
- Produces: `Embedding(model_id: str, values: tuple[float, ...])`, `Embedding.dimension: int`, and `SymbolicState(id: str, expression: str, language: str, context: tuple[str, ...] = ())`.

- [ ] **Step 1: Scaffold the package and write failing core-model tests**

Create `daydream/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "daydream-analogy"
version = "0.1.0"
description = "Domain models for analogical program synthesis"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8,<9"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.setuptools.packages.find]
where = ["src"]
```

Create `daydream/tests/test_models.py` with the first tests:

```python
from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from daydream.models import Embedding, SymbolicState


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
    [(), (True,), ("1",), (math.nan,), (math.inf,), (-math.inf,)],
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
```

- [ ] **Step 2: Run the core-model tests and verify the expected import failure**

Run from `daydream/`:

```bash
python3 -m pytest tests/test_models.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'daydream'` because the package and models do not exist yet.

- [ ] **Step 3: Implement validation helpers, `Embedding`, and `SymbolicState`**

Create `daydream/src/daydream/models.py`:

```python
from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import Iterable


def _require_text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not allow_empty and not value.strip():
        raise ValueError(f"{field} must not be blank")
    return value


def _text_tuple(values: Iterable[object], field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must be an iterable of strings, not one string")
    try:
        normalized = tuple(values)
    except TypeError as error:
        raise ValueError(f"{field} must be an iterable of strings") from error
    return tuple(_require_text(value, field) for value in normalized)


@dataclass(frozen=True, slots=True)
class Embedding:
    model_id: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        _require_text(self.model_id, "model_id")
        try:
            raw_values = tuple(self.values)
        except TypeError as error:
            raise ValueError("values must be a non-empty iterable of finite real numbers") from error
        if not raw_values:
            raise ValueError("values must not be empty")
        if any(isinstance(value, bool) or not isinstance(value, Real) for value in raw_values):
            raise ValueError("values must contain only real numbers, not booleans")
        normalized = tuple(float(value) for value in raw_values)
        if not all(math.isfinite(value) for value in normalized):
            raise ValueError("values must contain only finite numbers")
        object.__setattr__(self, "values", normalized)

    @property
    def dimension(self) -> int:
        return len(self.values)


@dataclass(frozen=True, slots=True)
class SymbolicState:
    id: str
    expression: str
    language: str
    context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.expression, "expression")
        _require_text(self.language, "language")
        object.__setattr__(self, "context", _text_tuple(self.context, "context"))
```

- [ ] **Step 4: Run the core-model tests and verify they pass**

Run from `daydream/`:

```bash
python3 -m pytest tests/test_models.py -q
```

Expected: `16 passed` (the parametrized cases count separately).

- [ ] **Step 5: Record the Task 1 checkpoint**

If Git metadata is available at execution time:

```bash
git add daydream/pyproject.toml daydream/src/daydream/models.py daydream/tests/test_models.py
git commit -m "feat: add daydream core value objects"
```

Otherwise, record that the core models pass in `.superpowers/sdd/2026-10-12-daydream-data-model/progress.md` and continue without initializing a repository.

### Task 2: Operator Geometry Records

**Files:**
- Modify: `daydream/src/daydream/models.py`
- Modify: `daydream/tests/test_models.py`

**Interfaces:**
- Consumes: `Embedding` and private text validation from Task 1.
- Produces: `PseudoOperator`, `OperatorRelation`, `OperatorComposition`, `OperatorTransformation`, and `AnalogicalTransfer` with the field names fixed by the design spec.

- [ ] **Step 1: Write failing tests for transition, relation, derivation, and analogy records**

Append to `daydream/tests/test_models.py` and extend its import:

```python
from daydream.models import (
    AnalogicalTransfer,
    Embedding,
    OperatorComposition,
    OperatorRelation,
    OperatorTransformation,
    PseudoOperator,
    SymbolicState,
)


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


@pytest.mark.parametrize("similarity", [True, math.nan, math.inf, -1.01, 1.01])
def test_analogy_rejects_invalid_similarity(similarity: object) -> None:
    with pytest.raises(ValueError, match="similarity"):
        AnalogicalTransfer("op-1", "op-2", "op-3", "op-4", similarity)  # type: ignore[arg-type]


def test_analogy_accepts_integer_cosine_boundaries_as_floats() -> None:
    low = AnalogicalTransfer("op-1", "op-2", "op-3", "op-4", -1)
    high = AnalogicalTransfer("op-1", "op-2", "op-3", "op-4", 1)

    assert low.similarity == -1.0
    assert high.similarity == 1.0
```

- [ ] **Step 2: Run the operator tests and verify they fail for missing classes**

Run from `daydream/`:

```bash
python3 -m pytest tests/test_models.py -q
```

Expected: collection fails with an `ImportError` naming `AnalogicalTransfer` or another operator record not yet defined.

- [ ] **Step 3: Implement the five operator-geometry dataclasses**

Append to `daydream/src/daydream/models.py`:

```python
@dataclass(frozen=True, slots=True)
class PseudoOperator:
    id: str
    source_state_id: str
    target_state_id: str
    embedding: Embedding

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.source_state_id, "source_state_id")
        _require_text(self.target_state_id, "target_state_id")
        if not isinstance(self.embedding, Embedding):
            raise ValueError("embedding must be an Embedding")


@dataclass(frozen=True, slots=True)
class OperatorRelation:
    id: str
    source_operator_id: str
    target_operator_id: str
    embedding: Embedding

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.source_operator_id, "source_operator_id")
        _require_text(self.target_operator_id, "target_operator_id")
        if not isinstance(self.embedding, Embedding):
            raise ValueError("embedding must be an Embedding")


@dataclass(frozen=True, slots=True)
class OperatorComposition:
    left_operator_id: str
    right_operator_id: str
    result_operator_id: str

    def __post_init__(self) -> None:
        _require_text(self.left_operator_id, "left_operator_id")
        _require_text(self.right_operator_id, "right_operator_id")
        _require_text(self.result_operator_id, "result_operator_id")


@dataclass(frozen=True, slots=True)
class OperatorTransformation:
    source_operator_id: str
    relation_id: str
    result_operator_id: str

    def __post_init__(self) -> None:
        _require_text(self.source_operator_id, "source_operator_id")
        _require_text(self.relation_id, "relation_id")
        _require_text(self.result_operator_id, "result_operator_id")


@dataclass(frozen=True, slots=True)
class AnalogicalTransfer:
    source_from_operator_id: str
    source_to_operator_id: str
    target_from_operator_id: str
    target_to_operator_id: str
    similarity: float

    def __post_init__(self) -> None:
        _require_text(self.source_from_operator_id, "source_from_operator_id")
        _require_text(self.source_to_operator_id, "source_to_operator_id")
        _require_text(self.target_from_operator_id, "target_from_operator_id")
        _require_text(self.target_to_operator_id, "target_to_operator_id")
        if isinstance(self.similarity, bool) or not isinstance(self.similarity, Real):
            raise ValueError("similarity must be a finite real number in [-1.0, 1.0]")
        normalized = float(self.similarity)
        if not math.isfinite(normalized) or not -1.0 <= normalized <= 1.0:
            raise ValueError("similarity must be a finite real number in [-1.0, 1.0]")
        object.__setattr__(self, "similarity", normalized)
```

- [ ] **Step 4: Run all model tests and verify the operator records pass**

Run from `daydream/`:

```bash
python3 -m pytest tests/test_models.py -q
```

Expected: all current tests pass.

- [ ] **Step 5: Record the Task 2 checkpoint**

If Git metadata is available:

```bash
git add daydream/src/daydream/models.py daydream/tests/test_models.py
git commit -m "feat: model daydream operator geometry"
```

Otherwise, append the passing command and test count to `.superpowers/sdd/2026-10-12-daydream-data-model/progress.md`.

### Task 3: Programs, Verification Evidence, Library Entries, and Public API

**Files:**
- Modify: `daydream/src/daydream/models.py`
- Create: `daydream/src/daydream/__init__.py`
- Modify: `daydream/tests/test_models.py`

**Interfaces:**
- Consumes: `PseudoOperator` and private validation helpers from Tasks 1–2.
- Produces: `VerificationStatus`, `ProgramCandidate`, `VerificationEvidence`, `VerifiedProgram`, `OperatorLibraryEntry`, and package-root imports for all public Daydream types.

- [ ] **Step 1: Write failing program, verification, library, and public-API tests**

Extend `daydream/tests/test_models.py` imports and append these tests:

```python
from daydream import (
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
```

- [ ] **Step 2: Run the expanded suite and verify missing program types fail collection**

Run from `daydream/`:

```bash
python3 -m pytest tests/test_models.py -q
```

Expected: collection fails with an `ImportError` for the missing package-root API or program model types.

- [ ] **Step 3: Implement program, verification, and library records**

Add `Enum` to the imports in `daydream/src/daydream/models.py`, then append:

```python
from enum import Enum


class VerificationStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ProgramCandidate:
    id: str
    initial_state_id: str
    final_state_id: str
    operator_ids: tuple[str, ...]
    language: str
    source: str

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.initial_state_id, "initial_state_id")
        _require_text(self.final_state_id, "final_state_id")
        normalized = _text_tuple(self.operator_ids, "operator_ids")
        if not normalized:
            raise ValueError("operator_ids must not be empty")
        object.__setattr__(self, "operator_ids", normalized)
        _require_text(self.language, "language")
        _require_text(self.source, "source")


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    verifier: str
    status: VerificationStatus
    artifact_ref: str | None = None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.verifier, "verifier")
        if not isinstance(self.status, VerificationStatus):
            raise ValueError("status must be a VerificationStatus")
        if self.artifact_ref is not None:
            _require_text(self.artifact_ref, "artifact_ref")
        object.__setattr__(self, "diagnostics", _text_tuple(self.diagnostics, "diagnostics"))


@dataclass(frozen=True, slots=True)
class VerifiedProgram:
    candidate: ProgramCandidate
    evidence: VerificationEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, ProgramCandidate):
            raise ValueError("candidate must be a ProgramCandidate")
        if not isinstance(self.evidence, VerificationEvidence):
            raise ValueError("evidence must be VerificationEvidence")
        if self.evidence.status is not VerificationStatus.ACCEPTED:
            raise ValueError("verified programs require accepted evidence")


@dataclass(frozen=True, slots=True)
class OperatorLibraryEntry:
    operator: PseudoOperator
    program: VerifiedProgram
    name: str
    description: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.operator, PseudoOperator):
            raise ValueError("operator must be a PseudoOperator")
        if not isinstance(self.program, VerifiedProgram):
            raise ValueError("program must be a VerifiedProgram")
        _require_text(self.name, "name")
        _require_text(self.description, "description", allow_empty=True)
        object.__setattr__(self, "tags", _text_tuple(self.tags, "tags"))
```

Place `from enum import Enum` with the other imports rather than in the middle of the completed module.

- [ ] **Step 4: Add the explicit package-root public API**

Create `daydream/src/daydream/__init__.py`:

```python
"""Domain models for analogical program synthesis."""

from daydream.models import (
    AnalogicalTransfer,
    Embedding,
    OperatorComposition,
    OperatorLibraryEntry,
    OperatorRelation,
    OperatorTransformation,
    ProgramCandidate,
    PseudoOperator,
    SymbolicState,
    VerificationEvidence,
    VerificationStatus,
    VerifiedProgram,
)

__all__ = [
    "AnalogicalTransfer",
    "Embedding",
    "OperatorComposition",
    "OperatorLibraryEntry",
    "OperatorRelation",
    "OperatorTransformation",
    "ProgramCandidate",
    "PseudoOperator",
    "SymbolicState",
    "VerificationEvidence",
    "VerificationStatus",
    "VerifiedProgram",
]
```

- [ ] **Step 5: Run the complete Daydream test suite**

Run from `daydream/`:

```bash
python3 -m pytest -q
```

Expected: all Daydream tests pass with no warnings.

- [ ] **Step 6: Verify package metadata and isolation**

Run from `daydream/`:

```bash
python3 -m pip install --no-deps -e .
python3 -c "import daydream; print(sorted(daydream.__all__))"
python3 -c "import sys, daydream; assert 'lean_gym' not in sys.modules"
```

Expected: editable installation succeeds; the first Python command prints the 12 public names; the isolation assertion exits successfully with no output.

- [ ] **Step 7: Run the existing proof-gym tests as a regression check**

Run from `lean_gym/`:

```bash
python3 -m pytest -q
```

Expected: the existing Lean gym suite passes unchanged. If Lean itself is unavailable, model and non-Lean unit tests must still pass, and the unavailable external tool must be reported exactly rather than treated as a Daydream failure.

- [ ] **Step 8: Record the final checkpoint**

If Git metadata is available:

```bash
git add daydream
git commit -m "feat: add standalone daydream data model"
```

Otherwise, append the final Daydream and Lean-gym verification commands and outcomes to `.superpowers/sdd/2026-10-12-daydream-data-model/progress.md`.

