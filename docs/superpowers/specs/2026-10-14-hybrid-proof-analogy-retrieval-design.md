# Hybrid Proof-Structure and Natural-Language Analogy Retrieval Design

## Goal

Improve Lean tactic recovery by retrieving cross-domain transformations using a
deterministic proof representation together with natural language. Tactic
recovery is the primary objective. Interpretability is a required diagnostic,
but it must not displace Lean-verified recovery as the success criterion.

This design replaces global cosine retrieval over a single Qwen last-token
embedding. It preserves local Qwen weight and hidden-state inspection while
using deterministic Lean structure to prevent semantically unrelated poetry or
physics examples from entering the ranking prompt.

## Motivation

The first standalone experiment established two separate facts:

1. Teacher-forced tactic-string scoring on MPS is a viable stable ranking path.
   It avoids autoregressive ranking token budgets and ranking timeouts while
   retaining access to the frozen Qwen checkpoint and its activations.
2. The current analogy representation is not discriminative. Distinct proof
   needs retrieve nearly identical cake/action transformations with cosine
   similarities around `0.9`, and those transformations do not improve tactic
   ordering.

The retrieval failure is therefore a representation problem rather than a Lean
verification problem. The new design makes formal proof structure the primary
retrieval constraint and uses Qwen only for corpus-side schema extraction and
soft pairwise semantic scoring.

## Scope

### Included

- A deterministic structural representation produced from elaborated Lean
  expressions and prior verifier outcomes.
- A deterministic natural-language rendering of that proof representation.
- Offline Qwen extraction of poetry and physics transitions into a strict,
  shared transformation schema.
- Structural shortlisting followed by teacher-forced Qwen proof–analogy pair
  scoring.
- Teacher-forced scoring of actual tactic strings, not generated ranking
  indices.
- Three matched retrieval conditions: none, structurally matched random, and
  analogy.
- A fixed shared candidate pool for the primary reranking comparison.
- A separately reported rescue phase that may propose at most two additional
  tactics after the shared pool is exhausted.
- Lean verification as the only correctness authority.
- Cached proof representations and transformation records, plus optional Qwen
  hidden vectors retained for inspection and ablation rather than retrieval.
- Standalone execution on both solved and unsolved benchmark examples.
- Explicit local MPS inference and frozen-weight provenance.

### Excluded

- Training or fine-tuning Qwen.
- Learned retrieval weights in the first experiment.
- Reference-proof access during inference.
- Treating reference-tactic agreement as correctness.
- Letting retrieval silently alter, remove, or rewrite the shared candidate
  pool.
- Requiring an analogy when no structurally compatible record exists.
- Making a rescue-generation timeout invalidate the completed reranking result.

## Experimental boundary

One trial is one theorem, condition, and seed. All three conditions receive the
same:

- theorem and current Lean state;
- deterministic proof signature and its natural-language rendering;
- frozen Qwen model revision;
- conventional tactic candidate tuple;
- candidate-generation seed and parameters;
- verifier-call, search, and wall-clock budgets;
- Lean toolchain and verifier implementation.

The conditions differ only in retrieved corpus context:

1. **No retrieval:** no corpus transformations.
2. **Matched random:** random records from the structurally eligible shortlist.
3. **Analogy:** the highest teacher-forced pair-scored records from the same
   structurally eligible shortlist.

This boundary ensures that access to a proof-need description is not confounded
with access to retrieval. Random retrieval is structurally matched so it tests
semantic selection within plausible transformations rather than comparing
analogy with obviously irrelevant noise.

## Architecture

### Offline corpus path

```text
poetry/physics before→after pair
    → constrained Qwen schema extraction
    → strict schema validation
    → canonical TransformationRecord
    → optional cached hidden representation for diagnostics
    → immutable corpus index
```

### Online proof path

```text
elaborated Lean goal, local context, and failed attempts
    → deterministic ProofSignature
    → deterministic natural-language rendering
    → structural eligibility filter and score
    → bounded shortlist
    → batched teacher-forced Qwen pair scoring
    → retrieved transformations
    → batched teacher-forced tactic-string scoring
    → Lean verification
    → optional bounded rescue generation after pool exhaustion
```

## Deterministic Lean proof representation

### Source of structure

The proof signature must be derived from Lean's elaborated expressions, not from
Qwen and not by heuristically interpreting prose. The verifier probe emits a
machine-readable structural summary for every open goal and relevant local
hypothesis. A small Lean-side serializer recognizes logical constructors and
otherwise emits a stable opaque application/atom category.

The serializer recognizes at least:

- implication and universal quantification;
- conjunction, disjunction, and biconditional;
- equality and negation;
- existential quantification;
- propositions, applications, constants, bound variables, and local variables.

Local names and benchmark story names do not participate in structural
identity. Alpha-equivalent states must produce identical signatures.

### `ProofSignature`

Each open state produces an immutable record with:

- `target_root`: the outer logical constructor or `atom`;
- `target_shape`: a canonical bounded-depth expression tree;
- `hypothesis_roots`: a sorted multiset of outer constructors;
- `hypothesis_shapes`: canonical bounded-depth trees;
- `target_occurrence_paths`: paths where the target or its canonical subterms
  occur in hypotheses;
- `candidate_operations`: a nonempty set drawn from the shared operation enum;
- `input_arity` and `output_arity`;
- `permutation`: `none`, `swap`, `general`, or `unknown`;
- `information_flow`: any applicable values from `preserve`, `select`, `add`,
  `discard`, and `restructure`;
- `failed_operation_classes`: operation classes represented by rejected or
  repeated candidate tactics at the current state;
- `goal_count`;
- a versioned canonical serialization and SHA-256 fingerprint.

`candidate_operations` describes structurally possible moves; it is not an
oracle for the correct tactic. Initial rules include:

- implication/forall target → `introduce`;
- conjunction target → `combine`;
- disjunction target → `select_branch`;
- a target occurring inside a structured hypothesis → `decompose` or
  `select`;
- target and available proposition differing only by child order → `reorder`;
- matching function premise and target result → `specialize`;
- equality capable of changing a target subterm → `rewrite`;
- target identical to a hypothesis → `reuse`.

Rules may produce multiple operation classes. Failed tactic classes remove
confidence but do not delete a structurally possible operation unless Lean has
shown that exact operation/state combination to repeat.

### Natural-language proof rendering

Natural language on the proof side is rendered deterministically from the
signature. It is not generated. For example:

> The target is atomic. A conjunction hypothesis contains the target as its
> second component. A plausible next transformation selects one component and
> discards the other.

The renderer uses a versioned template, contains no local identifiers or tactic
names, and is stable for identical signatures. This text supplies Qwen with a
readable bridge to corpus summaries without allowing the model to hallucinate
formal proof facts.

## Corpus transformation representation

### `TransformationRecord`

Qwen converts each raw poetry or physics `before → after` transition into one
JSON object with:

- `operation`: one shared operation enum value;
- `input_arity` and `output_arity`: `zero`, `one`, `two`, `many`, or `unknown`;
- `permutation`: `none`, `swap`, `general`, or `unknown`;
- `information_flow`: a nonempty list from the shared enum;
- `focus_before` and `focus_after`: short noun-role phrases;
- `domain_neutral_summary`: one concise sentence describing the structural
  change without Lean syntax;
- source domain and immutable provenance supplied by the loader, not Qwen;
- extractor prompt version, model revision, and cache key.

The shared operation enum initially contains:

- `introduce`;
- `decompose`;
- `select`;
- `combine`;
- `select_branch`;
- `reorder`;
- `specialize`;
- `rewrite`;
- `reuse`;
- `replace`;
- `remove`;
- `restructure`;
- `unknown`.

The extractor must return strict JSON. Unknown keys, missing required keys,
invalid enum values, blank summaries, tactic names, non-finite metadata, and
duplicate canonical records are rejected. Rejected records never enter the
retrieval index. Extraction occurs once per corpus record and is cached.

## Structural shortlisting

### Eligibility

A corpus transformation is structurally eligible when:

- its operation is in `ProofSignature.candidate_operations`, or one of the
  versioned compatible operation pairs; and
- no known arity, permutation, or information-flow field directly conflicts
  with the proof signature.

`unknown` corpus fields neither pass nor fail a match; they contribute no
score. If no record is eligible, retrieval returns an explicit empty result and
the condition preserves baseline ordering.

### Compatibility score

Eligible records receive a deterministic score in `[0, 1]` composed of:

- operation agreement: `0.40`;
- information-flow agreement: `0.20`;
- input/output arity agreement: `0.15`;
- permutation agreement: `0.15`;
- source/target shape-role agreement: `0.10`.

Weights, enum compatibility pairs, and all component scores are versioned and
written to trial output. Ties break by transformation ID. The top configurable
`structural_shortlist_size` records continue to semantic pair scoring.

Matched-random retrieval samples from this exact shortlist with a deterministic
seed. Analogy retrieval sees no larger or different eligibility pool.

## Teacher-forced Qwen pair scoring

The pair scorer receives:

- the canonical proof signature;
- its deterministic natural-language rendering;
- one validated transformation schema;
- the transformation's domain-neutral summary.

It evaluates fixed completions representing `relevant` and `irrelevant` in a
single batched forward pass. The pair score is the normalized log-likelihood
margin between those completions. No reasoning or ranking tokens are generated.

All shortlisted pairs are scored together when memory permits. Deterministic
micro-batches are allowed when the batch would exceed a configured token limit.
The output records both completion likelihoods, their margin, token counts,
batch boundaries, model revision, device, and selected hidden-layer metadata.

Analogy retrieval selects the highest pair scores with deterministic ID tie
breaks. A configurable minimum margin may reject all analogies. The first
experiment uses a fixed threshold selected before outcome evaluation; it is not
tuned theorem by theorem.

## Stable tactic reranking

### Shared pool

The conventional candidate pool is created exactly once per proof-state
fingerprint and cached across conditions. Candidate generation is outside the
retrieval comparison. A generation failure is recorded once for the state and
affects every condition equally.

### Candidate-string likelihood

Each condition constructs the same ranking prompt except for retrieved
transformations. Every actual tactic string is appended as a candidate
completion. One batched Qwen forward pass computes token log likelihoods.

The primary tactic score is mean token log likelihood, which normalizes for
candidate length. Sum likelihood and token count are also recorded. Stable
ties preserve the conventional candidate order.

The ranker does not generate indices, explanations, or reasoning tokens. Qwen
runs frozen under inference mode. Local execution explicitly requests `mps` on
supported Apple Silicon and fails fast rather than silently falling back to CPU.

## Lean verification and rescue generation

Lean independently tests tactics in ranked order. Outcomes remain:

- rejected;
- accepted with a repeated state;
- accepted with a novel open state;
- theorem-completing;
- timeout or verifier failure.

Only Lean completion counts as a solved theorem. An accepted tactic is an
intermediate search event.

After every member of the shared pool has failed, repeated, or exhausted its
search branch, each condition may invoke the same rescue generator with its own
context. This preserves a fair comparison:

- no retrieval rescue receives no transformations;
- matched-random rescue receives its random transformations;
- analogy rescue receives its pair-scored transformations.

The rescue generator may return at most two one-line tactic strings under a
small fixed token and time budget. Duplicates and tactics already in the shared
pool are discarded. Lean verifies every proposal. Rescue results are reported
separately and never overwrite the fixed-pool reranking result. A rescue model
timeout records zero rescue gains but leaves the trial valid.

## Caching and inspectability

Cache keys include every input that changes meaning:

- raw source identity and content hash;
- canonical Lean state fingerprint;
- proof-signature and natural-language renderer versions;
- corpus extraction prompt and schema versions;
- Qwen model name and resolved revision;
- hidden layer, pooling rule, dtype, and dimension;
- pair-scoring and tactic-scoring prompt versions.

The experiment caches:

- validated `TransformationRecord` objects;
- optional corpus-summary hidden vectors and metadata for inspection and
  representation ablations; these vectors do not choose neighbors in the
  primary experiment;
- `ProofSignature` objects;
- deterministic proof renderings;
- optional proof-rendering hidden vectors under the same diagnostic-only rule;
- optional pair scores when the complete key matches.

Ordinary JSONL traces contain vector metadata and content hashes, not full
4096-dimensional activations. Full vectors remain in cache artifacts. Every run
records a small deterministic parameter fingerprint, resolved model revision,
device, and layer metadata so local-weight use is auditable.

For each retrieval and tactic decision, the trace explains:

- which structural fields matched and conflicted;
- every compatibility-score component;
- pairwise Qwen likelihoods and margin;
- retrieved source, domain, and transformation summary;
- conventional, no-retrieval, random, and analogy tactic orders;
- the score delta for each tactic after context;
- every Lean verification outcome;
- whether completion came from reranking or rescue.

## Leakage prevention

Reference proofs are loaded into a separate evaluation mapping and cannot enter:

- proof signatures;
- natural-language proof renderings;
- Qwen prompts;
- candidate generation;
- corpus extraction;
- retrieval eligibility or scores;
- reranking or rescue generation.

Solved benchmark examples mask every known tactic and are reconstructed from
the theorem statement plus Lean feedback. Unsolved examples use the identical
path without reference evaluation metadata. Corpus records may not contain
benchmark proofs or Lean tactic corpora.

## Failure handling

- Invalid Lean structural output is a verifier/protocol failure, not a prompt
  fallback to guessed structure.
- Malformed corpus extraction is excluded and counted by reason.
- An empty structural shortlist produces no retrieval.
- Pair-scoring failure preserves the no-retrieval candidate order for that
  condition and records an operational failure.
- Non-finite likelihoods invalidate only the affected scoring batch and preserve
  the conventional order.
- MPS unavailability is a setup error when `mps` is explicitly requested.
- Lean timeouts and internal failures remain distinct from tactic rejection.
- Completed trials and JSONL records survive interruption.
- Rescue failures do not invalidate completed reranking observations.

## Evaluation protocol

### Staged runs

1. **Smoke:** three mixed-difficulty problems under all conditions.
2. **Initial comparison:** twenty-five mixed-difficulty problems, all three
   conditions, one seed.
3. **Replication:** the same twenty-five problems with three seeds, only after
   the initial comparison completes without systemic operational failure.

Problems, conditions, and seeds are paired. Condition order is deterministic
and recorded. Model warm-up is performed before timed inference, and condition
order may be counterbalanced across problems to prevent sustained-load order
from masquerading as a treatment effect.

### Primary metric

- Lean theorem solve rate under a fixed verifier-call budget.

### Secondary metrics

- top-ranked tactic acceptance;
- rank of the first Lean-accepted tactic;
- verifier calls, expansions, and wall-clock time to completion;
- paired newly solved and regressed theorem IDs;
- fixed-pool solve rate;
- incremental rescue solve rate;
- structural shortlist size and empty-shortlist rate;
- structural compatibility components;
- pair-score distribution and selected-versus-rejected margin;
- neighbor diversity across distinct proof signatures;
- ranking changes and per-tactic likelihood deltas;
- operational failure and fallback counts.

The result may be negative. Experiment validity does not require analogy to
win. A useful positive result is a paired solve-rate improvement or fewer
verifier calls without a material regression in solved theorems. Interpretive
claims must name the structural fields and Qwen score changes that support them.

## Testing

### Unit tests

- Lean expression serialization is deterministic and alpha-renaming invariant.
- Supported logical constructors map to the expected canonical trees.
- Proof-operation inference returns documented sets without tactic names.
- Deterministic natural-language rendering is stable and name-free.
- Transformation JSON validation accepts only the exact schema and enums.
- Duplicate and malformed corpus records are excluded deterministically.
- Structural eligibility, component scoring, weights, and tie-breaking match
  fixed examples.
- Matched random and analogy retrieval use the identical shortlist.
- Pair scoring calculates the expected teacher-forced log-likelihood margin.
- Candidate scoring masks prompt and padding tokens and length-normalizes only
  completion tokens.
- Candidate pools are immutable and shared across conditions.
- Cache keys change for every semantic version input and remain stable
  otherwise.
- Empty retrieval and scoring failures preserve baseline order.
- Rescue is invoked only after shared-pool exhaustion and contributes at most
  two novel tactics.
- Reference tactics never enter inference records or prompts.

### Integration tests

- A real pinned-Lean test emits a structural signature for an open goal and
  verifies alpha-equivalent signatures.
- A fake-model experiment demonstrates different none, matched-random, and
  analogy orderings over one immutable tactic pool.
- A real-Lean experiment proves only Lean determines tactic acceptance and
  completion.
- A local Qwen smoke test on MPS confirms the resolved revision, frozen
  parameters, hidden dimension, finite pair scores, and finite tactic scores.
- A cache replay produces identical retrieval IDs and rankings without corpus
  re-extraction.
- A rescue timeout leaves the reranking trial complete and reportable.

## Acceptance criteria

Implementation is complete when:

1. all preservation and focused tests pass;
2. the deterministic Lean signature integration test passes;
3. the actual poetry and physics inputs produce a nonempty validated index with
   exclusion counts;
4. a real-Qwen MPS smoke run completes all three conditions without
   autoregressive ranking;
5. every condition in a proof state uses the same candidate tuple and budget;
6. every selected analogy includes structural and Qwen scoring provenance;
7. a three-problem real-Lean smoke run writes reranking and rescue outcomes
   separately;
8. information-flow tests confirm the reference-proof mapping is never read by
   inference or copied into a prompt (a model may independently propose the same
   tactic text);
9. the twenty-five-problem run can be launched through the existing single
   command entry point.

These criteria establish a valid experiment. They do not require analogy to
outperform the controls.
