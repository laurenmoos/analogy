# Joint Activation Candidates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate and evaluate `K` complete analogy-plus-proof-reasoning candidates in one call each, separately measure proof relevance, reasoning diversification, causal utility, and predecessor value, then abstain when causal evidence does not beat the configured floor.

**Architecture:** Add one strict joint prompt and parser that adapt into the existing `GeneratedAnalogyCandidate` interface and preserve the actual response prefix used to generate tactics. Extend cross-fitted measurement with an explicit frozen-embedding proof-relevance component while retaining the existing displacement, causal patch, and predecessor components; add threshold gating and execute the winner's original tactics without a second generation.

**Tech Stack:** Python 3.11+, frozen dataclasses, YAML prompt catalog/configuration, pytest, MLX-backed model provider interfaces.

**Spec:** `docs/superpowers/specs/2026-10-17-joint-activation-candidates-design.md`

## Global Constraints

- Preserve exact attempt logging, deterministic adjacent retry seeds, and token-position accounting.
- Keep Lean unavailable during collection and selection.
- Generate tactics last in the same response, conditioned on analogy, mapping, proof reasoning, and predicted state.
- When scoring a tactic, use that actual explanatory response prefix and treat the tactic itself as the completion; never duplicate the tactic inside its own prefix.
- Keep proof-state relevance distinct from neutral-trace reasoning displacement.
- Submit the selected candidate's existing tactics; do not regenerate selected tactics after selection.
- Require causal effect strictly greater than `minimum_causal_effect`; an exact zero abstains.
- Preserve unrelated dirty-worktree changes.

## Review Focus

- Nested joint JSON that is syntactically valid but fails one existing component validator must retry the whole candidate with the precise diagnostic.
- Retry seed blocks for one candidate must never consume another candidate's initial seed.
- Tactic-scoring prefixes must contain analogy, mapping, proof reasoning, and predicted state while omitting only the tactic completion itself.
- Proof-state similarity and neutral-trace displacement must remain distinct serialized components.
- A winner above the normalized score floor but at or below the causal floor must abstain.
- Equal-compute accounting must include joint generation once and exclude the removed selected-arm generation.

---

### Task 1: Joint prompt and candidate parser

**Files:**
- Modify: `prompts.yaml`
- Modify: `src/analogy/experiments/activation_generation.py`
- Modify: `tests/test_activation_generation.py`
- Modify: `tests/test_prompts.py`

**Interfaces:**
- Consumes: existing `parse_source_analogy`, `parse_alignment`, `parse_proof_reasoning_transfer`, and `parse_rescue_proposals` validators.
- Produces: `parse_joint_activation_candidate(raw, *, candidate_id, need, tactic_limit)` and one-call `ActivationAnalogyGenerator.generate_candidates(...)` results using `GeneratedAnalogyCandidate` extended with `predicted_state`.

- [ ] **Step 1: Write failing joint-generation tests**

Add tests asserting one valid provider call per configured candidate, full proof visibility in every prompt, ordered explanatory fields before tactics, complete nested parsing, exact cost/attempt logging, and stable per-candidate seed blocks.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest tests/test_activation_generation.py tests/test_prompts.py -q`

Expected: failures because `activation_joint_candidate` and its parser do not exist and the current generator makes four calls.

- [ ] **Step 3: Add the strict prompt and parser**

Add prompt template `activation_joint_candidate`. Implement the parser by decoding the outer object, requiring exactly the ordered keys `analogy`, `mapping`, `proof_reasoning`, `predicted_state`, and `tactics`, then serializing nested values through the existing component parsers with assigned IDs.

- [ ] **Step 4: Replace the staged loop with one bounded joint call per candidate**

Render the full theorem, current state, accepted/failed tactics, complete `ProofNeed`, domain exploration cue, and tactic limit. Use candidate seed block `seed + index * prompt_max_attempts`; retain whole-candidate repair and failure records with stage `joint`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_activation_generation.py tests/test_prompts.py -q`

Expected: all pass.

### Task 2: Same-response tactic trace and selected-arm reuse

**Files:**
- Modify: `src/analogy/experiments/activation_generation.py`
- Modify: `src/analogy/experiments/activation_runner.py`
- Modify: `src/analogy/inference/model_provider.py`
- Modify: `tests/test_activation_generation.py`
- Modify: `tests/test_activation_runner.py`
- Modify: `tests/test_activation_final_contracts.py`

**Interfaces:**
- Consumes: `GeneratedAnalogyCandidate` from Task 1.
- Produces: a deterministic explanatory-prefix rendering containing analogy, mapping, proof reasoning, and predicted state; `_selected_candidate_slate(...)` adapting stored tactics and charges into `_ArmSlate` without provider generation.

- [ ] **Step 1: Write failing leakage and selected-reuse tests**

Assert each scoring prefix reproduces the joint response through `predicted_state`, excludes only the tactic completion, and the selected arm executes stored tactics without a final selected-generation call.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run pytest tests/test_activation_generation.py tests/test_activation_runner.py tests/test_activation_final_contracts.py -q`

Expected: failures because the current code constructs a separate tactic prompt and the runner calls `generate_selected_slate`.

- [ ] **Step 3: Reconstruct the same-response explanatory prefix**

Replace the separate activation tactic prompt with a measurement context made from the original joint prompt plus a canonical assistant prefix containing `analogy`, `mapping`, `proof_reasoning`, and `predicted_state`. Preserve predecessor ablation as the only difference between matched previous-state contexts, and never invoke model generation in this step.

- [ ] **Step 4: Reuse stored selected tactics in the runner**

Construct the selected `_ArmSlate` from `candidate.tactics`, collection generation charges, and the scoring messages. Retain post-freeze verified-tactic causal diagnostics. Leave shuffled regeneration intact.

- [ ] **Step 5: Update compute-ledger expectations and verify GREEN**

Run: `uv run pytest tests/test_activation_generation.py tests/test_activation_runner.py tests/test_activation_final_contracts.py -q`

Expected: all pass with one joint generation charge and no selected final-generation charge.

### Task 3: Separate proof relevance from reasoning diversification

**Files:**
- Modify: `src/analogy/experiments/activation_records.py`
- Modify: `src/analogy/experiments/activation_metrics.py`
- Modify: `src/analogy/experiments/activation_runner.py`
- Modify: `src/analogy/solver/proof_views.py`
- Modify: `config.activation-analogy.yaml`
- Modify: `tests/test_activation_records.py`
- Modify: `tests/test_activation_metrics.py`
- Modify: `tests/test_activation_runner.py`

**Interfaces:**
- Consumes: frozen embeddings of the current proof language/structure rendering and the joint explanatory response.
- Produces: `proof_state_relevance` as cosine similarity plus the unchanged `reasoning_displacement`, `causal_effect`, and `previous_state_value`; four-component calibration and score weights.

- [ ] **Step 1: Write failing factor-separation tests**

Assert proof relevance uses frozen embedding cosine similarity, displacement retains the existing whitened activation-distance calculation, changing one factor does not mutate another, and all four raw/standardized/weighted components serialize independently.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run pytest tests/test_activation_records.py tests/test_activation_metrics.py tests/test_activation_runner.py -q`

Expected: failures because candidate measurements and calibration currently contain only three components.

- [ ] **Step 3: Add proof relevance and four-component calibration**

Add deterministic proof and explanatory renderers, cosine similarity validation, `proof_state_relevance` to measurement records, and four-component tuples throughout calibration/scoring. Change `score_weights` to four explicit values while preserving the original displacement, causal, and predecessor formulas.

- [ ] **Step 4: Capture and charge frozen embeddings pre-Lean**

Embed the proof representation once per cell and each candidate explanation once, record exact charges, and carry relevance into cross-fit selection without any Lean outcome.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_activation_records.py tests/test_activation_metrics.py tests/test_activation_runner.py -q`

Expected: all pass.

### Task 4: Explicit causal abstention thresholds

**Files:**
- Modify: `config.activation-analogy.yaml`
- Modify: `src/analogy/experiments/activation_records.py`
- Modify: `src/analogy/experiments/activation_metrics.py`
- Modify: `src/analogy/experiments/activation_runner.py`
- Modify: `tests/test_activation_records.py`
- Modify: `tests/test_activation_metrics.py`
- Modify: `tests/test_activation_runner.py`

**Interfaces:**
- Consumes: finite scored measurements and `ActivationEvalConfig`.
- Produces: `select_candidate(..., minimum_selection_score, minimum_causal_effect)` returning `None` when either floor fails.

- [ ] **Step 1: Write failing configuration and selector tests**

Add strict config tests for both finite threshold fields. Add selector tests proving negative and zero causal effects abstain at the default zero floor, positive causal evidence can pass, and the selection-score floor is inclusive.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run pytest tests/test_activation_records.py tests/test_activation_metrics.py tests/test_activation_runner.py -q`

Expected: failures because thresholds are not parsed or enforced.

- [ ] **Step 3: Add threshold configuration**

Add `minimum_selection_score: 0.0` and `minimum_causal_effect: 0.0` to the activation config schema, dataclass, loader, and checked-in profile.

- [ ] **Step 4: Gate selection and wire the runner**

Extend `select_candidate` with finite thresholds. Require `selection_score >= minimum_selection_score` and `causal_effect > minimum_causal_effect`; wire both values through `freeze_selections`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_activation_records.py tests/test_activation_metrics.py tests/test_activation_runner.py -q`

Expected: all pass.

### Task 5: Semantics documentation and full verification

**Files:**
- Modify: `docs/benchmarks/activation-evaluation-v3-semantics.md`
- Modify: `README.md`
- Test: full `tests/` suite

**Interfaces:**
- Consumes: completed behavior from Tasks 1–4.
- Produces: user-facing semantics matching the executable experiment.

- [ ] **Step 1: Update experiment documentation**

Document joint `K`-sample generation, same-response tactic tracing, the four separate factors, causal abstention, selected tactic reuse, retry behavior, and revised compute accounting.

- [ ] **Step 2: Run focused activation suite**

Run: `uv run pytest tests/test_activation_*.py -q`

Expected: all pass.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`

Expected: all pass; any unrelated pre-existing failures are named explicitly.

- [ ] **Step 4: Inspect the diff and artifact compatibility**

Run: `git diff --check` and inspect only files touched by this plan. Confirm collection artifacts still serialize complete candidates and attempts.
