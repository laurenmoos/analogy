# Remove the Inference Namespace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove `inference` as a project namespace and result label without changing proof-search behavior or breaking existing run artifacts.

**Architecture:** Model-facing modules move intact to `analogy.model`, while proof-search composition moves to `analogy.solver.run`. Runtime result types adopt solve-oriented names, and artifact readers accept the legacy `inference` key while writers emit `summary`.

**Tech Stack:** Python 3.11+, dataclasses, pytest, setuptools

**Spec:** `docs/superpowers/specs/2026-10-18-remove-inference-namespace-design.md`

## Global Constraints

- Preserve proof-search behavior and CLI behavior.
- Do not retain import aliases for the retired namespaces.
- Preserve compatibility with existing `run.json` files containing the top-level `inference` key.
- Do not rewrite historical specs and plans.

## Review Focus

- A clean install must discover `analogy.model` and must not discover `analogy.inference`; Task 1 adds an import-discovery assertion.
- Relative imports inside the moved MLX backend must still resolve; Task 1 runs all model and prompt tests.
- New run artifacts must contain `summary` and omit `inference`; Task 2 adds an artifact assertion.
- Missing problem indexes must rebuild from both new and legacy artifacts; Task 2 tests each key independently.
- CLI reports must read the renamed `SolveRun.summary` field without output changes; Task 2 runs CLI and report tests.

---

### Task 1: Rehome model-facing modules

**Files:**
- Move: `src/analogy/inference/__init__.py` to `src/analogy/model/__init__.py`
- Move: `src/analogy/inference/model_provider.py` to `src/analogy/model/model_provider.py`
- Move: `src/analogy/inference/mlx_model.py` to `src/analogy/model/mlx_model.py`
- Move: `src/analogy/inference/parser.py` to `src/analogy/model/parser.py`
- Move: `src/analogy/inference/prompt_runner.py` to `src/analogy/model/prompt_runner.py`
- Move: `src/analogy/inference/prompts.py` to `src/analogy/model/prompts.py`
- Modify: all production and test imports of these modules
- Modify: `src/analogy/model/model_provider.py` dynamic MLX import path
- Test: `tests/test_dependencies.py`
- Test: model, prompt, parser, runtime, and solver unit tests that import the moved modules

**Interfaces:**
- Consumes: the existing public symbols from the six `analogy.inference` modules
- Produces: the same symbols at the corresponding `analogy.model` import paths

- [ ] **Step 1: Extend `test_retired_execution_packages_are_absent`**

Assert `find_spec("analogy.inference") is None` and `find_spec("analogy.model") is not None`.

- [ ] **Step 2: Run the dependency test and verify it fails**

Run: `uv run pytest tests/test_dependencies.py -q`
Expected: FAIL because `analogy.inference` still exists and `analogy.model` does not.

- [ ] **Step 3: Move the package and update imports**

Move all six modules, change internal and external imports from `analogy.inference` to `analogy.model`, change `import_module("analogy.inference.mlx_model")` to `import_module("analogy.model.mlx_model")`, and update the package docstring to describe model generation, prompting, and output parsing.

- [ ] **Step 4: Run focused model-facing tests**

Run: `uv run pytest tests/test_dependencies.py tests/test_model.py tests/test_model_provider.py tests/test_prompt_runner.py tests/test_prompts.py tests/test_runtime.py tests/test_analogy_state.py tests/test_base_model_rescue.py tests/test_semantic.py tests/test_episode.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/analogy tests
git commit -m "refactor: rehome model runtime"
```

### Task 2: Rename solve orchestration and result vocabulary

**Files:**
- Move: `src/analogy/solver/inference.py` to `src/analogy/solver/run.py`
- Modify: `src/analogy/records.py`
- Modify: `src/analogy/__main__.py`
- Modify: `src/analogy/problem_index.py`
- Modify: imports and assertions in `tests/test_cli.py` and `tests/test_solve_report.py`
- Modify: `tests/test_problem_index.py`
- Test: `tests/test_dependencies.py`

**Interfaces:**
- Consumes: model symbols at `analogy.model.*` from Task 1
- Produces: `SolveStatus`, `ProblemResult`, and `SolveSummary` in `analogy.records`; `SolveRun.summary: SolveSummary` and `solve_problems(...) -> SolveRun` in `analogy.solver.run`
- Produces: new `run.json` payloads with `summary`; `ProblemIndex._reconstruct(...)` accepts `summary` first and legacy `inference` second

- [ ] **Step 1: Add failing namespace and compatibility tests**

Extend `tests/test_dependencies.py` to assert `find_spec("analogy.solver.inference") is None` and `find_spec("analogy.solver.run") is not None`. In `tests/test_problem_index.py`, parameterize reconstruction over `result_key` values `"summary"` and `"inference"`. In `tests/test_solve_report.py`, assert a newly written `run.json` contains `summary` and does not contain `inference`.

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `uv run pytest tests/test_dependencies.py tests/test_problem_index.py tests/test_solve_report.py tests/test_cli.py -q`
Expected: FAIL on the new module paths, new artifact key, and renamed interfaces.

- [ ] **Step 3: Rename the records and orchestration module**

Rename `InferenceStatus` to `SolveStatus`, `InferenceProblemResult` to `ProblemResult`, `InferenceRun` to `SolveSummary`, and `SolveRun.inference` to `SolveRun.summary`. Move the solver module to `run.py`, update all imports and references, and keep CLI/report text unchanged.

- [ ] **Step 4: Migrate artifact writing with backward-compatible reading**

Write `asdict(summary)` under `run.json["summary"]`. In `ProblemIndex._reconstruct`, read `payload["summary"]` when it is a dictionary; otherwise fall back to a dictionary-valued `payload["inference"]`; malformed values continue to be ignored.

- [ ] **Step 5: Run focused solve tests**

Run: `uv run pytest tests/test_dependencies.py tests/test_problem_index.py tests/test_solve_report.py tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/analogy tests
git commit -m "refactor: rename proof search run results"
```

### Task 3: Verify the retired terminology boundary

**Files:**
- Modify: `README.md` only if current documentation names a retired path or result field

**Interfaces:**
- Consumes: the renamed namespaces and result interfaces from Tasks 1 and 2
- Produces: a codebase with no live imports or paths using the retired architecture name

- [ ] **Step 1: Search live code for stale references**

Run: `rg -n 'analogy\.inference|solver\.inference|from \.\.inference|from \.inference|Inference(Status|ProblemResult|Run)|\.inference\b|["'"']inference["'"']' src tests README.md`
Expected: no matches except semantically correct library APIs such as `torch.inference_mode()`; update accidental stale references before continuing.

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 3: Inspect the final diff**

Run: `git diff --check && git status --short && git diff --stat`
Expected: no whitespace errors; only the planned namespace, interface, compatibility, test, and documentation changes are present.

- [ ] **Step 4: Commit any final documentation cleanup**

```bash
git add README.md docs/superpowers/specs/2026-10-18-remove-inference-namespace-design.md docs/superpowers/plans/2026-10-18-remove-inference-namespace.md
git commit -m "docs: describe proof search module ownership"
```
