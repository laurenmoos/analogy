# Notebook Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create reproducible top-level notebooks for capability evidence, baseline behavior, analogy inspection, latent traversal, and final comparisons without adding evaluation code to the production package.

**Architecture:** Notebooks read immutable run artifacts and call stable public APIs for small demonstrations. Shared correctness logic stays in the package; notebooks own tables, plots, qualitative examples, and research conclusions.

**Tech Stack:** Jupyter, nbformat, pandas, matplotlib, existing analogy package

**Spec:** `docs/superpowers/specs/2026-10-13-unified-analogical-rl-layout-design.md`

## Global Constraints

- Notebooks are never imported by production code or tests.
- Every notebook runs from a clean kernel in numeric order and records the run IDs it analyzes.
- Large vectors, checkpoints, and generated results remain under ignored `runs/`.
- Report negative and null results alongside improvements.

## Review Focus

- Missing run artifacts produce a clear setup cell rather than a misleading empty chart.
- Comparisons use identical benchmark subsets and budgets.
- Confidence intervals are computed over problems or seeds, not individual tactic attempts.
- Qualitative examples include failures and random controls.
- Saved notebook outputs contain no absolute secrets or machine-specific cache paths.

---

### Task 1: Capability and baseline notebooks

**Files:** `notebooks/00_model_capability.ipynb`, `notebooks/01_tactic_baseline.ipynb`, `tests/test_notebooks.py`

- [ ] Write a failing `nbformat` structure test requiring a title, configuration/run-ID cell, artifact existence check, deterministic execution seed, and summary table in each notebook.
- [ ] Create `00_model_capability.ipynb` showing solved count, per-connective results, tactic sequences, and representative failures.
- [ ] Create `01_tactic_baseline.ipynb` showing success, invalid rate, search cost, and stuck-state distribution.
- [ ] Execute both notebooks from clean kernels and save outputs.

### Task 2: Analogy and traversal notebooks

**Files:** `notebooks/02_analogy_generation.ipynb`, `notebooks/03_latent_traversal.ipynb`, `tests/test_notebooks.py`

- [ ] Extend the failing structure test to require prompted/random comparisons and qualitative failure samples.
- [ ] Create the analogy notebook with proof state, generated proposal, pertinence judgment, tactic ordering before/after, and Lean outcome.
- [ ] Create the traversal notebook with action norms, reward distributions, zero/random/learned controls, and selected analogy examples.
- [ ] Execute both notebooks from clean kernels and save outputs.

### Task 3: Final paired comparison

**Files:** `notebooks/04_results.ipynb`, `README.md`, `tests/test_notebooks.py`

- [ ] Write a failing notebook test requiring the four conditions: unconditioned, prompted analogy, random traversal, learned traversal.
- [ ] Build paired per-problem tables, bootstrap confidence intervals over problems, held-out results, cost metrics, and a limitations section.
- [ ] Link notebooks and exact run directories from README without promoting exploratory conclusions to package guarantees.
- [ ] Run the full Python/Lean suite, then execute every notebook from a clean kernel in numeric order.
