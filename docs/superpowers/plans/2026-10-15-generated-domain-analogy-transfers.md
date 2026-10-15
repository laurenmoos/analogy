# Generated Domain Analogy Transfers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a corpus-free pipeline that generates a concrete source-domain analogy, aligns it to the active Lean proof need, translates the alignment into tactic-free proof reasoning, decodes tactics, verifies them, and renders the complete process as a connected proof trace.

**Architecture:** Add strict transfer records beside the legacy `AnalogyHop` control, enlarge the shared rescue-policy observation from two snapshot embeddings to four trajectory channels plus scalars, and introduce a generated-domain loop assembled from independent source, alignment, reasoning, and tactic generators. Preserve the legacy loop for ablation, keep Lean authoritative, and render both ordinary and analogical actions on one theorem-level state graph.

**Tech Stack:** Python 3.11+, frozen Qwen/MLX backend, PyTorch policy head, Lean 4 subprocess verifier, strict JSON dataclasses, pytest.

**Spec:** `docs/superpowers/specs/2026-10-15-generated-domain-analogy-transfers-design.md`

## Global Constraints

- Do not import or depend on `scripts/standalone_analogy_recovery.py`; reimplement only its generic typed-transformation ideas.
- Do not require a persistent external analogy corpus; source situations are generated online.
- Qwen remains frozen; only the rescue policy is trainable.
- A latent policy action conditions source-domain generation only, never alignment, proof transfer, or tactic decoding.
- Lean is the only correctness authority; prose quality, alignment confidence, and post-hoc judge scores are never rewards.
- Unverified transfers never mutate the proof frontier.
- Keep the current proof-paraphrase loop runnable as the `legacy` control.
- Persist prompt versions, seeds, hashes, budgets, parsed stages, actual Lean attempts, and stop reasons.
- Use the existing five theorem examples as stable human-readable trace fixtures.

## Review Focus

- A source card that merely repeats Lean identifiers or tactic names must fail before alignment or tactic generation (`test_source_analogy_rejects_proof_paraphrases_and_lean_syntax`).
- An alignment with nonblank text but no declared source-to-proof role correspondence must fail (`test_alignment_requires_declared_roles_on_both_sides`).
- The first rescue step has no previous transition or analogy; it must use deterministic zero channels without changing tensor dimensions (`test_first_observation_uses_zero_optional_channels`).
- After an accepted successor, the next policy observation must contain that exact verified transition and latest transfer rather than stale state (`test_generated_loop_refreshes_observation_after_successor`).
- Trace rendering must distinguish rejected unchanged states, accepted repeated candidates, multiple open subgoals, and completion (`test_trace_renderer_connects_failures_repeats_and_completion`).

---

### Task 1: Strict source-analogy and transfer records

**Files:**
- Create: `src/analogy/analogy_transfer.py`
- Create: `tests/test_analogy_transfer.py`

**Interfaces:**
- Consumes: strict JSON text and name-free proof-role declarations.
- Produces: `ProofNeed`, `TransformationRecord`, `SourceAnalogy`, `AnalogyAlignment`, `ProofReasoningTransfer`, `derive_proof_need(ProofStructure, str) -> ProofNeed`, and `parse_*` functions used by Tasks 2–5.

- [ ] **Step 1: Write failing parser and validation tests**

```python
def test_source_analogy_rejects_proof_paraphrases_and_lean_syntax() -> None:
    payload = valid_source_payload()
    payload["source_before"] = "apply h1 to prove P"
    with pytest.raises(TransferOutputError, match="source domain"):
        parse_source_analogy(json.dumps(payload), analogy_id="analogy-1")


def test_alignment_requires_declared_roles_on_both_sides() -> None:
    payload = valid_alignment_payload()
    payload["mapping"] = {"unknown source": "unknown proof role"}
    with pytest.raises(TransferOutputError, match="declared roles"):
        parse_alignment(
            json.dumps(payload),
            source=source_analogy(),
            need=proof_need(),
            alignment_id="alignment-1",
        )


def test_transfer_must_target_active_subgoal_not_fixed_target() -> None:
    payload = valid_transfer_payload(active_subgoal="C → S")
    with pytest.raises(TransferOutputError, match="active subgoal"):
        parse_proof_reasoning_transfer(
            json.dumps(payload),
            need=proof_need(active_subgoal="C"),
            alignment=alignment(),
            transfer_id="transfer-1",
        )


def test_derive_proof_need_prefers_reuse_for_matching_hypothesis() -> None:
    need = derive_proof_need(structure_with_matching_hypothesis(), "C")
    assert need.operation is OperationClass.REUSE
    assert need.active_subgoal == "C"
    assert need.proof_roles == ("active_subgoal", "matching_hypothesis")
```

- [ ] **Step 2: Run the focused tests and observe RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_analogy_transfer.py`

Expected: collection fails because `analogy.analogy_transfer` does not exist.

- [ ] **Step 3: Implement the typed records and strict parsers**

Define the standalone-inspired enums and immutable records:

```python
class OperationClass(str, Enum):
    INTRODUCE = "introduce"
    DECOMPOSE = "decompose"
    SELECT = "select"
    COMBINE = "combine"
    SELECT_BRANCH = "select_branch"
    REORDER = "reorder"
    SPECIALIZE = "specialize"
    REWRITE = "rewrite"
    REUSE = "reuse"
    REPLACE = "replace"
    REMOVE = "remove"
    RESTRUCTURE = "restructure"
    UNKNOWN = "unknown"


class Arity(str, Enum):
    ZERO = "zero"
    ONE = "one"
    TWO = "two"
    MANY = "many"
    UNKNOWN = "unknown"


class Permutation(str, Enum):
    NONE = "none"
    SWAP = "swap"
    GENERAL = "general"
    UNKNOWN = "unknown"


class InformationFlow(str, Enum):
    PRESERVE = "preserve"
    SELECT = "select"
    ADD = "add"
    DISCARD = "discard"
    RESTRUCTURE = "restructure"


@dataclass(frozen=True, slots=True)
class TransformationRecord:
    operation: OperationClass
    input_arity: Arity
    output_arity: Arity
    permutation: Permutation
    information_flow: tuple[InformationFlow, ...]
    focus_before: str
    focus_after: str
    domain_neutral_summary: str


@dataclass(frozen=True, slots=True)
class ProofNeed:
    fixed_target: str
    active_subgoal: str
    proof_roles: tuple[str, ...]
    operation: OperationClass
    input_arity: Arity
    output_arity: Arity
    permutation: Permutation
    information_flow: tuple[InformationFlow, ...]
    focus_before: str
    desired_focus_after: str
    summary: str


@dataclass(frozen=True, slots=True)
class SourceAnalogy:
    analogy_id: str
    domain: str
    source_before: str
    source_after: str
    source_roles: tuple[str, ...]
    relation_chain: tuple[str, ...]
    transformation: TransformationRecord
    rationale: str


@dataclass(frozen=True, slots=True)
class AnalogyAlignment:
    alignment_id: str
    source_analogy_id: str
    mapping: tuple[tuple[str, str], ...]
    invariant: str
    source_operation: str
    proof_operation: str
    limitation: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ProofReasoningTransfer:
    transfer_id: str
    alignment_id: str
    active_subgoal: str
    premises: tuple[str, ...]
    reasoning_steps: tuple[str, ...]
    predicted_next_subgoal: str
    completion_expected: bool
```

Use exact-key JSON parsing, canonical enum order, finite confidence in `[0, 1]`, declared-role validation, two-to-four source relations, two-to-six mappings, and a source-text guard covering Lean tactic words, `⊢`, theorem identifiers, and exact proposition fragments supplied by `ProofNeed`.

Port the standalone script's deterministic operation inference into
`derive_proof_need`: implications/foralls imply `INTRODUCE`, matching hypotheses
imply `REUSE`, equalities imply `REWRITE`, conjunction goals imply `COMBINE`, and
structured hypotheses containing the target imply `DECOMPOSE`/`SELECT`. Return
`UNKNOWN` rather than inventing an operation when no rule applies.

- [ ] **Step 4: Run focused tests and observe GREEN**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_analogy_transfer.py`

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit the records**

```bash
git add src/analogy/analogy_transfer.py tests/test_analogy_transfer.py
git commit -m "feat: define generated analogy transfers"
```

### Task 2: Transition-aware policy observation

**Files:**
- Modify: `src/analogy/analogy_transfer.py`
- Modify: `src/analogy/rescue.py`
- Modify: `src/analogy/proof_views.py`
- Modify: `src/analogy/rl.py`
- Modify: `src/analogy/training.py`
- Modify: `tests/test_proof_views.py`
- Modify: `tests/test_rl.py`
- Modify: `tests/test_training.py`

**Interfaces:**
- Consumes: `ProofRescueContext`, optional verified transition text, optional `ProofReasoningTransfer`, surprise/repetition/resource scalars.
- Produces: `ProofRepresentation` with four equal-dimensional embedding channels and `scalar_features`; `DirectRescuePolicy.sample(ProofRepresentation, Generator)` accepts the enlarged fixed-width observation.

Add the readable pre-embedding record:

```python
@dataclass(frozen=True, slots=True)
class AnalogyObservation:
    fixed_target: str
    current_state: str
    previous_state: str | None
    last_tactic: str | None
    accepted_tactics: tuple[str, ...]
    rejected_tactics: tuple[tuple[str, tuple[str, ...]], ...]
    latest_source: SourceAnalogy | None
    latest_alignment: AnalogyAlignment | None
    latest_transfer: ProofReasoningTransfer | None
    predicted_transition: str | None
    actual_transition: str | None
    surprise: float | None
    surprise_delta: float | None
    repeated_state: bool
    iteration_index: int
    remaining_model_calls: int
    remaining_lean_calls: int
```

- [ ] **Step 1: Write failing observation and tensor-shape tests**

```python
def test_first_observation_uses_zero_optional_channels() -> None:
    representation = build_policy_representation(
        context(), structure(), encoder(), layer=-1,
        previous_state=None, latest_transfer=None,
        scalar_features=(0.0, 1.0, 1.0, 0.0),
    )
    assert representation.transition_embedding.values == (0.0,) * 4
    assert representation.analogy_embedding.values == (0.0,) * 4
    assert representation.scalar_features == (0.0, 1.0, 1.0, 0.0)


def test_policy_consumes_four_channels_and_scalars() -> None:
    policy = DirectRescuePolicy(4, 8, 2.0, -2.0, scalar_feature_count=4)
    assert policy.body[0].in_features == 20
    action = policy.sample(representation(), torch.Generator().manual_seed(3))
    assert action.delta.shape == (4,)
```

Add a checkpoint test expecting schema version `3` and rejection of version `2`.

- [ ] **Step 2: Run focused tests and observe RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_proof_views.py tests/test_rl.py tests/test_training.py`

Expected: failures for missing transition/analogy channels and constructor argument.

- [ ] **Step 3: Extend representations and rendering**

Add to `ProofRepresentation`:

```python
transition_embedding: Embedding
analogy_embedding: Embedding
scalar_features: tuple[float, ...]
```

Add `render_observed_transition(previous, tactic, current)` and
`render_transfer_observation(source, alignment, transfer)`. Add
`build_policy_representation(...)` which uses real embeddings when supplied and
`zero_embedding_like(language.embedding, rendering="(none)")` otherwise.

Update `DirectRescuePolicy` to concatenate four embedding vectors and the exact
configured scalar count. Set `schema_version = 3`. Reject non-finite scalars and
wrong scalar lengths before constructing the input tensor.

- [ ] **Step 4: Adapt direct and legacy builders with neutral channels**

Direct rescue and legacy analogy calls pass zero transition/analogy channels and
the normalized scalar tuple:

```python
(
    iteration_index / max(max_iterations - 1, 1),
    remaining_model_calls / max_model_calls,
    remaining_lean_calls / max_lean_calls,
    float(repeated_state),
)
```

Generated-domain calls provide the verified transition and latest transfer.

- [ ] **Step 5: Run focused tests and the checkpoint preservation tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_proof_views.py tests/test_rl.py tests/test_training.py`

Expected: all selected tests pass; old schema checkpoints fail only in the test that asserts the clear incompatibility.

- [ ] **Step 6: Commit the observation change**

```bash
git add src/analogy/rescue.py src/analogy/proof_views.py src/analogy/rl.py src/analogy/training.py tests/test_proof_views.py tests/test_rl.py tests/test_training.py
git commit -m "feat: observe verified proof trajectories"
```

### Task 3: Source, alignment, and proof-transfer generators

**Files:**
- Create: `src/analogy/analogy_transfer_generation.py`
- Create: `tests/test_analogy_transfer_generation.py`
- Modify: `src/analogy/analogy_generation.py`

**Interfaces:**
- Consumes: `AnalogyObservation`, `ProofNeed`, a `PolicyAction`, and bounded generation attempts.
- Produces: `GeneratedSourceAnalogy`, `GeneratedAlignment`, `GeneratedProofTransfer`, each containing the parsed record and raw `GenerationAttempt` tuple; `TransferredTacticPlan` citing `transfer_id`.

- [ ] **Step 1: Write failing prompt-boundary tests**

```python
def test_latent_action_is_used_only_for_source_generation() -> None:
    pipeline = generator_pipeline(recording_backend())
    pipeline.generate_source(observation(), need(), action(), max_attempts=2)
    pipeline.generate_alignment(observation(), need(), source(), max_attempts=2)
    pipeline.generate_transfer(observation(), need(), source(), alignment(), max_attempts=2)
    assert pipeline.backend.calls[0].delta is not None
    assert pipeline.backend.calls[1].delta is None
    assert pipeline.backend.calls[2].delta is None


def test_source_prompt_requests_concrete_nonproof_domain() -> None:
    prompt = render_source_prompt(observation(), need())
    assert "concrete source domain" in prompt
    assert "Do not restate the proof" in prompt
    assert "source_before" in prompt and "source_after" in prompt


def test_tactic_plan_cites_transfer_not_analogy_hop() -> None:
    plan = parse_transferred_tactic_plan(raw_plan(), transfer())
    assert plan.motivating_transfer_id == transfer().transfer_id
```

- [ ] **Step 2: Run focused tests and observe RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_analogy_transfer_generation.py`

Expected: collection fails because the generation module does not exist.

- [ ] **Step 3: Implement four separate strict generation stages**

Implement:

```python
class SourceAnalogyGenerator:
    def generate(self, observation, need, action, *, analogy_index, max_attempts): ...

class AlignmentGenerator:
    def generate(self, observation, need, source, *, analogy_index, max_attempts): ...

class ProofTransferGenerator:
    def generate(self, observation, need, source, alignment, *, analogy_index, max_attempts): ...

class TransferredTacticGenerator:
    def generate(self, state, structure, source, alignment, transfer, *, candidate_count, max_attempts): ...
```

Reuse `_generate_strict` only after adding an explicit `delta: Tensor | None`
boundary. Source generation passes the policy delta; all downstream generators
pass `None`. Each prompt has its own version constant and records raw invalid and
repair outputs.

- [ ] **Step 4: Run focused and legacy-generation tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_analogy_transfer_generation.py tests/test_analogy_generation.py tests/test_analogy_stream.py`

Expected: all tests pass and the legacy prompt/parser behavior remains unchanged.

- [ ] **Step 5: Commit the staged generators**

```bash
git add src/analogy/analogy_transfer_generation.py src/analogy/analogy_generation.py tests/test_analogy_transfer_generation.py
git commit -m "feat: generate and translate source analogies"
```

### Task 4: Generated-domain loop and conventional finisher

**Files:**
- Create: `src/analogy/analogy_transfer_loop.py`
- Create: `tests/test_analogy_transfer_loop.py`
- Modify: `src/analogy/config.py`
- Modify: `src/analogy/rescue.py`
- Modify: `src/analogy/training.py`
- Modify: `config.yaml`
- Modify: `config.mlx-8b-fast.yaml`

**Interfaces:**
- Consumes: Task 2 observation builder, Task 3 generators, Lean evaluator, existing surprise controller and budgets.
- Produces: `GeneratedAnalogyIteration`, `GeneratedAnalogyTrace`, and `GeneratedDomainAnalogyLoopRunner.search(...) -> GenericRescueOutcome`; configuration `analogy.strategy: legacy | generated_domain`.

- [ ] **Step 1: Write failing causal-loop tests**

```python
def test_generated_loop_refreshes_observation_after_successor() -> None:
    runner, recorder = runner_with_two_accepted_steps()
    outcome = runner.search(state_p(), goal(), (), (), evaluator(), cancellation())
    assert recorder.observations[1].previous_state == state_p().expression
    assert recorder.observations[1].current_state == state_r().expression
    assert recorder.observations[1].last_tactic == "apply h1.mpr"
    assert recorder.observations[1].latest_transfer.transfer_id == "transfer-1"
    assert outcome.rollouts[0].analogy_trace.iterations[1].source.analogy_id == "analogy-2"


def test_conventional_finisher_closes_trivial_successor_before_new_analogy() -> None:
    runner, recorder = runner_with_successor_and_assumption_finish()
    outcome = runner.search(state_p(), goal(), (), (), evaluator(), cancellation())
    assert outcome.completed_proof is not None
    assert recorder.generated_source_count == 1
    assert outcome.rollouts[0].attempts[-1].proposal.candidate.source == "assumption"


def test_no_downstream_generation_after_invalid_source() -> None:
    runner, recorder = runner_with_invalid_source()
    runner.search(state(), goal(), (), (), evaluator(), cancellation())
    assert recorder.calls == ["source", "source_repair"]


def test_generated_evaluation_conditions_share_call_budgets() -> None:
    conditions = generated_evaluation_conditions(config())
    selected = [item for item in conditions if item.name.startswith("generated_")]
    assert [item.name for item in selected] == [
        "generated_unsteered",
        "generated_random",
        "generated_learned",
        "generated_shuffled",
    ]
    assert len({(item.max_model_calls, item.max_lean_calls) for item in selected}) == 1
```

- [ ] **Step 2: Run focused tests and observe RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_analogy_transfer_loop.py`

Expected: collection fails because the generated-domain runner does not exist.

- [ ] **Step 3: Implement the readable coordinator**

Mirror the resource boundaries of `AnalogyLoopRunner`, but keep these operations
visibly ordered in one `search` method:

```python
observation = build_observation(...)
need = proof_need_builder(observation)
action = action_sampler(observation)
source = source_generator.generate(observation, need, action, ...)
alignment = alignment_generator.generate(observation, need, source, ...)
transfer = transfer_generator.generate(observation, need, source, alignment, ...)
plan = tactic_generator.generate(current, structure, source, alignment, transfer, ...)
verified = verify_plan(current, plan, ...)
finished = conventional_finisher(verified.successor, ("assumption",), ...)
decision = controller.decide(...)
```

Store every stage and call count even when later stages do not run. After an
accepted successor, set `previous_state`, `current_state`, `last_tactic`, and
`latest_transfer` before the next policy sample. Count a repeated accepted state
as a discarded candidate, not progress.

- [ ] **Step 4: Wire strategy selection and configuration**

Add strict config parsing with default `legacy` for existing profiles:

```yaml
analogy:
  strategy: legacy
```

Set `generated_domain` only in a copied experiment profile used in Task 6.
`_new_analogy_runner` returns the legacy or generated runner without changing
conventional and learned-direct conditions.

When `strategy: generated_domain`, evaluation adds four matched conditions:

- `generated_unsteered`: zero latent delta;
- `generated_random`: seeded untrained policy;
- `generated_learned`: checkpoint policy;
- `generated_shuffled`: learned observation policy with source analogies shuffled
  across structurally compatible stuck states before alignment.

All four inherit identical model-call, Lean-call, expansion, and wall-time
budgets. Training uses `generated_learned`; the other modes are evaluation-only.

- [ ] **Step 5: Run loop, config, training, and preservation tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_analogy_transfer_loop.py tests/test_analogy_loop.py tests/test_config.py tests/test_training.py`

Expected: all tests pass.

- [ ] **Step 6: Commit the generated-domain loop**

```bash
git add src/analogy/analogy_transfer_loop.py src/analogy/config.py src/analogy/rescue.py src/analogy/training.py config.yaml config.mlx-8b-fast.yaml tests/test_analogy_transfer_loop.py tests/test_config.py tests/test_training.py
git commit -m "feat: run generated domain analogy rescue"
```

### Task 5: Connected human-readable trace renderer

**Files:**
- Create: `src/analogy/trace_render.py`
- Create: `tests/test_trace_render.py`
- Create: `tests/fixtures/generated_analogy_traces/`
- Modify: `src/analogy/training.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: theorem problem, accepted ordinary prefix, legacy or generated analogy iterations, tactic attempts, and termination.
- Produces: `render_theorem_trace(...) -> str` and per-run `readable_traces.md` grouped by theorem and connected proof path.

- [ ] **Step 1: Write failing snapshot and edge tests**

```python
def test_trace_renderer_connects_failures_repeats_and_completion() -> None:
    rendered = render_theorem_trace(complex_trace_fixture())
    assert "THEOREM TARGET (fixed): R → Q" in rendered
    assert "[State 2] ACTIVE SUBGOAL: P" in rendered
    assert "SOURCE ANALOGY" in rendered
    assert "ALIGNMENT" in rendered
    assert "TRANSFERRED PROOF REASONING" in rendered
    assert "[State 2 unchanged]" in rendered
    assert "[DISCARDED: repeated_state" in rendered
    assert "[COMPLETE: theorem target proved]" in rendered


@pytest.mark.parametrize(
    "problem_id",
    ("imp_intro_04", "imp_intro_05", "not_intro_05", "iff_intro_04", "iff_intro_05"),
)
def test_readability_fixture(problem_id: str) -> None:
    assert render_fixture(problem_id) == fixture_markdown(problem_id)
```

- [ ] **Step 2: Run focused tests and observe RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_trace_render.py`

Expected: collection fails because `analogy.trace_render` does not exist.

- [ ] **Step 3: Implement state-graph rendering**

Build states from the fixed theorem target and accepted tactic prefix. Number
proof states from zero and analogies from one. Render accepted tactics as downward
edges, rejected tactics as edges to `[State n unchanged]`, repeated successors
as candidate states followed by discard edges, multiple goals as numbered active
subgoals, and completion as no remaining subgoals.

For generated-domain records, render exactly these nested labels:

```text
SOURCE ANALOGY
ALIGNMENT
TRANSFERRED PROOF REASONING
TACTIC
LEAN RESULT
```

Never expose `iteration_index` as the human analogy number.

- [ ] **Step 4: Write all five fixture snapshots from objective artifacts**

Use the same theorem targets, ordinary accepted prefixes, active states, tactics,
Lean statuses, successors, repeated-state discard, and stop reasons already
captured in the October 15 artifacts. Replace only the generated source analogy,
alignment, and proof transfer with deterministic fake-generator fixture values.

- [ ] **Step 5: Persist traces during run finalization**

After finalizing machine-readable JSONL, atomically write
`<run-directory>/readable_traces.md`. An interrupted render must not corrupt
JSONL or change summary metrics. Document the artifact and notation in README.

- [ ] **Step 6: Run renderer and full preservation tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_trace_render.py`

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: focused snapshots pass; the complete suite passes.

- [ ] **Step 7: Commit the renderer**

```bash
git add src/analogy/trace_render.py src/analogy/training.py tests/test_trace_render.py tests/fixtures/generated_analogy_traces README.md
git commit -m "feat: render connected analogy proof traces"
```

### Task 6: Same-example generated-domain benchmark

**Files:**
- Create: `config.mlx-8b-generated-analogy.yaml`
- Create: `docs/benchmarks/2026-10-15-generated-domain-analogy-smoke.md`
- Modify: `README.md`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: the existing training/validation and held-out problem splits, local MLX Qwen3-8B, and Tasks 1–5.
- Produces: immutable generated-domain run artifacts, connected traces for the same examples, and a comparison against legacy, unsteered, random, learned, and shuffled controls.

- [ ] **Step 1: Add a generated-domain MLX profile and CLI smoke test**

Copy the fast 8B profile, set:

```yaml
experiment:
  stage: train_rescue
analogy:
  enabled: true
  strategy: generated_domain
  samples: 1
  min_iterations: 2
  max_iterations: 3
```

The CLI test uses a fake backend and asserts that generated-domain construction
does not affect conventional-only setup and produces `readable_traces.md` when
analogy iterations occur.

- [ ] **Step 2: Run focused CLI and full tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_cli.py`

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: all tests pass before a real model run.

- [ ] **Step 3: Run the same-example 8B smoke experiment**

Run the existing `imp_intro_04`, `imp_intro_05`, `not_intro_05`,
`iff_intro_04`, and `iff_intro_05` examples with fixed seed `0`. Preserve source
hashes, wall time, model/Lean call counts, all stage outputs, and rendered traces.
Do not overwrite the October 15 legacy artifacts.

- [ ] **Step 4: Compare causal conditions under matched budgets**

Report conventional, legacy card, generated unsteered, generated random,
generated learned, and shuffled-source conditions. Lead with relative completion,
novel-successor, accepted-tactic, repeated-state, and completion-per-Lean-call
metrics. Then report source-domain validity/diversity, alignment validity,
multi-analogy continuation, and the exact connected traces.

- [ ] **Step 5: Write the evidence-backed smoke report**

The report must separate objective Lean facts from model-generated interpretation,
state that five examples cannot estimate generalization, and explicitly answer:

1. Did the system generate concrete domains rather than proof paraphrases?
2. Did alignment produce tactic-free proof reasoning grounded in the active subgoal?
3. Did a verified successor cause a genuinely new second analogy?
4. Did the conventional finisher close the prior `⊢ R` near-solve?
5. Was generated-domain rescue better than unsteered/random under matched calls?

- [ ] **Step 6: Commit the profile and report**

```bash
git add config.mlx-8b-generated-analogy.yaml docs/benchmarks/2026-10-15-generated-domain-analogy-smoke.md README.md tests/test_cli.py
git commit -m "test: benchmark generated domain analogies"
```

### Task 7: Final verification and review

**Files:**
- Verify all files changed by Tasks 1–6.

**Interfaces:**
- Consumes: the complete generated-domain implementation and saved artifacts.
- Produces: a clean branch with reproducible tests and a review-ready comparison.

- [ ] **Step 1: Run static artifact checks**

Run: `PYTHONPATH=src .venv/bin/python -m compileall -q src tests scripts`

Run: `git diff --check`

Expected: both commands exit zero.

- [ ] **Step 2: Run the complete suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: all tests pass with only the existing documented skip.

- [ ] **Step 3: Verify causal and artifact boundaries manually**

Confirm from saved config and JSONL that only source generation receives the
policy delta; alignment, transfer, and tactic decoding record no delta. Confirm
source-run hashes did not change, every displayed trace edge corresponds to a
Lean attempt or accepted prefix, and post-hoc judgment does not enter policy
inputs or rewards.

- [ ] **Step 4: Request whole-branch review**

Review from the pre-plan base through `HEAD`, prioritizing observation leakage,
source/proof schema separation, stale-state use, resource accounting, checkpoint
compatibility, trace factuality, and unfair condition budgets. Fix Critical and
Important findings with focused RED→GREEN tests, rerun the full suite, and commit
one review-fix pass.
