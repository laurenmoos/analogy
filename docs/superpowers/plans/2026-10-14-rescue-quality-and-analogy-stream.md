# Rescue Quality and Analogy Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make rescue generations observable and trainable, stabilize latent exploration, build reproducible stuck-state replay, and add interpretable analogy streams with matched controls.

**Architecture:** Extend immutable rescue records before changing runners, then replace the saturated action distribution and reward boundary. Add replay as a separate persistence/reconstruction module, and add analogy streams as a second `GenericRescueRunner` implementation so `LocalRescueSearch` remains unchanged.

**Tech Stack:** Python 3.11+, PyTorch, Transformers/Qwen3-8B, Lean 4.29.1, pytest, JSONL

**Spec:** `docs/superpowers/specs/2026-10-14-rescue-quality-and-analogy-stream-design.md`

## Global Constraints

- Qwen remains frozen; only policy parameters receive gradients.
- Every tactic must pass the existing Lean evaluator before entering the frontier or receiving positive reward.
- No external analogy corpus, proof retrieval, or fixed reasoning-operator vocabulary.
- Runs are non-overwriting and preserve raw generation, seeds, model/prompt provenance, budgets, and reward components.
- Train, validation, and test replay records remain structurally disjoint.
- Conventional-only search remains unchanged when rescue is disabled.

## Review Focus

- A model runtime exception must not be mislabeled as invalid structured output or receive policy reward.
- A repair call must consume model budget exactly once and never consume Lean budget by itself.
- Direction normalization must remain finite for an all-zero or extremely small sampled vector.
- Replay reconstruction must reject a state whose normalized Lean text differs from its record.
- Shuffled analogy evaluation must never assign a held-out stream to its original state or cross the configured structural split.

---

### Task 1: Generation Attempt Traces and Repair

**Files:**
- Modify: `src/analogy/rescue.py`
- Modify: `src/analogy/training.py`
- Modify: `tests/test_rescue.py`
- Modify: `tests/test_training.py`

**Interfaces:**
- Produces: `GenerationStatus`, `GenerationAttempt`, `RescueTermination.INVALID_OUTPUT`, `render_repair_prompt(raw, diagnostic)`, and new keyword-only `max_model_calls` and `max_repair_calls` constructor arguments on `DirectRescueRunner`.
- `RescueRollout.generations: tuple[GenerationAttempt, ...]` defaults to empty for compatibility.

- [ ] **Step 1: Write failing record, parser, and runner tests**

```python
def test_invalid_output_records_raw_text_and_one_successful_repair():
    runner = direct_runner(("not json", '{"predicted_state":"done","tactics":["exact h"]}'), max_repair_calls=1)
    calls = []
    outcome = runner.search(
        state("stuck"), state("goal"), (), (),
        evaluator_for({"exact h": (TacticStatus.COMPLETE, "by exact h")}, calls),
        NeverCancelledToken(),
    )
    assert [g.status for g in outcome.rollouts[0].generations] == [GenerationStatus.INVALID, GenerationStatus.VALID]
    assert outcome.rollouts[0].generations[0].raw_text == "not json"

def test_runtime_generation_error_is_not_repaired_or_trainable():
    runner = direct_runner((RuntimeError("device lost"),), max_repair_calls=1)
    outcome = runner.search(
        state("stuck"), state("goal"), (), (),
        evaluator_for({}, []), NeverCancelledToken(),
    )
    assert outcome.rollouts[0].termination is RescueTermination.GENERATION_ERROR
    assert len(generator.calls) == 1
```

- [ ] **Step 2: Run the focused tests and verify missing types/arguments fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_rescue.py -k 'repair or generation_attempt' -q`

- [ ] **Step 3: Implement immutable generation records and fenced-object extraction**

```python
class GenerationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    RUNTIME_ERROR = "runtime_error"

@dataclass(frozen=True, slots=True)
class GenerationAttempt:
    seed: int
    raw_text: str
    status: GenerationStatus
    repair: bool = False
    error_kind: str | None = None
    diagnostic: str | None = None
```

Permit either one bare JSON object or one fenced `json` object with no surrounding prose. Keep schema and tactic validation unchanged after extraction.

- [ ] **Step 4: Add bounded repair inside `DirectRescueRunner`**

Catch `RescueOutputError` separately from generator exceptions. Record invalid text, render a repair-only prompt, and retry while both repair and total model-call budgets remain. Record generator exceptions as runtime attempts and terminate without repair.

- [ ] **Step 5: Persist generation attempts to `generations.jsonl`**

Create the file in `RescueRunWriter.create`; `_persist_episode` writes one row per attempt with rollout/problem/phase identifiers.

- [ ] **Step 6: Run focused and preservation tests, then commit**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_rescue.py tests/test_training.py tests/test_loop.py -q`

Commit: `feat: trace and repair rescue generations`

---

### Task 2: Direction-and-Radius Policy

**Files:**
- Modify: `src/analogy/rl.py`
- Modify: `src/analogy/rescue.py`
- Modify: `src/analogy/training.py`
- Modify: `tests/test_rl.py`
- Modify: `tests/test_training.py`

**Interfaces:**
- `PolicyAction` adds scalar `radius` and `raw_direction_norm` tensors.
- `DirectRescuePolicy` adds radius mean/log-scale heads and `schema_version = 2`.
- Checkpoints persist and validate `policy_schema_version`.

- [ ] **Step 1: Write failing distribution tests**

```python
def test_initial_policy_does_not_saturate_high_dimensional_actions():
    actions = [policy.sample(representation(4096), seeded(i)) for i in range(128)]
    saturated = sum(a.delta.norm() >= 0.99 * policy.max_delta_norm for a in actions)
    assert saturated / len(actions) < 0.10

def test_action_radius_matches_delta_norm_and_retains_gradient():
    action = policy.sample(representation(8), seeded(1))
    assert action.delta.norm() == pytest.approx(action.radius)
    assert action.log_probability.requires_grad
```

- [ ] **Step 2: Run tests and confirm current hard-clipped Gaussian saturates**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_rl.py -k 'saturate or radius' -q`

- [ ] **Step 3: Implement stochastic direction and radius**

Sample dimension-normalized direction noise, normalize with `clamp_min(torch.finfo(raw_direction.dtype).tiny)`, sample a scalar Normal radius logit, and compute `radius = max_delta_norm * sigmoid(raw_radius)`. Initialize radius mean to `logit(0.1)` and add both distribution log probabilities and entropies.

- [ ] **Step 4: Record radius diagnostics and reject incompatible checkpoints**

Extend `PolicyActionRecord`; add a checkpoint schema field and explicit mismatch error in both training resume and evaluation load.

- [ ] **Step 5: Run policy/training tests and commit**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_rl.py tests/test_training.py tests/test_rescue.py -q`

Commit: `feat: stabilize rescue policy radius`

---

### Task 3: Dense Failure Rewards and Moving Baseline

**Files:**
- Modify: `src/analogy/config.py`
- Modify: `config.yaml`
- Modify: `src/analogy/rl.py`
- Modify: `src/analogy/training.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_rl.py`
- Modify: `tests/test_training.py`

**Interfaces:**
- `RewardWeights` adds `invalid_output`, `empty_output`, and `hop_cost`.
- `RewardBreakdown` mirrors those components.
- `RewardBaseline.update(state_id, reward) -> float` returns an advantage.

- [ ] **Step 1: Write failing reward and trainability tests**

```python
def test_invalid_and_empty_outputs_receive_distinct_negative_rewards():
    assert verifier_reward(invalid_rollout(), weights).invalid_output == -0.05
    assert verifier_reward(empty_rollout(), weights).empty_output == -0.02

def test_runtime_error_and_cancellation_are_not_trainable():
    assert not rollout_is_trainable(RescueTermination.GENERATION_ERROR)
    assert not rollout_is_trainable(RescueTermination.CANCELLED)
    assert rollout_is_trainable(RescueTermination.INVALID_OUTPUT)
```

- [ ] **Step 2: Run focused tests and verify current zero rewards fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_rl.py tests/test_training.py -k 'invalid or empty or baseline' -q`

- [ ] **Step 3: Implement strict config and component rewards**

Require finite negative invalid/empty weights and a non-negative hop cost smaller than completion reward. Keep completion/progress as the only positive components.

- [ ] **Step 4: Add an exponential moving baseline for singleton state groups**

Use sibling mean subtraction for groups of two or more. For a singleton, subtract the pre-update EMA for that structural training split, then update the EMA after the optimizer step. Persist baseline state in checkpoints.

- [ ] **Step 5: Record prediction alignment and gate surprise reward**

For every accepted attempt, encode `proposal.predicted_state` and `attempt.result.state.expression` with the frozen proof encoder, record cosine distance, and add a clipped intrinsic component only when the named evaluation/training condition enables surprise. Tests prove rejected tactics always receive zero intrinsic reward and that surprise cannot exceed progress reward.

- [ ] **Step 6: Verify non-finite and infrastructure exclusions, then commit**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_rl.py tests/test_training.py tests/test_config.py -q`

Commit: `feat: learn from structured rescue failures`

---

### Task 4: Stuck-State Dataset and Lean Reconstruction

**Files:**
- Create: `src/analogy/replay.py`
- Modify: `src/analogy/loop.py`
- Modify: `src/analogy/training.py`
- Create: `tests/test_replay.py`
- Modify: `tests/test_loop.py`
- Modify: `tests/test_training.py`

**Interfaces:**
- Produces: `StuckStateRecord`, `StuckStateSink`, `JsonlStuckStateStore`, `reconstruct_stuck_state(record, verifier)` and `CurriculumSampler`.
- `LocalRescueSearch` accepts optional `stuck_state_sink` and emits only after an observable stuckness decision.

- [ ] **Step 1: Write failing immutable record/store tests**

```python
def test_store_deduplicates_by_problem_prefix_and_fingerprint(tmp_path):
    store = JsonlStuckStateStore(tmp_path / "states.jsonl")
    store.add(record); store.add(record)
    assert store.load() == (record,)

def test_reconstruction_rejects_normalized_state_mismatch():
    with pytest.raises(ReplayMismatchError):
        reconstruct_stuck_state(replace(record, proof_state="forged"), verifier)
```

- [ ] **Step 2: Run tests and verify the replay module is missing**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_replay.py -q`

- [ ] **Step 3: Implement records, append-only store, and exact reconstruction**

Reconstruct with `verifier.state_after(problem, accepted_tactics)`, require `OPEN`, and compare normalized state text and fingerprint before returning a `SymbolicState`.

- [ ] **Step 4: Capture stuck states and enforce split identity**

The training writer creates `stuck_states.jsonl`. Capture the ordinary attempts, candidate sources, theorem data, difficulty/connective, accepted prefix, and split. Reject a replay source whose record split differs from its current problem split.

- [ ] **Step 5: Add deterministic stratified curriculum sampling**

Sort by difficulty, connective, problem ID, and fingerprint; select round-robin across connectives with a seeded rotation. Tests cover deterministic ordering, minimum-count failure, and no cross-split sampling.

- [ ] **Step 6: Mix reconstructed replay episodes into training**

Add a `ReplayProblemRunner` that reconstructs the state, invokes the configured rescue runner directly, and returns the same `ProblemEpisode` type as online search. At each epoch, deterministically interleave online episodes and replay episodes according to `replay_mixture_ratio`; validation and test never sample training replay records.

- [ ] **Step 7: Run replay/loop/training suites and commit**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_replay.py tests/test_loop.py tests/test_training.py -q`

Commit: `feat: replay verified stuck proof states`

---

### Task 5: Analogy Hop Records, Prompts, and Parsing

**Files:**
- Create: `src/analogy/analogy_stream.py`
- Create: `tests/test_analogy_stream.py`
- Modify: `src/analogy/__init__.py`

**Interfaces:**
- Produces: `AnalogyHop`, `AnalogyStream`, `parse_analogy_hop`, `render_analogy_hop_prompt`, `render_analogy_tactic_prompt`.

- [ ] **Step 1: Write failing strict-schema tests**

```python
def test_parse_hop_preserves_readable_relation_chain_and_mapping():
    hop = parse_analogy_hop(
        '{"domain":"river","situation":"a blocked channel",'
        '"relation_chain":["flow is blocked","pressure finds another channel"],'
        '"mapping":{"channel":"proof branch"},'
        '"continuation":"open a second channel",'
        '"predicted_consequence":"split the remaining goal",'
        '"rationale":"the obstruction suggests branching","stop":true}'
    )
    assert hop.relation_chain == ("flow is blocked", "pressure finds another channel")
    assert hop.mapping == (("channel", "proof branch"),)

@pytest.mark.parametrize("bad", ["{}", '{"stop":true}', "not json"])
def test_parse_hop_rejects_incomplete_or_opaque_cards(bad):
    with pytest.raises(AnalogyOutputError): parse_analogy_hop(bad)
```

- [ ] **Step 2: Run tests and verify the module is missing**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_analogy_stream.py -q`

- [ ] **Step 3: Implement immutable hop/stream records and canonical prompts**

Require nonblank domain, situation, continuation, prediction, rationale; at least one relation and mapping pair; explicit Boolean `stop`; no unknown keys. Prompt later hops with prior hop JSON and the unchanged proof context.

- [ ] **Step 4: Add tactic-decoding prompt with hop citations and tests**

The output remains the direct rescue tactic schema plus `motivating_hop_id`; validate that every citation belongs to the supplied stream.

- [ ] **Step 5: Run focused tests and commit**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_analogy_stream.py tests/test_rescue.py -q`

Commit: `feat: define interpretable analogy streams`

---

### Task 6: Multi-Hop Analogy Runner and Return Propagation

**Files:**
- Modify: `src/analogy/analogy_stream.py`
- Modify: `src/analogy/rl.py`
- Modify: `src/analogy/training.py`
- Modify: `tests/test_analogy_stream.py`
- Modify: `tests/test_rl.py`

**Interfaces:**
- Produces: `StreamRescuePolicy`, `AnalogyStreamRunner` implementing `GenericRescueRunner`, and `discounted_stream_samples(actions, final_reward, hop_cost, discount)`.

- [ ] **Step 1: Write failing two-hop success and invalid-branch tests**

```python
def test_runner_chains_two_hops_then_decodes_and_verifies_tactic():
    runner = analogy_runner(
        hop_outputs=(valid_hop(stop=False), valid_hop(stop=True)),
        tactic_output='{"predicted_state":"no goals","tactics":["exact h"]}',
        max_hops=3,
    )
    outcome = runner.search(
        state("stuck"), state("goal"), (), (),
        evaluator_for({"exact h": (TacticStatus.COMPLETE, "by exact h")}, []),
        NeverCancelledToken(),
    )
    assert [hop.parent_id for hop in outcome.rollouts[0].analogy_stream.hops] == [None, "hop-0"]
    assert outcome.completed_proof == "by exact h"

def test_invalid_second_hop_preserves_first_hop_and_terminates_only_stream():
    runner = analogy_runner(
        hop_outputs=(valid_hop(stop=False), "not json"),
        tactic_output=None,
        max_hops=3,
        max_repair_calls=0,
    )
    outcome = runner.search(
        state("stuck"), state("goal"), (), (),
        evaluator_for({}, []), NeverCancelledToken(),
    )
    assert len(outcome.rollouts[0].analogy_stream.hops) == 1
    assert outcome.rollouts[0].termination is RescueTermination.INVALID_OUTPUT
```

- [ ] **Step 2: Run focused tests and verify the runner is absent**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_analogy_stream.py -k runner -q`

- [ ] **Step 3: Implement bounded stream generation and tactic decoding**

Implement `StreamRescuePolicy` with proof language/structure inputs, latest-hop language/structure inputs (zeros before hop one), normalized hop index, direction/radius action heads, and a Bernoulli stop head. Enforce total model-call, repair-call, hop, tactic, and Lean-call budgets before every call. Encode each valid hop for the next policy action. Ignore stop before hop one and force tactic decoding at `max_hops`.

- [ ] **Step 4: Propagate discounted final return through every live action**

For actions ordered oldest to newest, use `discount ** distance * final_reward - hop_cost * remaining_hops`. Tests assert every log probability receives a finite nonzero gradient after a successful two-hop stream.

- [ ] **Step 5: Run focused and generic rescue suites and commit**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_analogy_stream.py tests/test_rl.py tests/test_loop.py -q`

Commit: `feat: run trainable analogy streams`

---

### Task 7: Matched Evaluation, CLI, and Real Smoke Benchmark

**Files:**
- Modify: `src/analogy/config.py`
- Modify: `config.yaml`
- Modify: `src/analogy/training.py`
- Modify: `src/analogy/__main__.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_training.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`

**Interfaces:**
- Evaluation conditions: `conventional`, `unperturbed`, `random`, `learned_direct`, `direct_deliberation`, `learned_analogy`, `shuffled_analogy`.

- [ ] **Step 1: Write failing matched-budget and shuffled-assignment tests**

Assert every rescue condition receives identical total model calls, Lean calls, tactic count, wall-clock limit, theorem IDs, and seeds. Assert shuffled streams never remain paired with their source state and never cross the test split.

- [ ] **Step 2: Run training/config/CLI tests and verify missing condition support**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_training.py tests/test_config.py tests/test_cli.py -q`

- [ ] **Step 3: Implement strict configuration and runner composition**

Build each condition from one immutable `EvaluationBudget`; direct deliberation consumes no fewer and no more generation calls than analogy; conventional reports zero rescue calls. Refuse shuffled evaluation with fewer than two compatible test states.

- [ ] **Step 4: Extend summaries and README**

Report valid generation rate, repair success, saturation rate, rescue opportunities, novel states, completion per Lean call, newly solved/regressed IDs, and per-condition failures.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`

- [ ] **Step 6: Run real Qwen/Lean smoke**

Use at least one replayed stuck state, one repaired generation, two analogy hops, and one matched direct control. Confirm all immutable artifacts exist and raw diagnostics are present.

- [ ] **Step 7: Run the minimum useful benchmark**

Use at least 30 train, five validation, and five held-out stuck states over three seeds. Do not claim analogy benefit unless learned analogy beats conventional, learned direct, direct deliberation, and shuffled analogy under matched budgets.

- [ ] **Step 8: Commit orchestration and documentation**

Commit: `feat: benchmark matched analogy rescue`

---

## Final Verification

- [ ] Confirm the full suite passes from a clean tree.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m compileall -q src tests`.
- [ ] Run `git diff --check`.
- [ ] Review all changes against both design specs.
- [ ] Preserve benchmark artifacts under a non-overwriting run directory and report negative results plainly.
