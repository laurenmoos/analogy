# Unified Package Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the existing Daydream and Lean Gym projects into one root `analogy` package and one validated `config.yaml` without changing behavior.

**Architecture:** Move the proven modules into `src/analogy`, keeping Lean Gym as the only subpackage. `loop.py` remains the single search loop; `daydream.py` owns curiosity behavior; `records.py` owns shared records. A thin composition root loads configuration but does not introduce model behavior.

**Tech Stack:** Python 3.11+, Lean 4.29.1, setuptools, PyYAML 6, pytest 8

**Spec:** `docs/superpowers/specs/2026-10-13-unified-analogical-rl-layout-design.md`

## Global Constraints

- Preserve all 102 Daydream and 40 Lean Gym behaviors before deleting old paths.
- Keep the pinned `leanprover/lean4:v4.29.1` toolchain and clean no-`sorry` completion check.
- Do not add Qwen, Torch, notebooks, RL, or empty future modules in this migration.
- The workspace is not currently a Git repository; use test checkpoints rather than commit steps unless Git is initialized before execution.
- `config.yaml` is the only runtime configuration file after migration.

## Review Focus

- A missing, malformed, or unknown YAML key fails with a path-specific configuration error.
- Resource paths work outside the repository working directory.
- Lean subprocesses still locate the bundled Lake project and pinned toolchain.
- Old Daydream public imports either migrate deliberately or fail in tests before their source is removed.
- The package test suite does not depend on notebooks, archived documents, or JavaScript probes.

---

### Task 1: Root package and strict configuration

**Files:**
- Create: `pyproject.toml`
- Create: `config.yaml`
- Create: `src/analogy/__init__.py`
- Create: `src/analogy/config.py`
- Create: `tests/test_config.py`
- Remove after green: `daydream/pyproject.toml`, `lean_gym/pyproject.toml`, `data-config.json`

**Interfaces:**
- Produces: `load_config(path: Path) -> AppConfig`
- Produces immutable records `AppConfig`, `ExperimentConfig`, `ModelConfig`, `BenchmarkConfig`, `SearchConfig`, `AnalogyConfig`, `RLConfig`, `DataConfig`, and `RunConfig`.

- [x] **Step 1: Write failing tests for valid config, unknown keys, missing keys, and relative path resolution**

```python
def test_load_config_resolves_paths_from_config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("""experiment:\n  stage: capability\nbenchmark:\n  corpus: corpus/problems.json\nsearch:\n  top_k: 8\n  max_steps: 32\n  stuck_after: 3\nrun:\n  output_dir: runs\n  seed: 0\ndata:\n  root: /tmp/analogy-data\n""")
    config = load_config(path)
    assert config.benchmark.corpus == tmp_path / "corpus/problems.json"

def test_load_config_rejects_unknown_root_key(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("unknown: true\n")
    with pytest.raises(ConfigError, match=r"config\.unknown"):
        load_config(path)
```

- [x] **Step 2: Run `lean_gym/.venv/bin/python -m pytest tests/test_config.py -v` and verify the import fails.**

- [x] **Step 3: Add the root build metadata and minimal strict loader.** Use `yaml.safe_load`, exact-key checks, frozen dataclasses, positive integer checks, and `Path.resolve()` relative to the YAML file.

- [x] **Step 4: Create `config.yaml` with every section in the approved spec: `stage: capability`, Qwen3-8B model settings, the current corpus, search limits, disabled analogy and RL settings, `/tmp/analogy-data`, and `runs`; run the focused tests to green.**

### Task 2: Move shared records and the existing main loop

**Files:**
- Create: `src/analogy/records.py`
- Create: `src/analogy/loop.py`
- Create: `src/analogy/daydream.py`
- Create: `tests/test_records.py`, `tests/test_loop.py`, `tests/test_daydream.py`
- Source: `daydream/src/daydream/models.py`, `search_types.py`, `outer_loop.py`, `curiosity.py`

**Interfaces:**
- Produces the existing search and curiosity records under `analogy.records`.
- Produces `LocalRescueSearch` under `analogy.loop` with its current constructor and `search(initial_state)` behavior.
- Produces `CuriositySearch` under `analogy.daydream`.

- [x] **Step 1: Copy the existing tests into the flat test files and change imports from `daydream.*` to `analogy.*`; run them and verify collection fails.**
- [x] **Step 2: Move search records into `records.py`, preserving validation and frozen dataclasses; run `test_records.py`.**
- [x] **Step 3: Move `LocalRescueSearch`, stuckness, frontier, and policies into `loop.py`; run `test_loop.py`.**
- [x] **Step 4: Move curiosity records and `CuriositySearch` into `daydream.py`; run `test_daydream.py`.**
- [x] **Step 5: Move the tested program-synthesis records into `records.py` without wiring them into the current loop; preserve their validation and public imports for later work.**
- [x] **Step 6: Run all migrated tests and compare the count and named scenarios with the original 102-test suite before removing `daydream/`.**

### Task 3: Move Lean Gym and its resources

**Files:**
- Create: `src/analogy/lean_gym/*.py`
- Create: `src/analogy/lean_gym/corpus/*`
- Create: `src/analogy/lean_gym/lean_project/*`
- Create: flat `tests/test_lean_*.py`
- Source: existing `lean_gym/`

**Interfaces:**
- Preserve `LeanSubprocessVerifier`, `ProofSession`, `load_problems`, `replay_trace`, and their existing value types.

- [x] **Step 1: Move and rename the existing tests to `test_lean_*.py`, update imports to `analogy.lean_gym`, and verify collection fails.**
- [x] **Step 2: Move Python modules without semantic edits and restore the 40 unit tests.**
- [x] **Step 3: Move corpus and Lean project resources; resolve them using `importlib.resources.files("analogy.lean_gym")`, not the process working directory.**
- [x] **Step 4: Run all 25 reference proofs and verify clean Lean completion under `leanprover/lean4:v4.29.1`.**
- [x] **Step 5: Delete the old `lean_gym/` only after the migrated tests and reference-proof verification pass.**

### Task 4: Add the thin entry point and archive obsolete probes

**Files:**
- Create: `src/analogy/__main__.py`
- Create: `src/analogy/tools.py` only if a current Python caller needs a data helper
- Modify: `src/analogy/__init__.py`, `README.md`, `.gitignore`
- Move: old completed documents to `docs/archive/`
- Remove or archive: root `src/*.mjs`, `test/*.mjs`, `package.json`, `package-lock.json`

**Interfaces:**
- Produces `main(argv: Sequence[str] | None = None) -> int`.

- [x] **Step 1: Write a failing CLI test that passes `--config`, loads the capability stage, lists its configuration, and exits zero without loading a model.**
- [x] **Step 2: Implement `__main__.py` as a composition root that calls `load_config` and dispatches known stages; an unavailable stage returns a clear nonzero error.**
- [x] **Step 3: Archive or remove JavaScript probes after confirming no production import or documented command references them.**
- [x] **Step 4: Update README setup, layout, configuration, and `python -m analogy --config config.yaml` instructions.**
- [x] **Step 5: Run `python -m pytest -v`, compile the package, run the CLI from a different working directory, and verify 25 Lean reference proofs.**

## Completion Evidence

Record exact test counts, reference-proof count, CLI output, and any intentionally archived APIs in the execution ledger. Do not begin Plan 1 until this migration is green.
