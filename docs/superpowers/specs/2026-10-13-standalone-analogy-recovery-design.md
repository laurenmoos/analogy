# Standalone Analogy Retrieval Experiment Design

## Goal

Build one inspectable script that tests whether structurally similar transitions from poetry and physics improve Lean tactic recovery. The experiment must run directly on the bundled benchmark and must not depend on any earlier numbered implementation plan, generated run artifact, or `analogy` package module.

The experiment compares three conditions under matched inputs and budgets:

1. no retrieval;
2. random cross-domain retrieval;
3. analogy-based cross-domain retrieval.

Lean theorem completion is the primary outcome. Acceptance of an individual tactic is an intermediate observation, not proof that the tactic advances the theorem.

## Scope

### Included

- A single executable script at `scripts/standalone_analogy_recovery.py`.
- Direct loading of the bundled benchmark problems and reference proofs.
- Direct loading of Gutenberg poetry lines and TRIP physical stories.
- Construction and caching of cross-domain transition records.
- Qwen-based structural descriptions and vector embeddings.
- A fixed tactic candidate pool shared by all three conditions at each proof state.
- Best-first, Lean-verified proof search with a fixed budget.
- JSONL trial records and a JSON summary.
- Reporting over the complete benchmark and over baseline-solved and baseline-unsolved subgroups.

### Excluded

- Imports from `src/analogy`.
- Inputs produced by Plans 0–7 or by an earlier experiment run.
- Training, fine-tuning, reinforcement learning, latent intervention, or tactic invention by the retrieval conditions.
- Treating exact agreement with the reference proof as the success criterion.
- A database, vector-search service, notebook, web service, or new Python package.

## Runtime dependencies

The script uses Python's standard library, the already selected local Qwen model through `torch` and `transformers`, and the installed `lake`/`lean` executables. These are runtime requirements of the standalone experiment, not dependencies on prior repository milestones.

The script receives all paths through command-line flags with repository-relative defaults:

- benchmark problems;
- reference proofs;
- pinned Lean project;
- poetry archive;
- TRIP JSONL file;
- output directory;
- optional cache directory.

It must fail with a direct, actionable message when an input or executable is unavailable.

## Experimental unit and leakage boundary

One trial is one benchmark theorem under one retrieval condition and one seed. The target theorem's reference tactics are hidden from every model prompt and every candidate-generation input. Reference tactics are loaded for evaluation only after a trial result has been finalized.

The same theorem statement, seed, model revision, candidate-generation parameters, proof-search budget, retrieval count, and Lean verifier are used for all three conditions.

At each reached Lean state, the script generates the conventional tactic candidate pool once. All conditions receive the same immutable candidate tuple for that state. Retrieval may only change the ordering of this tuple. It may not add, rewrite, or remove tactic text.

This boundary isolates tactic selection from tactic availability. A failed trial may therefore record that the needed tactic was absent from the candidate pool.

## Numbered method

The implementation preserves the intent of the proposed six stages while making their inputs and outputs testable.

### 1. Mask known tactics

For every benchmark theorem, retain the binders and target but keep its complete reference tactic sequence outside all inference prompts. The sequence is evaluation metadata only. The experiment begins from the theorem's initial Lean state and attempts to construct a complete proof without revealing the reference.

### 2. Embed the incomplete proof

Render a canonical query containing:

- overall theorem target;
- current Lean goals;
- accepted tactic prefix discovered by the current condition;
- failed candidates at this state.

Ask Qwen for a short structural description that avoids Lean tactic names, then embed that description using a deterministic hidden-state pooling rule. Normalize the vector and retain its model revision, layer, dimension, and source-text hash in the trial record.

### 3. Retrieve structurally similar poetry or physics examples

Build cross-domain transitions as follows:

- poetry: adjacent nonblank lines from the same Gutenberg work;
- physics: adjacent sentences from each TRIP story, retaining plausibility and conflict metadata as provenance.

Each transition is stored as a source-domain, source identifier, before text, after text, and structural transformation. The analogy condition selects the highest cosine-similarity transformations. The random condition selects the same number from the same eligible index using a recorded deterministic seed. The no-retrieval condition selects none.

No condition may retrieve benchmark proofs or the target theorem's reference tactics.

### 4. Extract how examples evolve or resolve

Qwen converts each raw `before → after` transition into one compact, domain-neutral transformation description. The prompt forbids Lean syntax and asks what changed structurally rather than what the passage means thematically.

Transformations and their normalized embeddings are cacheable by a key containing:

- input file identity and content hash;
- transition identity;
- model name and revision;
- structural prompt version;
- embedding layer and pooling rule.

The cache is an optimization only. A run with an empty cache must reconstruct the same deterministic index.

### 5. Transfer transformations into candidate Lean tactics

For each condition, Qwen receives the incomplete proof description, the identical indexed candidate tuple, and either no transformations, randomly selected transformations, or nearest transformations. It returns only a ranked list of candidate indices.

The parser rejects malformed output, duplicate indices, and out-of-range indices. Missing candidates are appended in their original conventional order. The resulting order is the only effect retrieval has on proof search.

### 6. Verify with Lean

Try ranked candidates through an isolated temporary Lean file in the pinned Lean project. Record each candidate as:

- rejected by Lean;
- accepted with a repeated state;
- accepted with a novel open state;
- theorem-completing.

Only a theorem-completing result counts as solved. Novel accepted states may enter the best-first frontier until the shared expansion or verifier-call budget is exhausted.

## Cross-domain index construction

Index construction must be bounded and reproducible. The script exposes maximum poetry and physics transition counts and samples eligible records deterministically before model processing. It balances the two domains when both have enough records and records actual per-domain counts.

Malformed JSON, blank text, duplicate transitions, cross-work poetry pairs, and stories with fewer than two usable sentences are skipped with aggregate counters. A fully empty index is an error for random and analogy conditions but does not prevent an explicitly requested no-retrieval-only run.

## Model behavior

The model is frozen and placed in evaluation mode. Generation and embedding use no gradients. Deterministic generation is the default. If sampling is enabled explicitly, every call derives and records its seed from the trial seed, theorem identifier, proof-state fingerprint, and operation name.

The embedding rule is fixed for one run: selected hidden layer, final non-padding token, float32 conversion, and L2 normalization. Zero or non-finite vectors are rejected.

Model prompts have separate version strings for:

- conventional candidate generation;
- proof-need structural description;
- example transformation extraction;
- transformation-conditioned candidate ranking.

These versions are written to the summary so cached and uncached runs remain auditable.

## Lean search and state identity

The script writes a temporary theorem containing the accepted prefix plus the candidate being tested and invokes Lean through `lake env lean`. Diagnostics and the remaining goal state are normalized to remove temporary paths.

State identity combines the theorem identifier with the normalized Lean goal text. A candidate that returns the same state does not count as progress. A novel state is eligible for the frontier even when it has more goals; completion, not a hand-written goal-distance heuristic, remains authoritative.

The search uses deterministic best-first ordering with stable tie breaks. All conditions share the same maximum expansions, maximum verifier calls, candidate-pool size, per-call timeout, and theorem timeout.

## Conditions and subgroup analysis

Run the no-retrieval condition first for every theorem. Its completion result defines the subgroup label for that seed:

- baseline-solved;
- baseline-unsolved.

Then run random and analogy retrieval with the matched candidate pools and budgets. Report paired changes rather than comparing unrelated totals.

Primary metrics:

- theorem completion count and rate;
- completion delta against no retrieval;
- newly solved and regressed theorem identifiers;
- completion delta within baseline-solved and baseline-unsolved subgroups.

Secondary diagnostics:

- verifier calls and expansions;
- rejected, repeated, and novel accepted tactics;
- whether a reference tactic appeared anywhere in each fixed pool;
- exact reference-tactic overlap in the final proof;
- retrieved domains, similarities, and transformation identifiers;
- ranking changes relative to conventional order.

Exact reference overlap is never treated as correctness because Lean may accept a different complete proof.

## Output format

Each run creates a new non-overwriting directory containing:

- `config.json`: resolved CLI arguments, input hashes, environment, model revision, prompt versions, and budgets;
- `transitions.jsonl`: the bounded indexed transition metadata without full activation tensors;
- `trials.jsonl`: one finalized theorem-condition-seed result per line;
- `attempts.jsonl`: Lean attempts, state fingerprints, candidate pools, rankings, and retrieval provenance;
- `summary.json`: aggregate and paired metrics;
- `cache/` only when the user chooses a run-local cache.

Writes use temporary files followed by atomic replacement for finalized JSON files. Existing nonempty run directories are never overwritten.

## Command-line interface

The default command runs all three conditions on all bundled problems. CLI filters support a small smoke run without changing experiment semantics:

- theorem ID selection;
- condition selection;
- seed list;
- transition limits;
- candidate-pool size;
- retrieval count;
- search budgets;
- model name, device, layer, and generation limits;
- input, cache, and output paths.

`--dry-run` validates inputs, parses both external corpora, constructs transitions without loading Qwen, verifies Lean availability, and prints the resolved experiment size.

## Failure handling

A failure in one theorem-condition trial is recorded and does not erase completed trials. Failures distinguish:

- configuration or missing input;
- model loading;
- model timeout;
- malformed model output;
- invalid embedding;
- Lean timeout;
- Lean invocation failure;
- exhausted search;
- empty candidate pool.

Fatal setup failures stop before creating trial claims. Interrupts flush completed JSONL records and mark the run incomplete.

## Testing and acceptance

Tests may import the script as a module, but production execution remains a single file. Unit tests use small fake tokenizers/models and a fake Lean runner to cover:

- strict masking of target reference tactics;
- deterministic transition construction and sampling;
- final-token normalized embeddings;
- cosine and random retrieval behavior;
- strict candidate-index ranking;
- immutable shared pools across conditions;
- repeated-state rejection and novel-state continuation;
- completion-only success classification;
- subgroup and paired metric calculation;
- cache-key invalidation;
- CLI validation and dry-run behavior.

One integration test uses the real pinned Lean project on a small theorem and deterministic fake model outputs. It must demonstrate a fixed pool where the three conditions use different rankings and only Lean determines completion.

The implementation is accepted when:

1. the complete preservation test suite passes;
2. the standalone script's focused tests pass;
3. `--dry-run` succeeds against the actual poetry, TRIP, benchmark, and Lean inputs;
4. a real-Lean smoke experiment writes valid outputs for at least one theorem under all three conditions;
5. inspection confirms that no model prompt or retrieval record contains the target theorem's reference tactics.
