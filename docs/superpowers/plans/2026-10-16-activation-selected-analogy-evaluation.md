# Activation-Selected Analogy Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, frozen-Qwen3-8B experiment that selects cross-domain proof analogies from token-aligned activation displacement and causal influence, then compares their Lean-solving value with neutral, prompted, and shuffled controls.

**Architecture:** Add an isolated `analogy.experiments` package rather than extending the production solver lifecycle. The experiment first freezes shared stuck-state anchors, then collects all pre-Lean activation measurements, cross-fits calibration by held-out problem, freezes analogy selections, and finally runs matched Lean conditions under equal tactic and equal token-position budgets. Extend the two model providers with a narrow teacher-forced activation/scoring API; keep proof-state novelty in the existing solver module and report it separately.

**Tech Stack:** Python 3.11+, frozen Qwen3-8B through Transformers or MLX, Lean 4 verifier, PyYAML, standard-library statistics/JSON, pytest.

**Spec:** `docs/superpowers/specs/2026-10-16-activation-selected-analogy-evaluation-design.md`

## Global Constraints

- Use `mlx-community/Qwen3-8B-4bit`; never update Qwen or the latent policy.
- Lean is the sole judge of rejection, progress, and completion.
- Use exactly `imp_intro_05`, `iff_intro_03`, `iff_intro_05`, `not_intro_05`, `or_intro_05`, and `or_intro_06` for the headline experiment.
- Start with 16 paired seeds; do not silently replace this result with a later 32-seed run.
- Select analogies without Lean outcomes, reference tactics, or successor-state novelty.
- Keep activation displacement, causal influence, preceding-state explanatory value, proof-state diversity, and Lean utility as separate fields.
- Default to four analogy candidates, four tactics per slate, an eight-tactic canonical union, and three resolved middle-to-late layers.
- Use leave-one-problem-out pre-Lean calibration with diagonal variance and a configured positive floor.
- Treat processed model token positions as the authoritative equal-compute proxy; keep Lean-call caps equal.
- Preserve the existing uncommitted package reorganization and do not restore deleted legacy modules.
- Persist activation summaries by default; full tensors require the explicit diagnostic flag.

## Review Focus

- A conventional run that solves before becoming stuck must produce `no_stuck_anchor`, remain visible in the report, and never enter rescue denominators (Task 4).
- A cell whose analogy candidates are all malformed or unscorable must record failed selection without falling back to prompted analogy (Tasks 3 and 6).
- A root stuck state without a real predecessor must record preceding-state explanatory value as unavailable, not zero (Tasks 4 and 5).
- Activation capture must pool only completion tokens, while causal patching must modify the final prompt token rather than a completion token (Task 2).
- An equal-compute top-up call that would exceed the selected condition's token-position cost must not start, and no arm may gain extra Lean calls (Task 6).

---

### Task 1: Strict standalone configuration and experiment records

**Files:**
- Create: `src/analogy/experiments/__init__.py`
- Create: `src/analogy/experiments/activation_records.py`
- Create: `config.activation-analogy.yaml`
- Create: `tests/test_activation_records.py`

**Interfaces:**
- Consumes: `analogy.config.load_config(Path) -> AppConfig` for the referenced base configuration.
- Produces: `ActivationCondition`, `ActivationEvalConfig`, `AnchorRecord`, `AnalogyCandidateRecord`, `CandidateMeasurement`, `ConditionOutcome`, `ActivationTrialRecord`, and `load_activation_eval_config(path: Path) -> ActivationEvalConfig`.

- [ ] **Step 1: Write failing configuration tests**

Add tests asserting that `load_activation_eval_config(ROOT / "config.activation-analogy.yaml")` resolves `base_config`, contains the six exact problem IDs in the specified order, and loads these exact defaults: `seed_count=16`, `analogy_candidates=4`, `tactics_per_slate=4`, `tactic_union_limit=8`, `activation_layer_count=3`, `variance_floor=1e-6`, `persist_full_activations=False`, and score weights `(1.0, 1.0, 1.0)`. Add rejection tests for unknown keys, duplicate problem IDs, non-positive counts, a union limit smaller than a slate, a non-positive variance floor, and non-finite weights.

- [ ] **Step 2: Run the focused tests and verify the import/config failures**

Run: `uv run pytest tests/test_activation_records.py -q`

Expected: FAIL because `analogy.experiments.activation_records` and the YAML profile do not exist.

- [ ] **Step 3: Implement strict immutable records and config loading**

In `activation_records.py`, define:

```python
class ActivationCondition(str, Enum):
    NEUTRAL = "neutral"
    PROMPTED = "prompted_analogy"
    SELECTED = "activation_selected"
    SHUFFLED = "shuffled_analogy"

def load_activation_eval_config(path: Path) -> ActivationEvalConfig: ...
```

Use exact-key validation and finite-number checks matching `analogy.config`. Keep nested run artifacts serializable with `dataclasses.asdict`. `AnchorRecord` must retain the current `SymbolicState`, optional predecessor, accepted prefix, failed tactics and diagnostics, hashes, seed, and `no_stuck_anchor`; `CandidateMeasurement` must distinguish unavailable metrics from numeric zero.

- [ ] **Step 4: Add the frozen Qwen3-8B profile**

Create `config.activation-analogy.yaml` with `schema_version: activation-analogy-v1`, `base_config: config.yaml`, the exact six problem IDs, seeds `0..15` through `seed_start: 0` and `seed_count: 16`, four domain cues (`physical/mechanical`, `biological/ecological`, `social/economic`, `information/control`), the fixed counts above, score weights of `1.0`, variance floor `1e-6`, `persist_full_activations: false`, and output namespace `activation_analogy`.

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest tests/test_activation_records.py tests/test_config.py -q`

Expected: PASS.

```bash
git add config.activation-analogy.yaml src/analogy/experiments/__init__.py src/analogy/experiments/activation_records.py tests/test_activation_records.py
git commit -m "feat: define activation analogy experiment records"
```

### Task 2: Teacher-forced activation capture, token accounting, and causal scoring

**Files:**
- Modify: `src/analogy/inference/model_provider.py:20-52,165-221,223-390`
- Modify: `src/analogy/inference/mlx_model.py:15-42,62-281`
- Modify: `tests/test_model_provider.py`
- Create: `tests/test_activation_provider.py`

**Interfaces:**
- Consumes: existing `Message`, `CompletionScore`, provider chat templates, and single-layer delta injection.
- Produces: `LayerActivation`, `CompletionActivationTrace`, and these `ModelProvider` members:

```python
decoder_layer_count: int
def resolve_decoder_layers(self, count: int, *, through: int) -> tuple[int, ...]: ...
def count_token_positions(self, messages: tuple[Message, ...], completion: str) -> int: ...
def capture_completion_trace(
    self, messages: tuple[Message, ...], completion: str, *, layers: tuple[int, ...]
) -> CompletionActivationTrace: ...
def score_completion_with_delta(
    self, messages: tuple[Message, ...], completion: str, *, delta: tuple[float, ...], layer: int
) -> CompletionScore: ...
```

- [ ] **Step 1: Write provider-contract tests with deterministic fake tensors**

Test that three resolved layers are unique, ordered, and end at the configured intervention layer; that token cost equals chat-prefill tokens plus completion tokens; that `capture_completion_trace` returns one mean residual vector per requested decoder block using only completion-token positions; and that invalid layers, empty completions, dimension mismatches, and non-finite deltas fail explicitly.

- [ ] **Step 2: Write the causal-boundary regression test**

Using a tiny fake decoder block, assert that `score_completion_with_delta` changes the hidden vector at index `prompt_token_count - 1` and leaves every completion-token input position unpatched. This pins the Review Focus boundary condition independently from likelihood arithmetic.

- [ ] **Step 3: Run the focused tests and verify failure**

Run: `uv run pytest tests/test_activation_provider.py tests/test_model_provider.py -q`

Expected: FAIL because the provider protocol and implementations lack the activation methods.

- [ ] **Step 4: Implement the generic records and Transformers path**

Add finite immutable `LayerActivation(layer: int, values: tuple[float, ...])` and `CompletionActivationTrace(score: CompletionScore, activations: tuple[LayerActivation, ...], completion_token_count: int, processed_token_positions: int)` records. In `QwenGenerator`, tokenize the rendered prompt and completion separately, run one no-grad teacher-forced pass with `output_hidden_states=True`, mean-pool only completion positions at requested decoder-block outputs, and compute the completion likelihood from the same logits. Implement patched scoring with a temporary forward hook that targets the final prompt position in the full teacher-forced sequence and is always removed in `finally`.

- [ ] **Step 5: Implement the MLX path**

Expose `decoder_layer_count`; perform one explicit full-sequence layer walk to capture requested block outputs and one model-logit pass when required for likelihood, charging every actual sequence row/pass in `processed_token_positions`. Generalize `_DeltaLayer` or add a scoring-only wrapper that patches a supplied absolute prompt-boundary index during a full-sequence call, while preserving the existing cached-generation behavior. Always restore the original layer in `finally`.

- [ ] **Step 6: Run provider and existing model tests, then commit**

Run: `uv run pytest tests/test_activation_provider.py tests/test_model_provider.py tests/test_model.py -q`

Expected: PASS.

```bash
git add src/analogy/inference/model_provider.py src/analogy/inference/mlx_model.py tests/test_activation_provider.py tests/test_model_provider.py
git commit -m "feat: expose causal completion activation traces"
```

### Task 3: Explicit analogy candidates and matched prompt controls

**Files:**
- Create: `src/analogy/experiments/activation_generation.py`
- Modify: `prompts.yaml:151-235`
- Create: `tests/test_activation_generation.py`
- Modify: `tests/test_prompts.py`

**Interfaces:**
- Consumes: `ProofNeed`, `SourceAnalogy`, `AnalogyAlignment`, `ProofReasoningTransfer`, their strict parsers in `transfer/domain.py`, `parse_rescue_proposals`, `PromptRunner`, and the prompt catalog.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class GeneratedAnalogyCandidate:
    record: AnalogyCandidateRecord
    source: SourceAnalogy
    alignment: AnalogyAlignment
    transfer: ProofReasoningTransfer
    tactics: tuple[RescueProposal, ...]
    messages: tuple[tuple[Message, ...], ...]
    raw_outputs: tuple[str, ...]
    processed_token_positions: int

class ActivationAnalogyGenerator:
    def generate_candidates(
        self, context: ProofRescueContext, structure: ProofStructure,
        need: ProofNeed, *, seed: int
    ) -> tuple[GeneratedAnalogyCandidate | CandidateGenerationFailure, ...]: ...

def generate_neutral_slate(...) -> GeneratedTacticSlate: ...
def generate_prompted_slate(...) -> GeneratedTacticSlate: ...
def generate_shuffled_slate(...) -> GeneratedTacticSlate: ...
```

- [ ] **Step 1: Write strict generation and failure tests**

Use a scripted fake model to assert one source, alignment, transfer, and tactic call per domain cue; four distinct analogy IDs; explicit role mappings and limitations; stable tactic-union canonicalization capped at eight; and accumulated token-position cost. Assert that a malformed stage yields a typed `CandidateGenerationFailure` for that candidate without consuming an unconfigured replacement call.

- [ ] **Step 2: Write matched-control tests**

Assert that neutral uses `direct_rescue`, prompted uses the existing `analogy_rescue` schema/family rotation, selected uses the complete explicit candidate, and shuffled deterministically rotates the selected alignment's proof-role targets before asking for tactics. Verify shuffled serialization cannot mutate the selected candidate record.

- [ ] **Step 3: Run tests and verify missing services/prompts**

Run: `uv run pytest tests/test_activation_generation.py tests/test_prompts.py -q`

Expected: FAIL because the activation-generation service and four explicit transfer prompts do not exist.

- [ ] **Step 4: Add strict source, alignment, transfer, and tactic prompts**

Add `activation_source_analogy`, `activation_alignment`, `activation_transfer`, and `activation_tactic` templates. Source prompts receive one required domain cue and neutral structural need; alignment prompts receive the declared source roles and current `ProofNeed`; transfer prompts must target the exact active subgoal; tactic prompts receive the verified current state, an explicit previous-state/transition field whose value is either the real predecessor or `(omitted)`, and the complete source/alignment/transfer object, then return the existing rescue proposal schema. Keep source-domain text tactic-free through the current parsers. The with/without-predecessor scoring prompts must otherwise be byte-identical.

- [ ] **Step 5: Implement generation and deterministic shuffling**

Use `derive_proof_need(structure, context.state.expression, fixed_target=context.goal.expression)`. Generate exactly one candidate per configured domain cue, preserve every raw attempt, and compute usage with `count_token_positions`. Canonicalize the union with `normalize_lean4_tactic` in neutral-first then candidate-generation order. Shuffling rotates only right-hand proof roles and never reads a reference proof or Lean outcome.

- [ ] **Step 6: Run generation tests and commit**

Run: `uv run pytest tests/test_activation_generation.py tests/test_prompts.py tests/test_proof_need.py -q`

Expected: PASS.

```bash
git add prompts.yaml src/analogy/experiments/activation_generation.py tests/test_activation_generation.py tests/test_prompts.py
git commit -m "feat: generate explicit analogy evaluation candidates"
```

### Task 4: Freeze reproducible first-stuck anchor states

**Files:**
- Create: `src/analogy/experiments/activation_anchor.py`
- Create: `tests/test_activation_anchor.py`

**Interfaces:**
- Consumes: `run_episode`, `ObservableStucknessPolicy`, `LeanGymAdapter`, `TacticRanker`, `CanonicalStateFingerprinter`, `AcceptNovelState`, and `AnchorRecord`.
- Produces:

```python
def build_anchor(
    problem: Problem, generator: TextGenerator, verifier: Verifier,
    app_config: AppConfig, *, seed: int
) -> AnchorRecord: ...

def build_anchor_manifest(
    problems: Sequence[Problem], seeds: Sequence[int], ...
) -> tuple[AnchorRecord, ...]: ...

def replay_anchor(anchor: AnchorRecord, problem: Problem, verifier: Verifier) -> LeanGymAdapter: ...
```

- [ ] **Step 1: Write anchor and predecessor tests**

With fake ranker/verifier paths, assert that the sink cancels immediately after the first observable stuckness decision; all conditions can replay the exact recorded current state and prefix; a real predecessor and accepted transition are retained when present; and root stuckness stores `predecessor=None`.

- [ ] **Step 2: Add the `no_stuck_anchor` regression test**

Make conventional search complete before stuckness and assert a visible `AnchorRecord(no_stuck_anchor=True)` with no rescue state, rather than omission or failure.

- [ ] **Step 3: Run tests and verify failure**

Run: `uv run pytest tests/test_activation_anchor.py -q`

Expected: FAIL because the anchor builder is absent.

- [ ] **Step 4: Implement first-stuck capture and immutable replay**

Use a cancellable token plus `stuck_state_sink` to stop the existing episode at the first captured stuck state. Recover the predecessor from the returned expansion graph by `parent_state_id`; never infer it from text. Record canonical state/prompt hashes and ordinary failures. `replay_anchor` must initialize a fresh `LeanGymAdapter` and register the recorded state so every arm begins with the identical prefix.

- [ ] **Step 5: Run anchor and episode tests, then commit**

Run: `uv run pytest tests/test_activation_anchor.py tests/test_episode.py -q`

Expected: PASS.

```bash
git add src/analogy/experiments/activation_anchor.py tests/test_activation_anchor.py
git commit -m "feat: freeze matched stuck-state anchors"
```

### Task 5: Cross-fitted displacement, patching, and explanation metrics

**Files:**
- Create: `src/analogy/experiments/activation_metrics.py`
- Create: `tests/test_activation_metrics.py`

**Interfaces:**
- Consumes: `CompletionActivationTrace`, provider patched scoring, `CandidateMeasurement`, and problem IDs from the experiment config.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class DiagonalCalibration:
    held_out_problem_id: str
    mean: tuple[float, ...]
    variance: tuple[float, ...]
    component_center: tuple[float, float, float]
    component_scale: tuple[float, float, float]

def fit_leave_one_problem_out(
    neutral_vectors: Sequence[NeutralVector], prelean_components: Sequence[RawComponents],
    *, problem_ids: tuple[str, ...], variance_floor: float
) -> dict[str, DiagonalCalibration]: ...

def measure_candidate(...) -> CandidateMeasurement: ...
def select_candidate(...) -> CandidateMeasurement | None: ...
```

- [ ] **Step 1: Write exact diagonal-whitening tests**

Use two-dimensional fixtures with hand-computed means and variances. Assert that each problem's calibration excludes every row from that problem, applies `variance_floor`, concatenates layers in recorded order, and returns finite whitened squared distance.

- [ ] **Step 2: Write causal and preceding-state tests**

Assert `causal_effect = patched.total_log_likelihood - neutral.total_log_likelihood`; `previous_state_effect = with_previous.total_log_likelihood - without_previous.total_log_likelihood`; and a missing predecessor yields `None`. Test robust aggregation as a deterministic 10% trimmed mean that reduces to the ordinary mean for fewer than ten tactics.

- [ ] **Step 3: Write selection and leakage tests**

Assert standardized components use only the held-out problem's calibration object, missing `E_prev` is omitted and weights renormalized over present components, ties break by stable analogy ID, non-finite candidates are unscorable, and all-unscorable input returns `None` without fallback.

- [ ] **Step 4: Run tests and verify failure**

Run: `uv run pytest tests/test_activation_metrics.py -q`

Expected: FAIL because the metric module is absent.

- [ ] **Step 5: Implement pure-Python cross-fitted metrics**

Avoid a new numerical dependency. Use stable two-pass per-coordinate mean/variance, the configured floor, finite checks, and clipped patch vectors with maximum norm `app_config.rl.max_delta_norm`. Preserve raw, standardized, aggregate, and weighted component values in each measurement.

- [ ] **Step 6: Run metric tests and commit**

Run: `uv run pytest tests/test_activation_metrics.py -q`

Expected: PASS.

```bash
git add src/analogy/experiments/activation_metrics.py tests/test_activation_metrics.py
git commit -m "feat: score causal analogy displacement"
```

### Task 6: Two-phase matched condition runner and fair budgets

**Files:**
- Create: `src/analogy/experiments/activation_runner.py`
- Create: `tests/test_activation_runner.py`

**Interfaces:**
- Consumes: anchor manifest, generation services, provider traces, cross-fitted metrics, `LeanGymAdapter`, existing proof-state diversity, and experiment records.
- Produces:

```python
def collect_prelean(
    config: ActivationEvalConfig, app_config: AppConfig, anchors: Sequence[AnchorRecord],
    generator: ModelProvider
) -> PreLeanCollection: ...

def freeze_selections(collection: PreLeanCollection, ...) -> FrozenSelections: ...

def run_lean_conditions(
    selections: FrozenSelections, problems: Sequence[Problem], verifier: Verifier, ...
) -> tuple[ActivationTrialRecord, ...]: ...

def run_activation_evaluation(...) -> ActivationEvaluationRun: ...
```

- [ ] **Step 1: Write the two-phase/no-Lean-leakage test**

Use fakes that raise if the verifier is called during `collect_prelean` or `freeze_selections`. Assert all problem/seed pre-Lean cells are collected before calibration is fit, selections are immutable before `run_lean_conditions`, and the selected candidate never changes after a Lean result.

- [ ] **Step 2: Write matched-condition outcome tests**

For one anchor, script neutral rejection, prompted progress, selected completion, and shuffled rejection. Assert each condition begins from the identical anchor state, uses the same final tactic and Lean-call caps, records immediate utility separately from conventional continuation, and only the selected arm solves.

- [ ] **Step 3: Write equal-compute and failure tests**

Assert the selected arm's actual processed-token cost becomes cell budget `C`; neutral/prompted top-ups use a disjoint deterministic seed range; a next call whose estimated cost exceeds `C` is not started; extra candidates can consume only the original Lean cap; shuffled and selected receive no hidden extra Lean calls; `no_stuck_anchor` and failed selection are serialized without condition fallback.

- [ ] **Step 4: Run tests and verify failure**

Run: `uv run pytest tests/test_activation_runner.py -q`

Expected: FAIL because the two-phase runner is absent.

- [ ] **Step 5: Implement pre-Lean collection and selection freezing**

For every candidate/tactic pair, capture neutral and analogy traces on identical completion text, compute the layerwise delta, score neutral and bounded patched likelihoods, and—only when a predecessor exists—score with/without predecessor contexts. Accumulate actual token-position cost from generation, traces, scoring, and patching. Fit leave-one-problem-out calibration only after all pre-Lean cells exist, then freeze selections.

- [ ] **Step 6: Implement Lean arms and conventional continuation**

Try each arm's canonical tactics independently from a fresh replayed anchor until the fixed Lean cap, first completion, or first accepted novel successor. For a successor, continue with the same conventional ranker and remaining expansion/Lean budget but no further analogy rescue. Observe accepted states with a fresh `FrozenEmbeddingDiversity` instance and record proof-state novelty separately from activation metrics.

- [ ] **Step 7: Implement equal-compute replay**

Use the selected arm's measured `C` as the per-cell token-position ceiling. Before each neutral/prompted top-up, estimate the call from its rendered prompt and maximum completion allowance; do not start calls that cannot fit. Record both equal-tactic and equal-compute candidate/outcome views without overwriting either.

Execute the four condition arms in a deterministic seed-rotated order while preserving their condition-specific generation seeds. This prevents a fixed condition from always receiving the warmest model/cache state without changing paired randomness.

- [ ] **Step 8: Run runner and solver regression tests, then commit**

Run: `uv run pytest tests/test_activation_runner.py tests/test_episode.py tests/test_base_model_rescue.py tests/test_state_diversity.py -q`

Expected: PASS.

```bash
git add src/analogy/experiments/activation_runner.py tests/test_activation_runner.py
git commit -m "feat: run matched activation analogy conditions"
```

### Task 7: Immutable artifacts, report, CLI, and fake end-to-end proof

**Files:**
- Create: `src/analogy/experiments/activation_report.py`
- Modify: `src/analogy/__main__.py:1-92`
- Modify: `README.md`
- Create: `tests/test_activation_report.py`
- Modify: `tests/test_cli.py`
- Create: `tests/test_activation_end_to_end.py`

**Interfaces:**
- Consumes: `ActivationEvaluationRun`, base runtime dependencies, and `run_activation_evaluation`.
- Produces:

```python
def write_activation_artifacts(run: ActivationEvaluationRun, output_dir: Path) -> Path: ...
def render_activation_report(run: ActivationEvaluationRun) -> str: ...
def run_activation_eval(args: argparse.Namespace) -> int: ...
```

- [ ] **Step 1: Write report and artifact tests**

Assert the writer creates a new `.artifacts/runs/activation_analogy/<run-id>/` directory with resolved config, `anchors.jsonl`, `prelean.jsonl`, `trials.jsonl`, `summary.json`, and `report.md`; uses exclusive creation; flushes records incrementally; stores only activation summaries by default; and retains typed infrastructure failures separately from tactic rejection.

- [ ] **Step 2: Write report-content tests**

Assert `report.md` contains per-problem/condition tables, positive-control and hard-negative rows, both budget views, `no_stuck_anchor` counts, failed selections, solve/accepted-progress rates, patch likelihood effects, score/Lean-utility association, proof-state novelty, token positions, calls, elapsed time, and the local-causal-proxy limitation. Assert aggregate numbers cannot replace the six per-problem rows.

- [ ] **Step 3: Write CLI and fake end-to-end tests**

Add `activation-eval --config config.activation-analogy.yaml`. With a two-problem test config and deterministic fake provider/verifier, assert the command builds anchors, completes the pre-Lean phase before Lean, freezes a selected candidate, runs all four conditions, writes resumable artifacts, and returns zero. Assert an existing run directory or mismatched resume metadata fails instead of overwriting.

- [ ] **Step 4: Run tests and verify missing CLI/report**

Run: `uv run pytest tests/test_activation_report.py tests/test_activation_end_to_end.py tests/test_cli.py -q`

Expected: FAIL because the report writer and command do not exist.

- [ ] **Step 5: Implement artifact writing, descriptive uncertainty, and CLI**

Write append-and-flush JSONL phase records, then atomically finalize summary/report files in the run directory. Compute exact paired counts plus deterministic bootstrap confidence intervals with a run-seeded `random.Random`; label them descriptive. Add resume validation keyed by schema version, model revision, problem IDs, seed range, prompt hash, and anchor-manifest hash.

- [ ] **Step 6: Document the standalone commands**

Add commands for the fake/unit suite and the real frozen-MLX run, state that 16 seeds are the initial result, explain equal-tactic versus equal-compute views, and warn that successful implementation is not a completed real-model experiment until its artifacts exist.

- [ ] **Step 7: Run the full automated suite**

Run: `uv run pytest -q`

Expected: PASS with no real 8B model load.

- [ ] **Step 8: Run one bounded real-model smoke cell when MLX is available**

Run: `uv run --extra mlx python -m analogy activation-eval --config config.activation-analogy.yaml --problem imp_intro_05 --seed 0 --max-cells 1`

Expected: exit 0; one immutable run directory containing all phases and four conditions. If MLX/model availability prevents the run, preserve the passing fake evidence and report the environmental limitation rather than claiming the real smoke passed.

- [ ] **Step 9: Commit**

```bash
git add README.md src/analogy/__main__.py src/analogy/experiments/activation_report.py tests/test_activation_report.py tests/test_activation_end_to_end.py tests/test_cli.py
git commit -m "feat: expose standalone activation analogy evaluation"
```

### Task 8: Final verification and experiment handoff

**Files:**
- Modify only if verification reveals a scoped defect in files owned by Tasks 1-7.

**Interfaces:**
- Consumes: the completed standalone command and its test suite.
- Produces: verified implementation evidence and an exact command for the full 16-seed run.

- [ ] **Step 1: Run focused experiment tests**

Run: `uv run pytest tests/test_activation_records.py tests/test_activation_provider.py tests/test_activation_generation.py tests/test_activation_anchor.py tests/test_activation_metrics.py tests/test_activation_runner.py tests/test_activation_report.py tests/test_activation_end_to_end.py -q`

Expected: PASS.

- [ ] **Step 2: Run all repository tests**

Run: `uv run pytest -q`

Expected: PASS.

- [ ] **Step 3: Validate the resolved real profile without loading Qwen**

Run: `uv run python -c 'from pathlib import Path; from analogy.experiments.activation_records import load_activation_eval_config; print(load_activation_eval_config(Path("config.activation-analogy.yaml")))'`

Expected: a valid `ActivationEvalConfig` naming the six fixed problems and 16 seeds.

- [ ] **Step 4: Inspect the final diff for unrelated changes and artifact leakage**

Run: `git status --short && git diff --check && git diff --stat`

Expected: only task-owned implementation/test/docs paths differ from the pre-existing user work; no `.artifacts` activation tensors or run outputs are staged.

- [ ] **Step 5: Record the full-run command without claiming it ran**

Command: `uv run --extra mlx python -m analogy activation-eval --config config.activation-analogy.yaml`

Only report 16-seed scientific results after this command finishes and its immutable artifacts have been inspected.
