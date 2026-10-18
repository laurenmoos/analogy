# Report Package Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate solver computation from run reporting and model-facing analogy presentation.

**Architecture:** Replace the top-level report and run-record modules with a focused `solver/report/` package whose public facade preserves the five recipe calls. Move formatted analogy block behavior from computational state classes into an `analogy/protocol.py` model-I/O boundary.

**Tech Stack:** Python 3.11+, dataclasses, pytest

**Spec:** `docs/superpowers/specs/2026-10-18-report-package-boundary-design.md`

## Global Constraints

- `solver/run.py` continues to contain exactly `run_vanilla` and `run_analogy_assisted`.
- The two recipes keep their current high-level report calls and behavior.
- Computational case classes contain no HTML, Markdown, prompt-template, JSON, or filesystem presentation logic.
- Generated JSON, Markdown, checkpoint state, and CLI text remain unchanged.
- Existing untracked `docs/paper/` remains untouched.

## Review Focus

- Failed searches still produce the same failure status and public payload.
- JSON output still excludes private model seeds and writes exactly once.
- Checkpoint status is updated only after a problem record is persisted.
- Analogy history never retains `<analogy>` blocks after protocol extraction.
- Empty and active scratchpads render the same prompt context as before.

---

### Task 1: Extract the analogy text protocol

**Files:**
- Create: `src/analogy/solver/analogy/protocol.py`
- Modify: `src/analogy/solver/analogy/state.py`
- Modify: `src/analogy/solver/model_policy.py`
- Modify: `src/analogy/solver/analogy/rescue.py`
- Modify: `tests/test_analogy_state.py`
- Modify: `tests/test_model_provider.py`
- Modify: `tests/test_base_model_rescue.py`

**Interfaces:**
- Consumes: `AnalogyState` and `AnalogyScratchpad` from `analogy.state`.
- Produces: `render_analogy_state(state) -> str`, `extract_analogy_block(text) -> tuple[AnalogyState, str]`, `remove_analogy_blocks(text) -> str`, `render_scratchpad_context(scratchpad) -> str`, and `record_model_history(scratchpad, text) -> None`.

- [ ] Add dependency assertions that `AnalogyState` has no `render` method, `AnalogyScratchpad` has no `context_block` method, and `analogy.protocol` is importable.
- [ ] Run the new assertion and confirm it fails against the current state classes.
- [ ] Move block regexes, parsing, rendering, prompt context, and history sanitization into `analogy/protocol.py`; leave validation and lifecycle mutation in `state.py`.
- [ ] Update model-policy, rescue, and tests to use the protocol functions.
- [ ] Run analogy state, model-policy, rescue, prompt, and dependency tests and confirm they pass.

### Task 2: Create the report package

**Files:**
- Create: `src/analogy/solver/report/__init__.py`
- Create: `src/analogy/solver/report/records.py`
- Create: `src/analogy/solver/report/build.py`
- Create: `src/analogy/solver/report/render.py`
- Create: `src/analogy/solver/report/storage.py`
- Delete: `src/analogy/solver/report.py`
- Delete: `src/analogy/solver/run_records.py`
- Modify: `src/analogy/solver/run.py`
- Modify: `src/analogy/solver/model_policy.py`
- Modify: `src/analogy/__main__.py`
- Modify: `tests/test_solve_report.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_dependencies.py`

**Interfaces:**
- Consumes: `SearchResult`, `Problem`, `AppConfig`, `Checkpoint`, and structured model events.
- Produces: the five-function public facade used by `run.py`, report record classes, `render_problem_trace(...) -> str`, `render_run_report(run, traces) -> str`, and `render_cli_report(run) -> str`.

- [ ] Add dependency tests asserting `analogy.solver.report` is a package and the retired `analogy.solver.run_records` module is absent.
- [ ] Run those assertions and confirm they fail against the current module layout.
- [ ] Move record data classes without adding rendering or filesystem methods.
- [ ] Move search-to-record conversion into `build.py`, pure string rendering into `render.py`, and output/checkpoint effects into `storage.py`.
- [ ] Move prompt-catalog configuration from run storage into model-policy initialization so report storage has no model-protocol side effect.
- [ ] Update the two recipes and CLI to import the report facade; move CLI summary rendering into `report.render`.
- [ ] Update report and CLI tests while preserving exact output assertions.
- [ ] Run report, CLI, run-recipe, runtime, and dependency tests and confirm they pass.

### Task 3: Verify the boundary and behavior

**Files:**
- Modify: `tests/test_dependencies.py`

**Interfaces:**
- Consumes: the completed analogy protocol and report package.
- Produces: architecture assertions preventing presentation logic from returning to computational state modules.

- [ ] Add an AST/import assertion that `analogy/state.py` does not import prompt templates and report record classes expose no rendering or writing methods.
- [ ] Run the architecture test and confirm it passes.
- [ ] Run the full test suite and confirm all tests pass.
- [ ] Run `git diff --check` and inspect the complete diff against both run recipes.
- [ ] Commit the refactor directly to `main`, excluding `docs/paper/`.
