# Run-Rooted Solver Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every solver capability that is not reachable from the two public run recipes.

**Architecture:** Use `run_vanilla` and `run_analogy_assisted` as production roots, prune detached modules first, then prune dead hooks and compatibility modes inside live modules. Keep shared proof execution below both recipes and analogy policy in `solver/analogy`.

**Tech Stack:** Python 3.11+, pytest

**Spec:** `docs/superpowers/specs/2026-10-18-run-rooted-solver-cleanup-design.md`

## Global Constraints

- `solver/run.py` contains exactly `run_vanilla` and `run_analogy_assisted`.
- Production reachability, not direct test imports, determines whether solver code remains.
- Preserve the behavior and output of both recipes.
- Leave `docs/paper/` untouched.

## Review Focus

- Ordinary and analogy searches preserve tactic submission and classification order.
- Analogy repair retries retain scratchpad transition, cancellation, and rollout evidence.
- Semantic recovery retains proof-goal parsing and countermodel behavior after the syntax split.
- State fingerprints remain normalized and stable after replay removal.
- CLI and persisted run artifacts remain unchanged.

---

### Task 1: Restore and pin the two production roots

**Files:**
- Restore: `src/analogy/solver/run.py`
- Modify: `tests/test_dependencies.py`

- [x] Add/retain the AST architecture test for exactly two recipes.
- [x] Restore `run.py` from committed `main` using an apply-patch edit.
- [x] Run the architecture test.

### Task 2: Remove detached proof subsystems

**Files:**
- Delete: `src/analogy/solver/proof/replay.py`
- Delete: `src/analogy/solver/proof/views.py`
- Replace: `src/analogy/solver/proof/inventory.py` with `src/analogy/solver/proof/syntax.py`
- Create: `src/analogy/solver/proof/state.py`
- Modify: `src/analogy/model/prompts.py`, `src/analogy/solver/proof/semantic.py`, `src/analogy/solver/search.py`
- Modify/delete: proof tests that only exercise retired APIs

- [x] Add failing dependency assertions for retired modules and live syntax/state APIs.
- [x] Move only production-reachable parsing and fingerprint behavior.
- [x] Delete detached modules and their tests.
- [x] Run prompt, semantic, inventory/syntax, and dependency tests.

### Task 3: Prune dead hooks and rescue compatibility modes

**Files:**
- Modify: `src/analogy/solver/proof/adapter.py`
- Modify: `src/analogy/solver/search.py`
- Modify: `src/analogy/solver/episode.py`
- Modify: `src/analogy/solver/analogy/rescue.py`
- Modify: related tests

- [x] Add failing API assertions that replay adapter methods, stuck-state capture, and scratchpad-free rescue APIs are absent.
- [x] Remove the unreachable methods/protocol field/branch.
- [x] Make analogy rescue require its scratchpad and remove the generic parser/schema branch and unused rollout action record.
- [x] Run focused solver tests.

### Task 4: Audit and verify the remaining solver tree

**Files:**
- Modify: any live solver module containing a demonstrably unreachable symbol
- Modify: `tests/test_dependencies.py`

- [x] Run a repository-wide definition/reference audit and manually classify single-reference symbols.
- [x] Remove remaining symbols whose only consumers are tests or exports, updating tests first.
- [x] Run the full test suite and `git diff --check`.
- [x] Review the whole diff against both recipes and commit.
