# Explicit Solver Run Recipes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make vanilla and analogy-assisted proof runs readable end to end in one place, as two explicit procedural recipes in `solver/run.py`.

**Architecture:** `solver/run.py` will expose separate `run_vanilla` and `run_analogy_assisted` workflows. Each function will visibly iterate over problems and call one clearly named action per line: initialize the proof, configure the appropriate model policy, run the model-policy/Lean search, build and write the problem record, update the checkpoint, and accumulate the report. The search state machine and tactic-level iteration remain in `episode.py`; the recipes describe that work through a plainly named call so the existing class boundaries stay intact.

**Tech Stack:** Python 3.11+, dataclasses, pytest

**Spec:** Conversation-approved procedural shape from 2026-10-18.

## Global Constraints

- Preserve proof-search behavior and the serialized problem, run, and report formats.
- Keep both workflows together in `solver/run.py`; do not introduce a mode class or a separate modes module.
- Make the difference between vanilla and analogy-assisted setup explicit at the call site.
- Keep tactic-level search mechanics in `episode.py` and Lean operations in `lean_gym`.
- Continue writing one problem record per problem and one `SolveRunRecord` artifact per run.
- Leave the existing untracked `docs/paper/` tree untouched.

## Review Focus

- A reader can scan `run_vanilla` and `run_analogy_assisted` and identify every major action in order.
- Vanilla construction has no analogy scratchpad or analogy fallback behavior.
- Analogy-assisted construction shares one scratchpad between its model policy and fallback for a problem, and isolates it across problems.
- `solve_problems` dispatches to exactly one of the two workflows based on configuration.
- Existing CLI output, checkpoint updates, traces, and persisted JSON remain unchanged.

---

### Task 1: Specify the two orchestration paths

**Files:**
- Modify: `tests/test_solve_report.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: behavioral tests for `run_vanilla(...)`, `run_analogy_assisted(...)`, and `solve_problems(...)` dispatch

- [ ] **Step 1: Replace `_run_runtime`-focused tests with tests that call the named vanilla and analogy-assisted workflows.**
- [ ] **Step 2: Assert vanilla contexts omit analogy state and fallback execution, while assisted contexts share one scratchpad within a problem and isolate it between problems.**
- [ ] **Step 3: Assert `solve_problems` selects the vanilla workflow when analogy is disabled and the assisted workflow when it is enabled.**
- [ ] **Step 4: Run the focused tests and verify they fail because the new public workflows do not exist yet.**

### Task 2: Extract readable per-problem actions and recipes

**Files:**
- Modify: `src/analogy/solver/run.py`
- Modify, only if needed to represent vanilla cleanly: `src/analogy/solver/episode.py`

**Interfaces:**
- Produces: `run_vanilla(...) -> RuntimeResult`
- Produces: `run_analogy_assisted(...) -> RuntimeResult`
- Consumes: the existing `run_episode(...)` search engine through a clearly named orchestration helper

- [ ] **Step 1: Introduce a small internal runtime result value carrying the summary, rendered traces, and problem details.**
- [ ] **Step 2: Extract named helpers for proof initialization, model-policy/search execution, problem-record construction and writing, checkpoint recording, and trace accumulation.**
- [ ] **Step 3: Implement `run_vanilla` as a visible problem loop whose body is one named action call per line and constructs no analogy scratchpad.**
- [ ] **Step 4: Implement `run_analogy_assisted` beside it with explicit scratchpad creation and analogy-aware model-policy/fallback construction.**
- [ ] **Step 5: If required, make the episode fallback optional and derive fallback availability from its presence, removing the redundant `analogy_enabled` flag without changing search behavior.**
- [ ] **Step 6: Run the focused report, CLI, and episode tests until they pass.**

### Task 3: Make final run persistence follow the selected recipe

**Files:**
- Modify: `src/analogy/solver/run.py`
- Modify: `tests/test_solve_report.py`

**Interfaces:**
- Consumes: the selected workflow result
- Produces: the existing `SolveRunRecord`, `run.json`, and `report.md`

- [ ] **Step 1: Make `solve_problems` visibly choose between `run_vanilla` and `run_analogy_assisted`.**
- [ ] **Step 2: Extract and name the final run-record and report-writing actions so `solve_problems` also reads procedurally.**
- [ ] **Step 3: Verify persisted payloads and reports are byte-for-byte compatible except for unstable timing and generated run IDs.**
- [ ] **Step 4: Run `uv run pytest -q`, `git diff --check`, and inspect the final diff.**
- [ ] **Step 5: Commit the refactor directly to `main`.**
