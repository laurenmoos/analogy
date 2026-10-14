# Qwen Capability Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Demonstrate that frozen local Qwen3-8B can solve at least one Lean Logic Gym problem before analogy or RL work begins.

**Architecture:** A narrow model adapter emits a JSON tactic sequence for a problem. The existing `ProofSession` executes every tactic in Lean, and a capability runner records verified outcomes without exposing reference proofs to the model.

**Tech Stack:** Python, PyTorch, Transformers `AutoTokenizer` and `AutoModelForCausalLM`, Qwen/Qwen3-8B, Lean 4.29.1

**Spec:** `docs/superpowers/specs/2026-10-13-unified-analogical-rl-layout-design.md`

**Primary references:** [Qwen3-8B model](https://huggingface.co/Qwen/Qwen3-8B) and its [model configuration](https://huggingface.co/Qwen/Qwen3-8B/blob/main/config.json).

## Global Constraints

- Keep Qwen frozen and local.
- Use the official `Qwen/Qwen3-8B` checkpoint and chat template.
- Reference proofs are validation data and never enter prompts.
- The capability stage passes only when at least one generated proof is Lean-verified.
- Unit tests use an injected fake backend and never download model weights.

## Review Focus

- Invalid JSON and non-string tactics become recorded model failures, not crashes.
- Empty generations and generation timeouts leave the benchmark run resumable.
- A generated proof counts only after clean Lean verification.
- Model device selection reports unavailable CUDA/MPS explicitly.
- Repeated runs with the same seed write distinct, immutable run records.

---

### Task 1: Define model and capability records

**Files:** `src/analogy/model.py`, `src/analogy/records.py`, `tests/test_model.py`

**Interfaces:**
- Produces `TextGenerator.generate(messages: tuple[Message, ...], *, seed: int) -> str`.
- Produces `CapabilityResult(problem_id, tactics, solved, failure, elapsed_seconds)`.

- [x] Write failing tests for valid JSON arrays, malformed JSON, empty tactics, and non-string entries.
- [x] Run the focused tests and verify missing symbols fail.
- [x] Implement `parse_tactic_sequence(text: str) -> tuple[str, ...]` using `json.loads`, rejecting anything except a nonempty string array.
- [x] Run focused and full tests to green.

### Task 2: Add the frozen Qwen backend

**Files:** `src/analogy/model.py`, `pyproject.toml`, `tests/test_model.py`

**Interfaces:**
- Produces `QwenGenerator.from_config(config: ModelConfig) -> QwenGenerator`.
- `generate` applies the chat template and decodes only newly generated tokens.

- [x] Write a failing adapter test using injected tokenizer/model doubles that verifies chat-template input, `max_new_tokens`, deterministic seed, output slicing, and explicit rejection of unavailable configured devices.
- [x] Add optional dependencies `torch`, `transformers>=4.51,<5`, and `accelerate`; do not import them at package import time.
- [x] Implement lazy loading with `AutoTokenizer.from_pretrained` and `AutoModelForCausalLM.from_pretrained(device_map="auto")`.
- [x] Freeze parameters with `requires_grad_(False)` and call `eval()`.
- [x] Run unit tests without network access; then run one explicitly authorized local smoke generation with the real checkpoint.

### Task 3: Run the verified capability gate

**Files:** `src/analogy/model.py`, `src/analogy/__main__.py`, `tests/test_capability.py`, `config.yaml`

**Interfaces:**
- Produces `run_capability(config, generator, verifier) -> tuple[CapabilityResult, ...]`.

- [x] Write a failing integration test where a fake generator returns `['intro h', 'exact h']` and the real Lean verifier closes an implication problem; also assert that two runs using the same seed receive distinct run IDs and never overwrite artifacts.
- [x] Implement a prompt containing only binders, target, allowed output schema, and basic Lean tactic guidance.
- [x] Execute each generated tactic through `ProofSession`; stop on rejection or completion and save one JSONL result per problem under `runs/capability/<run-id>/`.
- [x] Add `model.max_new_tokens`, `model.timeout_seconds`, and `experiment.minimum_solved: 1` to strict configuration.
- [x] Run the full 25-problem capability stage and record solved count, attempted count, tactic sequences, failures, model revision, seed, and elapsed time.
- [x] Stop the research sequence if solved count is zero; improve only the baseline prompt/interface before proceeding.

### Task 4: Explore a stronger native baseline

**Files:** `src/analogy/model.py`, `src/analogy/config.py`, `config.yaml`, `tests/test_model.py`, `tests/test_config.py`, `tests/test_capability.py`, `README.md`

**Interfaces:**
- Extends `ModelConfig` with explicit sampling controls.
- Keeps each decoding run independently Lean-verified and immutable.

- [x] Add failing tests for configurable sampling, structural prompt guidance, strict decoding configuration, and recorded decoding metadata.
- [x] Add benchmark-agnostic Lean guidance for implication, conjunction, disjunction, iff, negation, and bound hypotheses; never expose reference proofs.
- [x] Run the improved prompt greedily, then run three reproducible low-temperature samples.
- [x] Compare single-run results and the union of solved problems against the original 4/25 control.
- [x] Preserve the strongest honest configuration and document the results.
