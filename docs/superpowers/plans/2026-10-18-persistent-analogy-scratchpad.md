# Persistent Analogy Scratchpad Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one persistent, externally stored analogy per proof episode, update it only at stuck states, and force a domain rebuild after countermodels.

**Architecture:** A focused `AnalogyScratchpad` module owns validated analogy state, tagged-block extraction, lifecycle transitions, abandoned domains, and bounded non-analogy history. The runtime shares one scratchpad between `TacticRanker` and `AnalogyFallback`; both inject its current block into prompts and strip generated blocks before parsing tactics. The episode tells fallback whether semantic recovery forces a domain change.

**Tech Stack:** Python 3.12+, frozen dataclasses, YAML prompts, pytest, Lean evaluator protocol.

**Spec:** `docs/superpowers/specs/2026-10-18-persistent-analogy-scratchpad-design.md`

## Global Constraints

- Analogy state is created at proof start, unchanged during ordinary progress, updated only when stuck, and rebuilt after a countermodel.
- Prompt order is system, current analogy block, then proof/search history.
- Stored history contains tactics and Lean results but no `<analogy>` text.
- `search.top_k` ranks ordinary tactics only and never scores analogies.
- New/change-domain proposals are selected by Lean progress, then prediction agreement; no numeric analogy score is introduced.
- Abandoned domains are unique, bounded to eight, and rendered one per line.

## Review Focus

- Malformed or multiple analogy blocks must not mutate scratchpad state, covered in Task 1.
- Ordinary generations must not silently change analogy state, covered in Task 2.
- Step revision must preserve domain and mapping byte-for-byte, covered in Task 3.
- Countermodel recovery must clear the active analogy and prohibit step-only revision, covered in Task 3.
- Failed domain trials must not overwrite a viable prior analogy, except forced countermodel destruction, covered in Task 3.

---

### Task 1: Typed scratchpad and block protocol

**Files:**
- Create: `src/analogy/solver/analogy_state.py`
- Create: `tests/test_analogy_state.py`

**Interfaces:**
- Produces: `AnalogyState`, `AnalogyUpdateAction`, `AnalogyScratchpad`, `parse_analogy_block(text)`, and `extract_analogy_block(text)`.

- [ ] Write failing tests for canonical rendering, extraction/removal, malformed/multiple blocks, lifecycle invariants, abandoned-domain bounds, and history block stripping.
- [ ] Run `uv run pytest -q tests/test_analogy_state.py` and confirm feature-missing failures.
- [ ] Implement the minimal typed state and scratchpad transitions.
- [ ] Run `uv run pytest -q tests/test_analogy_state.py` and confirm pass.

### Task 2: Initialize and inject analogy during ordinary search

**Files:**
- Modify: `src/analogy/solver/model_policy.py`
- Modify: `src/analogy/inference/parser.py`
- Modify: `prompts.yaml`
- Modify: `tests/test_model_provider.py`
- Modify: `tests/test_prompts.py`

**Interfaces:**
- Consumes: shared `AnalogyScratchpad`.
- Produces: `TacticRanker(..., analogy_scratchpad=None)` that initializes enabled scratchpads, injects current state between system and user messages, strips blocks before JSON parsing, and prevents ordinary updates.

- [ ] Write failing ranker/prompt tests for initial required block, prompt ordering, block-free parsing/history, unchanged repetition, and disabled compatibility.
- [ ] Run focused tests and confirm expected failures.
- [ ] Implement block-aware tactic parsing and prompt assembly.
- [ ] Run focused tests and confirm pass.

### Task 3: Stuck-state updates and domain trials

**Files:**
- Modify: `src/analogy/solver/rescue.py`
- Modify: `src/analogy/solver/episode.py`
- Modify: `prompts.yaml`
- Modify: `tests/test_base_model_rescue.py`
- Modify: `tests/test_episode.py`

**Interfaces:**
- Consumes: shared scratchpad and `force_domain_change: bool` derived from `SemanticResult.COUNTERMODEL`.
- Produces: update-aware `AnalogyFallback.search(...)` that trials first tactics, commits on progress or prediction match, and preserves lifecycle invariants.

- [ ] Write failing tests for revise-steps, voluntary/forced domain change, abandoned domains, several domain trials, Lean-progress selection, prediction-match selection, and exhausted forced rebuild.
- [ ] Run focused tests and confirm expected failures.
- [ ] Implement update parsing, trial evaluation, commit policy, and episode countermodel signal.
- [ ] Run focused tests and confirm pass.

### Task 4: Runtime wiring and reporting compatibility

**Files:**
- Modify: `src/analogy/solver/inference.py`
- Modify: `src/analogy/solver/rescue.py`
- Modify: `tests/test_solve_report.py`

**Interfaces:**
- Produces: one scratchpad per problem shared by ranker/fallback; rollout `analogy_trace` contains canonical current state.

- [ ] Write a failing runtime composition test proving per-problem sharing and isolation.
- [ ] Run the focused test and confirm expected failure.
- [ ] Wire one scratchpad into both components and keep serialized records backward-compatible.
- [ ] Run focused tests and confirm pass.

### Task 5: Verification

**Files:**
- Modify only files needed for attributable failures.

**Interfaces:**
- Produces: passing supported suite and static evidence that numeric analogy scoring was not added.

- [ ] Run `uv run pytest -q`.
- [ ] Run `git diff --check`.
- [ ] Run `rg -n "analogy_score|score_analogy|top_k.*analogy|analogy.*top_k" src tests prompts.yaml config.yaml` and inspect every match.
- [ ] Review the final diff against the spec, fix concrete defects, and rerun verification.
