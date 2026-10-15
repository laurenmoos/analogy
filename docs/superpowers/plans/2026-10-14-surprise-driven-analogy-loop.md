# Surprise-Driven Analogy Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a readable closed-loop rescue controller that generates analogy cards, decodes analogy-conditioned tactics, verifies them with Lean, and iterates until transition surprise converges or a hard budget stops it.

**Architecture:** Pure records and a `SurpriseController` define stopping semantics; promoted standalone proof-view code supplies Lean structure and frozen transition embeddings; generators own strict prompts/parsing; `analogy_loop.py` contains only linear orchestration. Training and evaluation consume immutable iteration traces without changing the conventional solver path.

**Tech Stack:** Python 3.11+, PyTorch, Hugging Face Transformers, Lean 4.29.1, pytest, JSONL artifacts.

**Spec:** `docs/superpowers/specs/2026-10-14-surprise-driven-analogy-loop-design.md`

## Global Constraints

- Lean is the only authority for proof completion and successor acceptance.
- The relation vocabulary remains open-ended; do not add a fixed analogy-operator taxonomy.
- Frozen Qwen embeddings use one configured layer and normalized final non-padding hidden state.
- Every continuation or stop decision must be reproducible from immutable logs without rerunning the model.
- Hard model-call, Lean-call, tactic, iteration, cancellation, and wall-clock budgets take precedence over model-proposed stopping.
- Keep `src/analogy/analogy_loop.py` linear and readable; parsing, embedding, repair, serialization, and reward math belong elsewhere.
- Do not import `scripts/standalone_analogy_recovery.py`; promote the required logic into package modules with package tests.

## Review Focus

- A NaN or mismatched-dimension transition embedding must terminate that stream as an infrastructure failure, never silently become zero surprise; Task 2 tests this.
- A model `stop=true` before `min_iterations` must be ignored, while a hard budget must still stop immediately; Task 1 tests both priorities.
- Rejected tactics have no actual transition and therefore no intrinsic surprise reward; Task 5 tests this.
- Repeated accepted states must not create an infinite analogy loop; Task 4 tests termination through the existing progress classification.
- Cancellation between generation, embedding, and Lean verification must persist the completed prefix and return promptly; Task 4 tests every boundary.

---

### Task 1: Strict Hyperparameters and Pure Surprise Controller

**Files:**
- Modify: `src/analogy/config.py`
- Modify: `config.yaml`
- Create: `src/analogy/surprise.py`
- Modify: `tests/test_config.py`
- Create: `tests/test_surprise.py`

**Interfaces:**
- Consumes: finite scalar validation conventions from `analogy.config`.
- Produces: `SurpriseConfig`; `StopReason`; `SurpriseState`; `SurpriseDecision`; `SurpriseController.decide(state, observation) -> SurpriseDecision`.

- [ ] **Step 1: Write failing strict-config tests**

Add the complete analogy configuration to `_valid_yaml()` and assert parsing plus boundary rejection:

```python
assert config.analogy.min_iterations == 1
assert config.analogy.max_iterations == 6
assert config.analogy.surprise_threshold == 0.2

@pytest.mark.parametrize(("old", "new"), [
    ("min_iterations: 1", "min_iterations: 7"),
    ("surprise_threshold: 0.2", "surprise_threshold: 2.1"),
    ("discount: 0.95", "discount: 0"),
    ("iteration_cost: 0.01", "iteration_cost: -0.01"),
])
def test_analogy_loop_config_rejects_invalid_ranges(tmp_path, old, new):
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml().replace(old, new), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)
```

- [ ] **Step 2: Run config tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_config.py -k analogy_loop -q`

Expected: FAIL because the fields are unknown or absent.

- [ ] **Step 3: Write failing controller tests**

```python
def test_low_surprise_stops_only_after_minimum_and_patience():
    controller = SurpriseController(config(min_iterations=2, surprise_patience=2))
    first = controller.decide(SurpriseState(), observation(0, 0.1, model_stop=True))
    second = controller.decide(first.state, observation(1, 0.1))
    assert first.continue_loop
    assert second.reason is StopReason.SURPRISE_CONVERGED

def test_hard_budget_overrides_required_minimum():
    result = SurpriseController(config(min_iterations=3)).decide(
        SurpriseState(), observation(0, 0.9, remaining_model_calls=0)
    )
    assert result.reason is StopReason.MODEL_BUDGET

def test_small_improvement_stops_after_patience():
    controller = SurpriseController(
        config(min_surprise_improvement=0.05, improvement_patience=2)
    )
    first = controller.decide(SurpriseState(), observation(0, 0.8))
    second = controller.decide(first.state, observation(1, 0.78))
    third = controller.decide(second.state, observation(2, 0.77))
    assert third.reason is StopReason.IMPROVEMENT_STALLED
```

- [ ] **Step 4: Run controller tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_surprise.py -q`

Expected: collection FAIL because `analogy.surprise` does not exist.

- [ ] **Step 5: Implement config and pure state machine**

Use frozen records and stable reason values:

```python
class StopReason(str, Enum):
    CONTINUE = "continue"
    COMPLETE = "complete"
    NO_SUCCESSOR = "no_successor"
    SURPRISE_CONVERGED = "surprise_converged"
    IMPROVEMENT_STALLED = "improvement_stalled"
    MODEL_STOP = "model_stop"
    MAX_ITERATIONS = "max_iterations"
    MODEL_BUDGET = "model_budget"
    LEAN_BUDGET = "lean_budget"
    CANCELLED = "cancelled"

@dataclass(frozen=True, slots=True)
class SurpriseState:
    values: tuple[float, ...] = ()
    low_surprise_streak: int = 0
    low_improvement_streak: int = 0

@dataclass(frozen=True, slots=True)
class SurpriseDecision:
    continue_loop: bool
    reason: StopReason
    state: SurpriseState
```

Implement priority order: cancellation/completion/hard budgets/no-successor; update streaks; maximum iterations; convergence; stalled improvement; advisory model stop; continue.

- [ ] **Step 6: Run focused tests and commit**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_config.py tests/test_surprise.py -q`

Commit: `feat: define surprise loop controls`

---

### Task 2: Promote Structural Proof Views and Transition Embeddings

**Files:**
- Create: `src/analogy/proof_views.py`
- Modify: `src/analogy/model.py`
- Modify: `src/analogy/lean_gym/verifier.py`
- Create: `tests/test_proof_views.py`
- Modify: `tests/test_model.py`
- Modify: `tests/test_lean_verifier.py`

**Interfaces:**
- Consumes: `Problem`, `SymbolicState`, Qwen `embed_text`, and pinned Lean verifier.
- Produces: `ProofStructure`; `EmbeddingProvenance`; `ProofViews`; `TransitionComparison`; `render_language_view`; `render_structure_view`; `render_transition`; `compare_transitions`; `LeanSubprocessVerifier.proof_structure_after(problem, tactics)`.

- [ ] **Step 1: Write failing view and comparison tests**

```python
def test_transition_comparison_measures_moves_not_raw_successors():
    encoder = MappingEncoder({
        "before -> predicted": (1.0, 0.0),
        "before -> actual": (0.8, 0.6),
    })
    result = compare_transitions("before", "predicted", "actual", encoder, -1)
    assert result.surprise == pytest.approx(0.2)
    assert result.predicted.rendering == "before -> predicted"

def test_transition_comparison_rejects_nonfinite_or_dimension_mismatch():
    encoder = MappingEncoder({
        canonical_transition("before", "predicted"): (1.0, float("nan")),
        canonical_transition("before", "actual"): (1.0,),
    })
    with pytest.raises(ProofViewError):
        compare_transitions("before", "predicted", "actual", encoder, -1)
```

- [ ] **Step 2: Write failing Lean structural-signature test**

Use one fixture theorem and assert the returned signature contains goal count, target root, and hypothesis shapes without source paths or hidden identifiers.

- [ ] **Step 3: Run proof-view tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_proof_views.py tests/test_lean_verifier.py -k 'proof_structure or transition' -q`

Expected: FAIL on missing module and verifier method.

- [ ] **Step 4: Promote the standalone representation logic**

Port the Lean `analogyExprShape` prelude and normalized final-token embedding provenance into package-owned types. Keep transition rendering canonical:

```python
def render_transition(before: str, after: str) -> str:
    return f"Before proof state:\n{normalize(before)}\nAfter proof state:\n{normalize(after)}"

def compare_transitions(before, predicted, actual, encoder, layer):
    predicted_rendering = render_transition(before, predicted)
    actual_rendering = render_transition(before, actual)
    predicted_embedding = encoder.embed_text(predicted_rendering, layer=layer)
    actual_embedding = encoder.embed_text(actual_rendering, layer=layer)
    surprise = cosine_distance(predicted_embedding, actual_embedding)
    return TransitionComparison(
        EmbeddedTransition(predicted_rendering, predicted_embedding),
        EmbeddedTransition(actual_rendering, actual_embedding),
        surprise,
    )
```

Record model revision, resolved layer, token index, and text hash. Extend `QwenGenerator.embed_text` to return this provenance without changing its normalized vector semantics.

- [ ] **Step 5: Verify focused tests and standalone preservation**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_proof_views.py tests/test_model.py tests/test_lean_verifier.py tests/test_standalone_analogy_recovery.py -q`

- [ ] **Step 6: Commit**

Commit: `feat: promote structural proof transition views`

---

### Task 3: Condition Cards and Tactics on the Complete Trace

**Files:**
- Modify: `src/analogy/analogy_stream.py`
- Create: `src/analogy/analogy_generation.py`
- Modify: `tests/test_analogy_stream.py`
- Create: `tests/test_analogy_generation.py`

**Interfaces:**
- Consumes: `ProofViews`, `ProofStructure`, `AnalogyStream`, previous `AnalogyIteration` summaries, and strict generation/repair primitives.
- Produces: `AnalogyGenerator.generate(context) -> GeneratedHop`; `AnalogyTacticGenerator.generate(context) -> GeneratedTacticPlan`; versioned prompt renderers.

- [ ] **Step 1: Write failing full-conditioning prompt tests**

```python
def test_later_hop_prompt_contains_views_outcomes_and_surprise_trend():
    prompt = render_hop_prompt(context_with_two_iterations())
    assert "Language view hash:" in prompt
    assert "Lean structural signature:" in prompt
    assert '"actual_transition"' in prompt
    assert '"surprise":0.61' in prompt
    assert '"surprise_delta":-0.14' in prompt

def test_tactic_prompt_contains_complete_stream_and_requires_valid_citation():
    context = tactic_context_with_hops("hop-0", "hop-1")
    prompt = render_tactic_prompt(context)
    assert '"hop_id":"hop-0"' in prompt
    assert '"hop_id":"hop-1"' in prompt
    with pytest.raises(AnalogyOutputError, match="cite"):
        parse_analogy_tactic_plan(
            '{"predicted_state":"done","tactics":["exact h"],'
            '"motivating_hop_id":"outside"}',
            context.analogy.stream,
        )
```

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_analogy_generation.py tests/test_analogy_stream.py -q`

Expected: FAIL because generation contexts and versioned renderers are missing.

- [ ] **Step 3: Extract generation responsibilities from the runner**

Define strict contexts:

```python
@dataclass(frozen=True, slots=True)
class AnalogyGenerationContext:
    proof: ProofRescueContext
    views: ProofViews
    structure: ProofStructure
    stream: AnalogyStream
    prior_iterations: tuple[IterationSummary, ...]

@dataclass(frozen=True, slots=True)
class TacticGenerationContext:
    analogy: AnalogyGenerationContext
    active_hop_id: str
```

Move model calls, strict parsing, bounded repair, raw-attempt capture, and deterministic seeds into `analogy_generation.py`. The tactic prompt must include every hop and require `motivating_hop_id` from the active stream.

- [ ] **Step 4: Verify generation tests and commit**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_analogy_generation.py tests/test_analogy_stream.py tests/test_rescue.py -q`

Commit: `feat: condition tactics on analogy traces`

---

### Task 4: Implement the Readable Closed-Loop Coordinator

**Files:**
- Create: `src/analogy/analogy_loop.py`
- Modify: `src/analogy/analogy_stream.py`
- Modify: `src/analogy/rescue.py`
- Create: `tests/test_analogy_loop.py`
- Modify: `tests/test_loop.py`

**Interfaces:**
- Consumes: Tasks 1–3 interfaces plus existing `GenericRescueRunner` evaluator callback.
- Produces: `AnalogyIteration`; `AnalogyLoopTrace`; `AnalogyLoopRunner.search(state, goal, tactic_pool, ordinary_attempts, evaluator, cancellation) -> GenericRescueOutcome`.

- [ ] **Step 1: Write failing three-iteration convergence test**

```python
def test_loop_conditions_tactics_and_stops_after_surprise_patience():
    runner = loop_runner(
        predicted=("p1", "p2", "p3"),
        actual=("a1", "a2", "a3"),
        surprises=(0.8, 0.1, 0.09),
        threshold=0.2,
        patience=2,
    )
    outcome = runner.search(
        state("stuck"), state("goal"), (), (), evaluator(), NeverCancelled()
    )
    trace = outcome.rollouts[0].analogy_trace
    assert [item.surprise for item in trace.iterations] == [0.8, 0.1, 0.09]
    assert trace.stop_reason is StopReason.SURPRISE_CONVERGED
    assert all(item.tactic_plan.motivating_hop_id == item.hop.hop_id for item in trace.iterations)
```

- [ ] **Step 2: Write failing control-boundary tests**

Add separate tests for cancellation before card generation, before tactic generation, before Lean, and before transition embedding; repeated-state/no-successor termination; advisory early stop; maximum iterations; and each hard budget.

- [ ] **Step 3: Run loop tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_analogy_loop.py -q`

Expected: collection FAIL because `analogy.analogy_loop` is missing.

- [ ] **Step 4: Implement the linear coordinator**

Keep `search` visually close to this shape:

```python
while controller.may_start_iteration(resources, cancellation):
    views = proof_views.build(current_state, goal, history)
    generated_hop = analogy_generator.generate(views, stream, history)
    stream = stream.append(generated_hop.hop)
    tactic_plan = tactic_generator.generate(views, stream, history)
    verified = verify_plan(tactic_plan, evaluator, resources, cancellation)
    comparison = compare_if_accepted(current_state, tactic_plan, verified)
    iteration = build_iteration(views, generated_hop, tactic_plan, verified, comparison)
    decision = controller.decide(controller_state, observation(iteration, resources))
    trace_sink.add(iteration.with_decision(decision))
    if not decision.continue_loop:
        return finish(iteration, decision)
    current_state = verified.successor.state
return finish_budget_exhausted(trace, resources)
```

Helpers perform validation and mutation-free record construction. Replace `AnalogyStreamRunner` with a compatibility alias or thin adapter to `AnalogyLoopRunner`; do not retain two orchestration implementations.

- [ ] **Step 5: Run focused and conventional-loop tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_analogy_loop.py tests/test_loop.py tests/test_rescue.py -q`

- [ ] **Step 6: Commit**

Commit: `feat: iterate analogies until surprise converges`

---

### Task 5: Immutable Iteration Logs and Multi-Iteration Credit

**Files:**
- Modify: `src/analogy/training.py`
- Modify: `src/analogy/rl.py`
- Modify: `src/analogy/analogy_loop.py`
- Modify: `tests/test_training.py`
- Modify: `tests/test_rl.py`
- Modify: `tests/test_analogy_loop.py`

**Interfaces:**
- Consumes: `AnalogyLoopTrace`, live `PolicyAction` values, configured discount/cost.
- Produces: `analogy_iterations.jsonl`; `discounted_iteration_samples(trace, actions, verified_reward, discount, iteration_cost)`.

- [ ] **Step 1: Write failing trace-completeness test**

Run a deterministic fake three-iteration episode, persist it, and assert every JSONL row contains identifiers, proof views, structure, raw-generation references, card, action diagnostics, tactic plan, every Lean attempt, transition comparison, surprise trend, controller state, decision, budgets, and elapsed time.

- [ ] **Step 2: Write failing credit tests**

```python
def test_verified_return_reaches_every_iteration_action():
    samples = discounted_iteration_samples(trace, actions, 1.0, 0.9, 0.01)
    assert [sample.reward for sample in samples] == pytest.approx([0.79, 0.89, 1.0])
    loss = sum(-sample.reward * sample.action.log_probability for sample in samples)
    loss.backward()
    assert all(action.log_probability.grad_fn is not None for action in actions)

def test_rejected_iteration_has_no_surprise_reward():
    assert intrinsic_surprise_reward(rejected_iteration, cap=0.1) == 0.0
```

- [ ] **Step 3: Run tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_training.py tests/test_rl.py tests/test_analogy_loop.py -k 'iteration or surprise_reward' -q`

- [ ] **Step 4: Implement JSONL persistence and credit**

Create `analogy_iterations.jsonl` with `RescueRunWriter`; serialize one row immediately after each iteration. Preserve live actions until all discounted samples are constructed. Cap intrinsic surprise at `progress_reward`, require an accepted successor, and keep it disabled outside a named condition.

- [ ] **Step 5: Verify and commit**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_training.py tests/test_rl.py tests/test_analogy_loop.py -q`

Commit: `feat: log and train surprise analogy iterations`

---

### Task 6: Training Composition, Matched Evaluation, and Documentation

**Files:**
- Modify: `src/analogy/training.py`
- Modify: `src/analogy/__main__.py`
- Modify: `src/analogy/__init__.py`
- Modify: `tests/test_training.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `AnalogyLoopRunner`, verified replay records, strict analogy configuration.
- Produces evaluation conditions `conventional`, `learned_direct`, `analogy_loop`, and `shuffled_analogy`; per-condition metrics and failure records.

- [ ] **Step 1: Write failing matched-budget tests**

```python
def test_all_rescue_conditions_share_immutable_budget():
    conditions = build_evaluation_conditions(config)
    rescue = [item for item in conditions if item.name != "conventional"]
    assert len({item.budget for item in rescue}) == 1
    assert {item.name for item in conditions} == {
        "conventional", "learned_direct", "analogy_loop", "shuffled_analogy"
    }

def test_shuffled_analogies_change_assignment_without_crossing_split():
    records = (
        replay_record("test-a", split="test", connective="iff"),
        replay_record("test-b", split="test", connective="iff"),
    )
    shuffled = shuffle_analogy_assignments(records, seed=7)
    assert {item.target.problem.id for item in shuffled} == {"test-a", "test-b"}
    assert all(item.source.problem.id != item.target.problem.id for item in shuffled)
    assert all(item.source.split == item.target.split == "test" for item in shuffled)
```

- [ ] **Step 2: Run orchestration tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_training.py tests/test_cli.py -k 'analogy or budget or shuffled' -q`

- [ ] **Step 3: Compose training and evaluation runners**

Build loop runners only when `analogy.enabled`. Reconstruct replay states through Lean before training. Use identical theorem IDs, seeds, model/Lean/tactic/wall-clock budgets across rescue conditions. Refuse shuffled evaluation with fewer than two compatible test records.

- [ ] **Step 4: Extend summaries and README**

Report solved IDs, newly solved/regressed IDs, completion per Lean call, valid and repaired generation rates, mean iteration count, stop-reason histogram, mean surprise trajectory, saturation, failures, and artifact paths. Document the readable loop and each hyperparameter with one concise example trace.

- [ ] **Step 5: Run full automated verification**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m compileall -q src tests
git diff --check
```

- [ ] **Step 6: Run offline real smoke**

Set `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`. Use one verified replay state, `max_iterations=3`, at least one repaired card, at least two accepted analogy iterations, and a matched learned-direct control. Confirm `config.json`, `generations.jsonl`, `rollouts.jsonl`, `analogy_iterations.jsonl`, `problems.jsonl`, `metrics.jsonl`, and `summary.json` are nonempty and internally consistent.

- [ ] **Step 7: Commit**

Commit: `feat: benchmark surprise-driven analogy rescue`

---

## Final Verification

- [ ] Re-read the design specification and map every requirement to a completed task.
- [ ] Confirm the working tree contains no unrelated user changes.
- [ ] Run the full pytest and compile commands from Task 6 with fresh output.
- [ ] Inspect a complete `analogy_iterations.jsonl` trajectory for readability.
- [ ] Verify conventional search results are unchanged when analogy is disabled.
- [ ] Report real smoke limitations plainly; do not infer analogy benefit from a smoke run.
