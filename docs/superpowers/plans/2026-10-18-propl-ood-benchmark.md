# PropL OOD Benchmark Replacement Implementation Plan

Execution note: the standard `.superpowers/sdd` ledger helper cannot create
its hidden workspace in this managed checkout. Execution therefore keeps the
same RED-GREEN and verification gates in-session, with commits as the durable
task boundaries.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the saturated introductory corpus with a deterministic, verifier-backed 30-problem subset of PropL's online OOD logic benchmark and retire dataset-specific assets and prompt assumptions.

**Architecture:** A stdlib-only importer parses paired PropL CSV exports, applies the pinned compatibility/ranking rules, and emits runtime corpus, reference proofs, and non-model-facing provenance metadata. Runtime continues to load the existing strict corpus schema; tests independently validate source selection invariants and every checked-in reference proof with Lean.

**Tech Stack:** Python 3.12, JSON/YAML, pytest, Lean 4.29.1, PropL CSV data

**Spec:** `docs/superpowers/specs/2026-10-18-propl-ood-benchmark-design.md`

## Global Constraints

- Source only PropL revision `62a13de410999c20f60ec2f3be93457dcaac0d62`, split `test_ood`.
- Check in exactly 30 problems, each with all of `∧`, `∨`, and `→`, at least 15 source-proof tactics, and no one-step `simp` proof.
- Every reference proof must complete under the repository's Lean verifier.
- Keep selection metadata outside model-facing runtime prompts and problem rendering.
- Remove active `lean4game-logic`, curriculum-split, and `hardest3` assets.
- Use exhaustive selection by default and a step budget no smaller than the longest reference proof.

## Review Focus

- Multiline CSV targets must be extracted without changing precedence or Unicode connectives; importer fixture tests assert exact target preservation.
- Repeated state labels and alternative tactic indices must count failed attempts correctly; importer tests cover both `_tactic_0` and `_tactic_1`.
- Nested traces cannot silently enter the flat tactic protocol; importer tests assert their exclusion.
- Corpus and proof IDs must remain one-to-one; active-corpus tests assert exact coverage and 30 unique IDs.
- A source proof accepted by PropL can still fail under this Lean version; the verifier integration test checks all 30 checked-in proofs.

---

### Task 1: Reproducible PropL subset importer

**Files:**
- Create: `scripts/import_propl_ood.py`
- Create: `tests/test_propl_import.py`

**Interfaces:**
- Consumes: paths to PropL `normal/test_ood.csv` and `backtrack/test_ood.csv`.
- Produces: `parse_records(path: Path)`, `select_candidates(normal_path: Path, backtrack_path: Path, limit: int = 30)`, and JSON-ready corpus/proof/metadata records.

- [ ] **Step 1: Write failing importer tests** for exact target/tactic parsing, alternative-index counting, nested-trace exclusion, deterministic ordering, and metadata provenance.
- [ ] **Step 2: Run `uv run pytest tests/test_propl_import.py -q`**; expect import failure because the script does not exist.
- [ ] **Step 3: Implement the stdlib-only importer** with the pinned source revision and explicit selection predicates; verification and one-step `simp` acceptance are supplied as recorded eligibility inputs, not guessed by text heuristics.
- [ ] **Step 4: Run `uv run pytest tests/test_propl_import.py -q`**; expect all tests to pass.
- [ ] **Step 5: Commit** with message `feat: add reproducible PropL OOD importer`.

### Task 2: Replace the active corpus and verification contract

**Files:**
- Modify: `src/analogy/lean_gym/corpus/problems.json`
- Modify: `src/analogy/lean_gym/corpus/reference_proofs.json`
- Modify: `src/analogy/lean_gym/corpus/LICENSE-source`
- Create: `src/analogy/lean_gym/corpus/benchmark_metadata.json`
- Delete: `src/analogy/lean_gym/corpus/curriculum_splits.json`
- Create: `tests/test_propl_corpus.py`
- Modify: `config.yaml`
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: importer output and source IDs selected under Task 1.
- Produces: the strict schema-v1 runtime corpus, exact source proof traces, separate provenance/selection metadata, and an exhaustive default configuration.

- [ ] **Step 1: Write failing corpus tests** asserting 30 PropL OOD records, exact source revision metadata, mixed-connective/minimum-length invariants, no legacy IDs, exhaustive default selection, and a step budget at least the maximum reference length.
- [ ] **Step 2: Run `uv run pytest tests/test_propl_corpus.py tests/test_config.py -q`**; expect failures against the old corpus/config.
- [ ] **Step 3: Generate and check in the 30 selected records**, exact proof traces, metadata, and updated attribution; remove the curriculum split and set `benchmark.selection: exhaustive` with `search.max_steps` equal to 32.
- [ ] **Step 4: Run the focused tests**, then run the all-reference Lean verification test; expect 30 complete proofs.
- [ ] **Step 5: Commit** with message `feat: replace saturated corpus with PropL OOD set`.

### Task 3: Generalize prompts and retire old experiment assets

**Files:**
- Modify: `prompts.yaml`
- Modify: `tests/test_prompts.py`
- Modify: `README.md`
- Modify: `docs/benchmarks/current-model-test-examples.md`
- Modify: `.gitignore`
- Delete: `.artifacts/experiment-configs/hardest3/config.yaml`
- Delete: `.artifacts/experiment-configs/hardest3/problems.json`
- Delete: `.artifacts/experiment-configs/hardest3/run_comparison.py`
- Delete: `.artifacts/experiment-configs/hardest3/render_detailed_report.py`
- Delete: `tests/test_experiment_protocol.py`
- Delete: `tests/test_detailed_report.py`

**Interfaces:**
- Consumes: the active PropL corpus/config from Task 2.
- Produces: dataset-neutral model instructions and current documentation with no active old-dataset artifacts.

- [ ] **Step 1: Update prompt tests first** to require conditional dependency-evidence wording and a broader tactic reminder without the fixed `Or.inl`/`Or.inr`/pair footer.
- [ ] **Step 2: Run `uv run pytest tests/test_prompts.py -q`**; expect failures against current prompt text.
- [ ] **Step 3: Generalize `prompts.yaml`**, remove tracked `hardest3` assets/tests and their ignore exceptions, and update active README/benchmark documentation with PropL provenance and selection rationale.
- [ ] **Step 4: Run `uv run pytest tests/test_prompts.py -q`**, then `uv run pytest -q`; expect the complete suite to pass.
- [ ] **Step 5: Commit** with message `chore: retire saturated benchmark artifacts`.

### Task 4: Final verification and independent review

**Files:**
- Modify only if a verified defect is found.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: a clean, review-approved branch.

- [ ] **Step 1: Run `uv run pytest -q`** and record the exact passing count.
- [ ] **Step 2: Run corpus/reference verification and repository searches** proving all 30 references complete, no active legacy corpus IDs remain, and all model-facing strings live in `prompts.yaml`.
- [ ] **Step 3: Request one fresh-context whole-branch review** against the spec; fix any Important or Critical issue with RED-GREEN tests.
- [ ] **Step 4: Re-run the full suite and inspect `git diff --check` and `git status --short`**.

