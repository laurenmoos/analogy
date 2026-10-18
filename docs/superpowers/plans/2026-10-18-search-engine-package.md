# Search Engine Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect the one-problem proof-search engine behind a single `solver/search/` package facade while leaving `run.py` as the visible recipe layer.

**Architecture:** Split the existing search vocabulary, result, episode state machine, and setup wiring into focused modules under `solver/search/`. Preserve the existing `analogy.solver.search` public imports through the package facade and delete the retired top-level episode and setup modules.

**Tech Stack:** Python 3.11+, dataclasses, pytest

**Spec:** `docs/superpowers/specs/2026-10-18-search-engine-package-design.md`

## Global Constraints

- `solver/run.py` continues to contain exactly `run_vanilla` and `run_analogy_assisted`.
- `run.py` imports search construction and transitions only from the `solver.search` facade.
- `proof` does not import `search`; dependencies remain one-way from search to proof services.
- Search behavior, result schemas, analogy rescue, reports, and CLI output remain unchanged.
- No compatibility modules remain at `solver.episode` or `solver.search_setup`.
- Existing untracked `docs/paper/` remains untouched.

## Review Focus

- Ordinary tactic batches retain their candidate ordering and width.
- Analogy rescue results still re-enter the same search session and terminal result.
- Cancellation and expansion-budget termination remain unchanged after the move.
- Semantic pruning, recovery, forced moves, and diversity observations remain in `SearchResult`.
- Type-only imports from model and analogy modules do not introduce package cycles.

---

### Task 1: Establish the search package facade

**Files:**
- Create: `src/analogy/solver/search/__init__.py`
- Create: `src/analogy/solver/search/result.py`
- Create: `src/analogy/solver/search/policies.py`
- Create: `src/analogy/solver/search/session.py`
- Create: `src/analogy/solver/search/setup.py`
- Delete: `src/analogy/solver/search.py`
- Delete: `src/analogy/solver/episode.py`
- Delete: `src/analogy/solver/search_setup.py`
- Modify: `src/analogy/solver/run.py`
- Modify: `src/analogy/solver/model_policy.py`
- Modify: `src/analogy/solver/analogy/rescue.py`
- Modify: affected tests

**Interfaces:**
- Consumes: existing `SearchResult`, policies, `EpisodeContext`, `SearchSession`, tactic batches, `initialize_search`, `start_search`, and `update_search` behavior.
- Produces: the same names through `analogy.solver.search`, with `run.py` consuming `initialize_search` and `update_search` from that facade.

- [ ] Add failing dependency assertions that `analogy.solver.search` is a package, its four internal modules exist, and the retired episode/setup modules are absent.
- [ ] Run the dependency assertion and confirm it fails against the current module layout.
- [ ] Move `SearchResult` to `result.py`, protocols/default policies to `policies.py`, the episode state machine to `session.py`, and configuration wiring to `setup.py`.
- [ ] Export the stable engine API from `search/__init__.py` and update production/test imports without compatibility shims.
- [ ] Run episode, analogy rescue, reporting, runtime, and dependency tests and confirm they pass.
- [ ] Commit the package migration directly to `main`.

### Task 2: Enforce the engine boundary

**Files:**
- Modify: `tests/test_dependencies.py`
- Modify: `docs/superpowers/plans/2026-10-18-search-engine-package.md`

**Interfaces:**
- Consumes: the completed `solver.search` facade.
- Produces: architecture assertions that keep `run.py` on the facade and prevent reverse dependencies from proof to search.

- [ ] Add an AST assertion that `run.py` imports no retired search modules and imports both engine operations from `solver.search`.
- [ ] Add an import-graph assertion that no module under `solver/proof/` imports `solver.search`.
- [ ] Run the architecture tests and confirm they pass.
- [ ] Run the full test suite and `git diff --check`.
- [ ] Inspect the complete diff against both run recipes and commit the guardrails directly to `main`.
