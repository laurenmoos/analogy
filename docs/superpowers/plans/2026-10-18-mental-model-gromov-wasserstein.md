# Mental-Model Gromov–Wasserstein Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Represent analogies as persistent ordered mental models and rank candidate revisions by structural Gromov–Wasserstein fit to the current proof belief before Lean submission.

**Architecture:** `analogy/state.py` and `analogy/protocol.py` own the mental-model representation and exact model-facing contract. A focused `analogy/structural_scoring.py` builds embedded relational graphs and computes an entropic Gromov–Wasserstein score. `AnalogyFallback` generates a bounded revision batch, ranks it structurally, and evaluates candidates best-first without mutating persistent state until Lean accepts one.

**Tech Stack:** Python 3.11+, frozen dataclasses, existing embedding provider, pure-Python numerical routines, pytest.

**Spec:** `docs/superpowers/specs/2026-10-18-mental-model-gromov-wasserstein-design.md`

## Global Constraints

- Countermodels guide destructive revision but never enter the mental model's scored structure.
- Gromov–Wasserstein derives correspondence; generated proof fields are not fixed anchors.
- Lower distance ranks first and ties retain generation order.
- Only a Lean-accepted revision mutates the persistent mental model.
- No new dependency, no vanilla-path change, and no additional function in `run.py`.
- Leave untracked `docs/paper/` untouched.

## Review Focus

- Disconnected, singleton, and unequal-size graphs must produce finite deterministic scores; Task 2 tests each.
- Zero-norm or dimension-mismatched embeddings must fail clearly rather than yield NaN; Task 2 tests both.
- Invalid candidate generations must not reduce the validity of other candidates or mutate state; Task 3 tests this.
- All structurally scored candidates rejected by Lean must leave the original mental model active; Task 3 tests this.
- Countermodel-forced candidates must all change domain and retain per-track abandoned-domain isolation; Task 3 tests this.

---

### Task 1: Mental-Model Representation and Prompt Protocol

**Files:**
- Modify: `src/analogy/solver/analogy/state.py`
- Modify: `src/analogy/solver/analogy/protocol.py`
- Modify: `src/analogy/solver/model_policy.py`
- Modify: `src/analogy/solver/analogy/rescue.py`
- Modify: `prompts.yaml`
- Modify: `tests/test_analogy_state.py`
- Modify: `tests/test_prompts.py`

**Interfaces:**
- Produces: `MentalModelCorrespondence`, `MentalModel`, strict `<mental_model>` JSON parsing/rendering, and compatibility aliases only where needed by unaffected call sites.

- [ ] Write failing tests for complete and partial correspondences, strict JSON shape, ordered round trips, transition validation, and prompts that request mental-model correspondences.
- [ ] Run `uv run pytest tests/test_analogy_state.py tests/test_prompts.py -q` and confirm failures are caused by the old analogy representation.
- [ ] Implement the model, protocol, and prompt changes; update policy/rescue parsing without changing rescue selection yet.
- [ ] Run the focused tests and the full suite; both must pass.
- [ ] Commit with `git commit -m "refactor: represent analogies as mental models"`.

### Task 2: Structural Gromov–Wasserstein Scorer

**Files:**
- Create: `src/analogy/solver/analogy/structural_scoring.py`
- Create: `tests/test_structural_analogy_scoring.py`

**Interfaces:**
- Consumes: `MentalModel`, `SearchSession`, and `TextGenerator.embed_text`.
- Produces: `ProofBelief`, `StructuralGraph`, `MentalModelFit`, `build_proof_belief(search)`, and `score_mental_model_revision(model, belief, embedder, *, layer) -> MentalModelFit`; equal normalized state descriptions share nodes, mismatched adjacent correspondences remain disconnected, and earliest correspondence position supplies the order relation.

- [ ] Write failing tests for role-conditioned embedding, proof graph construction, relational matrices, isomorphic-before-shuffled ordering, stable values, singleton/disconnected/unequal graphs, and invalid embeddings.
- [ ] Run `uv run pytest tests/test_structural_analogy_scoring.py -q` and confirm the module is missing.
- [ ] Implement graph construction, cosine/shortest-path blended distances, uniform entropic Gromov–Wasserstein iterations, coupling validation, and typed results.
- [ ] Run focused and full suites; both must pass.
- [ ] Commit with `git commit -m "feat: score mental models structurally"`.

### Task 3: Score Candidate Revisions Before Lean

**Files:**
- Modify: `src/analogy/solver/analogy/rescue.py`
- Modify: `src/analogy/solver/analogy/harness.py`
- Modify: `src/analogy/solver/run.py`
- Modify: `tests/test_base_model_rescue.py`
- Modify: `tests/test_analogy_harness.py`
- Modify: `tests/test_dependencies.py`

**Interfaces:**
- Consumes: Task 2 scorer and existing rescue/search submission boundaries.
- Produces: immutable scored revision candidates, bounded independent generation, stable best-first evaluation, and accepted-only scratchpad mutation.

- [ ] Write failing tests proving all valid revisions are scored before any Lean call, best structural fit is submitted first, stable ties preserve generation order, rejection advances to the next scored revision, invalid candidates remain isolated, total calls stay bounded, and countermodels force every valid revision to change domain.
- [ ] Run `uv run pytest tests/test_base_model_rescue.py tests/test_analogy_harness.py tests/test_dependencies.py -q` and confirm failures describe sequential unscored behavior.
- [ ] Pass the current proof belief and embedding layer through the rescue plan; generate candidates independently, score and rank them, then evaluate without persistent mutation until acceptance.
- [ ] Preserve the visible top-level run recipe and reporting traces, adding structural scores/coupling metadata to analogy generation records.
- [ ] Run focused tests, `uv run pytest -q`, and `git diff --check`; all must pass.
- [ ] Commit with `git commit -m "feat: rank mental model revisions by structure"`.
