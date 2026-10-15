# Generic Stuck-Rescue RL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a domain-neutral, stuck-only latent rescue policy that proposes new Lean tactics, records predicted proof states, and learns from verifier-only rewards before any analogy-specific behavior is introduced.

**Architecture:** Preserve `LocalRescueSearch` as the conventional solver and add a parallel generic rescue injection beside the existing fixed-pool `CuriositySearch`. A small trainable Gaussian policy consumes two frozen-Qwen proof embeddings, perturbs one frozen-Qwen prefill activation, and decodes strict JSON tactic proposals plus a predicted next state; Lean classifies every proposal through the existing evaluator. Training uses on-policy REINFORCE over completion, novel-state, and rejection rewards, with conventional, unperturbed, random, and learned conditions sharing search and verifier budgets.

**Tech Stack:** Python 3.11, PyTorch, Transformers/Qwen3-8B, Lean 4.29.1, pytest

**Spec:** `docs/superpowers/specs/2026-10-14-associative-analogy-rescue-rl-design.md`

## Global Constraints

- Start from the reusable `src/analogy` package on merged `main`; do not import or move production code from `scripts/standalone_analogy_recovery.py`.
- Keep Qwen frozen and in evaluation mode; only `DirectRescuePolicy` parameters receive gradients.
- Invoke generic rescue only after the existing observable stuckness decision.
- Permit rescue tactics outside the conventional candidate pool, but verify every tactic with Lean before returning it to the frontier.
- Do not add analogy domains, analogy cards, external retrieval, surprise reward, multi-hop streams, or fixed reasoning operators in this plan.
- Preserve the existing `CuriositySearch` path and its tests as a fixed-pool comparison.
- Reference proofs remain evaluation-only metadata and never enter prompts, rewards, embeddings, or policy inputs.
- Use deterministic derived seeds and shared per-state tactic/verifier budgets across unperturbed, random, and learned rescue conditions.
- Persist scalar action metadata and embedding provenance; never serialize full hidden activations or trainable Torch computation graphs.

## Review Focus

- A generated tactic absent from the conventional pool must still pass through the existing Lean evaluator and state deduplication before entering the frontier.
- Invalid, multiline, duplicate, ordinary-attempted, or over-budget rescue tactics must never consume positive reward or appear as successors.
- A zero latent delta under an identical seed must reproduce unperturbed generation, and the activation hook must be removed after success or exception.
- Non-finite embeddings, latent actions, rewards, losses, or gradients must abort the affected rollout before optimizer mutation.
- Conventional-only behavior and the existing Daydream trace must remain byte-for-byte equivalent when generic rescue is disabled.

## File Structure

- Create `src/analogy/rescue.py` for domain-neutral rescue records, strict proposal parsing, prompts, protocols, and the direct rescue runner.
- Create `src/analogy/rl.py` for the trainable policy, live policy samples, verifier-only reward, REINFORCE update, checkpoint payloads, and training metrics.
- Create `src/analogy/training.py` for split selection, rollout orchestration, matched evaluation conditions, JSONL output, and checkpoint lifecycle.
- Modify `src/analogy/model.py` only for frozen embedding extraction and scoped latent intervention.
- Modify `src/analogy/loop.py` only to invoke the generic rescue runner and preserve its traces beside existing Daydream traces.
- Modify `src/analogy/config.py`, `config.yaml`, and `src/analogy/__main__.py` only when the corresponding runtime behavior is introduced.
- Create `tests/test_rescue.py`, `tests/test_rl.py`, and `tests/test_training.py`; extend existing loop, model, config, and CLI tests rather than duplicating their fixtures.

---

### Task 1: Domain-Neutral Rescue Records and Strict Proposal Parsing

**Files:**
- Create: `src/analogy/rescue.py`
- Modify: `src/analogy/daydream.py`
- Create: `tests/test_rescue.py`
- Modify: `tests/test_daydream.py`
- Modify: `src/analogy/__init__.py`

**Interfaces:**
- Consumes: `SymbolicState`, `SearchNode`, `TacticCandidate`, `TacticResult`, `TacticStatus`, `DiscardedSuccessor`, and `CancellationToken` from `analogy.records`.
- Produces: `TacticEvaluation`, `ProofRescueContext`, `RescueProposal`, `RescueAttempt`, `PolicyActionRecord`, `RescueTermination`, `RescueRollout`, `GenericRescueOutcome`, `GenericRescueRunner`, `parse_rescue_proposals(text, limit)`, and `render_direct_rescue_prompt(context)`.

- [ ] **Step 1: Write failing immutable-record and parser tests**

```python
def test_parse_rescue_proposals_returns_new_analogical_candidates() -> None:
    parsed = parse_rescue_proposals(
        '{"predicted_state":"one goal remains","tactics":["cases h","simp_all"]}',
        limit=2,
    )
    assert [item.candidate.source for item in parsed] == ["cases h", "simp_all"]
    assert all(item.candidate.origin is CandidateOrigin.ANALOGICAL for item in parsed)
    assert all(item.predicted_state == "one goal remains" for item in parsed)
    assert sum(item.candidate.score for item in parsed) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "{}",
        '{"predicted_state":"","tactics":["simp"]}',
        '{"predicted_state":"goal","tactics":[]}',
        '{"predicted_state":"goal","tactics":["simp\\nexact h"]}',
        '{"predicted_state":"goal","tactics":["simp"," simp "]}',
        '{"predicted_state":"goal","tactics":["simp"],"extra":true}',
    ],
)
def test_parse_rescue_proposals_rejects_malformed_output(text: str) -> None:
    with pytest.raises(RescueOutputError):
        parse_rescue_proposals(text, limit=2)


def test_rollout_rejects_action_metadata_with_nonfinite_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        PolicyActionRecord(seed=7, delta_norm=math.nan, log_probability=0.0, entropy=0.0)
```

- [ ] **Step 2: Run the focused tests and verify the new module is missing**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_rescue.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'analogy.rescue'`.

- [ ] **Step 3: Implement the records, validation, parser, and canonical prompt**

Move the existing domain-neutral `TacticEvaluation` dataclass from `daydream.py` into `rescue.py`. Import it back into `daydream.py` so `from analogy.daydream import TacticEvaluation` remains compatible, and add a preservation test for that import before defining the new records:

```python
class RescueTermination(str, Enum):
    COMPLETE = "complete"
    PROGRESS = "progress"
    REJECTED = "rejected"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"
    GENERATION_ERROR = "generation_error"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True, slots=True)
class ProofRescueContext:
    state: SymbolicState
    goal: SymbolicState
    accepted_tactics: tuple[str, ...]
    failed_tactics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RescueProposal:
    candidate: TacticCandidate
    predicted_state: str


@dataclass(frozen=True, slots=True)
class PolicyActionRecord:
    seed: int
    delta_norm: float
    log_probability: float
    entropy: float


@dataclass(frozen=True, slots=True)
class RescueAttempt:
    proposal: RescueProposal
    result: TacticResult
    discarded: DiscardedSuccessor | None = None


@dataclass(frozen=True, slots=True)
class RescueRollout:
    rollout_id: str
    state_id: str
    action: PolicyActionRecord
    proposals: tuple[RescueProposal, ...]
    attempts: tuple[RescueAttempt, ...]
    termination: RescueTermination
    reward: float | None = None


@dataclass(frozen=True, slots=True)
class GenericRescueOutcome:
    completed_proof: str | None = None
    successor: SearchNode | None = None
    rollouts: tuple[RescueRollout, ...] = ()
    cancelled: bool = False
```

Implement `render_direct_rescue_prompt` with the exact section order `Overall target`, `Current goals`, `Accepted prefix`, `Failed tactics`, and a final instruction requiring exactly `predicted_state` and `tactics`. Build `accepted_tactics` from `state.context`; build `failed_tactics` from ordinary attempt sources plus any adapter-recorded failures supplied later by the runner.

`parse_rescue_proposals` must accept exactly one JSON object with keys `predicted_state` and `tactics`, canonicalize tactic whitespace, reject duplicates after canonicalization, reject blank/multiline/NUL tactics, truncate only after validation, and assign uniform scores summing to one.

- [ ] **Step 4: Add adversarial validation tests from Review Focus**

Add tests proving Boolean limits, non-positive limits, mismatched proposal/result candidates, blank IDs, invalid termination values, a `COMPLETE` rollout without a completing attempt, and a `PROGRESS` rollout without a successor-producing attempt all raise `ValueError`.

- [ ] **Step 5: Run focused and preservation tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_rescue.py tests/test_records.py tests/test_daydream.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the record boundary**

```bash
git add src/analogy/rescue.py src/analogy/daydream.py src/analogy/__init__.py tests/test_rescue.py tests/test_daydream.py
git commit -m "feat: define generic rescue rollout records"
```

---

### Task 2: Generic Rescue Injection at Existing Stuck States

**Files:**
- Modify: `src/analogy/loop.py`
- Modify: `src/analogy/rescue.py`
- Modify: `src/analogy/model.py`
- Modify: `tests/test_loop.py`
- Modify: `tests/test_baseline.py`

**Interfaces:**
- Consumes: `GenericRescueRunner.search(state, goal, tactic_pool, ordinary_attempts, evaluator, cancellation) -> GenericRescueOutcome`.
- Produces: optional `rollout_rescue` construction argument on `LocalRescueSearch`; `SearchResult.rescue_rollouts`; `_DisabledRolloutRescue`; unchanged legacy `curiosity_search` behavior.

- [ ] **Step 1: Write failing loop tests for out-of-pool rescue and disabled parity**

```python
class ProposingRolloutRescue:
    def __init__(self, tactic_source: str):
        self.tactic_source = tactic_source
        self.calls = []

    def search(self, state, goal, tactic_pool, ordinary_attempts, evaluator, cancellation):
        candidate = TacticCandidate(self.tactic_source, 1.0, CandidateOrigin.ANALOGICAL)
        evaluation = evaluator(candidate)
        self.calls.append((state, tactic_pool, evaluation))
        rollout = rollout_for(candidate, evaluation)
        return GenericRescueOutcome(
            completed_proof=evaluation.completed_proof,
            successor=evaluation.successor,
            rollouts=(rollout,),
        )


def test_generic_rescue_may_verify_tactic_outside_ordinary_pool() -> None:
    ordinary = candidate("bad", 1.0)
    generated = TacticCandidate("exact h", 1.0, CandidateOrigin.ANALOGICAL)
    verifier = FakeVerifier({
        ("start", "bad"): TacticResult(ordinary, TacticStatus.ERROR),
        ("start", "exact h"): TacticResult(generated, TacticStatus.COMPLETE, proof="by exact h"),
    })
    result = make_search(
        FakeTacticRanker({"start": (ordinary,)}),
        verifier,
        curiosity_search=None,
        rollout_rescue=ProposingRolloutRescue("exact h"),
    ).search(state("start"), state("goal"))
    assert result.status is SearchStatus.COMPLETE
    assert verifier.calls == [("start", "bad"), ("start", "exact h")]
    assert len(result.rescue_rollouts) == 1
```

Add a parity test constructing the same conventional-only search with `rollout_rescue=None` and asserting the full `SearchResult` equals the pre-change expected value.

- [ ] **Step 2: Run the focused tests and verify the constructor rejects the new argument**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_loop.py -k 'generic_rescue or disabled_parity' -v`

Expected: FAIL because `LocalRescueSearch` does not accept `rollout_rescue` and `SearchResult` has no `rescue_rollouts`.

- [ ] **Step 3: Add the parallel generic rescue branch**

Keep `curiosity_search` optional and add `rollout_rescue: GenericRescueRunner | None = None`. Reject configurations where both are non-`None`; existing callers continue to pass `curiosity_search` and therefore preserve their behavior.

After stuckness, run exactly one configured rescue path:

```python
if self._rollout_rescue is not None:
    rescue = self._rollout_rescue.search(
        node.state,
        goal,
        ordinary.candidate_pool,
        ordinary.attempts,
        lambda selected: self._evaluate_for_rescue(node, goal, visited, selected),
        self._cancellation,
    )
    rescue_rollouts.extend(rescue.rollouts)
else:
    rescue = self._curiosity_search.search(...)
    rounds.extend(rescue.rounds)
```

Factor the existing evaluation callback into the domain-neutral `_evaluate_for_rescue`; keep `_evaluate_for_curiosity` as a compatibility wrapper if current tests call it directly. Preserve the existing completion, cancellation, and successor handling. Add `rescue_rollouts` as the final defaulted field on `SearchResult`. Do not weaken `_candidate_pool`'s requirement that the conventional ranker emits only `CandidateOrigin.ORDINARY`.

- [ ] **Step 4: Test invalid generic runner outcomes and state classification**

Add tests proving:

- a rescue result referencing the wrong candidate raises through `_verify_candidate`;
- a generated accepted repeated state is discarded and receives no successor;
- a generated novel state is pushed back to the ordinary frontier;
- generic rescue never runs after ordinary completion or progress;
- cancellation before and after rescue produces `SearchStatus.CANCELLED` with completed rollout traces preserved.

- [ ] **Step 5: Replace the baseline no-op only where generic rescue is selected**

Keep `_DisabledCuriosity` for the current baseline. Add `_DisabledRolloutRescue` returning `GenericRescueOutcome()` for generic-rescue experiment composition; do not enable it in `run_baseline`.

- [ ] **Step 6: Run loop, baseline, and full preservation suites**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_loop.py tests/test_baseline.py tests/test_daydream.py -q`

Then: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: PASS, including the merged standalone experiment tests.

- [ ] **Step 7: Commit the injection point**

```bash
git add src/analogy/loop.py src/analogy/rescue.py src/analogy/model.py tests/test_loop.py tests/test_baseline.py
git commit -m "feat: add generic stuck-state rescue boundary"
```

---

### Task 3: Frozen Proof Embeddings with Explicit Provenance

**Files:**
- Modify: `src/analogy/rescue.py`
- Modify: `src/analogy/model.py`
- Create: `tests/test_hidden_states.py`
- Modify: `tests/test_model.py`

**Interfaces:**
- Consumes: `ProofRescueContext` and `ModelConfig.hidden_layer`.
- Produces: `ProofRepresentation`; `render_proof_embeddings(context) -> tuple[str, str]`; `QwenGenerator.embed_text(text, *, layer) -> Embedding`.

- [ ] **Step 1: Write failing tests for the two proof views**

```python
def test_proof_embedding_prompts_separate_language_and_structure() -> None:
    context = ProofRescueContext(
        state=SymbolicState("s", "P Q : Prop\nh : P ∨ Q\n⊢ Q ∨ P", "lean4", ("intro h",)),
        goal=SymbolicState("g", "Q ∨ P", "lean4"),
        accepted_tactics=("intro h",),
        failed_tactics=("exact h",),
    )
    language, structure = render_proof_embeddings(context)
    assert "Accepted prefix:\nintro h" in language
    assert "Failed tactics:\nexact h" in language
    assert "Current Lean goals" in structure
    assert "Do not propose tactics" in structure
    assert language != structure
```

- [ ] **Step 2: Write synthetic hidden-state tests**

Use a fake tokenizer with padded attention masks and a fake model returning three hidden-state layers. Assert:

- final non-padding token selection ignores right padding;
- positive and equivalent negative layer indices return equal vectors;
- vectors are converted to float32 and L2-normalized;
- zero, NaN, infinity, wrong-rank tensors, and out-of-range layers raise `ModelConfigurationError` before a record is returned;
- two identical calls in eval mode produce identical `Embedding.values`.

- [ ] **Step 3: Run tests and verify `embed_text` is missing**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_hidden_states.py tests/test_model.py -q`

Expected: FAIL with `AttributeError: 'QwenGenerator' object has no attribute 'embed_text'`.

- [ ] **Step 4: Implement deterministic frozen embedding extraction**

```python
def embed_text(self, text: str, *, layer: int) -> Embedding:
    encoded = self._tokenizer([text], return_tensors="pt", padding=True).to(self._device)
    with self._torch.no_grad():
        output = self._model(**encoded, output_hidden_states=True, use_cache=False)
    hidden_states = output.hidden_states
    resolved = _resolve_hidden_layer(layer, len(hidden_states))
    token_index = int(encoded["attention_mask"][0].sum().item()) - 1
    vector = hidden_states[resolved][0, token_index].detach().float()
    norm = self._torch.linalg.vector_norm(vector)
    if not self._torch.isfinite(vector).all() or not self._torch.isfinite(norm) or norm.item() <= 0.0:
        raise ModelConfigurationError("embedding must be finite and nonzero")
    values = tuple(float(item) for item in (vector / norm).cpu().tolist())
    return Embedding(f"{self.model_revision}:layer={resolved}:final-nonpadding", values)
```

Validate the configured layer against the actual returned hidden-state tuple. Keep Torch objects out of `Embedding`.

- [ ] **Step 5: Build `ProofRepresentation` from both embeddings**

Define `ProofRepresentation(context, language_prompt_hash, structure_prompt_hash, language_embedding, structure_embedding)` and require equal dimensions and distinct prompt hashes. The embeddings may share a model/layer provenance ID because they are two views encoded by the same frozen model. Add `QwenProofEncoder.encode(context) -> ProofRepresentation`, calling `embed_text` exactly twice with the same configured layer.

- [ ] **Step 6: Run focused tests and a real-model smoke command**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_hidden_states.py tests/test_model.py tests/test_rescue.py -q`

Then, on a host with the cached model:

```bash
PYTHONPATH=src .venv/bin/python -c 'from analogy.config import load_config; from analogy.model import QwenGenerator; c=load_config(__import__("pathlib").Path("config.yaml")); g=QwenGenerator.from_config(c.model); e=g.embed_text("P : Prop\n⊢ P → P", layer=c.model.hidden_layer); print(e.dimension, round(sum(x*x for x in e.values), 6))'
```

Expected: dimension `4096` for Qwen3-8B and squared norm `1.0` within floating-point tolerance.

- [ ] **Step 7: Commit proof representation support**

```bash
git add src/analogy/rescue.py src/analogy/model.py tests/test_hidden_states.py tests/test_model.py
git commit -m "feat: encode stuck proof representations"
```

---

### Task 4: One-Step Gaussian Policy and Scoped Qwen Intervention

**Files:**
- Create: `src/analogy/rl.py`
- Modify: `src/analogy/model.py`
- Create: `tests/test_rl.py`
- Modify: `tests/test_hidden_states.py`
- Modify: `src/analogy/config.py`
- Modify: `tests/test_config.py`
- Modify: `config.yaml`

**Interfaces:**
- Consumes: `ProofRepresentation` with two same-sized embeddings.
- Produces: `PolicyAction`; `DirectRescuePolicy.sample(representation, torch_generator) -> PolicyAction`; `QwenGenerator.generate_with_delta(messages, *, delta, layer, seed) -> str`.

- [ ] **Step 1: Extend strict RL configuration with consumed fields**

Change the root configuration to:

```yaml
rl:
  enabled: false
  algorithm: reinforce
  rollouts_per_state: 4
  tactics_per_rollout: 2
  hidden_width: 256
  max_delta_norm: 2.0
  initial_log_scale: -2.0
  learning_rate: 0.0001
  entropy_weight: 0.001
  max_gradient_norm: 1.0
  completion_reward: 1.0
  progress_reward: 0.1
  rejection_reward: -0.1
  max_rescue_verifier_calls: 8
```

Add strict tests for unknown keys, Boolean numeric values, non-positive counts/rates/norms, non-finite signed values, and reward ordering `completion_reward > progress_reward >= 0 > rejection_reward`.

- [ ] **Step 2: Write failing policy tests**

```python
def test_policy_sample_is_seeded_bounded_and_differentiable() -> None:
    policy = DirectRescuePolicy(
        embedding_dimension=2,
        hidden_width=4,
        max_delta_norm=0.5,
        initial_log_scale=-2.0,
    )
    representation = proof_representation((1.0, 0.0), (0.0, 1.0))
    first = policy.sample(representation, torch.Generator().manual_seed(9))
    second = policy.sample(representation, torch.Generator().manual_seed(9))
    assert torch.equal(first.delta, second.delta)
    assert torch.linalg.vector_norm(first.delta) <= 0.5 + 1e-6
    assert first.log_probability.requires_grad
    assert first.entropy.requires_grad
```

Also test wrong embedding dimensions, non-finite policy output, batch mismatch, and float16 inputs yielding finite float32 distribution parameters.

Construct the sampling `torch.Generator` on the policy parameter device; reject a generator/device mismatch with a clear error rather than silently falling back to global RNG state.

- [ ] **Step 3: Implement the policy without importing Torch from record modules**

```python
class DirectRescuePolicy(torch.nn.Module):
    def __init__(self, embedding_dimension, hidden_width, max_delta_norm, initial_log_scale):
        super().__init__()
        self.body = torch.nn.Sequential(
            torch.nn.Linear(embedding_dimension * 2, hidden_width),
            torch.nn.Tanh(),
        )
        self.mean = torch.nn.Linear(hidden_width, embedding_dimension)
        self.log_scale = torch.nn.Linear(hidden_width, embedding_dimension)
        torch.nn.init.constant_(self.log_scale.bias, initial_log_scale)
        self.max_delta_norm = max_delta_norm

    def sample(self, representation, generator):
        inputs = torch.tensor(
            representation.language_embedding.values + representation.structure_embedding.values,
            dtype=torch.float32,
            device=next(self.parameters()).device,
        )
        hidden = self.body(inputs)
        mean = self.mean(hidden)
        log_scale = self.log_scale(hidden).clamp(-6.0, 2.0)
        noise = torch.randn(mean.shape, generator=generator, device=mean.device)
        raw = mean + log_scale.exp() * noise
        delta = _clip_vector_norm(raw, self.max_delta_norm)
        distribution = torch.distributions.Normal(mean, log_scale.exp())
        return PolicyAction(delta, distribution.log_prob(raw.detach()).sum(), distribution.entropy().sum())
```

Do not detach `log_probability` or `entropy`; do detach the action passed into frozen Qwen.

- [ ] **Step 4: Write synthetic transformer-hook tests**

Build a fake `model.model.layers` list whose block returns `(hidden, cache)`. Assert:

- only the final token of the initial multi-token prefill changes;
- earlier prompt tokens and every one-token decode call remain unchanged;
- zero delta is exactly equal to ordinary generation;
- tuple outputs preserve non-hidden members;
- dimension/device/batch mismatches fail before generation;
- the hook handle is removed when model generation raises.

- [ ] **Step 5: Implement `generate_with_delta` as a scoped prefill-only hook**

Resolve positive/negative layer indices once, detach and cast the delta to the block input's device/dtype, clone the hidden tensor, add only at `[:, -1, :]`, and guard with an `applied` Boolean so cached decoding cannot reapply it. Register inside `try` and call `handle.remove()` in `finally`.

Use the same tokenizer, seed, decoding parameters, timeout handling, and output slicing as `generate`; factor shared generation preparation rather than copying it.

- [ ] **Step 6: Run policy, hook, configuration, and full tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_rl.py tests/test_hidden_states.py tests/test_config.py tests/test_model.py -q`

Then: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: PASS.

- [ ] **Step 7: Commit policy and intervention support**

```bash
git add config.yaml src/analogy/config.py src/analogy/model.py src/analogy/rl.py tests/test_config.py tests/test_hidden_states.py tests/test_model.py tests/test_rl.py
git commit -m "feat: add one-step latent rescue policy"
```

---

### Task 5: Direct Rescue Runner with Shared Budgets

**Files:**
- Modify: `src/analogy/rescue.py`
- Modify: `src/analogy/rl.py`
- Modify: `tests/test_rescue.py`
- Modify: `tests/test_loop.py`
- Create: `tests/test_rescue_integration.py`

**Interfaces:**
- Consumes: `QwenProofEncoder`, `DirectRescuePolicy`, `QwenGenerator.generate_with_delta`, the existing evaluator callback, and `RLConfig` rollout/tactic/verifier budgets.
- Produces: `DirectRescueRunner.search(...) -> GenericRescueOutcome`; `RolloutCollector`; deterministic `derive_rescue_seed(base_seed, state_id, rollout_index, operation)`.

- [ ] **Step 1: Write failing fake-component runner tests**

Create fake encoder, policy, delta generator, evaluator, and cancellation token. Pin these behaviors:

```python
def test_direct_runner_generates_new_tactic_and_returns_verified_progress() -> None:
    runner = direct_runner(outputs=(
        '{"predicted_state":"h exposed","tactics":["cases h","simp"]}',
    ))
    outcome = runner.search(
        state("stuck"),
        state("goal"),
        (ordinary("exact h"),),
        (TacticResult(ordinary("exact h"), TacticStatus.ERROR),),
        evaluator_accepting("cases h", state("split")),
        NeverCancelled(),
    )
    assert outcome.successor.state.id == "split"
    assert outcome.rollouts[0].attempts[0].proposal.candidate.source == "cases h"
    assert outcome.rollouts[0].termination is RescueTermination.PROGRESS
```

Add cases for ordinary duplicate filtering, cross-rollout duplicate filtering, malformed generation isolation, cancellation, completion, all rejected, and exact budget exhaustion.

- [ ] **Step 2: Implement stable derived seeds and the collector**

Derive seeds with SHA-256 over a versioned UTF-8 string containing base seed, state ID, rollout index, and operation; convert the first eight digest bytes to an unsigned integer less than `2**63`. `RolloutCollector` retains live `PolicyAction` objects only until an optimizer step and exposes immutable `RescueRollout` records for persistence.

- [ ] **Step 3: Implement `DirectRescueRunner`**

For one stuck state:

1. Build `ProofRescueContext` from current state, goal, `state.context`, and ordinary failed attempts.
2. Encode the proof exactly once.
3. Sample `rollouts_per_state` actions unless cancelled.
4. Generate and parse at most `tactics_per_rollout` proposals.
5. Remove tactics already attempted ordinarily or by earlier rescue rollouts.
6. Call the supplied evaluator until completion, first novel successor, or `max_rescue_verifier_calls`.
7. Record scalar action statistics using detached values.
8. Return immediately on completion or progress; otherwise preserve all failed rollouts.

The predicted state is diagnostic only in this plan and must not influence candidate acceptance or reward.

- [ ] **Step 4: Add a real-`LocalRescueSearch` fake-Lean integration test**

Use an ordinary ranker whose only candidate errors, a direct runner that emits a new `cases h` candidate, and a fake verifier that accepts it into a novel state where the ordinary ranker emits a completing tactic. Assert control returns to ordinary search and the final `SearchResult` contains both ordinary expansions and the rescue rollout.

- [ ] **Step 5: Add one pinned real-Lean smoke test**

Use the bundled `or_swap`-shaped problem or construct `Problem("rescue_or_swap", "(P Q : Prop) (h : P ∨ Q)", "Q ∨ P")`. Supply deterministic fake model output proposing `cases h`; assert Lean accepts a novel open state and ordinary scripted branch tactics complete the theorem. Skip only when `lake` or the pinned toolchain is unavailable, using the same skip convention as existing end-to-end tests.

- [ ] **Step 6: Run focused and full tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_rescue.py tests/test_loop.py tests/test_rescue_integration.py -q`

Then: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: PASS.

- [ ] **Step 7: Commit direct rescue execution**

```bash
git add src/analogy/rescue.py src/analogy/rl.py tests/test_rescue.py tests/test_loop.py tests/test_rescue_integration.py
git commit -m "feat: run bounded direct rescue rollouts"
```

---

### Task 6: Verifier-Only Reward and REINFORCE Update

**Files:**
- Modify: `src/analogy/rl.py`
- Modify: `tests/test_rl.py`
- Modify: `tests/test_rescue.py`

**Interfaces:**
- Consumes: completed `RescueRollout` records and matching live `PolicyAction` values from `RolloutCollector`.
- Produces: `RewardWeights`; `verifier_reward(rollout, weights) -> RewardBreakdown`; `TrainingSample`; `TrainingMetrics`; `reinforce_step(policy, optimizer, samples, entropy_weight, max_gradient_norm) -> TrainingMetrics`.

- [ ] **Step 1: Write table-driven reward tests**

```python
@pytest.mark.parametrize(
    ("termination", "expected"),
    [
        (RescueTermination.COMPLETE, 1.0),
        (RescueTermination.PROGRESS, 0.1),
        (RescueTermination.REJECTED, -0.1),
        (RescueTermination.EXHAUSTED, 0.0),
        (RescueTermination.BUDGET_EXHAUSTED, 0.0),
    ],
)
def test_verifier_reward_is_determined_only_by_lean_outcome(termination, expected):
    assert verifier_reward(rollout(termination), weights()).total == expected
```

Prove that predicted-state text, tactic text, delta norm, domain-like words, and generation length do not change reward.

- [ ] **Step 2: Write deterministic policy-gradient tests**

Use a one-parameter fake policy/distribution with analytically fixed actions so the direction of a single optimizer step is deterministic. Assert:

- positive advantage increases the stored action's log probability after one optimizer step;
- negative advantage decreases it;
- zero advantage with zero entropy weight leaves parameters unchanged;
- Qwen/fake-generator parameters have no gradients;
- empty batches, detached log probabilities, NaN rewards, NaN losses, and non-finite gradients raise before `optimizer.step()`;
- gradient norm is clipped to the configured maximum.

- [ ] **Step 3: Implement immutable reward breakdowns**

```python
@dataclass(frozen=True, slots=True)
class RewardBreakdown:
    completion: float = 0.0
    progress: float = 0.0
    rejection: float = 0.0

    @property
    def total(self) -> float:
        return self.completion + self.progress + self.rejection
```

`verifier_reward` must map only the terminal classification to one configured component. It must reject a rollout whose declared termination contradicts its attempts.

- [ ] **Step 4: Implement one REINFORCE update**

```python
def reinforce_step(policy, optimizer, samples, entropy_weight, max_gradient_norm):
    rewards = torch.tensor([sample.reward for sample in samples], dtype=torch.float32)
    advantages = rewards - rewards.mean() if len(samples) > 1 else rewards
    log_probabilities = torch.stack([sample.action.log_probability for sample in samples])
    entropies = torch.stack([sample.action.entropy for sample in samples])
    loss = -(advantages.detach() * log_probabilities).mean() - entropy_weight * entropies.mean()
    if not torch.isfinite(loss):
        raise ValueError("training loss must be finite")
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    gradient_norm = torch.nn.utils.clip_grad_norm_(policy.parameters(), max_gradient_norm)
    if not torch.isfinite(gradient_norm):
        optimizer.zero_grad(set_to_none=True)
        raise ValueError("policy gradients must be finite")
    optimizer.step()
    return TrainingMetrics(float(loss.detach()), float(rewards.mean()), float(gradient_norm))
```

For a single-sample batch, use its raw reward rather than centering it to zero. Group normalization across sibling rollouts belongs in Task 7, where complete state-level batches are available.

- [ ] **Step 5: Attach rewards to persisted rollouts without retaining graphs**

Add `with_reward(rollout, breakdown.total)` using `dataclasses.replace`. Persist only scalar `PolicyActionRecord`; keep `PolicyAction` in `TrainingSample` until the step, then clear the collector.

- [ ] **Step 6: Run focused and full tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_rl.py tests/test_rescue.py -q`

Then: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: PASS.

- [ ] **Step 7: Commit verifier-only learning**

```bash
git add src/analogy/rl.py src/analogy/rescue.py tests/test_rl.py tests/test_rescue.py
git commit -m "feat: train rescue policy from Lean rewards"
```

---

### Task 7: Training Orchestration, Checkpoints, and Matched Controls

**Files:**
- Create: `src/analogy/training.py`
- Create: `tests/test_training.py`
- Modify: `src/analogy/__main__.py`
- Modify: `tests/test_cli.py`
- Modify: `src/analogy/model.py`
- Modify: `src/analogy/config.py`
- Modify: `tests/test_config.py`
- Modify: `config.yaml`
- Modify: `README.md`

**Interfaces:**
- Consumes: loaded benchmark problems, `DirectRescueRunner`, `DirectRescuePolicy`, `reinforce_step`, `LocalRescueSearch`, Qwen, and Lean verifier.
- Produces: `split_problems(problems) -> ProblemSplit`; `train_direct_rescue(config, generator, verifier) -> TrainingSummary`; `evaluate_direct_rescue(config, generator, verifier, checkpoint) -> EvaluationSummary`; CLI stages `train_rescue` and `evaluate_rescue`.

- [ ] **Step 1: Define deterministic structural splits and tests**

Add these exact corpus values to `rl.train_connectives`, `rl.validation_connectives`, and `rl.test_connectives`:

```text
train:      implication, and, or
validation: not
test:       iff
```

Extend the Task 4 RL configuration with `epochs: 10`, `checkpoint_interval: 1`, `checkpoint: null`, and those three connective lists. Implement explicit optional-path validation for `checkpoint`; evaluation rejects `null`, while training accepts it and uses the output directory for new checkpoints. Validate positive epoch counts, nonempty string lists, no duplicates, and pairwise disjointness. Tests must prove order-independent input produces stable ordered splits and every selected problem belongs to exactly one split.

- [ ] **Step 2: Write failing orchestration tests with fake generator and verifier**

Pin:

- optimizer updates consume train problems only;
- validation chooses checkpoints without optimizer mutation;
- test evaluation refuses to run twice in the same output directory;
- interrupted and model-failed rollouts are persisted but excluded from optimizer batches;
- sibling rollout rewards are normalized together per stuck state, never across unrelated theorems;
- conventional, unperturbed, random, and learned conditions receive identical ordinary search limits and maximum rescue verifier calls;
- random and learned conditions use the same derived seed family.

- [ ] **Step 3: Implement non-overwriting JSONL run output**

Each run creates `runs/rescue/<run-id>/` with:

```text
config.json
rollouts.jsonl
problems.jsonl
metrics.jsonl
checkpoints/epoch-0001.pt
summary.json
```

Write each JSONL record once, flush after every finalized problem, and write checkpoints atomically through a sibling temporary file followed by `Path.replace`. `config.json` records Git revision when available, model name/revision, Lean toolchain, prompt versions, split identities, budgets, and seed.

- [ ] **Step 4: Implement checkpoint save/resume equivalence**

The checkpoint contains:

```python
{
    "policy": policy.state_dict(),
    "optimizer": optimizer.state_dict(),
    "epoch": epoch,
    "python_rng": random.getstate(),
    "torch_rng": torch.random.get_rng_state(),
    "split": asdict(problem_split),
    "config_hash": resolved_config_hash,
}
```

Test uninterrupted two-epoch training against one epoch plus resume and assert equal parameters, optimizer tensors, next sampled action, and aggregate metrics. Reject a resume whose config hash or split differs.

- [ ] **Step 5: Implement matched evaluation conditions**

For each test problem run:

1. `conventional`: no generic rescue;
2. `unperturbed`: direct rescue with zero delta;
3. `random`: untrained policy initialized from the recorded seed;
4. `learned`: selected validation checkpoint.

Record completion, newly solved/regressed IDs, ordinary expansions, rescue calls, verifier calls, generated/valid/novel tactics, elapsed time, and reward components. Do not claim analogy benefit in this plan; no analogy is generated.

- [ ] **Step 6: Wire CLI stages and preserve old stages**

Extend the accepted stages from `{"capability", "baseline"}` to `{"capability", "baseline", "train_rescue", "evaluate_rescue"}`. Require `rl.enabled=true` and `analogy.enabled=false` for both new stages. `evaluate_rescue` requires an explicit checkpoint path in configuration; training never automatically evaluates the test split.

Add CLI tests proving capability and baseline dispatch are unchanged, invalid stage combinations return exit code 2, and fake train/evaluate functions receive the resolved configuration.

- [ ] **Step 7: Document the generic checkpoint and future boundary**

Add a README section with:

```text
conventional proof search
→ observable stuckness
→ direct latent rescue rollout
→ predicted next state + generated tactics
→ Lean verification
→ return progress to conventional search
```

State explicitly that this checkpoint contains no corpus retrieval and no analogy stream. Link the design and this plan, and identify one-hop analogy generation as the next separately reviewed plan.

- [ ] **Step 8: Run all verification**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_training.py tests/test_cli.py tests/test_config.py -q
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src/analogy
git diff --check
```

Expected: all tests pass, compilation succeeds silently, and `git diff --check` emits no output.

- [ ] **Step 9: Run one real-model development smoke test without test-split evaluation**

Use one training-split theorem, one rollout, two rescue tactics, and one optimizer step. Confirm the output directory contains finite policy metrics, at least one finalized rollout, model revision, resolved seed, and Lean results. This smoke test may fail to solve the theorem; success means the bounded pipeline completes and writes auditable artifacts.

- [ ] **Step 10: Commit the generic RL experiment**

```bash
git add config.yaml README.md src/analogy/__main__.py src/analogy/config.py src/analogy/model.py src/analogy/training.py tests/test_cli.py tests/test_config.py tests/test_training.py
git commit -m "feat: train and evaluate generic rescue policy"
```

## Deferred Follow-Up Plans

After this plan passes matched evaluation, write separate reviewed plans in this order:

1. predicted-versus-actual proof-state embeddings and gated surprise reward;
2. one-hop generated analogy lens with the direct rescue baseline as control;
3. multi-hop associative streams with stop decisions and return propagation;
4. free-form continuation, reframing, remapping, and abandonment behavior.

None of those follow-ups may import the standalone script or remove the direct-rescue ablation.
