# Analogy Benchmark Low-Hanging-Fruit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve analogy rescue performance with Lean-specific decoding, diagnostic repair, same-state refinement, wider candidate pools, and evidence-rich benchmark metrics.

**Architecture:** Extend the existing strict tactic generator with a diagnostic repair entry point, then compose it in the readable loop without weakening any resource boundary. Derive new benchmark measures from verified rollout records and expose the four-candidate setting through existing configuration.

**Tech Stack:** Python 3.11+, frozen dataclasses, PyTorch policy records, Lean verifier adapter, pytest, YAML.

**Spec:** `docs/superpowers/specs/2026-10-15-analogy-benchmark-low-hanging-fruit-design.md`

## Global Constraints

- Preserve strict JSON schemas and corpus-free operation.
- Keep `rerank_tactics: false` in the fast profile.
- Preserve all configured model, Lean, iteration, cancellation, and wall-clock limits.
- Record semantic repair and same-state refinement in the existing iteration JSONL.
- Tests use real record parsing/control flow; model and Lean boundaries may use deterministic fakes.

## Review Focus

- One remaining model call must not be consumed by an initial generation plus an impossible repair.
- One remaining Lean call must cap initial and repaired tactics together.
- Cancellation between initial verification and repair must stop without another model call.
- Rejected repeated-state successors must not count as repair success or surprise availability.
- Empty diagnostics must still produce a bounded, useful repair prompt.

---

### Task 1: Lean-4 decoding prompt and four-candidate profile

**Files:**
- Modify: `src/analogy/analogy_generation.py`
- Modify: `config.yaml`
- Modify: `config.mlx-8b-fast.yaml`
- Test: `tests/test_analogy_generation.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `render_tactic_prompt(context, candidate_count=...) -> str` with explicit Lean 4 syntax constraints.
- Produces: fast/default configuration requesting four tactics per analogy iteration and direct rollout.

- [ ] Write failing prompt and configuration behavior tests.
- [ ] Run the focused tests and verify failures name missing Lean guidance/counts.
- [ ] Implement the prompt contract and set both tactic counts to four.
- [ ] Run the focused tests and verify they pass.
- [ ] Commit the task.

### Task 2: Diagnostic semantic repair and same-state refinement

**Files:**
- Modify: `src/analogy/analogy_generation.py`
- Modify: `src/analogy/analogy_loop.py`
- Modify: `src/analogy/training.py`
- Test: `tests/test_analogy_generation.py`
- Test: `tests/test_analogy_loop.py`

**Interfaces:**
- Consumes: strict `AnalogyTacticPlan`, `RescueAttempt.result.diagnostics`, and existing loop budgets.
- Produces: `AnalogyTacticGenerator.repair(...) -> GeneratedTacticPlan`.
- Produces: `AnalogyIteration` repair fields and `refined_same_state` flag.

- [ ] Write failing tests for the diagnostic prompt, successful repair, exhausted budgets, cancellation, and second-hop same-state refinement.
- [ ] Run the focused tests and verify they fail for missing behavior.
- [ ] Implement one semantic repair call and merge its verified proposals/attempts into the iteration.
- [ ] Continue on the same state after total rejection when budgets and iteration count permit, carrying diagnostics and failed tactics into the next prompt.
- [ ] Run the focused tests and verify they pass.
- [ ] Commit the task.

### Task 3: Behavioral benchmark metrics

**Files:**
- Modify: `src/analogy/training.py`
- Test: `tests/test_training.py`
- Modify: `docs/benchmarks/2026-10-14-mlx-qwen8b-runtime.md`

**Interfaces:**
- Consumes: rollout attempts and `AnalogyIteration` repair/transition records.
- Produces: `ConditionSummary` rates for Lean acceptance, successors, repairs, and available surprise.

- [ ] Write a failing literal-fixture summary test covering rejected, repaired-progress, and completed attempts.
- [ ] Run the focused test and verify missing metric fields fail.
- [ ] Implement zero-safe metric derivation from verifier-backed records.
- [ ] Document the metrics and the baseline/ablation benchmark protocol.
- [ ] Run the focused tests and verify they pass.
- [ ] Commit the task.

### Task 4: Verification and benchmark ablation

**Files:**
- Modify: `docs/benchmarks/2026-10-15-analogy-repair-ablation.md`

**Interfaces:**
- Consumes: the fast 8B config, emitted summaries, and prior smoke baseline.
- Produces: fresh test evidence and a concise before/after benchmark record.

- [ ] Run the full pytest suite.
- [ ] Run compileall and inspect the branch diff.
- [ ] Run the fast 8B benchmark with the improved configuration if the local model/runtime is available.
- [ ] Record exact runtime, solve counts, and new intermediate metrics without claiming unsupported gains.
- [ ] Commit the benchmark note.

