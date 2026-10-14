# Daydream Data Model Design

**Date:** 2026-10-12  
**Status:** Ready for review

## Purpose

Create an immutable Python data model for analogical program synthesis without implementing encoding, search, learning, composition, transformation, or synthesis algorithms. The model represents symbolic states, learned transition operators, relationships between operators, derived operators, analogy hypotheses, candidate programs, verification evidence, and reusable library entries.

The work belongs to a new standalone `daydream` package. The existing `lean_gym` package remains unchanged and continues to provide only the proof REPL and Lean verification machinery.

## Package Boundary

```text
analogy/
├── daydream/
│   ├── pyproject.toml
│   ├── src/daydream/
│   │   ├── __init__.py
│   │   └── models.py
│   └── tests/test_models.py
└── lean_gym/
```

`daydream` uses only the Python standard library. It does not import from `lean_gym`, NumPy, an embedding library, or a model provider. A future integration layer may translate Lean proof-session results into Daydream verification records without coupling either package's core domain model to the other.

## Domain Model

All records are frozen, slotted dataclasses. Collection inputs are normalized to tuples so instances remain immutable and have stable value semantics.

### `Embedding`

Represents one point in a named embedding space.

- `model_id: str`
- `values: tuple[float, ...]`
- `dimension: int` as a derived property

The model ID must be non-empty. The vector must be non-empty and contain only finite real values. Boolean values are rejected even though Python treats `bool` as a subtype of `int`.

### `SymbolicState`

Represents a symbolic program or proof state independently of its embedding.

- `id: str`
- `expression: str`
- `language: str`
- `context: tuple[str, ...] = ()`

IDs, expressions, and language names must be non-empty. Context preserves order because binder and hypothesis order can be semantically relevant to an external executor.

### `PseudoOperator`

Represents the encoded transition

\[
s_i = E_\Delta(x_a, x_b).
\]

- `id: str`
- `source_state_id: str`
- `target_state_id: str`
- `embedding: Embedding`

The record stores state IDs rather than nesting full states, allowing a state registry to deduplicate shared endpoints. Identity transitions are allowed because the data layer should not impose a learning assumption.

### `OperatorRelation`

Represents the learned directed relationship

\[
r_{ij} = R(s_i, s_j).
\]

- `id: str`
- `source_operator_id: str`
- `target_operator_id: str`
- `embedding: Embedding`

Direction is explicit and significant. The relation embedding may use a different model or dimensionality from the operator embeddings; compatibility belongs to future algorithms.

### `OperatorComposition`

Records one composition result

\[
C(s_i, s_j) \rightarrow s_k.
\]

- `left_operator_id: str`
- `right_operator_id: str`
- `result_operator_id: str`

This class records a result or assertion. It does not compute composition or claim that composition is commutative.

### `OperatorTransformation`

Records one relational operator transformation

\[
T(s_i, r_{ij}) \rightarrow s_j.
\]

- `source_operator_id: str`
- `relation_id: str`
- `result_operator_id: str`

### `AnalogicalTransfer`

Represents a scored four-operator analogy

\[
R(s_i, s_j) \approx R(s_k, s_l).
\]

- `source_from_operator_id: str`
- `source_to_operator_id: str`
- `target_from_operator_id: str`
- `target_to_operator_id: str`
- `similarity: float`

Similarity must be finite and lie in `[-1.0, 1.0]`, matching cosine-style relation similarity. The class records a complete candidate quartet; partially specified analogy queries remain an algorithm-layer concern.

### `ProgramCandidate`

Represents a proposed symbolic instantiation of one or more pseudo-operators.

- `id: str`
- `initial_state_id: str`
- `final_state_id: str`
- `operator_ids: tuple[str, ...]`
- `language: str`
- `source: str`

At least one operator ID is required. The source is opaque to Daydream and may contain a Lean theorem, tactic script, or a program in another symbolic language.

### `VerificationEvidence`

Stores verifier-neutral evidence returned by an external symbolic system.

- `verifier: str`
- `status: VerificationStatus`
- `artifact_ref: str | None = None`
- `diagnostics: tuple[str, ...] = ()`

`VerificationStatus` contains `ACCEPTED`, `REJECTED`, and `ERROR`. The evidence deliberately does not import `lean_gym.VerificationResult`. A future adapter can map Lean statuses and preserve a reference to the detailed proof trace.

### `VerifiedProgram`

Pairs a program candidate with its verification evidence.

- `candidate: ProgramCandidate`
- `evidence: VerificationEvidence`

Construction requires `evidence.status` to be `ACCEPTED`; rejected and errored candidates remain represented by `ProgramCandidate` plus `VerificationEvidence` rather than being mislabeled as verified.

### `OperatorLibraryEntry`

Represents a successfully verified program compressed into a reusable abstraction.

- `operator: PseudoOperator`
- `program: VerifiedProgram`
- `name: str`
- `description: str = ""`
- `tags: tuple[str, ...] = ()`

This record captures provenance but does not perform compression, persistence, indexing, or retrieval.

## Validation and Errors

Invalid value objects fail immediately with `ValueError` and a field-specific message. Cross-record graph checks—such as whether referenced state or operator IDs exist, whether embedding spaces match, or whether a candidate's endpoints agree with its operator sequence—are excluded because they require a repository or synthesis context that does not yet exist.

## Public API

The public dataclasses and `VerificationStatus` are re-exported from `daydream.__init__`. Internal validation helpers remain private. No algorithm-shaped placeholder methods, abstract services, repositories, serialization formats, or persistence interfaces are introduced in this milestone.

## Testing

Unit tests cover:

- creation of each domain record;
- frozen dataclass behavior;
- tuple normalization from iterable inputs;
- derived embedding dimension;
- rejection of empty IDs and required text;
- rejection of empty, non-numeric, Boolean, or non-finite embedding values;
- analogy similarity range and finiteness;
- non-empty program operator sequences;
- the accepted-evidence invariant for `VerifiedProgram`;
- public imports from `daydream`;
- continued independence from `lean_gym`.

Tests do not exercise embedding generation, numerical similarity, program execution, Lean invocation, or synthesis.

## Out of Scope

- encoders `E_Δ` and relation learners `R`;
- composition or transformation algorithms;
- nearest-neighbor search and analogy solving;
- model training or inference;
- a state/operator registry or database;
- Lean adapters and proof execution;
- program compression and library retrieval;
- command-line or graphical interfaces.

