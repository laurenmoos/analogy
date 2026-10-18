# Tactic Quality and Analogy Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent incomplete and name-confused Lean tactics, let stuck-only analogy correct Lean-rejected proposals, and rerun the same three matched examples with complete reporting.

**Architecture:** Extend parsing with a narrowly typed incomplete-pool failure and a proof-state-aware tactic canonicalizer. Use that shared canonicalizer in both ordinary and analogy paths, then turn the analogy fallback into a bounded correction loop. Keep the prompt card compact and make the report derive its conclusions from run records.

**Tech Stack:** Python 3.12, pytest, Lean 4 verifier, MLX/Qwen experiment runner.

**Spec:** `docs/superpowers/specs/2026-10-17-tactic-quality-and-analogy-refinement-design.md`

## Global Constraints

- Ordinary search configuration must be identical in both experimental arms.
- Analogy remains stuck-only with three total model generations per stuck event.
- The tactic card remains at most 200 characters.
- Do not commit during this plan because the shared `main` checkout already contains interdependent uncommitted experiment work.
- Preserve all unrelated working-tree changes.

## Review Focus

- Mixed incomplete and valid pools must backfill valid entries before `top_k` and renormalize scores.
- Standalone valid tactics and suggestion tactics such as `intro`, `simp`, `exact?`, and `apply?` must not be rejected.
- Multi-goal states and complex branch continuations must not receive unsafe binder rewrites.
- Lean timeout/internal-error results must not trigger analogy correction.
- Report prose must reflect the new run rather than the previous hard-coded 1/3 versus 2/3 result.

---

### Task 1: Candidate Completeness and Conditional Repair

**Files:**
- Modify: `src/analogy/inference/parser.py`
- Modify: `src/analogy/inference/model_policy.py`
- Modify: `tests/test_model.py`
- Modify: `tests/test_model_provider.py`

**Interfaces:**
- Produces: `IncompleteCandidatePool(ModelOutputError)`, exact-head completeness validation, and ordinary ranker repair limited to that exception.
- Consumes: existing `PromptRunner.run` selective `schema_errors` behavior.

- [ ] Write failing parser tests for mixed-pool filtering/backfill, all-incomplete typed failure, valid standalone tactics, and exact-prefix suggestion tactics.
- [ ] Run the focused tests and confirm failures are caused by missing filtering.
- [ ] Implement exact-head filtering before truncation and normalized discarded-candidate metadata.
- [ ] Run focused parser tests to green.
- [ ] Write a failing ranker test proving one repair occurs only for `IncompleteCandidatePool` and traces record both attempts.
- [ ] Implement conditional repair messaging and tracing without broadening malformed-JSON retries.
- [ ] Run `pytest -q tests/test_model.py tests/test_model_provider.py`.

### Task 2: Proof-State-Aware Names and Compact Card

**Files:**
- Modify: `src/analogy/inference/parser.py`
- Modify: `src/analogy/inference/prompts.py`
- Modify: `src/analogy/solver/model_policy.py`
- Modify: `src/analogy/solver/rescue.py`
- Modify: `tests/test_model.py`
- Modify: `tests/test_prompts.py`
- Modify: `tests/test_base_model_rescue.py`

**Interfaces:**
- Consumes: exact-head validation from Task 1.
- Produces: `normalize_lean4_tactic(text, proof_state=None)` semantic canonicalization shared by both model paths.

- [ ] Write failing tests for conjunction semantic names, collision suffixes, same-line conjunction reference rewriting, bare disjunction normalization, unsafe disjunction continuation pass-through, first-goal-only parsing, and fallback names.
- [ ] Run focused tests and confirm the old neutral names/name erasure cause the failures.
- [ ] Implement conservative first-goal declaration parsing, semantic-name derivation, and state-aware canonicalization before dedupe/duplicate checks.
- [ ] Run focused normalization tests to green.
- [ ] Replace card tests with failing expectations for complete-operand guidance, semantic conjunction names, and absence of `analogy_left`, `analogy_right`, and disjunction splitting hints.
- [ ] Implement the revised at-most-two-recipe, 200-character card and update both model paths to pass proof state into normalization.
- [ ] Run `pytest -q tests/test_model.py tests/test_prompts.py tests/test_base_model_rescue.py`.

### Task 3: Analogy Lean-Feedback Retry Loop

**Files:**
- Modify: `src/analogy/solver/rescue.py`
- Modify: `tests/test_base_model_rescue.py`

**Interfaces:**
- Consumes: state-aware analogy parsing from Task 2 and `LeanInfrastructureResult` from `solver.lean_adapter`.
- Produces: bounded correction prompts for ordinary Lean rejection and accepted-but-discarded attempts.

- [ ] Replace the old one-rejection termination test with failing tests for rejection-then-success, discard-then-success, three-call exhaustion, bounded diagnostic text, and immediate infrastructure termination.
- [ ] Run focused rescue tests and confirm current first-valid-tactic return behavior fails them.
- [ ] Implement latest-failure correction messages, continue within the existing call loop, and preserve one rollout per generation/evaluation.
- [ ] Run `pytest -q tests/test_base_model_rescue.py tests/test_lean_adapter.py`.

### Task 4: Dynamic Report and Matched Rerun

**Files:**
- Modify: `.artifacts/experiment-configs/hardest3/render_detailed_report.py`
- Regenerate: `.artifacts/experiment-configs/hardest3/reasoning-steps.md`
- Regenerate: `.artifacts/experiment-configs/hardest3/comparison.md`

**Interfaces:**
- Consumes: solve JSON records and rollout traces produced by Tasks 1–3.
- Produces: a report whose counts, problem findings, retry description, and runtimes reflect the current run.

- [ ] Add a failing structural check or focused test proving summary solve counts and analogy submission language derive from supplied records.
- [ ] Remove hard-coded prior outcomes and render retry/evaluation details from the run records.
- [ ] Run the report-focused check to green.
- [ ] Run the full unit suite with `pytest -q` and resolve only regressions caused by this plan.
- [ ] Run `.venv/bin/python .artifacts/experiment-configs/hardest3/run_comparison.py` with GPU access.
- [ ] Regenerate the detailed report and verify all six problem-arm sections, retry diagnostics, solve totals, and descriptive runtime caveat.

