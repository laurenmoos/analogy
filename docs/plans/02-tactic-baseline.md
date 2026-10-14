# Conventional Tactic Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure a frozen Qwen3-8B policy that proposes top-k tactics at each proof state without analogies.

**Architecture:** Qwen emits a strict ranked JSON candidate list. A concrete `TacticRanker` feeds the existing `LocalRescueSearch`; a Lean adapter verifies candidates. Analogy remains disabled, establishing the control condition and exercising observable stuckness.

**Tech Stack:** Existing analogy package, Qwen adapter, Lean 4.29.1, pytest

**Spec:** `docs/superpowers/specs/2026-10-13-unified-analogical-rl-layout-design.md`

## Global Constraints

- Candidate tactics come only from Qwen's conventional policy prompt.
- Keep analogy and RL disabled.
- Preserve candidate order and normalize probabilities to sum to one.
- Lean is the only authority for tactic validity and proof completion.

## Review Focus

- Duplicate tactic strings are canonicalized before verification.
- NaN, negative, missing, or all-zero scores are rejected.
- A state with no valid candidate is classified as stuck, not complete.
- Search budgets stop deterministically at the configured limit.
- Result records distinguish model failure, invalid tactics, exhaustion, and completion.

---

### Task 1: Parse ranked tactic candidates

**Files:** `src/analogy/model.py`, `src/analogy/records.py`, `tests/test_model.py`

**Interfaces:** Produces `parse_tactic_candidates(text: str, top_k: int) -> tuple[TacticCandidate, ...]`.

- [x] Write table-driven failing tests for valid candidates and every Review Focus score error.
- [x] Implement strict JSON objects of shape `{"tactic": str, "score": float}`; deduplicate canonically, keep the highest score, truncate to `top_k`, and normalize.
- [x] Run focused and full tests.

### Task 2: Connect Qwen and Lean to the existing loop

**Files:** `src/analogy/model.py`, `src/analogy/loop.py`, `src/analogy/lean_gym/verifier.py`, `tests/test_baseline.py`

**Interfaces:**
- Produces `QwenTacticRanker.rank(state: SearchNode) -> tuple[TacticCandidate, ...]`.
- Produces `LeanGymAdapter.run_tactic(state, candidate) -> TacticResult`.

- [x] Write a failing end-to-end test with a deterministic generator and real Lean verifier that requires two search steps.
- [x] Implement the state prompt with current goals, accepted prefix, failed tactics, and strict JSON output schema.
- [x] Adapt Lean Gym results to the existing search records without weakening either validation boundary.
- [x] Run the end-to-end test and all regressions.

### Task 3: Persist the unconditioned baseline

**Files:** `src/analogy/__main__.py`, `src/analogy/records.py`, `config.yaml`, `tests/test_baseline.py`

**Interfaces:** Produces `run_baseline(...) -> BenchmarkRun` containing per-problem outcomes and aggregate counts.

- [x] Write a failing test that pins solved, invalid, repeated-state, stuck, and verifier-call counts.
- [x] Implement stage dispatch for `experiment.stage: baseline` and write immutable JSONL records under `runs/baseline/<run-id>/`.
- [x] Run a fixed-seed pilot large enough to validate search behavior and expose stuck states; do not spend further compute estimating a precise benchmark score.
- [x] Record the pilot outcome and stuck states used by later plans.

Pilot `98d06bc80dff4c23b17ab32b0b68f3c3` was intentionally stopped after 19 of 25 problems: 13 solved, 6 exhausted, and 7 stuck states were recorded across 254 Lean checks. Because the final six problems were not run, 13/19 is a diagnostic pilot result, not a benchmark success rate. The main result is architectural: state-by-state search works and emits the artifacts needed by later plans. Candidate quality is now the clear bottleneck—198 of 254 proposed tactics were rejected by Lean.
