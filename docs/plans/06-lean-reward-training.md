# Lean-Verified Reward Training Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Train only the traversal policy with REINFORCE using distal improvement in Lean-verified proof solving.

**Architecture:** For each stuck proof state, run paired unconditioned and traversed selections with the same candidate pool. Reward is the traversed outcome minus the baseline outcome, using proof completion first and verified progress second. Qwen and Lean remain fixed environment components.

**Tech Stack:** PyTorch REINFORCE, frozen Qwen3-8B, Lean 4.29.1

**Spec:** `docs/superpowers/specs/2026-10-13-unified-analogical-rl-layout-design.md`

## Global Constraints

- Train only `TraversalPolicy` parameters.
- Reward never scores analogy prose directly.
- Compare paired runs under identical proof state, candidate pool, budgets, and seed family.
- Keep held-out connective or difficulty groups out of optimizer updates.

## Review Focus

- Invalid Lean tactics cannot receive positive progress reward.
- Baseline subtraction uses the paired unconditioned outcome, not a stale global average.
- A failed or interrupted episode is persisted and excluded explicitly.
- Gradient accumulation cannot retain Qwen computation graphs.
- Checkpoint resume restores optimizer, policy, RNG, epoch, and split identity.

---

### Task 1: Define distal reward and trajectories

**Files:** `src/analogy/rl.py`, `src/analogy/records.py`, `tests/test_rl.py`

**Interfaces:**
- Produces `EpisodeTrajectory(problem_id, action, baseline_outcome, traversed_outcome, reward)`.
- Produces `lean_reward(baseline, traversed, config) -> float`.

- [ ] Write failing table-driven tests covering completion gain/loss, verified progress, invalid tactics, equal outcomes, and search-cost penalties.
- [ ] Implement lexically ordered reward components with exact configured weights and finite-value validation.
- [ ] Add reward weights to `config.yaml`; run focused and full tests.

### Task 2: Implement one REINFORCE update

**Files:** `src/analogy/rl.py`, `tests/test_training.py`

**Interfaces:** Produces `reinforce_step(policy, optimizer, trajectories, entropy_weight) -> TrainingMetrics`.

- [ ] Write a failing deterministic test where positive reward increases the sampled action's log probability and Qwen parameters remain unchanged.
- [ ] Implement loss `-(reward.detach() * log_probability).mean() - entropy_weight * entropy.mean()` with gradient clipping.
- [ ] Reject empty batches and non-finite rewards before `backward()`.
- [ ] Run focused and full tests.

### Task 3: Train, checkpoint, and resume

**Files:** `src/analogy/rl.py`, `src/analogy/__main__.py`, `tests/test_training.py`, `config.yaml`

**Interfaces:** Produces `train_traversal(config, environment) -> TrainingSummary`.

- [ ] Write failing tests comparing uninterrupted two-epoch training with one epoch plus checkpoint restore and proving failed/interrupted episodes are persisted but excluded from optimizer batches.
- [ ] Implement deterministic split loading, paired episodes, periodic JSONL metrics, and atomic Torch checkpoints under `runs/training/<run-id>/`.
- [ ] Add `experiment.stage: train`, learning rate, epochs, entropy weight, gradient norm, checkpoint interval, and split definition to configuration.
- [ ] Run training on the train split, choose checkpoints using validation only, and evaluate the held-out split once.
- [ ] Stop if learned traversal does not outperform random traversal within the predefined confidence interval; preserve negative results.
