# Post-Hoc LLM Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Diagnose the existing analogy RL process with an entirely post-hoc LLM audit that cannot affect the experiment.

**Architecture:** A standalone auditor reads completed run artifacts, reconstructs each analogy iteration, and asks a frozen LLM for a strict failure-stage diagnosis. It writes separate audit artifacts and never imports into or executes the training/search control path.

**Tech Stack:** Python 3.11+, MLX Qwen3-8B generator, JSONL, pytest.

**Spec:** `docs/superpowers/specs/2026-10-15-llm-judged-analogy-rl-design.md`

## Global Constraints

- Do not modify analogy generation, proof search, RL, reward, or experiment configuration.
- Audit only completed artifacts after the experiment.
- Never mutate the source run directory.
- Never expose reference proofs to the judge.
- Preserve raw LLM output and parsing failures for inspection.
- Bound calls and make interrupted audits resumable without duplicate rows.

## Review Focus

- Source runs must remain byte-identical before and after auditing.
- Rejected tactics with no successor must still receive useful stage diagnoses.
- Accepted but regressive transitions must not be labeled successful merely because Lean accepted them.
- Malformed judge output must not disappear from failure accounting.
- Resuming after interruption must neither skip nor duplicate completed iteration keys.

---

### Task 1: Strict post-hoc judgment

**Files:**
- Create: `src/analogy/progress_judge.py`
- Create: `tests/test_progress_judge.py`

**Interfaces:**
- Consumes: a normalized `AuditIteration` and `generator.generate(messages, seed=...)`.
- Produces: `AuditStage`, `AuditJudgment`, `JudgeAttempt`, and `LLMProcessJudge.evaluate(iteration) -> JudgeAttempt`.

- [ ] **Step 1: Write failing judgment tests**

Test strict keys and enums, finite score ranges, prompts for accepted and rejected
iterations, inclusion of Lean diagnostics, exclusion of reference proofs, one
successful repair, and preserved raw output on persistent parse failure.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_progress_judge.py`

Expected: FAIL because `analogy.progress_judge` does not exist.

- [ ] **Step 3: Implement the strict judge**

Add immutable input/output records, exact JSON parsing, deterministic operation
seeds derived from run/rollout/iteration identity, concise prompts, and one
bounded schema-repair request.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest -q tests/test_progress_judge.py`

Expected: all judgment tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/analogy/progress_judge.py tests/test_progress_judge.py
git commit -m "feat: judge logged analogy iterations"
```

### Task 2: Immutable, resumable run auditor

**Files:**
- Create: `src/analogy/run_audit.py`
- Create: `tests/test_run_audit.py`
- Modify: `src/analogy/__main__.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `audit_analogy_run(source_run, output_dir, generator, corpus, seed, max_calls) -> AuditSummary`.
- Produces: separate `config.json`, append-only `judgments.jsonl`, finalized `summary.json`, and CLI stage `audit_analogy`.

- [ ] **Step 1: Write failing artifact-loader tests**

Create a synthetic completed run containing accepted, rejected, completed, and
malformed iteration rows. Assert deterministic normalized inputs, clear errors for
missing required files, and safe skipping of non-analogy phases.

- [ ] **Step 2: Run loader tests and verify RED**

Run: `pytest -q tests/test_run_audit.py -k load`

Expected: FAIL because `analogy.run_audit` does not exist.

- [ ] **Step 3: Implement artifact loading and normalization**

Read `config.json`, `analogy_iterations.jsonl`, and `summary.json`; join theorem
targets from the configured corpus by problem ID; normalize tactic outcomes and
diagnostics; identify rows by `(problem_id, phase, rollout_id, iteration_index)`.

- [ ] **Step 4: Write failing execution, resumption, and immutability tests**

Hash every source file before and after the audit. Assert the call cap, one output
row per key, raw failures, resumption without duplicates, aggregate stage counts,
score means, coverage, and atomic final summary creation.

- [ ] **Step 5: Run execution tests and verify RED**

Run: `pytest -q tests/test_run_audit.py -k 'audit or resume or immutable or summary'`

Expected: FAIL because audit execution and persistence are absent.

- [ ] **Step 6: Implement the bounded auditor and CLI**

Create a new output directory before calls, persist its unresolved config, flush
each judgment row, resume from existing keys, stop at `max_calls`, aggregate only
post-hoc metrics, then atomically finalize config and summary. Add CLI arguments
for source run, output directory, seed, and call cap. Document that the audit is
causally disconnected from the experiment.

- [ ] **Step 7: Run focused and complete tests and verify GREEN**

Run: `pytest -q tests/test_progress_judge.py tests/test_run_audit.py tests/test_cli.py`

Expected: all focused tests pass.

Run: `pytest -q`

Expected: the complete suite passes with no new failures.

- [ ] **Step 8: Commit**

```bash
git add src/analogy/run_audit.py src/analogy/__main__.py tests/test_run_audit.py tests/test_cli.py README.md
git commit -m "feat: audit analogy runs post hoc"
```

### Task 3: Collect and audit training and held-out test examples

**Files:**
- Create: `docs/benchmarks/2026-10-15-post-hoc-analogy-audit.md`
- Runtime artifacts: a new audit directory under `runs-fast/` (git-ignored)

**Interfaces:**
- Consumes: completed analogy-condition training/validation and held-out test runs plus the MLX Qwen3-8B generator.
- Produces: separately labeled training and test example sets, post-hoc judgments, a failure-stage summary, concrete reconstructed chains, and follow-up hypotheses.

- [ ] **Step 1: Verify the source runs and offline model**

Record source hashes and validate the existing MLX 8B runtime probe. Select the
latest completed analogy training run containing nonempty `train` or `validation`
iterations and its held-out evaluation containing nonempty `analogy_loop`
iterations. Preserve `train`, `validation`, and `test` source labels.

- [ ] **Step 2: Audit all available analogy-condition examples within a fixed cap**

Run `audit_analogy` with MLX Qwen3-8B over the training/validation source and the
held-out test source. Preserve both audit directories, elapsed times, raw
judgments, parse failures, split labels, and final source hashes. Expected:
source hashes remain unchanged and every eligible analogy-condition row up to
each cap has an output row.

- [ ] **Step 3: Reconstruct and classify the process**

For representative training and test theorems, show `state -> analogy -> proposed
tactic -> Lean result -> LLM diagnosis`; group failures by analogy quality,
tactic translation, Lean execution, and proof progress. Contrast LLM opinion
with objective Lean acceptance and actual completion, and compare diagnosis
distributions between training and held-out test examples.

- [ ] **Step 4: Write a concise evidence-backed report**

Lead with where the existing process goes wrong, then report training-versus-test
coverage and stage counts, accepted-successor and solve context, at least three
concrete chains from each split when available, judge uncertainty, runtime, and
the lowest-risk improvements suggested by repeated diagnoses. Do not claim the
judge improved solver performance.

- [ ] **Step 5: Commit the report**

```bash
git add docs/benchmarks/2026-10-15-post-hoc-analogy-audit.md
git commit -m "test: diagnose analogy rl with llm judge"
```
