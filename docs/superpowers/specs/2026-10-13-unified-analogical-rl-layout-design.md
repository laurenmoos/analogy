# Unified Analogical RL Repository Design

Date: 2026-10-13

## Purpose

Reshape the repository into one readable Python package for the Analogical RL experiment while preserving the working Lean verifier and Daydream search scaffold.

The repository should have:

- one `config.yaml` for model, benchmark, search, analogy, RL, data, and run settings;
- one `src/analogy/` package;
- one main proof-search loop;
- notebooks at the repository root for evaluation and interpretation;
- numbered implementation plans that build on what already works.

This restructuring does not implement Qwen, hidden-state traversal, or RL. It prepares a coherent home for those milestones without rewriting verified behavior.

## Current Implementation

### Complete

- `lean_gym/` contains the 25-problem constructive-logic benchmark, pinned Lean project, subprocess verifier, proof sessions, JSONL traces, replay, CLI, and 40 passing tests.
- `daydream/` contains immutable domain and search records, observable stuckness, best-first search, local rescue, iterative analogy proposal refinement, pertinence gating interfaces, constrained tactic selection, and 102 passing tests.
- The existing `LocalRescueSearch` is the main-loop scaffold. It should be moved and adapted, not replaced.
- Root JavaScript probes can load the external poetry and physics datasets through `data-config.json`.

### Missing

- a local Qwen3-8B adapter;
- a capability baseline showing that the model solves some Lean Logic Gym problems;
- a conventional tactic proposal policy;
- hidden-state extraction for the proof's needed transformation;
- proof-conditioned analogy generation;
- a latent traversal policy and RL training;
- concrete pertinence scoring;
- an end-to-end model-to-Lean experiment;
- notebook-based comparisons and analysis.

## Design Principles

### One package

All reusable Python code lives under `src/analogy/`. There are no sibling Python projects and no separate experiment package.

### Start flat

A concern begins as a file. It becomes a directory only after it has multiple stable responsibilities that cannot remain readable together. Lean Gym is the only immediate subpackage because it already contains several cohesive modules and bundled resources.

### Preserve verified behavior

The restructuring is a mechanical migration first. Existing public behavior and tests must remain green before model work begins.

### Configuration selects experiments

`config.yaml` selects the current experimental stage and component settings. Python package structure does not change between experiments.

### Evaluation stays in notebooks

Plots, comparisons, tables, and research interpretation live in top-level notebooks. Production modules return structured records but do not own evaluation narratives.

## Target Layout

```text
analogy/
├── config.yaml
├── pyproject.toml
├── README.md
├── src/
│   └── analogy/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py
│       ├── records.py
│       ├── loop.py
│       ├── daydream.py
│       ├── model.py
│       ├── rl.py
│       ├── tools.py
│       └── lean_gym/
│           ├── __init__.py
│           ├── corpus.py
│           ├── models.py
│           ├── session.py
│           ├── verifier.py
│           ├── replay.py
│           ├── traces.py
│           ├── corpus/
│           └── lean_project/
├── tests/
│   ├── test_config.py
│   ├── test_records.py
│   ├── test_loop.py
│   ├── test_daydream.py
│   ├── test_model.py
│   ├── test_rl.py
│   └── test_lean_*.py
├── notebooks/
│   ├── 00_model_capability.ipynb
│   ├── 01_tactic_baseline.ipynb
│   ├── 02_analogy_generation.ipynb
│   ├── 03_latent_traversal.ipynb
│   └── 04_results.ipynb
├── docs/
│   ├── plans/
│   └── archive/
└── runs/
```

Empty future modules and notebooks should not be created during the layout migration. Each appears in the subplan that first gives it executable behavior.

## Module Responsibilities

### `config.py`

Load and strictly validate `config.yaml`. Resolve paths relative to the repository or configured data root. Unknown keys, missing required values, and invalid ranges fail before a run begins.

### `records.py`

Own shared immutable records used across the main loop, model adapter, Lean verifier, and RL code. It absorbs the existing records from `daydream/models.py` and `daydream/search_types.py`, including the program-synthesis records reserved for later work. The migration preserves their tested behavior without integrating them into the current loop.

### `loop.py`

Own the readable proof-search control flow. The existing `LocalRescueSearch`, stuckness policy, frontier, progress policy, and related protocols move here with behavior preserved.

The stable high-level flow is:

```text
propose conventional tactics
→ try ordinary selection
→ detect local stuckness
→ optionally generate and refine analogies
→ rerank only the permitted tactic candidates
→ verify with Lean
→ record the result
```

### `daydream.py`

Own analogy generation, refinement, pertinence, and constrained tactic choice. Initially it receives the existing curiosity scaffold. Later plans connect it to Qwen hidden states and latent traversal.

### `model.py`

Own Qwen loading, tokenization, inference, tactic proposals, analogy generation, and hidden-state extraction. The first model milestone may keep some methods unimplemented, but the public boundary must be explicit and testable.

### `rl.py`

Own the traversal policy, trajectory records, reward calculation, and training step. It is introduced only after deterministic latent extraction and analogy-conditioned tactic selection work without RL.

### `tools.py`

Own reusable dataset inspection or conversion helpers. Disposable JavaScript probes are archived rather than mixed into the Python package. A helper moves here only if a current experiment still calls it.

### `lean_gym/`

Own the existing Lean benchmark and executable semantics. Its current verifier, sessions, trace format, replay behavior, corpus validation, pinned toolchain, and clean final verification are preserved.

### `__main__.py`

Load `config.yaml`, construct concrete dependencies, and invoke the existing main-loop scaffold. It is a thin composition root, not a second search implementation.

## Configuration Contract

The initial root configuration is:

```yaml
experiment:
  stage: capability

model:
  name: Qwen/Qwen3-8B
  device: auto
  hidden_layer: -1

benchmark:
  corpus: src/analogy/lean_gym/corpus/problems.json

search:
  top_k: 8
  max_steps: 32
  stuck_after: 3

analogy:
  enabled: false
  samples: 4

rl:
  enabled: false
  algorithm: reinforce

run:
  output_dir: runs
  seed: 0

data:
  root: /tmp/analogy-data
```

Later plans may add fields only when they are consumed. Disabled sections remain valid so one configuration can describe the whole experiment without separate per-stage files.

## Notebook Boundary

Notebooks import public package APIs and read structured run records from `runs/`. They may:

- execute small controlled runs;
- compare baselines and ablations;
- build tables and plots;
- inspect generated analogies and hidden-state behavior;
- summarize experimental conclusions.

Core search, verification, reward, and model behavior must remain importable and testable without running a notebook.

## Migration Map

```text
daydream/src/daydream/search_types.py  → src/analogy/records.py
daydream/src/daydream/models.py        → src/analogy/records.py
daydream/src/daydream/outer_loop.py    → src/analogy/loop.py
daydream/src/daydream/curiosity.py     → src/analogy/daydream.py
lean_gym/src/lean_gym/*.py             → src/analogy/lean_gym/*.py
lean_gym/corpus/*                      → src/analogy/lean_gym/corpus/*
lean_gym/LeanGym and Lake files        → src/analogy/lean_gym/lean_project/*
root src/*.mjs and test/*.mjs          → archive, unless replaced by a currently used Python helper
data-config.json                       → config.yaml
completed historical plans            → docs/archive/
```

The migration must avoid compatibility wrappers unless an external consumer is identified. This repository is not currently a Git repository, so verification must happen after every move and before deleting the old location.

## Subplan Sequence

The detailed implementation plans will be written separately after this design is approved:

0. Unified package and configuration migration.
1. Qwen capability check on Lean Logic Gym.
2. Conventional tactic baseline and stuckness integration.
3. Proof-need hidden-state extraction.
4. Proof-conditioned analogy generation and pertinence.
5. One-step latent traversal policy.
6. Lean-verified reward and RL training.
7. Notebook comparisons and final ablations.

Each subplan begins with the implementation state left by the previous plan. Existing scaffolding is referenced rather than rebuilt.

## Validation

The layout migration is complete when:

- there is one root `pyproject.toml` and one installable `analogy` package;
- there is one validated `config.yaml`;
- the existing Daydream and Lean Gym tests pass from the repository root;
- the 25 reference proofs still verify with the pinned Lean toolchain;
- the main loop is available from `analogy.loop` and through `python -m analogy`;
- no production module imports archived JavaScript probes or historical plan artifacts;
- notebook code is not required by the package or test suite.

## Non-Goals

- implementing or downloading Qwen during the directory migration;
- training an RL policy during the directory migration;
- adding program synthesis or tactic generation outside the conventional candidate pool;
- integrating the reserved program-synthesis records into the current tactic-discovery loop;
- creating empty modules, notebooks, or directories before a subplan uses them.
