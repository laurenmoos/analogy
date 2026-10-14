# One-Step Latent Traversal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a small policy make one bounded change to the proof-conditioned activation before Qwen decodes an analogy.

**Architecture:** A trainable policy maps the frozen proof-need vector to a diagonal Gaussian over a 4096-dimensional delta. During prompt prefill, a scoped layer hook adds the sampled, norm-clipped delta only to the final prompt token at the configured layer; Qwen then generates normally with cached states.

**Tech Stack:** PyTorch, frozen Qwen3-8B, Transformers forward hooks

**Spec:** `docs/superpowers/specs/2026-10-13-unified-analogical-rl-layout-design.md`

## Global Constraints

- Freeze every Qwen parameter.
- Modify only one configured layer, one prompt token, and one prefill pass.
- Bound delta L2 norm by `rl.max_delta_norm`.
- A zero delta must reproduce untraversed generation under the same seed.

## Review Focus

- Hooks are removed after success or exception.
- Decode-token activations are not repeatedly perturbed.
- Mixed precision does not produce non-finite deltas.
- Batch size greater than one applies the corresponding delta to each row.
- Saved actions retain log probability for later REINFORCE updates.

---

### Task 1: Define the traversal policy and action

**Files:** `src/analogy/rl.py`, `src/analogy/records.py`, `tests/test_rl.py`, `config.yaml`

**Interfaces:**
- Produces `TraversalAction(delta, log_probability, entropy)`.
- Produces `TraversalPolicy.sample(proof_need, generator) -> TraversalAction`.

- [ ] Write failing tests for output shape, deterministic seeded sampling, norm clipping, float16/float32 finite values, and gradient flow into policy parameters only.
- [ ] Implement a two-layer MLP with hidden width from config and separate mean/log-scale heads; clamp log-scale and clip sampled delta norm.
- [ ] Add `rl.hidden_width`, `rl.max_delta_norm`, and `rl.initial_log_scale` to strict configuration.
- [ ] Run focused and full tests.

### Task 2: Apply a scoped activation intervention

**Files:** `src/analogy/model.py`, `tests/test_hidden_states.py`

**Interfaces:** Produces `QwenGenerator.generate_analogy(prompt, *, intervention: TraversalAction | None, seed: int) -> str`.

- [ ] Write failing synthetic-layer tests proving only the final prefill token changes, batch rows receive their corresponding deltas, decode steps are untouched, and the hook is removed after an injected exception.
- [ ] Implement a context manager registering a forward hook on the configured transformer block; mutate a cloned hidden-state output only when sequence length exceeds one.
- [ ] Assert delta dimension, batch dimension, device, and dtype before generation.
- [ ] Run unit tests and verify zero-delta parity on the real model.

### Task 3: Add random and learned traversal conditions

**Files:** `src/analogy/daydream.py`, `src/analogy/__main__.py`, `tests/test_analogy_integration.py`

**Interfaces:** Adds condition labels `prompted`, `random_traversal`, and `learned_traversal` to run records.

- [ ] Write a failing integration test that confirms all three conditions preserve the same conventional candidate pool.
- [ ] Wire the selected action into analogy generation and persist action norm, log probability, layer, and seed.
- [ ] Run a no-training comparison to verify random traversal is a valid control before adding reward learning.
