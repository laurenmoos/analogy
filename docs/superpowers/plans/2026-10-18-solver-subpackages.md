# Solver Subpackages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the solver tree readable at a glance by separating analogy-only code from proof-domain code while leaving run orchestration at the package root.

**Architecture:** `solver/analogy` owns only persistent analogy state and analogy fallback behavior. `solver/proof` owns proof-state adaptation, representations, embeddings, inventory, replay, semantic reasoning, and diversity. `run.py`, `episode.py`, `search.py`, and `model_policy.py` stay directly under `solver` as orchestration; generic model generation records move to `model.prompt_runner` instead of being mislabeled as analogy behavior.

**Tech Stack:** Python 3.11+, dataclasses, pytest

**Spec:** Conversation-approved solver package structure from 2026-10-18.

## Global Constraints

- Preserve runtime behavior and serialized output.
- Do not leave compatibility modules at the retired solver paths.
- Keep `solver/analogy` strict: every symbol there must implement analogy state or analogy fallback behavior.
- Keep proof embedding logic in `solver/proof`, never `lean_gym`.
- Keep run orchestration directly under `solver`, not in a child package.

## Review Focus

- Existing CLI and report imports continue to resolve after package moves.
- Prompt generation preserves generation-attempt records after their ownership moves to `model.prompt_runner`.
- Analogy fallback retains scratchpad updates, retry behavior, and trace serialization.
- Proof representation embeddings retain dimensions, provenance, and transition comparisons.
- No retired flat solver module remains importable accidentally.

---

### Task 1: Create the proof subpackage

**Files:**
- Create: `src/analogy/solver/proof/__init__.py`
- Move: `src/analogy/solver/lean_adapter.py` to `src/analogy/solver/proof/adapter.py`
- Move: `src/analogy/solver/diversity.py` to `src/analogy/solver/proof/diversity.py`
- Move: `src/analogy/solver/proof_inventory.py` to `src/analogy/solver/proof/inventory.py`
- Move: `src/analogy/solver/proof_views.py` to `src/analogy/solver/proof/views.py`
- Move: `src/analogy/solver/replay.py` to `src/analogy/solver/proof/replay.py`
- Move: `src/analogy/solver/semantic.py` to `src/analogy/solver/proof/semantic.py`
- Modify: solver, model, and test imports that consume these modules

**Interfaces:**
- Consumes: existing public classes and functions without behavior changes
- Produces: the same APIs under `analogy.solver.proof.*`

- [ ] **Step 1: Change proof-facing tests to import from `analogy.solver.proof.*`.**
- [ ] **Step 2: Run the focused tests and verify collection fails because the proof package does not exist.**
- [ ] **Step 3: Move the six proof modules, add the package initializer, and repair relative imports.**
- [ ] **Step 4: Update production consumers and run proof, semantic, episode, and report tests.**

### Task 2: Make the analogy subpackage strict

**Files:**
- Create: `src/analogy/solver/analogy/__init__.py`
- Move: `src/analogy/solver/analogy_state.py` to `src/analogy/solver/analogy/state.py`
- Move and split: `src/analogy/solver/rescue.py` to `src/analogy/solver/analogy/rescue.py`
- Modify: `src/analogy/solver/proof/views.py`
- Modify: `src/analogy/model/prompt_runner.py`
- Modify: solver, model, and test imports that consume these modules

**Interfaces:**
- Consumes: proof representation types from `solver.proof.views` and generic generation records from `model.prompt_runner`
- Produces: `AnalogyFallback`, analogy state/scratchpad types, analogy parsing, analogy-specific generation/rollout records, and analogy rescue outcomes under `solver.analogy.*`

- [ ] **Step 1: Change analogy-facing tests to import from `analogy.solver.analogy.*` and verify collection fails.**
- [ ] **Step 2: Move `GenerationStatus` and the label-free `GenerationAttempt` into `model.prompt_runner`, where generic prompt attempts are produced; add `AnalogyGenerationAttempt` in analogy rescue for prediction labels.**
- [ ] **Step 3: Move `ProofRescueContext`, `ProofRepresentation`, and `zero_embedding_like` into `solver.proof.views`, where proof embeddings are built.**
- [ ] **Step 4: Move persistent analogy state to `solver.analogy.state` and the remaining analogy-only rescue implementation to `solver.analogy.rescue`.**
- [ ] **Step 5: Rename `GenericRescueOutcome` and `GenericRescueRunner` to `AnalogyRescueOutcome` and `AnalogyRescueRunner` so the public API states its actual scope.**
- [ ] **Step 6: Update production consumers and run analogy, prompt, episode, and report tests.**

### Task 3: Verify and record the final hierarchy

**Files:**
- Modify: `src/analogy/solver/__init__.py`

**Interfaces:**
- Consumes: the package layout from Tasks 1 and 2
- Produces: documented package roles and retired-path assertions

- [ ] **Step 1: Document the three solver layers in package docstrings without re-exporting child APIs.**
- [ ] **Step 2: Run `uv run pytest -q` and require all tests to pass.**
- [ ] **Step 3: Run `git diff --check`, inspect the final tree, and commit directly to `main`.**
