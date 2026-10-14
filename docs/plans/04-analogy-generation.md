# Proof-Conditioned Analogy Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate analogies directly from a stuck proof's Qwen context and use pertinent analogies only to rerank the original tactic candidates.

**Architecture:** Qwen receives the canonical proof-need prompt and samples several analogies from poetry, physics, or unrestricted domains. A separate judgment prompt scores pertinence. The existing constrained chooser may reorder, but never expand, the conventional top-k candidate set.

**Tech Stack:** Frozen Qwen3-8B, existing Daydream and search scaffolds, Lean verifier

**Spec:** `docs/superpowers/specs/2026-10-13-unified-analogical-rl-layout-design.md`

## Global Constraints

- No relation bank or nearest-neighbor retrieval.
- Generated analogy text is conditioned on the proof prompt that produced the proof-need representation.
- Analogy generation occurs only after observable stuckness.
- Analogy may rerank only untried conventional candidates.

## Review Focus

- Empty, duplicate, or malformed analogies are discarded.
- Pertinence scoring cannot see Lean reference proofs or final outcomes.
- A rejected analogy leaves baseline ordering unchanged.
- The chooser cannot inject or rewrite tactic text.
- Sampling seeds and generation parameters are recorded for replay.

---

### Task 1: Generate structured analogy proposals

**Files:** `src/analogy/model.py`, `src/analogy/daydream.py`, `tests/test_daydream.py`, `config.yaml`

**Interfaces:** Produces `QwenAnalogyGenerator.generate(proof_need, count, seed) -> tuple[RelationProposal, ...]`.

- [ ] Write failing tests for strict JSON proposals with `domain`, `source`, `transformation`, and `application` strings.
- [ ] Implement a prompt that asks for structural possibilities, not Lean tactics, and sample exactly `analogy.samples` independent outputs.
- [ ] Canonicalize whitespace, discard duplicates, and preserve prompt/seed provenance.
- [ ] Run focused and full tests.

### Task 2: Add a concrete pertinence gate

**Files:** `src/analogy/model.py`, `src/analogy/daydream.py`, `tests/test_daydream.py`

**Interfaces:** Produces `QwenPertinenceGate.assess(proof_need, proposal) -> PertinenceAssessment` with a score in `[0, 1]` and a short reason.

- [ ] Write failing tests for valid judgments, malformed scores, threshold boundaries, and information isolation.
- [ ] Implement a deterministic judgment prompt and strict JSON parser.
- [ ] Add `analogy.pertinence_threshold` to configuration and accept only `score >= threshold`.
- [ ] Run focused and full tests.

### Task 3: Rerank the hard candidate pool

**Files:** `src/analogy/daydream.py`, `src/analogy/loop.py`, `tests/test_analogy_integration.py`

**Interfaces:** Produces `QwenConstrainedChooser.choose(state, candidates, proposals) -> TacticCandidate`.

- [ ] Write a failing real-loop test where an analogy changes the selected member of a fixed candidate tuple and Lean verifies it.
- [ ] Parse the chooser response as an integer candidate index; reject indices outside the supplied tuple.
- [ ] Wire generation, gating, and selection into the existing stuck-only rescue path.
- [ ] Run baseline, prompted-analogy, and random-analogy conditions with identical problems and seeds; persist condition labels in run records.
