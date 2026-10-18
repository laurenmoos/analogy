# Centralize Model Prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `prompts.yaml` the single source of truth for every instruction, repair message, tactic card, and embedding prompt presented to a model.

**Architecture:** Extend the prompt catalog with versioned text fragments alongside chat prompts. Python continues to compute proof-derived values, but renders all model-facing prose and wrappers from named YAML entries.

**Tech Stack:** Python 3.11, PyYAML, pytest

**Spec:** `docs/superpowers/specs/2026-10-18-persistent-analogy-scratchpad-design.md`

## Global Constraints

- Preserve current prompt behavior and analogy lifecycle semantics.
- Keep dynamic proof state, diagnostics, and generated identifiers in Python values.
- Put all static model-facing prose in `prompts.yaml`.

## Review Focus

- Chat prompts must reject fragment rendering and fragments must reject chat rendering.
- Repair prompts must preserve their current output-schema contract.
- Tactic-card dynamic recipes and its hard size ceiling must remain intact.
- Analogy history and context must still appear in the same message order.
- Dormant proof-view renderers must resolve every catalog entry they reference.

---

### Task 1: Versioned Text Fragments

**Files:**
- Modify: `src/analogy/inference/prompts.py`
- Modify: `tests/test_prompts.py`

**Interfaces:**
- Produces: `PromptCatalog.text(name: str, **values: object) -> str`

- [x] Write failing catalog tests for text fragments and kind mismatch errors.
- [x] Run the focused tests and confirm they fail because text fragments are unsupported.
- [x] Implement the minimal fragment schema and renderer.
- [x] Run focused tests and the full prompt test module.

### Task 2: Move Model-Facing Prose to YAML

**Files:**
- Modify: `prompts.yaml`
- Modify: `src/analogy/inference/prompts.py`
- Modify: `src/analogy/solver/model_policy.py`
- Modify: `src/analogy/solver/rescue.py`
- Modify: `tests/test_prompts.py`
- Modify: `tests/test_base_model_rescue.py`

**Interfaces:**
- Consumes: `PromptCatalog.text(...)`
- Produces: named YAML fragments for tactic cards, injected context, output contracts, and all repair messages

- [x] Write failing behavior tests proving prompt wording can be supplied by YAML fragments.
- [x] Run them and confirm failure from hard-coded Python prose.
- [x] Add fragment entries and replace Python prose with catalog rendering.
- [x] Restore the five proof-view chat prompts referenced by runtime code.
- [x] Run focused and full tests.

### Task 3: Audit and Verify

**Files:**
- Modify: `docs/superpowers/plans/2026-10-18-centralize-model-prompts.md`

**Interfaces:**
- Consumes: completed prompt consolidation
- Produces: evidence that no static model-facing prose remains outside the catalog

- [x] Audit all generation and embedding call paths for hard-coded instructional prose.
- [x] Run `uv run pytest -q` and `git diff --check`.
- [x] Review the final diff and commit the consolidation.
