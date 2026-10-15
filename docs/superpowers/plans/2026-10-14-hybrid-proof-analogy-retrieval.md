# Hybrid Proof-Structure and Natural-Language Analogy Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace global hidden-vector analogy retrieval with deterministic Lean structural shortlisting, teacher-forced Qwen pair scoring, stable tactic-string reranking, and separately measured rescue proposals.

**Architecture:** The standalone script asks Lean to serialize elaborated goals into a canonical `ProofSignature`, renders that signature into deterministic natural language, and compares it with strictly validated corpus-side `TransformationRecord` objects. Structural compatibility creates a shared shortlist for matched-random and analogy conditions; frozen local Qwen then scores proof–analogy pairs and tactic strings with batched forward passes, while Lean remains the sole correctness authority.

**Tech Stack:** Python 3.11+, Lean 4.29.1, PyTorch, Transformers, local Qwen3-8B, pytest

**Spec:** `docs/superpowers/specs/2026-10-14-hybrid-proof-analogy-retrieval-design.md`

## Global Constraints

- Keep production execution in `scripts/standalone_analogy_recovery.py`; do not import `src/analogy`.
- Derive proof structure from elaborated Lean expressions, never from Qwen output or reference proofs.
- Keep the local Qwen checkpoint frozen and run scoring under `torch.inference_mode()`.
- Use explicit `mps` for real-model Apple Silicon runs and fail instead of silently falling back to CPU.
- Give all conditions the identical proof signature, natural-language rendering, candidate tuple, seed, Lean verifier, and budgets.
- Use only corpus context to distinguish `none`, structurally matched `random`, and `analogy` conditions.
- Rank actual tactic-string completions by mean token log likelihood; do not generate ranking indices or reasoning text.
- Permit at most two rescue tactics only after the shared pool is exhausted, and report rescue separately.
- Keep reference proofs in evaluation-only data flow; coincidental model generation of the same tactic text is allowed.
- Preserve non-overwriting output directories, atomic finalized JSON, and append-only JSONL progress records.

## Review Focus

- Alpha-renamed Lean goals and contexts must yield the same signature, while malformed structural trace JSON must yield a verifier protocol failure; Task 2 pins both cases.
- An empty or entirely incompatible structural shortlist must preserve baseline candidate ordering without fabricated retrieval; Task 4 pins this fallback.
- Variable-length candidate completions, right padding, and multi-token tactics must score completion tokens only; Task 5 pins prompt/padding masking.
- Matched-random and analogy retrieval must draw from the identical structural shortlist for a state and seed; Task 6 pins shortlist identity in the integrated ranker.
- MPS unavailability and rescue timeout must be explicit, non-corrupting outcomes rather than CPU fallback or lost reranking data; Tasks 7 and 8 pin these paths.

---

## File structure

- Modify `scripts/standalone_analogy_recovery.py`: all records, Lean protocol, corpus extraction, retrieval, scoring, search, CLI, and output logic remain self-contained.
- Modify `tests/test_standalone_analogy_recovery.py`: focused unit, protocol, integration, CLI, and real-Lean tests for the standalone flow.
- Modify `run_experiment.sh`: expose stable hybrid-retrieval defaults and explicit device selection while preserving user overrides.
- Modify `README.md`: document the three conditions, MPS invocation, staged runs, and interpretation of reranking versus rescue metrics.

The script is already large, but the standalone requirement is deliberate. Organize new code into contiguous sections headed `Proof structure`, `Transformation schema`, `Structural retrieval`, and `Teacher-forced scoring`; do not create a second Python package whose imports become hidden runtime dependencies.

---

### Task 1: Define strict shared structural records

**Files:**
- Modify: `scripts/standalone_analogy_recovery.py:93-176`
- Modify: `scripts/standalone_analogy_recovery.py:236-365`
- Test: `tests/test_standalone_analogy_recovery.py`

**Interfaces:**
- Consumes: existing `Transition`, `Message`, `_nonblank`, and `sha256_text` helpers.
- Produces: `OperationClass`, `Arity`, `Permutation`, `InformationFlow`, `ExpressionShape`, `ProofSignature`, `TransformationRecord`, `parse_transformation_record(text: str) -> TransformationRecord`, and `render_transformation_prompt(transition: Transition) -> tuple[Message, ...]`.

- [ ] **Step 1: Add failing tests for exact schema validation and canonical records**

```python
def test_transformation_record_parser_accepts_only_exact_schema() -> None:
    module = load_script()
    parsed = module.parse_transformation_record(json.dumps({
        "operation": "select",
        "input_arity": "two",
        "output_arity": "one",
        "permutation": "none",
        "information_flow": ["select", "discard"],
        "focus_before": "a composite object",
        "focus_after": "its second component",
        "domain_neutral_summary": "Select one component from a two-part object.",
    }))
    assert parsed.operation is module.OperationClass.SELECT
    assert parsed.information_flow == (
        module.InformationFlow.SELECT,
        module.InformationFlow.DISCARD,
    )

    invalid = {
        "operation": "select",
        "input_arity": "two",
        "output_arity": "one",
        "permutation": "none",
        "information_flow": ["select"],
        "focus_before": "composite",
        "focus_after": "component",
        "domain_neutral_summary": "Use exact h.right.",
        "extra": True,
    }
    with pytest.raises(module.ModelOutputError):
        module.parse_transformation_record(json.dumps(invalid))
```

Add parameterized rejection cases for missing fields, unknown enum values, duplicate information-flow entries, blank focus text, tactic names, and a summary exceeding the documented length limit.

- [ ] **Step 2: Run the focused tests and confirm the missing interfaces fail**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'transformation_record_parser' -v
```

Expected: failure because `parse_transformation_record` and the enum records do not exist.

- [ ] **Step 3: Implement immutable enum-backed records and strict parsing**

Implement these exact public shapes:

```python
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

@dataclass(frozen=True, slots=True)
class ExpressionShape:
    root: str
    children: tuple["ExpressionShape", ...] = ()

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
```

Validate tuple ordering and uniqueness in `__post_init__`. Replace the old free-form `parse_transformation` contract with exact-key JSON parsing. Update the extraction prompt to list every enum value and forbid Lean tactic syntax.

- [ ] **Step 4: Run parser tests and the existing prompt/parser tests**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'transformation or prompt' -v
```

Expected: all selected tests pass after updating the old free-form transformation expectations to the strict record.

- [ ] **Step 5: Commit the shared schema**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py
git commit -m "feat: define structural analogy schema"
```

---

### Task 2: Extract deterministic proof signatures through Lean

**Files:**
- Modify: `scripts/standalone_analogy_recovery.py:1149-1440`
- Test: `tests/test_standalone_analogy_recovery.py`

**Interfaces:**
- Consumes: `ExpressionShape`, `ProofSignature`, existing `LeanVerifier`, and Lean 4.29.1 diagnostics.
- Produces: `parse_proof_signature(payload: Mapping[str, Any]) -> ProofSignature`, `render_proof_signature(signature: ProofSignature) -> str`, `proof_signature_fingerprint(signature: ProofSignature) -> str`, `classify_failed_tactic(tactic: str) -> OperationClass | None`, and `LeanResult.signature: ProofSignature | None`.

- [ ] **Step 1: Add failing pure-Python tests for parsing, operation inference, rendering, and alpha invariance**

```python
def test_proof_signature_is_name_free_and_alpha_invariant() -> None:
    module = load_script()
    first = module.parse_proof_signature({
        "goal_count": 1,
        "target": {"root": "atom", "children": []},
        "hypotheses": [{
            "root": "and",
            "children": [
                {"root": "atom", "children": []},
                {"root": "target", "children": []},
            ],
        }],
    })
    second = module.parse_proof_signature({
        "goal_count": 1,
        "target": {"root": "atom", "children": []},
        "hypotheses": [{
            "root": "and",
            "children": [
                {"root": "atom", "children": []},
                {"root": "target", "children": []},
            ],
        }],
    })
    assert first == second
    assert first.candidate_operations == (
        module.OperationClass.DECOMPOSE,
        module.OperationClass.SELECT,
    )
    assert module.render_proof_signature(first) == (
        "The target is atomic. A conjunction hypothesis contains the target as "
        "its second component. Plausible operations decompose or select a "
        "component, selecting information and discarding the remainder."
    )
```

Add tests for implication introduction, conjunction combination, swapped
conjunction reorder, exact hypothesis reuse, equality rewrite, multiple goals,
unknown applications, and deterministic sorting of hypotheses.

- [ ] **Step 2: Add failing real-Lean protocol tests**

```python
def test_real_lean_emits_signature_and_rejects_malformed_signature(tmp_path: Path) -> None:
    module = load_script()
    verifier = module.LeanVerifier(
        module.REPOSITORY_ROOT / "src/analogy/lean_gym/lean_project",
        lake_executable=shutil.which("lake") or "lake",
    )
    problem = module.Problem("sig", "Signature", "(P Q : Prop) (h : P ∧ Q)", "Q", 1)
    result = verifier.initial_state(problem)
    assert result.status is module.LeanStatus.OPEN
    assert result.signature is not None
    assert module.OperationClass.SELECT in result.signature.candidate_operations
```

Add a fake Lean diagnostic containing an invalid JSON marker and assert the
result is `LeanStatus.INTERNAL_ERROR` with a `protocol` diagnostic rather than a
text-derived fallback.

- [ ] **Step 3: Run the signature tests and confirm they fail**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'proof_signature or emits_signature or malformed_signature' -v
```

Expected: failures because `LeanResult` does not carry a signature and the Lean probe emits only `trace_state`.

- [ ] **Step 4: Embed a Lean expression serializer in verifier probe source**

Add a versioned `LEAN_STRUCTURE_PRELUDE` string to the standalone script. The
Lean code must:

```lean
import Lean
open Lean Meta Elab Tactic

partial def analogyExprShape (needle : Option Expr) (e : Expr) (depth : Nat := 0) : MetaM Json := do
  if depth >= 8 then
    return Json.mkObj [("root", "opaque"), ("children", Json.arr #[])]
  let e ← instantiateMVars e
  if let some target := needle then
    if e == target then
      return Json.mkObj [("root", "target"), ("children", Json.arr #[])]
  match e with
  | .forallE _ domain body _ =>
      let root := if body.hasLooseBVar then "forall" else "implication"
      let left ← analogyExprShape needle domain (depth + 1)
      let right ← analogyExprShape needle body (depth + 1)
      return Json.mkObj [("root", root), ("children", Json.arr #[left, right])]
  | _ =>
      let root := match e.getAppFn.constName? with
        | some ``And => "and"
        | some ``Or => "or"
        | some ``Iff => "iff"
        | some ``Eq => "equality"
        | some ``Not => "not"
        | some ``Exists => "exists"
        | some ``False => "false"
        | _ => if e.isApp then "application" else "atom"
      let children ← e.getAppArgs.mapM (analogyExprShape needle · (depth + 1))
      return Json.mkObj [("root", root), ("children", Json.arr children)]
```

Define a `trace_proof_structure` tactic that serializes the first open goal's
target, proposition-valued local declarations, and goal count after stripping
local names. Emit exactly one diagnostic prefixed by
`ANALOGY_PROOF_STRUCTURE:`. Keep the ordinary state trace as a separate marker.

- [ ] **Step 5: Parse the structural marker and propagate signatures through search**

Extend `LeanResult` with `signature: ProofSignature | None`. Require one valid
structural marker for every `OPEN` result and no signature for `COMPLETE` or
failed results. Extend the search frontier tuple to store the signature returned
for each state:

```python
frontier: list[tuple[int, int, str, ProofSignature, tuple[str, ...]]]
```

Use `parse_proof_signature` to derive operation classes, arity, permutation,
information flow, and target occurrence paths. Merge
`classify_failed_tactic(item)` results at rank time without changing the
canonical state fingerprint. Implement `proof_signature_fingerprint` as the
SHA-256 of compact, sorted-key JSON from `_jsonable(signature)`.

- [ ] **Step 6: Run signature tests, real-Lean tests, and all verifier tests**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'signature or standalone_real_lean or search_counts' -v
.venv/bin/pytest tests/test_lean_verifier.py tests/test_lean_end_to_end.py -v
```

Expected: all selected tests pass; real Lean reports a name-free signature for an open conjunction-elimination goal.

- [ ] **Step 7: Commit deterministic proof extraction**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py
git commit -m "feat: extract deterministic Lean proof signatures"
```

---

### Task 3: Build and cache validated corpus transformation records

**Files:**
- Modify: `scripts/standalone_analogy_recovery.py:779-979`
- Modify: `scripts/standalone_analogy_recovery.py:2080-2160`
- Test: `tests/test_standalone_analogy_recovery.py`

**Interfaces:**
- Consumes: `Transition`, `TransformationRecord`, `QwenModel.generate`, and existing cache helpers.
- Produces: `IndexedTransformation`, `transformation_cache_key(...)`, `build_transformation_index(transitions, model, cache_dir, hidden_layer, *, capture_diagnostic_embeddings=False) -> tuple[tuple[IndexedTransformation, ...], Mapping[str, int]]`, and aggregate exclusion counters.

- [ ] **Step 1: Add failing cache and exclusion tests**

```python
def test_transformation_cache_round_trips_strict_record(tmp_path: Path) -> None:
    module = load_script()
    transition = module.Transition("t", "physics", "before", "after", {"source_id": "s"})
    model = SpyModel(module)
    index, exclusions = module.build_transformation_index(
        (transition,), model, tmp_path, hidden_layer=-1
    )
    assert index[0].record.operation is module.OperationClass.SELECT
    assert exclusions == {}
    assert len(list(tmp_path.glob("*.json"))) == 1

def test_invalid_transformation_is_excluded_not_indexed(tmp_path: Path) -> None:
    module = load_script()
    model = SpyModel(module, transformation_output='{"operation":"bad"}')
    index, exclusions = module.build_transformation_index(
        (module.Transition("t", "poetry", "a", "b", {"source_id": "s"}),),
        model,
        tmp_path,
        hidden_layer=-1,
    )
    assert index == ()
    assert exclusions == {"invalid_schema": 1}
```

Update `SpyModel` with a complete valid transformation JSON fixture. Add cache
invalidation assertions for schema version, extraction prompt version, model
revision, raw source hash, layer, and pooling rule.

- [ ] **Step 2: Run the cache tests and confirm the old string index fails**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'transformation_cache or invalid_transformation or cache_key' -v
```

Expected: failures because the current index stores a free-form transformation string and has no exclusion result.

- [ ] **Step 3: Replace `IndexedTransition` with `IndexedTransformation`**

Implement:

```python
@dataclass(frozen=True, slots=True)
class IndexedTransformation:
    transition: Transition
    record: TransformationRecord
    diagnostic_embedding: Embedding | None
    cache_key: str
```

Generate and validate the strict record before optional diagnostic embedding.
Write cache files atomically. A cache hit must revalidate the complete record
and embedding metadata. Return `(index, exclusions)`; count at least
`invalid_schema`, `duplicate_record`, `invalid_embedding`, and `model_failure`.
Do not allow excluded records into either retrieval condition.

- [ ] **Step 4: Persist schema fields and exclusion counts in run outputs**

Change `transitions.jsonl` records to include `_jsonable(item.record)` and mark
diagnostic embeddings as `used_for_retrieval: false`. Add index input count,
valid count, and exclusion counts to `config.json` and `summary.json`.

- [ ] **Step 5: Run cache, output, and full standalone tests**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'cache or run_experiment_writes or manifest' -v
```

Expected: all selected tests pass and cached/uncached builds produce the same canonical records.

- [ ] **Step 6: Commit validated corpus indexing**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py
git commit -m "feat: index validated cross-domain transformations"
```

---

### Task 4: Implement transparent structural shortlisting

**Files:**
- Modify: `scripts/standalone_analogy_recovery.py:980-1116`
- Test: `tests/test_standalone_analogy_recovery.py`

**Interfaces:**
- Consumes: `ProofSignature`, `IndexedTransformation`, and `OperationClass` compatibility rules.
- Produces: `CompatibilityBreakdown`, `StructuralCandidate`, `structural_compatibility(signature, item) -> CompatibilityBreakdown | None`, `structural_shortlist(signature, index, limit) -> tuple[StructuralCandidate, ...]`, and `matched_random_retrieve(shortlist, count, seed)`.

- [ ] **Step 1: Add failing score-component and incompatibility tests**

```python
def test_structural_shortlist_explains_score_components() -> None:
    module = load_script()
    signature = selecting_signature(module)
    exact = indexed_transformation(module, operation="select", input_arity="two", output_arity="one")
    reorder = indexed_transformation(module, operation="reorder", input_arity="two", output_arity="two")
    shortlist = module.structural_shortlist(signature, (reorder, exact), limit=4)
    assert [item.item.transition.id for item in shortlist] == [exact.transition.id]
    assert shortlist[0].breakdown.operation == pytest.approx(0.40)
    assert shortlist[0].breakdown.total == pytest.approx(1.0)

def test_empty_structural_shortlist_is_an_explicit_fallback() -> None:
    module = load_script()
    assert module.structural_shortlist(
        selecting_signature(module),
        (indexed_transformation(module, operation="introduce"),),
        limit=4,
    ) == ()
```

Add tests for unknown fields contributing zero, known conflicts excluding a
record, compatible operation pairs, score range, ID tie-breaking, duplicate
IDs, nonpositive limits, and matched-random determinism.

- [ ] **Step 2: Run structural tests and confirm missing interfaces fail**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'structural_shortlist or structural_compatibility or matched_random' -v
```

Expected: failures because retrieval still uses embedding cosine distance.

- [ ] **Step 3: Implement the versioned compatibility table and exact weights**

Implement immutable score records:

```python
@dataclass(frozen=True, slots=True)
class CompatibilityBreakdown:
    operation: float
    information_flow: float
    arity: float
    permutation: float
    shape_role: float
    matches: tuple[str, ...]
    conflicts: tuple[str, ...]

    @property
    def total(self) -> float:
        return self.operation + self.information_flow + self.arity + self.permutation + self.shape_role

@dataclass(frozen=True, slots=True)
class StructuralCandidate:
    item: IndexedTransformation
    breakdown: CompatibilityBreakdown
```

Use the spec weights `0.40`, `0.20`, `0.15`, `0.15`, and `0.10`. Require an
operation overlap or a documented compatible pair. Exclude known direct
conflicts. Treat `unknown` as zero contribution. Sort by descending total and
then transition ID.

- [ ] **Step 4: Replace cosine and unconstrained random retrieval**

Delete production use of `cosine_retrieve` and `random_retrieve`. Keep optional
diagnostic embeddings out of selection. `matched_random_retrieve` must sample
only from the returned shortlist and preserve sampled IDs in deterministic
sorted order for stable traces.

- [ ] **Step 5: Run retrieval tests and preservation tests**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'structural or retrieval or no_retrieval' -v
```

Expected: all selected tests pass; an empty shortlist yields no hits and no
model ranking error.

- [ ] **Step 6: Commit structural retrieval**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py
git commit -m "feat: shortlist analogies by Lean structure"
```

---

### Task 5: Add batched teacher-forced completion scoring

**Files:**
- Modify: `scripts/standalone_analogy_recovery.py:154-170`
- Modify: `scripts/standalone_analogy_recovery.py:498-778`
- Test: `tests/test_standalone_analogy_recovery.py`

**Interfaces:**
- Consumes: `Message`, Qwen tokenizer/model/device, and fixed completion strings.
- Produces: `CompletionScore`, `TextModel.score_completions(messages, completions, *, max_batch_tokens=None)`, `QwenModel.score_completions(messages, completions, *, max_batch_tokens=None)`, `QwenModel._score_rendered_completions(requests, *, max_batch_tokens)`, and `score_relevance_pairs(model, pairs) -> tuple[PairScore, ...]`.

- [ ] **Step 1: Add failing synthetic token-likelihood tests**

```python
def test_completion_scoring_masks_prompt_and_right_padding() -> None:
    module = load_script()
    qwen = make_scoring_qwen(module)
    scores = qwen.score_completions(
        (module.Message("user", "state"),),
        ("intro", "exact h.right"),
    )
    assert [score.token_count for score in scores] == [1, 3]
    assert scores[0].sum_log_likelihood == pytest.approx(-0.5)
    assert scores[1].sum_log_likelihood == pytest.approx(-3.0)
    assert scores[1].mean_log_likelihood == pytest.approx(-1.0)
```

The fake tokenizer must create candidates of unequal length and right padding.
The fake model must assign nontrivial logits to prompt, completion, and padded
positions so an off-by-one or padding bug changes the assertion. Add rejection
tests for empty completions, non-finite logits, missing pad/EOS IDs, and a batch
token limit below the longest individual sequence.

- [ ] **Step 2: Add failing pair-margin tests**

```python
def test_pair_score_is_relevant_minus_irrelevant_log_likelihood() -> None:
    module = load_script()
    scores = module.score_relevance_pairs(
        PairScoringModel(relevant=-0.25, irrelevant=-1.75),
        ((selecting_signature(module), selecting_record(module)),),
    )
    assert scores[0].margin == pytest.approx(1.5)
    assert scores[0].relevant.mean_log_likelihood == pytest.approx(-0.25)
```

- [ ] **Step 3: Run the scoring tests and confirm the protocol is absent**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'completion_scoring or pair_score' -v
```

Expected: failures because `TextModel` exposes only generation and embedding.

- [ ] **Step 4: Implement completion-only batched forward scoring**

Add:

```python
@dataclass(frozen=True, slots=True)
class CompletionScore:
    text: str
    sum_log_likelihood: float
    mean_log_likelihood: float
    token_count: int

def score_completions(
    self,
    messages: tuple[Message, ...],
    completions: tuple[str, ...],
    *,
    max_batch_tokens: int | None = None,
) -> tuple[CompletionScore, ...]:
    rendered = self._render(messages, thinking=False)
    return self._score_rendered_completions(
        ((rendered, completions),),
        max_batch_tokens=max_batch_tokens,
    )[0]
```

Render the prompt once with `enable_thinking=False`. Encode prompt and completion
separately, concatenate IDs, right-pad deterministic micro-batches, and score
the logit at `prompt_length + completion_offset - 1`. Normalize only completion
tokens. Synchronize MPS before stopping the timer. Record micro-batch boundaries
through a scoring sink analogous to the generation sink.

- [ ] **Step 5: Implement pair scoring as fixed completion contrast**

Define the pair record before implementing the scorer:

```python
@dataclass(frozen=True, slots=True)
class PairScore:
    transition_id: str
    relevant: CompletionScore
    irrelevant: CompletionScore
    margin: float
```

Render each proof–transformation pair into the same versioned prompt and score
the exact completions `relevant` and `irrelevant`. Set `margin` to relevant mean
log likelihood minus irrelevant mean log likelihood. Batch all prompt/completion
pairs through a shared internal `_score_prompt_completions` method rather than
calling the model once per record.

- [ ] **Step 6: Run scoring tests and the existing embedding/generation tests**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'scor or embedding or generation_policy' -v
```

Expected: all selected tests pass, including variable-length tactics and
non-finite rejection.

- [ ] **Step 7: Commit teacher-forced scoring**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py
git commit -m "feat: score tactic completions with Qwen"
```

---

### Task 6: Integrate fair three-condition retrieval and reranking

**Files:**
- Modify: `scripts/standalone_analogy_recovery.py:807-840`
- Modify: `scripts/standalone_analogy_recovery.py:1035-1116`
- Modify: `scripts/standalone_analogy_recovery.py:1441-1791`
- Modify: `scripts/standalone_analogy_recovery.py:1992-2070`
- Test: `tests/test_standalone_analogy_recovery.py`

**Interfaces:**
- Consumes: state `ProofSignature`, structural shortlist, matched-random sampler, pair scorer, candidate completion scorer, and shared `CandidatePoolStore`.
- Produces: expanded `RankingDecision`, `HybridConditionRanker.rank(...)`, and attempt-level structural, pair-score, and tactic-score provenance.

- [ ] **Step 1: Add failing fairness and ranking tests**

```python
def test_conditions_share_signature_pool_budget_and_shortlist() -> None:
    module = load_script()
    model = HybridSpyModel(module)
    ranker = module.HybridConditionRanker(
        model=model,
        index=structural_index(module),
        retrieval_count=2,
        structural_shortlist_size=4,
        pair_margin_threshold=0.0,
    )
    signature = selecting_signature(module)
    candidates = ("split", "cases h", "exact h.right", "assumption")
    random_decision = ranker.rank(
        module.Condition.RANDOM, problem(module), "state", signature, (), (), candidates, 7
    )
    analogy_decision = ranker.rank(
        module.Condition.ANALOGY, problem(module), "state", signature, (), (), candidates, 7
    )
    assert random_decision.structural_shortlist_ids == analogy_decision.structural_shortlist_ids
    assert random_decision.candidates == analogy_decision.candidates == candidates
    assert random_decision.proof_rendering == analogy_decision.proof_rendering
```

Add tests proving no retrieval gets the same signature/rendering, analogy rejects
pairs below the margin threshold, matched random never samples outside the
shortlist, actual tactic strings are ranked by mean likelihood, and ties preserve
the conventional order.

- [ ] **Step 2: Run integrated ranker tests and confirm old condition logic fails**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'conditions_share or hybrid_ranker or margin_threshold' -v
```

Expected: failures because `SearchRanker.rank` has no signature argument and
the current ranker generates index JSON.

- [ ] **Step 3: Expand ranker records and protocol**

Make `SearchRanker.rank` accept `signature: ProofSignature` after `state`.
Replace `ProofNeed` in `RankingDecision` with:

```python
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
```

Replace the old cosine-oriented `RetrievalHit` fields with:

```python
@dataclass(frozen=True, slots=True)
class RetrievalHit:
    item: IndexedTransformation
    structural: CompatibilityBreakdown
    pair_score: PairScore | None
    selection: str
```

`selection` is exactly `matched_random` or `analogy`. A no-retrieval decision
has an empty hit tuple.

Update `_attempt_metadata` and `AttemptRecord` so every attempted tactic carries
signature fingerprint/version, proof rendering hash, compatibility components,
pair scores, candidate likelihoods, shortlist IDs, and fallback reason.

- [ ] **Step 4: Implement `HybridConditionRanker`**

For every condition:

1. merge deterministic failed-operation classes into the state signature;
2. render the identical proof description;
3. compute the structural shortlist once per state fingerprint and cache it;
4. select no hits, matched-random hits, or thresholded pair-scored hits;
5. build a condition prompt from the same state, signature, and candidate tuple;
6. score every actual tactic completion in one batch;
7. sort descending mean likelihood with original-index tie-breaking.

An empty shortlist or empty thresholded result is a valid no-context ranking,
not a model failure.

- [ ] **Step 5: Propagate Lean signatures through search and preserve shared pools**

Pass the signature stored on each frontier entry into the ranker. Use the
successor signature returned by Lean when pushing a new frontier entry. Keep
candidate generation outside each condition's theorem deadline exactly as the
existing shared-pool test requires.

- [ ] **Step 6: Run ranker, search, output, and three-condition smoke tests**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'ranker or shared_candidate or search or three_condition_smoke or run_experiment_writes' -v
```

Expected: all selected tests pass; no ranking-generation call occurs.

- [ ] **Step 7: Commit integrated reranking**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py
git commit -m "feat: rerank shared tactics with hybrid analogies"
```

---

### Task 7: Add bounded, separately reported rescue proposals

**Files:**
- Modify: `scripts/standalone_analogy_recovery.py:176-275`
- Modify: `scripts/standalone_analogy_recovery.py:1540-1791`
- Modify: `scripts/standalone_analogy_recovery.py:1888-1991`
- Test: `tests/test_standalone_analogy_recovery.py`

**Interfaces:**
- Consumes: exhausted shared pool, condition context, existing strict candidate parser, Qwen generation, and Lean verifier.
- Produces: `RescueDecision`, `generate_rescue_candidates(...)`, trial fields `fixed_pool_solved`, `rescue_attempted`, `rescue_candidates`, and `rescue_solved`.

- [ ] **Step 1: Add failing rescue gating, limit, and timeout tests**

```python
def test_rescue_runs_only_after_shared_pool_exhaustion() -> None:
    module = load_script()
    rescue = RescueSpy(("exact h.right",))
    solved = module.search_problem(
        problem(module), module.Condition.ANALOGY, 0,
        FixedPoolStore(("exact h.right",)), IdentityRanker(module),
        CompletingVerifier(module), limits(module), lambda record: None,
        rescue_generator=rescue,
    )
    assert solved.fixed_pool_solved is True
    assert rescue.calls == []

def test_rescue_timeout_preserves_completed_reranking_result() -> None:
    module = load_script()
    result = run_exhausted_search(module, rescue_generator=TimeoutRescue())
    assert result.fixed_pool_solved is False
    assert result.rescue_attempted is True
    assert result.rescue_solved is False
    assert result.status == "exhausted"
    assert result.rescue_failure == "TimeoutError: rescue generation exceeded 20 seconds"
```

Add tests that rescue returns at most two one-line tactics, removes duplicates
and shared-pool tactics, verifies every retained tactic with Lean, and gives all
conditions the same generation budget with only retrieval context differing.

- [ ] **Step 2: Run rescue tests and confirm the search has no rescue boundary**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'rescue' -v
```

Expected: failures because `search_problem` terminates when the shared frontier
is exhausted.

- [ ] **Step 3: Implement strict rescue prompt, parser, and decision record**

Use a versioned prompt that receives the state, proof rendering, failed shared
pool, and condition transformations. Request one JSON array with at most two
single-line tactic strings and no explanation. Parse with `parse_candidates`
using `top_k=2`, then remove duplicates and any shared-pool member.

```python
@dataclass(frozen=True, slots=True)
class RescueDecision:
    candidates: tuple[str, ...]
    attempted: bool
    failure: str | None
```

Use a fixed `rescue_max_new_tokens=32` and `rescue_timeout_seconds=20.0` default.

- [ ] **Step 4: Add rescue after fixed-pool exhaustion without rewriting results**

Track whether any fixed-pool branch completed. Only after all fixed candidates
and their reachable fixed-pool branches are exhausted, request rescue candidates
for the terminal state context and verify them. Extend `TrialResult` and summary
aggregation with separate fixed-pool and rescue counts. A rescue exception or
timeout populates `rescue_failure` and retains the pre-rescue status.

- [ ] **Step 5: Run rescue, search-budget, and summary tests**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'rescue or budget or summary' -v
```

Expected: all selected tests pass and rescue never runs after fixed-pool
completion.

- [ ] **Step 6: Commit rescue proposals**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py
git commit -m "feat: add bounded analogy rescue tactics"
```

---

### Task 8: Expose hybrid configuration, MPS safeguards, and complete provenance

**Files:**
- Modify: `scripts/standalone_analogy_recovery.py:1792-1887`
- Modify: `scripts/standalone_analogy_recovery.py:2071-2475`
- Modify: `run_experiment.sh`
- Test: `tests/test_standalone_analogy_recovery.py`

**Interfaces:**
- Consumes: hybrid ranker, rescue settings, existing `ExperimentConfig`, CLI parser, and manifest writers.
- Produces: `resolve_model_device(torch_module, requested: str) -> str`, validated CLI/config fields, explicit device behavior, warm-up/counterbalancing metadata, and staged-run selection.

- [ ] **Step 1: Add failing CLI and MPS fail-fast tests**

```python
class FakeMpsBackend:
    def __init__(self, built: bool, available: bool) -> None:
        self._built = built
        self._available = available

    def is_built(self) -> bool:
        return self._built

    def is_available(self) -> bool:
        return self._available

class FakeTorchForDevice:
    def __init__(self, *, mps_built: bool, mps_available: bool) -> None:
        self.backends = SimpleNamespace(
            mps=FakeMpsBackend(mps_built, mps_available)
        )
        self.cuda = SimpleNamespace(is_available=lambda: False)

def test_explicit_mps_does_not_fall_back_to_cpu() -> None:
    module = load_script()
    fake_torch = FakeTorchForDevice(mps_built=True, mps_available=False)
    with pytest.raises(module.ModelConfigurationError, match="mps device is unavailable"):
        module.resolve_model_device(fake_torch, "mps")

def test_cli_validates_hybrid_limits() -> None:
    module = load_script()
    parser = module.build_parser()
    args = parser.parse_args([
        "--structural-shortlist-size", "8",
        "--retrieval-count", "4",
        "--pair-margin-threshold", "0.0",
        "--max-batch-tokens", "4096",
        "--rescue-count", "2",
    ])
    assert args.structural_shortlist_size == 8
    assert args.rescue_count == 2
```

Add rejection cases for shortlist smaller than retrieval count, rescue count
above two, non-finite pair threshold, nonpositive batch tokens, and MPS requested
on a non-MPS host. Add `SimpleNamespace` to the test module's existing
`from types import ...` import.

- [ ] **Step 2: Run CLI tests and confirm new flags are unavailable**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'cli or explicit_mps or launcher' -v
```

Expected: failures for unrecognized hybrid flags.

- [ ] **Step 3: Extend `ExperimentConfig` and CLI validation**

Add exact fields:

```python
structural_shortlist_size: int = 16
pair_margin_threshold: float = 0.0
max_batch_tokens: int = 4096
rescue_count: int = 2
rescue_max_new_tokens: int = 32
rescue_timeout_seconds: float = 20.0
counterbalance_conditions: bool = True
capture_diagnostic_embeddings: bool = False
```

Validate every value before model load or output directory creation. Add
`--diagnostic-embeddings` as a `store_true` flag whose default leaves corpus and
proof hidden-vector capture off. Preserve
the user-specified condition labels `none`, `random`, and `analogy`, while the
manifest describes random as `structurally_matched_random_v1`.

- [ ] **Step 4: Record full scoring and frozen-model provenance**

Add to `config.json` and `summary.json`:

- proof, schema, compatibility, pair-prompt, tactic-prompt, and rescue-prompt versions;
- resolved model revision, explicit device, dtype, hidden dimension, and a
  deterministic first-4096-parameter L2 fingerprint;
- warm-up status and condition execution order per theorem;
- structural shortlist and pair threshold settings;
- batch token and rescue limits;
- fixed-pool versus rescue aggregates;
- operational fallback counts by reason.

Perform one unmeasured scoring warm-up after model load. Counterbalance
condition order by a deterministic hash of theorem ID and seed, but always use
the no-retrieval result for subgroup labels after all paired trials finalize.

- [ ] **Step 5: Update the single-command launcher**

Keep user arguments last so they override defaults. Add:

```sh
--device mps \
--structural-shortlist-size 16 \
--pair-margin-threshold 0.0 \
--max-batch-tokens 4096 \
--rescue-count 2 \
```

The launcher continues to select the three mixed smoke problems only when the
user supplied no `--problem` flag.

- [ ] **Step 6: Run CLI, launcher, manifest, and dry-run tests**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'cli or launcher or manifest or dry_run' -v
```

Expected: all selected tests pass; explicit MPS unavailability is an actionable
setup error.

- [ ] **Step 7: Commit configuration and provenance**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py run_experiment.sh
git commit -m "feat: configure hybrid retrieval experiment"
```

---

### Task 9: Verify end-to-end behavior and document staged execution

**Files:**
- Modify: `tests/test_standalone_analogy_recovery.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: complete hybrid experiment CLI and output schema.
- Produces: preservation evidence, real-Lean smoke coverage, local-Qwen MPS smoke evidence, and user-facing commands for 3-problem and 25-problem runs.

- [ ] **Step 1: Add an end-to-end fake-model experiment test**

```python
def test_hybrid_three_condition_experiment_is_paired_and_parseable(tmp_path: Path) -> None:
    module = load_script()
    output = module.run_experiment(
        hybrid_config(module, tmp_path),
        lambda config: DeterministicHybridModel(module),
        lambda config: CompletingVerifier(module),
    )
    trials = [json.loads(line) for line in (output / "trials.jsonl").read_text().splitlines()]
    assert {trial["condition"] for trial in trials} == {"none", "random", "analogy"}
    attempts = [json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()]
    initial = [item for item in attempts if item["prefix"] == []]
    assert len({tuple(item["candidates"]) for item in initial}) == 1
    summary = json.loads((output / "summary.json").read_text())
    assert "fixed_pool_solved" in summary["conditions"]["analogy"]
    assert "rescue_solved" in summary["conditions"]["analogy"]
```

Add an information-flow spy mapping that raises if reference proofs are accessed
before summary evaluation. Allow independently generated tactic strings to
equal a reference tactic.

- [ ] **Step 2: Add a real-Lean three-condition smoke test**

Use the pinned project and fake scoring model so the test remains fast. Assert:

```python
assert all(trial["status"] == "complete" for trial in trials)
assert config["lean"]["version"].startswith("Lean (version 4.29.1")
assert attempts_by_condition["random"][0]["structural_shortlist_ids"] == \
       attempts_by_condition["analogy"][0]["structural_shortlist_ids"]
```

- [ ] **Step 3: Run the new integration tests and complete suite**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'hybrid_three_condition or real_lean' -v
.venv/bin/pytest -q
```

Expected: the focused tests pass and the full suite reports zero failures.

- [ ] **Step 4: Run real input and Lean dry-run validation**

Run:

```bash
./run_experiment.sh --dry-run
```

Expected: exit code `0`; output reports the actual poetry and physics inputs,
pinned Lean 4.29.1, three mixed smoke problems, three conditions, structural
shortlist size 16, retrieval count 4, and device `mps` without loading Qwen.

- [ ] **Step 5: Run the real local-Qwen MPS smoke experiment**

Run outside the restricted sandbox so Metal is visible:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ./run_experiment.sh \
  --problem imp_intro_01 \
  --problem imp_intro_03 \
  --problem and_intro_05
```

Expected: a new non-overwriting result directory with nine finalized trials,
`run_complete: true`, `device: mps`, finite pair/tactic scores, identical shared
candidate pools per state, and separate fixed-pool/rescue fields. This command
tests experiment mechanics; analogy is not required to win.

- [ ] **Step 6: Document smoke, 25-problem, and interpretation commands**

Add README commands:

```bash
# Default three-problem smoke
./run_experiment.sh

# All twenty-five mixed-difficulty benchmark problems
./run_experiment.sh \
  --problem and_intro_01 --problem and_intro_02 --problem and_intro_03 \
  --problem and_intro_04 --problem and_intro_05 \
  --problem imp_intro_01 --problem imp_intro_02 --problem imp_intro_03 \
  --problem imp_intro_04 --problem imp_intro_05 \
  --problem or_intro_01 --problem or_intro_03 --problem or_intro_04 \
  --problem or_intro_05 --problem or_intro_06 \
  --problem iff_intro_01 --problem iff_intro_02 --problem iff_intro_03 \
  --problem iff_intro_04 --problem iff_intro_05 \
  --problem not_intro_01 --problem not_intro_02 --problem not_intro_03 \
  --problem not_intro_04 --problem not_intro_05

# Three-seed replication after the 25-problem run is operationally sound
./run_experiment.sh --seed 0 --seed 1 --seed 2
```

Document the primary solve-rate metric, matched-random control, fixed-pool
versus rescue split, MPS requirement, output files, and the fact that a null
analogy result is a valid experiment result.

- [ ] **Step 7: Re-run document-linked tests and the full suite**

Run:

```bash
.venv/bin/pytest tests/test_standalone_analogy_recovery.py -k 'launcher or direct_script or hybrid_three_condition' -v
.venv/bin/pytest -q
git diff --check
```

Expected: zero test failures and no whitespace errors.

- [ ] **Step 8: Commit documentation and end-to-end coverage**

```bash
git add tests/test_standalone_analogy_recovery.py README.md
git commit -m "test: verify hybrid analogy experiment"
```

---

## Final verification

- [ ] Run the complete automated suite:

```bash
.venv/bin/pytest -q
```

- [ ] Validate the actual standalone inputs and Lean toolchain:

```bash
./run_experiment.sh --dry-run
```

- [ ] Run the three-problem local-Qwen smoke outside the sandbox with offline model loading:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ./run_experiment.sh \
  --problem imp_intro_01 \
  --problem imp_intro_03 \
  --problem and_intro_05
```

- [ ] Inspect the newest result directory and confirm:

```bash
python3 -m json.tool results/NEW_RUN/config.json >/dev/null
python3 -m json.tool results/NEW_RUN/summary.json >/dev/null
test "$(wc -l < results/NEW_RUN/trials.jsonl)" -eq 9
rg '"device": "mps"' results/NEW_RUN/config.json
rg '"fixed_pool_solved"|"rescue_solved"' results/NEW_RUN/summary.json
```

- [ ] Confirm the branch contains only intentional commits and no disposable probe files:

```bash
git status --short
git log --oneline --decorate -12
find . -maxdepth 1 -name '.spike_*' -print
```
