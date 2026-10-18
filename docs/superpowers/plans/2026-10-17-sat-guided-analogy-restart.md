# SAT-Guided Analogy Restart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When ordinary proof search is stuck, validate the stuck path propositionally and run analogy from the nearest state that can still be proved.

**Architecture:** Add a small dependency-free propositional checker and typed semantic-recovery records. The episode controller invokes it only after the existing stuckness policy fires, resolves the current or nearest valid ancestor, and optionally calls analogy from that validated state with bounded SAT guidance; inference serialization and Markdown expose the decision.

**Tech Stack:** Python 3.11+, dataclasses, exhaustive Boolean valuation, pytest, existing Lean verifier and YAML prompt catalog.

**Spec:** `docs/superpowers/specs/2026-10-17-sat-guided-analogy-restart-design.md`

## Global Constraints

- SAT checking, semantic backtracking, and analogy rescue run only after the existing ordinary-search stuck condition fires.
- Both experiment arms use identical ordinary search and semantic checking; only analogy generation is conditional.
- The checker supports propositional `¬`, `∧`, `∨`, `→`, `↔`, plus standard `True` and `False`, without a new dependency.
- Unsupported subexpressions are opaque Boolean atoms; `P Q : Prop` declarations are atoms, not hypotheses.
- Analogy receives one bounded deterministic guidance field and keeps its existing malformed/duplicate retry limit.
- A false root or parser/checker failure terminates with distinct `semantic_failure`; analogy is never called on an unvalidated state.
- SAT does not prune, rank, or reorder the ordinary frontier and does not add ordinary reasoning steps.

## Review Focus

- Pretty-printed Lean states with grouped declarations and multiple goals must preserve goal-local assumptions and assess every goal.
- Quoted identifiers and nested delimiters must remain opaque instead of being split on connective glyphs inside them.
- A false descendant several levels deep must select the nearest valid ancestor and preserve abandoned tactics in rescue-to-trigger order.
- Repeated stuck recovery at an already assessed state must use the cache and still create a distinct event record.
- Runs that make ordinary progress, and disabled-analogy runs, must not make unintended analogy calls or alter frontier behavior.

---

### Task 1: Deterministic propositional state checker

**Files:**
- Create: `src/analogy/solver/semantic.py`
- Create: `tests/test_semantic.py`
- Modify: `src/analogy/solver/proof_inventory.py`

**Interfaces:**
- Consumes: `proposition_root(expression: str)` from `proof_inventory.py`.
- Produces: `SemanticResult`, `SemanticAssessment`, `SemanticCheckError`, and `ExhaustiveSemanticChecker.assess(state: SymbolicState) -> SemanticAssessment`.

- [x] **Step 1: Write failing checker tests** for valid/invalid goals, deterministic sorted countermodels with `False` before `True`, constants, grouped `Prop` declarations, quoted/nested atoms, and multi-goal states where the first invalid displayed goal wins.
- [x] **Step 2: Run `pytest tests/test_semantic.py -q`** and confirm failures are caused by the missing semantic module/interfaces.
- [x] **Step 3: Implement the parser, Boolean evaluator, all-goals state reader, typed records, and exhaustive checker** with strict parse failures and deterministic valuation order.
- [x] **Step 4: Run `pytest tests/test_semantic.py -q`** and confirm all checker tests pass.
- [x] **Step 5: Commit the checker and tests** with `feat: add propositional proof-state checker`.

### Task 2: Stuck-only semantic recovery and ancestor selection

**Files:**
- Modify: `src/analogy/records.py`
- Modify: `src/analogy/solver/search.py`
- Modify: `src/analogy/solver/episode.py`
- Modify: `tests/test_episode.py`

**Interfaces:**
- Consumes: `ExhaustiveSemanticChecker.assess` and Task 1 semantic records.
- Produces: `SemanticRecoveryEvent`, `SearchResult.semantic_recoveries`, `EpisodeContext.semantic_checker`, and `SearchStatus.SEMANTIC_FAILURE`.

- [x] **Step 1: Write failing episode tests** proving no semantic call during ordinary progress; valid-current recovery; one- and multi-level false-path restart; strict ancestry validation; first false root termination; parse/check failure termination; assessment caching; disabled-arm checking without analogy; and unchanged frontier behavior.
- [x] **Step 2: Run the focused episode tests** and confirm they fail for the missing recovery behavior.
- [x] **Step 3: Index nodes and expansions by state ID and resolve recovery only after stuckness**; cache assessments, collect abandoned tactics in rescue-to-trigger order, and terminate with `SEMANTIC_FAILURE` when no validated origin exists.
- [x] **Step 4: Anchor rescue evaluation and rollout validation to the resolved ancestor** while leaving ordinary expansion counts, budget, and frontier ordering unchanged.
- [x] **Step 5: Run `pytest tests/test_episode.py tests/test_semantic.py -q`** and confirm all tests pass.
- [x] **Step 6: Commit the controller integration** with `feat: restart stuck analogy from valid state`.

### Task 3: Countermodel-guided analogy prompt

**Files:**
- Modify: `src/analogy/solver/rescue.py`
- Modify: `prompts.yaml`
- Modify: `tests/test_base_model_rescue.py`
- Modify: `tests/test_prompts.py`

**Interfaces:**
- Consumes: deterministic bounded `semantic_guidance: str` from Task 2.
- Produces: `GenericRescueRunner.search(..., semantic_guidance: str)` and prompt variable `semantic_guidance`.

- [x] **Step 1: Write failing prompt/rescue tests** for the exact valid-state guidance, countermodel restart guidance, bounded assignments, propagation into the model prompt, and unchanged retry/submission semantics.
- [x] **Step 2: Run `pytest tests/test_base_model_rescue.py tests/test_prompts.py -q`** and confirm interface/prompt failures.
- [x] **Step 3: Add `semantic_guidance` to the rescue protocol and implementation and bump the analogy prompt version** without adding a model call.
- [x] **Step 4: Update episode test doubles to accept the new argument and assert the resolved state and guidance received by analogy.**
- [x] **Step 5: Run `pytest tests/test_base_model_rescue.py tests/test_prompts.py tests/test_episode.py -q`** and confirm all tests pass.
- [x] **Step 6: Commit prompt integration** with `feat: guide analogy with SAT recovery`.

### Task 4: Runtime wiring, records, and reports

**Files:**
- Modify: `src/analogy/solver/inference.py`
- Modify: `src/analogy/__main__.py`
- Modify: `tests/test_solve_report.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `SearchResult.semantic_recoveries` and `SearchStatus.SEMANTIC_FAILURE`.
- Produces: `InferenceStatus.SEMANTIC_FAILURE`, JSON semantic recovery records, and Markdown sections shown before associated analogy rollouts.

- [x] **Step 1: Write failing report/status tests** for trigger ID, result, deterministic assignments, rescue ID, abandoned tactics, false-root/check errors, disabled-arm traces, and association of multiple rescue generations with one event.
- [x] **Step 2: Run `pytest tests/test_solve_report.py tests/test_cli.py -q`** and confirm failures are limited to missing semantic reporting/status mapping.
- [x] **Step 3: Wire the checker into every episode and map semantic failures through problem/run records.**
- [x] **Step 4: Render semantic recovery before its analogy rollout and preserve `analogy_fallback_count` as submitted tactics only.**
- [x] **Step 5: Run `pytest tests/test_solve_report.py tests/test_cli.py -q`** and confirm all report tests pass.
- [x] **Step 6: Commit runtime/reporting changes** with `feat: report semantic analogy recovery`.

### Task 5: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-10-17-sat-guided-analogy-restart.md`

**Interfaces:**
- Consumes: completed behavior from Tasks 1–4.
- Produces: operator-facing explanation of stuck-only SAT recovery and final checked plan state.

- [x] **Step 1: Update README behavior and measurement text** to say SAT/backtracking/analogy occur only after stuckness and explain semantic-failure outcomes.
- [x] **Step 2: Run `pytest -q`** and require the full suite to pass.
- [x] **Step 3: Run `python -m compileall -q src tests` and `git diff --check`** and require clean output.
- [x] **Step 4: Mark completed plan checkboxes and request a whole-change code review.**
- [x] **Step 5: Address any actionable review findings test-first and rerun the full verification commands.**
- [x] **Step 6: Commit final documentation/review fixes** with `docs: explain SAT-guided stuck recovery`.
