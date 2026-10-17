# Single Runtime Analogy Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every executable proof-search alternative with one conventional controller whose only behavioral switch is `analogy.enabled`, invoking a one-tactic analogy fallback only after conventional stuckness.

**Architecture:** `run_episode` owns one frontier and one verifier path. An `AnalogyFallback` dependency is always present, but the episode calls it only when the boolean is enabled and conventional tactics are stuck; one valid generation yields at most one Lean submission. `solve_problems` executes this runtime once, while comparisons are made by separate on/off invocations outside proof search.

**Tech Stack:** Python 3.12+, frozen dataclasses/protocols, YAML prompt/config loading, pytest, Lean subprocess verifier.

**Spec:** `docs/superpowers/specs/2026-10-17-single-runtime-analogy-fallback-design.md`

## Global Constraints

- `AppConfig.analogy.enabled` is the sole persisted runtime behavior switch.
- CLI `--analogy` and `--no-analogy` override only that boolean.
- Analogy is never called before observable conventional stuckness.
- One stuckness event may make up to `analogy.max_model_calls` schema-generation attempts but may submit at most one analogy-guided tactic to Lean.
- The analogy has no route, cursor, stack, frontier, continuation controller, or alternate verifier path.
- Historical artifact directories are immutable and are not compatibility targets for the retired activation runtime.
- Existing unrelated user changes in the dirty checkout must be preserved.
- No commits are created because the checkout already contains broad uncommitted user work.

## Review Focus

- Invalid generations followed by a valid generation must cause exactly one Lean call, covered in Task 2.
- A disabled run must make zero analogy model calls even at a stuck state, covered in Task 1.
- A non-stuck enabled run must make zero analogy model calls, covered in Task 1.
- CLI overrides must not mutate cached configuration or change any other field, covered in Task 3.
- Legacy condition/mode/activation entry points must be absent rather than silently accepted, covered in Task 4.

---

### Task 1: Boolean-gated shared episode

**Files:**
- Modify: `src/analogy/solver/episode.py`
- Modify: `tests/test_episode.py`

**Interfaces:**
- Consumes: existing `GenericRescueRunner.search(...) -> GenericRescueOutcome`.
- Produces: `EpisodeContext(..., analogy_enabled: bool, analogy_fallback: GenericRescueRunner)` and unchanged `run_episode(...) -> SearchResult`.

- [ ] **Step 1: Write failing episode tests**

Add tests asserting: disabled + stuck makes zero fallback calls; enabled + non-stuck makes zero fallback calls; enabled + stuck calls fallback once after the ordinary Lean call; fallback progress re-enters the same frontier.

- [ ] **Step 2: Verify the tests fail for the missing required boolean contract**

Run: `uv run pytest -q tests/test_episode.py`

- [ ] **Step 3: Implement the boolean gate**

Make `analogy_enabled` the only gate. Remove optional-runner semantics and call the always-present fallback only inside the existing stuckness branch.

- [ ] **Step 4: Verify the episode tests pass**

Run: `uv run pytest -q tests/test_episode.py`

### Task 2: One-shot analogy fallback

**Files:**
- Modify: `src/analogy/solver/rescue.py`
- Modify: `prompts.yaml`
- Replace: `tests/test_base_model_rescue.py`

**Interfaces:**
- Consumes: proof state, goal, conventional pool/results, evaluator, cancellation token.
- Produces: `AnalogyFallback(generator, *, max_model_calls: int, seed: int, trace_sink=None)` implementing `GenericRescueRunner`; schema has exactly `analogy`, `predicted_state`, and singular `tactic`.

- [ ] **Step 1: Write failing one-shot fallback tests**

Assert strict singular-tactic parsing, retry after invalid JSON, exactly one Lean submission after the first valid generation, no retry after Lean rejection, cancellation before generation, and typed trace preservation.

- [ ] **Step 2: Verify the tests fail against the mode-based multi-tactic runner**

Run: `uv run pytest -q tests/test_base_model_rescue.py`

- [ ] **Step 3: Implement `AnalogyFallback` and the singular prompt**

Delete `mode`, direct prompt selection, family rotation as control flow, multi-proposal evaluation, and Lean-feedback retry loops. Keep schema repair bounded by `max_model_calls`; once parsing succeeds, evaluate one analogical candidate and return.

- [ ] **Step 4: Verify fallback and prompt tests pass**

Run: `uv run pytest -q tests/test_base_model_rescue.py tests/test_prompts.py`

### Task 3: Single-run solve and CLI override

**Files:**
- Modify: `src/analogy/solver/inference.py`
- Modify: `src/analogy/__main__.py`
- Modify: `src/analogy/runtime.py`
- Modify: `src/analogy/records.py`
- Replace: `tests/test_cli.py`
- Replace: `tests/test_solve_report.py`

**Interfaces:**
- Consumes: `AppConfig`, problems, one generator, one verifier.
- Produces: `SolveRun(run_id, analogy_enabled, inference, elapsed_seconds, output_directory)`; `solve_problems(...)` executes once; per-problem results expose `analogy_fallback_count` as actual Lean submissions.

- [ ] **Step 1: Write failing CLI and solve tests**

Assert one internal runtime invocation, on/off parsing defaults to config, immutable override of only `analogy.enabled`, singular report output, and fallback-count serialization.

- [ ] **Step 2: Verify the tests fail against paired execution**

Run: `uv run pytest -q tests/test_cli.py tests/test_solve_report.py`

- [ ] **Step 3: Implement one-run composition**

Replace paired result/comparison logic with `SolveRun`, pass the boolean into `EpisodeContext`, build `AnalogyFallback` unconditionally, and write one run directory/report labelled by the boolean.

- [ ] **Step 4: Verify CLI and solve tests pass**

Run: `uv run pytest -q tests/test_cli.py tests/test_solve_report.py tests/test_runtime.py`

### Task 4: Prune alternate executable paths and legacy selectors

**Files:**
- Delete: `src/analogy/experiments/activation_*.py`
- Delete: `src/analogy/transfer/generated_loop.py`
- Modify/Delete: condition-dispatch portions of `src/analogy/training/`
- Modify: `src/analogy/config.py`
- Modify: `config.yaml`
- Modify: `prompts.yaml`
- Modify: `README.md`
- Delete/Replace: tests that exclusively specify retired activation, direct-rescue, generated-route, shuffled-condition, and paired-arm behavior
- Modify: `tests/test_config.py`
- Modify: `tests/test_dependencies.py`

**Interfaces:**
- Consumes: Task 1-3 shared runtime and typed `RescueRollout` records.
- Produces: strict `AnalogyConfig(enabled: bool, max_model_calls: int)`; no executable condition/mode/route API; offline reward/record consumers may remain if they do not control search.

- [ ] **Step 1: Write failing absence and strict-config tests**

Assert the CLI rejects `activation-eval`, config rejects removed analogy keys, source contains no runtime condition/mode selectors, and dependency imports no retired modules.

- [ ] **Step 2: Verify the pruning tests fail**

Run: `uv run pytest -q tests/test_config.py tests/test_dependencies.py tests/test_cli.py`

- [ ] **Step 3: Delete/disconnect retired paths and migrate config/docs**

Remove activation execution, direct rescue, prompted/shuffled/generated route controllers, paired solve machinery, and their dedicated tests. Preserve historical `.artifacts` directories. Keep only training utilities that consume the shared rollout/reward records without executing proof search.

- [ ] **Step 4: Verify focused pruning tests pass**

Run: `uv run pytest -q tests/test_config.py tests/test_dependencies.py tests/test_cli.py tests/test_episode.py tests/test_base_model_rescue.py`

### Task 5: Whole-tree verification and fresh review

**Files:**
- Modify only files required by failures attributable to Tasks 1-4.

**Interfaces:**
- Consumes: completed implementation.
- Produces: a passing supported suite and evidence that obsolete runtime paths are absent.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -q`

- [ ] **Step 2: Run static repository checks**

Run: `git diff --check`

Run: `rg -n "activation-eval|PairedInferenceRun|FeedbackRescueRunner|direct_rescue|prompted_analogy|shuffled_analogy|generated_domain|condition=.*analogy|mode=.*analogy" src tests config.yaml prompts.yaml README.md`

Expected: no active runtime matches; any retained offline terminology must be reviewed and justified.

- [ ] **Step 3: Request a fresh whole-change review**

Ask a fresh reviewer to compare the diff to the design and report only concrete correctness, pruning, or test-coverage defects.

- [ ] **Step 4: Resolve findings and rerun verification**

Repeat `uv run pytest -q` and `git diff --check`; report exact counts and any deliberately retained offline utility.
