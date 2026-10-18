# Visible Search Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put every live general-search score calculation in `solver/scoring/` and make tactic scoring and proof-state scoring explicit in both run recipes.

**Architecture:** A dependency-light scoring package owns tactic weight processing, proof-state novelty, and frontier priority calculation. Model policy and search retain orchestration but call those calculations; `run.py` names the scored operations explicitly for both vanilla and analogy-assisted execution.

**Tech Stack:** Python 3.11+, dataclasses, heap-based search, pytest

**Spec:** `docs/superpowers/specs/2026-10-18-visible-search-scoring-design.md`

## Global Constraints

- Preserve candidate order, ordinary width, normalized values, retry behavior, successor selection, and frontier ordering.
- Score proof states only after search accepts them as valid, progressive, and non-repeated.
- Preserve the initial-state and analogy-rescue diversity observations.
- Keep `run.py` limited to `run_vanilla` and `run_analogy_assisted`.
- Scoring must not import model policy or search orchestration.
- Remove old operation names and `proof/diversity.py`; do not add compatibility shims.
- Do not restore activation experiments or add analogy-specific scoring.
- Leave untracked `docs/paper/` untouched.

## Review Focus

- A multi-attempt model response retains the same per-attempt and final normalization as before the move.
- Candidate order and `ordinary_width` remain independent of numeric score order.
- Rejected, repeated, and semantically refuted successors do not enter the diversity archive.
- Tactic score minus `0.02 * depth` remains primary, novelty secondary, and insertion order tertiary.
- Analogy-rescue successors receive the same diversity observation and frontier priority as ordinary successors.

---

### Task 1: Create the general scoring package

**Files:**
- Create: `src/analogy/solver/scoring/__init__.py`
- Create: `src/analogy/solver/scoring/tactics.py`
- Create: `src/analogy/solver/scoring/proof_states.py`
- Create: `src/analogy/solver/scoring/priority.py`
- Delete: `src/analogy/solver/proof/diversity.py`
- Modify: `src/analogy/model/parser.py`
- Modify: `src/analogy/solver/model_policy.py`
- Modify: `src/analogy/solver/search/policies.py`
- Modify: `src/analogy/solver/search/result.py`
- Modify: `src/analogy/solver/search/session.py`
- Modify: `src/analogy/solver/search/setup.py`
- Create: `tests/test_scoring.py`
- Delete: `tests/test_state_diversity.py`
- Modify: `tests/test_model.py`
- Modify: `tests/test_episode.py`

**Interfaces:**
- Consumes: model-generated candidate JSON, `TacticCandidate`, `SymbolicState`, `Embedding`, and `SearchNode`.
- Produces: `parse_scored_tactic_candidates(...)`, `normalize_tactic_scores(...)`, `DiversityObservation`, `FrozenEmbeddingDiversity.observe(...)`, and `search_priority(node, novelty)` through `analogy.solver.scoring`.

- [x] Add failing scoring tests that pin raw-weight validation and normalization, candidate ordering, cosine novelty caching, and the exact frontier priority tuple.
- [x] Run `uv run pytest tests/test_scoring.py tests/test_model.py tests/test_episode.py -q` and confirm the new imports or functions fail.
- [x] Move tactic candidate score parsing from `model/parser.py` to `scoring/tactics.py`; normalize once per generated response and again after retry pools merge, matching current values exactly.
- [x] Move proof-state diversity records and calculation to `scoring/proof_states.py` without changing rendering, caching, neighbor ordering, or cosine bounds.
- [x] Move the frontier score formula to `scoring/priority.py` and call it from the search frontier without changing heap ordering.
- [x] Update production and test imports, remove `proof/diversity.py` and `test_state_diversity.py`, and export the scoring facade.
- [x] Run the focused scoring, model, episode, rescue, reporting, and dependency tests and confirm they pass.
- [x] Commit the scoring package migration directly to `main`.

### Task 2: Make scored operations visible in both run recipes

**Files:**
- Modify: `src/analogy/solver/model_policy.py`
- Modify: `src/analogy/solver/search/__init__.py`
- Modify: `src/analogy/solver/search/session.py`
- Modify: `src/analogy/solver/run.py`
- Modify: affected tests
- Modify: `tests/test_dependencies.py`

**Interfaces:**
- Consumes: the scoring package and existing model/search state machines.
- Produces: `generate_and_score_tactics_with_model_policy(search) -> TacticBatch` and `update_search_with_scored_proof_states(search, submitted) -> None` as the only public names for these operations.

- [ ] Add failing AST assertions that both run recipes call `generate_and_score_tactics_with_model_policy`, `submit_tactics_to_lean`, and `update_search_with_scored_proof_states`, and never call the retired operation names.
- [ ] Add a dependency assertion that `solver.scoring` imports neither `solver.search` nor `solver.model_policy`.
- [ ] Run the architecture assertions and confirm they fail against the old public operation names.
- [ ] Rename the model-policy and search operations, update both ordinary loops and the analogy-rescue update, and remove the old exports without aliases.
- [ ] Run focused tests, the full suite, and `git diff --check`.
- [ ] Inspect both visible recipes and commit the naming/guardrail change directly to `main`.
