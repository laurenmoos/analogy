# Utilities Package for Checkpointing and Records Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move shared records and checkpointing into `analogy.utils`, replacing the stateful `ProblemIndex` facade with a small data record and utility functions.

**Architecture:** Existing record definitions move intact to `utils.records`. Existing checkpoint persistence remains byte-for-byte compatible at its external boundaries, while `utils.checkpoint` exposes a plain `Checkpoint` dataclass plus load, select, and record functions used by runtime and solver composition.

**Tech Stack:** Python 3.11+, dataclasses, JSON, pytest, setuptools

**Spec:** `docs/superpowers/specs/2026-10-18-utils-checkpoint-records-design.md`

## Global Constraints

- Preserve proof-search, CLI, restart, and persistence behavior.
- Keep `.artifacts/problem-index.json` and its schema unchanged.
- Keep existing `run.json` checkpoint key names and legacy result-key support unchanged.
- Do not retain aliases for the retired Python modules or `ProblemIndex`.
- Do not change the contents or validation behavior of the shared record types.

## Review Focus

- A clean install must expose both `analogy.utils.records` and `analogy.utils.checkpoint` while the old top-level modules are absent; Task 1 adds discovery assertions.
- Relative imports from model and solver packages must reach `utils.records` correctly; Task 1 runs all record-consuming tests.
- An empty but valid checkpoint must still be persisted and selectable; Task 2 retains the discovery-manifest test.
- Existing `problem-index.json` files and old `run.json` files must remain readable; Task 2 runs the current persistence and reconstruction cases unchanged.
- A failed problem must be recorded before the next problem starts so interrupted runs remain resumable; Task 2 keeps per-result recording in the solver loop and runs solve/runtime tests.

---

### Task 1: Create the utilities package and move shared records

**Files:**
- Create: `src/analogy/utils/__init__.py`
- Move: `src/analogy/records.py` to `src/analogy/utils/records.py`
- Modify: every production and test import of `analogy.records` or relative `records`
- Test: `tests/test_dependencies.py`
- Test: all tests importing shared record types

**Interfaces:**
- Consumes: all existing public definitions in `analogy.records`
- Produces: the same definitions at `analogy.utils.records`; no compatibility module at `analogy.records`

- [ ] **Step 1: Add namespace discovery assertions**

Extend `test_retired_execution_packages_are_absent` to assert `find_spec("analogy.records") is None` and `find_spec("analogy.utils.records") is not None`.

- [ ] **Step 2: Run the dependency test and verify it fails**

Run: `uv run pytest tests/test_dependencies.py -q`
Expected: FAIL because `analogy.records` still exists and `analogy.utils.records` does not.

- [ ] **Step 3: Move records and update imports**

Create `analogy.utils`, move `records.py` without changing its definitions, and update model modules, solver modules, the CLI, and tests to import through `analogy.utils.records` using correct relative paths.

- [ ] **Step 4: Run record-consuming tests**

Run: `uv run pytest tests/test_dependencies.py tests/test_model.py tests/test_model_provider.py tests/test_prompt_runner.py tests/test_prompts.py tests/test_base_model_rescue.py tests/test_cli.py tests/test_episode.py tests/test_lean_adapter.py tests/test_proof_inventory.py tests/test_semantic.py tests/test_solve_report.py tests/test_state_diversity.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/analogy tests
git commit -m "refactor: group shared records under utils"
```

### Task 2: Replace the problem index facade with checkpoint utilities

**Files:**
- Move and rewrite: `src/analogy/problem_index.py` to `src/analogy/utils/checkpoint.py`
- Modify: `src/analogy/runtime.py`
- Modify: `src/analogy/__main__.py`
- Modify: `src/analogy/solver/run.py`
- Move and modify: `tests/test_problem_index.py` to `tests/test_checkpoint.py`
- Modify: `tests/test_runtime.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_solve_report.py`
- Modify: `tests/test_dependencies.py`

**Interfaces:**
- Consumes: `Problem` and `AppConfig`; existing `problem-index.json` and compatible run artifacts
- Produces: `Checkpoint`; `load_checkpoint(config, problems) -> Checkpoint`; `select_problems(checkpoint, mode) -> tuple[Problem, ...]`; `record_status(checkpoint, problem_id, status) -> None`
- Produces: `Runtime.checkpoint: Checkpoint`; solver keyword `checkpoint: Checkpoint | None`

- [ ] **Step 1: Convert tests to the desired functional interface**

Move `tests/test_problem_index.py` to `tests/test_checkpoint.py`; replace `ProblemIndex.load(...)`, `.select(...)`, and `.record(...)` with `load_checkpoint(...)`, `select_problems(...)`, and `record_status(...)`. Update runtime, CLI, and solve tests to use the `checkpoint` field and keyword. Extend dependency discovery to assert `analogy.problem_index` is absent and `analogy.utils.checkpoint` exists.

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `uv run pytest tests/test_dependencies.py tests/test_checkpoint.py tests/test_runtime.py tests/test_cli.py tests/test_solve_report.py -q`
Expected: collection or assertion failures because the new checkpoint module, functions, field, and keyword do not yet exist.

- [ ] **Step 3: Implement the checkpoint data and functions**

Move persistence helpers into `utils/checkpoint.py`. Define the five-field `Checkpoint` dataclass and implement `load_checkpoint`, `select_problems`, and `record_status` with the existing validation, reconstruction, and atomic-write behavior. Keep persistence private in `_persist_checkpoint(checkpoint: Checkpoint) -> None`.

- [ ] **Step 4: Update runtime and solver composition**

Replace `ProblemIndex` construction and methods with the functional API. Rename `Runtime.problem_index` and solver keyword parameters to `checkpoint`, call `record_status` after each outcome, and continue writing the unchanged `problem_index_signature` and `problem_index_fingerprints` artifact keys from checkpoint fields.

- [ ] **Step 5: Run focused checkpoint tests**

Run: `uv run pytest tests/test_dependencies.py tests/test_checkpoint.py tests/test_runtime.py tests/test_cli.py tests/test_solve_report.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/analogy tests
git commit -m "refactor: expose functional checkpoint utilities"
```

### Task 3: Align current documentation and verify the package boundary

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the utilities package and functional checkpoint API from Tasks 1 and 2
- Produces: current documentation that calls the persisted state a checkpoint while accurately naming the unchanged artifact file

- [ ] **Step 1: Update checkpoint terminology in current documentation**

Describe `.artifacts/problem-index.json` as the checkpoint file and explain that it is updated after each attempted problem. Do not rewrite historical plans or specs.

- [ ] **Step 2: Search live code for retired imports and names**

Run: `rg -n -e 'analogy\.records' -e 'from \.\.records' -e 'from \.records' -e 'analogy\.problem_index' -e 'from \.problem_index' -e '\bProblemIndex\b' -e '\bproblem_index\b' src tests README.md`
Expected: only compatibility strings `problem_index_signature` and `problem_index_fingerprints`, plus tests that assert retired imports are absent.

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 4: Inspect the final diff**

Run: `git diff --check && git status --short && git diff --stat`
Expected: no whitespace errors and only the planned utilities, checkpoint API, imports, tests, and documentation changes.

- [ ] **Step 5: Commit documentation and design artifacts**

```bash
git add README.md docs/superpowers/specs/2026-10-18-utils-checkpoint-records-design.md docs/superpowers/plans/2026-10-18-utils-checkpoint-records.md
git commit -m "docs: describe checkpoint utilities"
```
