# Visible Solver Recipes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `solver/run.py` consist solely of two readable, executable recipes whose visible loops drive vanilla and analogy-assisted proof search.

**Architecture:** Replace the monolithic `run_episode()` with a mutable search session in `episode.py` that exposes phase operations consumed by the recipes. Move model generation, Lean submission, analogy rescue, recording, and reporting to their owning modules so `run.py` imports real operations rather than defining forwarding wrappers. The CLI selects one recipe directly.

**Tech Stack:** Python 3.11+, dataclasses, pytest

**Spec:** `docs/superpowers/specs/2026-10-18-visible-solver-recipes-design.md`

## Global Constraints

- `solver/run.py` contains exactly two function definitions and no classes.
- Both recipes visibly contain the problem loop and `while search.has_work()` loop.
- Retire `run_episode()` and `solve_problems()`; do not preserve compatibility wrappers.
- Preserve search behavior, persisted artifacts, checkpoint behavior, and CLI output.
- Preserve Python 3.11 compatibility.
- Leave the existing untracked `docs/paper/` tree untouched.

## Review Focus

- Cancellation or expansion-budget termination before the next tactic generation produces the same terminal result.
- Ordinary completion, repeated states, no-progress results, and semantic-refuted successors preserve call ordering and counts.
- Stuckness with unknown, valid, and countermodel semantic assessments preserves recovery, forced moves, and terminal status.
- Multi-attempt analogy rescue preserves generation/Lean interleaving, scratchpad mutation, retry budgets, and rollout evidence.
- Model, verifier, and proof-initialization failures remain per-problem records while later problems continue.

---

### Task 1: Expose an incremental ordinary-search session

**Files:**
- Modify: `src/analogy/solver/episode.py`
- Modify: `src/analogy/solver/model_policy.py`
- Modify: `src/analogy/solver/proof/adapter.py`
- Modify: `tests/test_episode.py`

**Interfaces:**
- Produces: `start_search(...) -> SearchSession`
- Produces: `SearchSession.has_work() -> bool`
- Produces: `generate_tactics_with_model_policy(search) -> TacticBatch`
- Produces: `submit_tactics_to_lean(proof, tactics) -> TacticBatchResult`; batches carry node identity, normalized candidates, and expansion evidence, and results preserve ordered `TacticResult` values
- Produces: `update_search(search, result) -> None`
- Retires: `run_episode(...)`

- [ ] **Step 1: Add failing characterization tests that drive complete, exhausted, cancelled, budget-exhausted, semantic-pruned, forced-move, repeated-state, and no-progress episodes through the incremental operations and compare them with fixed expected `SearchResult` values.**
- [ ] **Step 2: Run `tests/test_episode.py` and verify the new operation imports fail.**
- [ ] **Step 3: Introduce `SearchSession` in `episode.py`, moving the frontier, visited state, evidence accumulators, terminal result, and current expansion phase out of the local variables in `run_episode()`.**
- [ ] **Step 4: Move candidate generation into `generate_tactics_with_model_policy` in `model_policy.py` and ordinary Lean submission into `submit_tactics_to_lean` in `proof/adapter.py`; use typed batch records so neither function duplicates search-state ownership.**
- [ ] **Step 5: Implement `update_search` in `episode.py` to classify submitted results, update the frontier and evidence, resolve semantic recovery, expose forced moves as the next tactic batch, and set the next phase or terminal result without calling Lean.**
- [ ] **Step 6: Delete `run_episode()` and run `tests/test_episode.py`; require all ordinary-search tests to pass.**
- [ ] **Step 7: Commit the incremental ordinary-search API.**

### Task 2: Make analogy rescue an incremental search phase

**Files:**
- Modify: `src/analogy/solver/episode.py`
- Modify: `src/analogy/solver/analogy/state.py`
- Modify: `src/analogy/solver/analogy/rescue.py`
- Modify: `tests/test_episode.py`
- Modify: `tests/test_base_model_rescue.py`

**Interfaces:**
- Consumes: `SearchSession` and `update_search` from Task 1
- Produces: `initialize_analogy_scratchpad() -> AnalogyScratchpad`
- Produces: `update_analogy(search, scratchpad) -> AnalogyScratchpad`
- Produces: `generate_analogy_rescue(search, scratchpad) -> AnalogyRescue`
- Produces: `submit_analogy_rescue_to_lean(proof, rescue) -> AnalogyRescueResult`
- Produces: `SearchSession.is_stuck() -> bool`

- [ ] **Step 1: Add failing tests for analogy completion, progress, rejection, invalid output repair, cancellation, forced domain change, and exhausted retry budget using the incremental rescue operations.**
- [ ] **Step 2: Run the focused episode and rescue tests and verify they fail because the new operations do not exist.**
- [ ] **Step 3: Convert `AnalogyFallback.search` into an incremental rescue object that owns generation/retry state but delegates every Lean call to `submit_analogy_rescue_to_lean`; preserve the order of model generations and Lean results.**
- [ ] **Step 4: Teach `SearchSession` to expose stuck rescue context and accept `AnalogyRescueResult` through the same `update_search` boundary used by ordinary results.**
- [ ] **Step 5: Add scratchpad initialization/update operations in `analogy/state.py` without duplicating scratchpad rules from the existing class.**
- [ ] **Step 6: Run `tests/test_episode.py tests/test_base_model_rescue.py tests/test_analogy_state.py`; require all analogy behavior to pass.**
- [ ] **Step 7: Commit the incremental analogy phase.**

### Task 3: Move recording and reporting out of orchestration

**Files:**
- Create: `src/analogy/solver/run_records.py`
- Create: `src/analogy/solver/report.py`
- Modify: `tests/test_solve_report.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces from `run_records.py`: `SolveRunRecord`, `RunState`, `start_run(...) -> RunState`, `build_problem_record`, `write_problem_record`, `build_run_record`, and `write_run_record`
- Produces from `report.py`: problem-trace and run-report rendering/writing
- Consumes: terminal `SearchSession` values from Tasks 1 and 2

- [ ] **Step 1: Change report and persistence tests to import their APIs from `run_records.py` and `report.py`; verify collection fails.**
- [ ] **Step 2: Move record dataclasses, outcome conversion, public-payload filtering, JSON/checkpoint persistence, and final run construction into `run_records.py`.**
- [ ] **Step 3: Move all trace rendering and Markdown report writing into `report.py`.**
- [ ] **Step 4: Add failure-continuation tests proving initialization, model, and verifier failures write a problem record and do not prevent the next problem from running.**
- [ ] **Step 5: Run `tests/test_solve_report.py tests/test_cli.py`; require persisted JSON and Markdown expectations to pass.**
- [ ] **Step 6: Commit the recording/reporting split.**

### Task 4: Replace orchestration with exactly two visible recipes

**Files:**
- Rewrite: `src/analogy/solver/run.py`
- Modify: `src/analogy/__main__.py`
- Modify: `tests/test_solve_report.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_dependencies.py`

**Interfaces:**
- Consumes: all incremental search, analogy, recording, and reporting operations from Tasks 1–3
- Produces: `run_vanilla(...) -> SolveRunRecord`
- Produces: `run_analogy_assisted(...) -> SolveRunRecord`
- Retires: `solve_problems(...)`

- [ ] **Step 1: Add an AST-based architecture test asserting `run.py` has exactly two function definitions total (including nested scopes), named `run_vanilla` and `run_analogy_assisted`, no class definitions, a problem loop in each, and a `while ...has_work()` loop in each.**
- [ ] **Step 2: Add CLI dispatch tests proving analogy-disabled calls only `run_vanilla` and analogy-enabled calls only `run_analogy_assisted`.**
- [ ] **Step 3: Run the focused tests and verify they fail against the current orchestration.**
- [ ] **Step 4: Rewrite `run.py` so both recipes visibly perform the ordered calls in the spec and contain no supporting definitions.**
- [ ] **Step 5: Update the CLI to select the recipe directly and import `SolveRunRecord` from `run_records.py`.**
- [ ] **Step 6: Run the focused tests, then `uv run --extra dev pytest -q`; require the full suite to pass.**
- [ ] **Step 7: Run `git diff --check`, inspect `run.py` in full, and commit directly to `main`.**
