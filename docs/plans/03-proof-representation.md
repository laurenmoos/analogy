# Proof-Need Representation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract a deterministic hidden-state vector representing what a stuck proof needs next.

**Architecture:** The same frozen Qwen model encodes a canonical prompt containing the proof state, goal, and failed attempts. The selected layer's final non-padding prompt token is normalized and returned with provenance; no analogy is generated yet.

**Tech Stack:** PyTorch, Transformers, Qwen3-8B

**Spec:** `docs/superpowers/specs/2026-10-13-unified-analogical-rl-layout-design.md`

## Global Constraints

- Qwen remains frozen.
- Pool exactly the final non-padding prompt token for the first experiment.
- Store layer, token position, model revision, dtype, and vector dimension.
- Never serialize full model activations into ordinary search traces.

## Review Focus

- Positive and negative layer indices resolve to the same documented layer.
- Padding does not change the selected token representation.
- An out-of-range layer fails before inference.
- Zero or non-finite vectors are rejected.
- Identical prompts under deterministic inference produce numerically equal vectors.

---

### Task 1: Define the proof-need prompt and record

**Files:** `src/analogy/records.py`, `src/analogy/model.py`, `tests/test_model.py`

**Interfaces:**
- Produces `ProofNeed(text, vector, layer, token_index, model_revision)`.
- Produces `render_proof_need_prompt(node: SearchNode, failures: tuple[TacticResult, ...]) -> str`.

- [ ] Write failing tests pinning canonical prompt ordering and `ProofNeed` finite-vector validation.
- [ ] Implement the immutable record and renderer without importing Torch into `records.py`.
- [ ] Run focused and full tests.

### Task 2: Extract the selected hidden state

**Files:** `src/analogy/model.py`, `tests/test_hidden_states.py`, `config.yaml`

**Interfaces:** Produces `QwenGenerator.embed_proof_need(prompt: str, layer: int) -> ProofNeed`.

- [ ] Write failing tests with synthetic hidden states of shape `[batch, tokens, hidden]` for padding, layer resolution, normalization, and invalid vectors.
- [ ] Call the model with `output_hidden_states=True`, `use_cache=False`, and no gradient; select the final attention-mask token and L2-normalize in float32.
- [ ] Add validated `model.hidden_layer` and reject values outside `[-num_hidden_layers-1, num_hidden_layers]`.
- [ ] Run unit tests, then a real-model smoke check confirming dimension 4096 for Qwen3-8B.

### Task 3: Capture representations only at stuck states

**Files:** `src/analogy/loop.py`, `src/analogy/model.py`, `tests/test_loop.py`

**Interfaces:** Adds an injected `ProofNeedEncoder` called only after `StucknessDecision.is_stuck`.

- [ ] Write a failing loop test proving zero encoder calls during ordinary progress and exactly one call at a stuck node.
- [ ] Add the protocol and trace a compact `ProofNeedRef` containing metadata plus a run-local vector artifact path.
- [ ] Save vectors under `runs/<run-id>/representations/` and keep JSONL traces human-readable.
- [ ] Re-run the baseline and confirm proof behavior is unchanged when representation capture is enabled.
