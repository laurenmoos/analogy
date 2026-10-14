# Analogical RL Plan Index

These plans implement the approved [unified repository design](../superpowers/specs/2026-10-13-unified-analogical-rl-layout-design.md). Execute them in numeric order. Each plan leaves a working, testable system and is independently reviewable.

## Existing implementation

- Lean Logic Gym: complete benchmark, Lean verifier, sessions, traces, replay, CLI, 25 reference proofs, and 40 tests.
- Daydream: complete records, observable stuckness, best-first outer loop, local rescue, iterative relation refinement, pertinence interfaces, constrained reranking, and 102 tests.
- Domain probes: disposable JavaScript experiments with external poetry and TRIP data.
- Missing: Qwen adapter, model capability evidence, hidden-state extraction, proof-conditioned analogy generation, latent traversal, RL, and notebooks.

## Execution order

0. [Unified package migration](00-unified-package-migration.md) — consolidate the existing Python projects without changing behavior.
1. [Qwen capability gate](01-qwen-capability.md) — establish that frozen Qwen3-8B solves at least one benchmark problem.
2. [Conventional tactic baseline](02-tactic-baseline.md) — produce and measure top-k tactics without analogies.
3. [Proof-need representation](03-proof-representation.md) — extract a reproducible hidden-state vector for a stuck proof.
4. [Proof-conditioned analogy generation](04-analogy-generation.md) — generate, gate, and use analogies for constrained reranking.
5. [Latent traversal](05-latent-traversal.md) — learn a one-step perturbation of the proof-conditioned activation.
6. [Lean-verified RL](06-lean-reward-training.md) — train the traversal policy from distal verified reward.
7. [Notebook evaluation](07-notebook-evaluation.md) — compare baselines, ablations, and learned traversal outside production code.

The first implementation session should execute only Plan 0. Later plans require explicit review after the preceding plan's measurements are available.
