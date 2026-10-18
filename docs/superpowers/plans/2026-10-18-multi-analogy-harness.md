# Multi-Analogy Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate an isolated cohort of analogies at problem initialization and make the black-box score/select/update/rescue lifecycle explicit in `run_analogy_assisted`.

**Architecture:** A new `solver/analogy/harness.py` owns ordered `AnalogyTrack` objects, each with a private scratchpad, conventional model policy, and rescue fallback. The harness exposes initialization, opaque scoring, stable selection, lifecycle update, and activation functions; `run.py` only composes those operations with the existing shared search and Lean submission functions.

**Tech Stack:** Python 3.12, frozen dataclasses and protocols, pytest, existing prompt/model/search abstractions.

**Spec:** `docs/superpowers/specs/2026-10-18-multi-analogy-harness-design.md`

## Global Constraints

- Keep `run.py` limited to `run_vanilla` and `run_analogy_assisted`.
- Reuse `analogy.max_model_calls` as cohort size; add no configuration field.
- Keep scoring opaque: the default assigns equal finite scores and stable selection chooses the earliest track.
- Maintain one search runtime, one Lean frontier, and one Lean submission for only the selected rescue.
- Preserve tactic scoring, proof-state diversity, semantic pruning, forced moves, and last-valid-ancestor recovery.
- Never share scratchpads, histories, tactic rankers, or fallbacks across tracks.
- Leave untracked `docs/paper/` untouched.

## Review Focus

- A malformed initialization response is skipped, while a cohort with no valid response raises a clear initialization error; Task 1 tests both.
- Equal scores and custom equal maxima select deterministically by cohort order; Task 1 tests stable selection.
- NaN, infinity, booleans, and non-numeric scorer results are rejected before selection; Task 1 tests score validation.
- A countermodel invalidates every track independently without erasing each track's abandoned-domain memory; Task 2 tests destructive preparation.
- Activating a track must replace only policy/fallback context fields and leave frontier/search state untouched; Task 2 tests object identity for preserved fields.

---

### Task 1: Isolated Cohort Initialization and Opaque Scoring

**Files:**
- Create: `src/analogy/solver/analogy/harness.py`
- Modify: `src/analogy/solver/analogy/__init__.py`
- Modify: `src/analogy/solver/model_policy.py`
- Modify: `prompts.yaml`
- Test: `tests/test_analogy_harness.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: `initialize_model_policy(config, generator, proof, model_events, analogy_scratchpad=...)`, `initialize_analogy_fallback(config, generator, model_events, scratchpad)`, `extract_analogy_block(text)`, and precomputed `SymbolicState` initial/goal states.
- Produces: `AnalogyTrack`, `AnalogyHarness`, `AnalogyScorer`, `EqualAnalogyScorer`, `ScoredAnalogy`, `initialize_analogy_harness(config, generator, proof, model_events, initial_state, goal_state, *, scorer=None) -> AnalogyHarness`, `score_analogies(search, analogies) -> tuple[ScoredAnalogy, ...]`, and `select_analogy(scored) -> AnalogyTrack`.

- [ ] **Step 1: Write failing prompt and harness tests**

Add tests proving `analogy_initialize` requests exactly one standalone `<analogy>` block; `max_model_calls` independent calls use deterministic distinct seeds and exclude accepted domains; successful tracks have stable IDs and distinct scratchpad/history/policy/fallback identities; malformed candidates are skipped; no valid candidates raises `AnalogyHarnessInitializationError`; equal scoring preserves order; a custom scorer can select a later track; and invalid numeric outputs (`True`, NaN, infinities, text) raise `AnalogyScoreError`.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `uv run pytest tests/test_analogy_harness.py tests/test_prompts.py -q`

Expected: FAIL because the harness and initialization prompt do not exist.

- [ ] **Step 3: Implement the initialization prompt and harness types**

Add one-call parsing and tracing in `harness.py`. Construct each accepted `AnalogyTrack` only after its scratchpad is initialized, bind its ranker and fallback to that exact scratchpad, use IDs `analogy-1`, `analogy-2`, ..., and expose a read-only `TacticRanker.analogy_scratchpad` property for ownership validation.

- [ ] **Step 4: Implement the scoring seam**

Define `AnalogyScorer.score(track: AnalogyTrack, search: SearchSession | None) -> float`; default to `0.0`; validate real, finite, non-boolean scores; preserve track order in `score_analogies`; and choose the first maximal item in `select_analogy`.

- [ ] **Step 5: Run focused tests and verify they pass**

Run: `uv run pytest tests/test_analogy_harness.py tests/test_prompts.py tests/test_model_provider.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add prompts.yaml src/analogy/solver/analogy src/analogy/solver/model_policy.py tests/test_analogy_harness.py tests/test_prompts.py
git commit -m "feat: initialize isolated analogy cohort"
```

### Task 2: Cohort Lifecycle and Search Activation

**Files:**
- Modify: `src/analogy/solver/analogy/harness.py`
- Modify: `src/analogy/solver/analogy/state.py`
- Modify: `src/analogy/solver/search/setup.py`
- Test: `tests/test_analogy_harness.py`
- Test: `tests/test_episode.py`

**Interfaces:**
- Consumes: `AnalogyHarness`, `AnalogyTrack`, `SearchSession.recovery`, and immutable `EpisodeContext`.
- Produces: `update_analogies(search, analogies) -> AnalogyHarness`, `activate_analogy(search, analogy) -> None`, and optional precomputed-state arguments on `initialize_search` so harness initialization and search share one proof-state read.

- [ ] **Step 1: Write failing lifecycle and activation tests**

Add tests proving no-countermodel updates preserve every track unchanged; countermodel updates call `abandon_current` on every track and retain local abandoned-domain histories; repeated preparation is safe; activation rejects a mismatched policy/fallback/scratchpad track; and valid activation changes only `context.tactic_ranker` and `context.analogy_fallback`, preserving frontier, visited nodes, recovery state, budgets, cancellation, and scoring objects.

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `uv run pytest tests/test_analogy_harness.py tests/test_episode.py -q`

Expected: FAIL because lifecycle and activation functions do not exist.

- [ ] **Step 3: Implement lifecycle preparation and activation**

For `SemanticResult.COUNTERMODEL`, abandon every active analogy exactly once per pending recovery; otherwise return the cohort intact. Use `dataclasses.replace` on `search.context` to activate only the selected track. Keep fallback rebuilding at `search.rescue_node` through the existing rescue plan.

- [ ] **Step 4: Reuse initial and goal states in search setup**

Extend `initialize_search` with keyword-only `initial_state: SymbolicState | None = None` and `goal_state: SymbolicState | None = None`; call the proof adapter only for missing values and preserve existing callers.

- [ ] **Step 5: Run focused tests and verify they pass**

Run: `uv run pytest tests/test_analogy_harness.py tests/test_episode.py tests/test_search_setup.py -q`

Expected: PASS (omit `tests/test_search_setup.py` if setup coverage remains in `tests/test_episode.py`).

- [ ] **Step 6: Commit**

```bash
git add src/analogy/solver/analogy/harness.py src/analogy/solver/analogy/state.py src/analogy/solver/search/setup.py tests/test_analogy_harness.py tests/test_episode.py
git commit -m "feat: manage analogy cohort lifecycle"
```

### Task 3: Visible Assisted-Run Recipe

**Files:**
- Modify: `src/analogy/solver/run.py`
- Modify: `tests/test_solve_report.py`
- Modify: `tests/test_dependencies.py`

**Interfaces:**
- Consumes: every public harness operation from Tasks 1-2 and existing search/rescue submission functions.
- Produces: a `run_analogy_assisted` body whose initialization and stuck-state lifecycle are readable as one function call per line, with no helper functions added to `run.py`.

- [ ] **Step 1: Write failing orchestration and AST tests**

Assert that assisted initialization visibly calls `initialize_analogy_harness`, `score_analogies`, `select_analogy`, and `initialize_search`; its stuck branch visibly and in order calls `update_analogies`, `score_analogies`, `select_analogy`, `activate_analogy`, `generate_analogy_rescue`, `submit_analogy_rescue_to_lean`, and `update_search_with_scored_proof_states`; only the selected track is rescued/submitted; vanilla contains none of the harness operations; and `run.py` defines only the two public run functions.

- [ ] **Step 2: Run focused tests and verify they fail**

Run: `uv run pytest tests/test_solve_report.py tests/test_dependencies.py -q`

Expected: FAIL against the single-scratchpad assisted recipe.

- [ ] **Step 3: Rewrite only `run_analogy_assisted` orchestration**

Read the initial and goal proof states once, initialize/score/select the cohort, seed search with the selected track, then render the exact stuck-state sequence from the spec. Remove direct scratchpad/fallback construction and retired current-path helpers from `run.py`; do not add any other function.

- [ ] **Step 4: Run focused and full verification**

Run: `uv run pytest tests/test_solve_report.py tests/test_dependencies.py tests/test_analogy_harness.py -q`

Expected: PASS.

Run: `uv run pytest -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/analogy/solver/run.py tests/test_solve_report.py tests/test_dependencies.py
git commit -m "refactor: expose multi-analogy run lifecycle"
```
