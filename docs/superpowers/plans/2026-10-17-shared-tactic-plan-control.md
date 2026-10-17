# Shared Tactic-Plan Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare analogy-on with analogy-off using identical frozen tactic-plan execution and separate plan/recovery metrics.

**Architecture:** Add serializable tactic-plan and cursor records, freeze one plan for each active arm after analogy selection, and execute both through one controller before optional ordinary recovery. Retain historical enum values for decoding while restricting new evaluation and reports to two active conditions.

**Tech Stack:** Python dataclasses, pytest, Lean subprocess verifier, existing JSONL artifact journal.

**Spec:** `docs/superpowers/specs/2026-10-17-shared-tactic-plan-control-design.md`

## Global Constraints

- Preserve exact anchor reuse and old condition enum decoding.
- Both arms share plan bounds, Lean limits, recovery limits, and checkpoint semantics.
- New plan records are durable before Lean execution.
- Use TDD for every behavior change.

## Review Focus

- A rejected middle plan step must not be counted as frozen-plan success.
- A plan exhausting the Lean limit must not start recovery.
- Missing analogy selection must retain a visible failed analogy-on outcome.
- Trial journals must contain exactly two arms in both budget views.
- Old enum values must still decode without becoming active conditions.

---

### Task 1: Frozen plan records and cursor

**Files:**
- Create: `src/analogy/experiments/activation_plan.py`
- Modify: `src/analogy/experiments/activation_runner.py`
- Modify: `src/analogy/experiments/activation_report.py`
- Test: `tests/test_activation_plan.py`

**Interfaces:**
- Produces: `TacticPlan`, `TacticPlanStep`, `TacticPlanCursor`.
- Consumes: normalized tactics, arm, source state hash, and optional analogy candidate ID.

- [x] Write failing validation and cursor-transition tests.
- [x] Run focused tests and confirm failure.
- [x] Implement bounded immutable plans and cursor advancement.
- [x] Run focused tests and confirm pass.

### Task 2: Two active arms and pre-Lean plan freeze

**Files:**
- Modify: `prompts.yaml`
- Modify: `src/analogy/experiments/activation_runner.py`
- Test: `tests/test_activation_runner.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: Task 1 plan records.
- Produces: `FrozenCell.plans` for `neutral` and `activation_selected`; `ARMS` with two conditions.

- [x] Write failing tests for two-arm ordering, ordered vanilla plans, and frozen plans.
- [x] Run focused tests and confirm failure.
- [x] Freeze both plans before Lean and remove prompted/shuffled execution.
- [x] Run focused tests and confirm pass.

### Task 3: Shared execution and separate metrics

**Files:**
- Modify: `src/analogy/experiments/activation_runner.py`
- Test: `tests/test_activation_runner.py`
- Test: `tests/test_activation_final_contracts.py`

**Interfaces:**
- Consumes: frozen plans and cursor.
- Produces: plan status/results/calls plus recovery status/results/calls on every outcome.

- [x] Write failing tests for plan completion, rejection, exhaustion, and matched recovery.
- [x] Run focused tests and confirm failure.
- [x] Replace arm-specific route handling with the shared controller and metrics.
- [x] Run focused tests and confirm pass.

### Task 4: Two-arm artifacts and reports

**Files:**
- Modify: `src/analogy/experiments/activation_report.py`
- Test: `tests/test_activation_report.py`
- Test: `tests/test_activation_end_to_end.py`

**Interfaces:**
- Consumes: two-arm trials and new plan/recovery fields.
- Produces: restartable four-row-per-cell journals and analogy-on/off summaries.

- [x] Write failing journal and report tests.
- [x] Run focused tests and confirm failure.
- [x] Update validation, summary rows, paired counts, and Markdown prose.
- [x] Run focused tests and confirm pass.

### Task 5: Verification

**Files:**
- Verify all touched production, prompt, artifact, and test files.

- [x] Run `uv run pytest -q` and require zero failures.
- [x] Run `git diff --check` on touched files.
- [x] Obtain one fresh review focused on budgets, artifact resume, and arm symmetry.
