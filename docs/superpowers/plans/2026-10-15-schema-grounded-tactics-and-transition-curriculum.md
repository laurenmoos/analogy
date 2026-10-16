# Schema-Grounded Tactics and Verified Transition Curriculum Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ground transferred analogy reasoning in exact local Lean declarations, compile it into safe tactics, and train/evaluate from a larger curriculum of verified nonterminal proof transitions.

**Architecture:** Add a package-owned local proof inventory, strict grounded-plan schema, and deterministic tactic compiler between proof transfer and Lean verification. Build an offline curriculum from split-safe reference-proof prefixes, then run the existing latent policy online from those verified states without exposing reference next tactics or retrieving similar proofs at inference.

**Tech Stack:** Python 3.14, frozen dataclasses, strict JSON parsers, PyTorch policy code, MLX/Qwen generation, Lean 4 subprocess verifier, pytest, JSONL/Markdown artifacts.

**Spec:** `docs/superpowers/specs/2026-10-15-schema-grounded-tactics-and-transition-curriculum-design.md`

## Global Constraints

- Source analogies remain open-ended and corpus-free at inference; only source generation receives the learned latent action.
- Grounded records may cite only exact entries from the current verified Lean state; unknown or stale inventory IDs fail before a Lean call.
- The deterministic compiler owns Lean identifier spelling, brackets, rewrite direction, and canonical candidate deduplication.
- Lean remains the only correctness authority; schema validity, compiler success, source quality, alignment confidence, and LLM-judge output are never proof rewards.
- Every model stage retains the existing one-schema-repair maximum and shares the configured model, Lean, cancellation, and wall-time budgets.
- Infrastructure failures are excluded from optimization; repeated states are not progress.
- Curriculum splits are assigned before prefix extraction or analogy augmentation; all derivatives of one theorem remain in one split.
- Reference tactics and verified next states are never exposed to the online generator or used for similarity retrieval.
- Historical generated records remain readable; new grounded artifacts have explicit schema, prompt, compiler, corpus, model, and Lean versions.
- The first implementation does not fine-tune Qwen3-8B and does not build a general Lean metaprogramming synthesizer.

## Review Focus

- A role binding from the prior state must be rejected after a verified transition changes the inventory; Task 2 adds a stale-inventory regression.
- Shadowed or renamed Lean declarations must resolve by stable inventory ID rather than guessed surface name; Task 1 adds an identity regression.
- Iff/equality direction and rewrite brackets must compile exactly and never fall back to raw model syntax; Task 3 adds directional compiler regressions.
- A multi-step plan with an intermediate proposition must either compile as a typed term/`have` block or return a counted unsupported diagnostic without a Lean call; Task 3 adds both cases.
- Prefixes, alternative candidates, and analogy augmentations from one theorem must never cross train/validation/test splits or collapse duplicate state invocations; Task 6 adds leakage and identity regressions.

---

### Task 1: Build the exact local proof inventory

**Files:**
- Create: `src/analogy/proof_inventory.py`
- Create: `tests/test_proof_inventory.py`
- Modify: `src/analogy/lean_gym/verifier.py`
- Modify: `tests/test_lean_verifier.py`

**Interfaces:**
- Consumes: `SymbolicState`, `ProofStructure`, and the verified accepted tactic prefix owned by `LeanSearchAdapter`.
- Produces: `InventoryEntry`, `LocalProofInventory`, `build_local_inventory(state: SymbolicState, structure: ProofStructure, *, fixed_target: str) -> LocalProofInventory`, and `LeanSearchAdapter.local_inventory_for(state) -> LocalProofInventory`.

- [ ] **Step 1: Write failing inventory identity and stale-state tests**

```python
def test_inventory_preserves_exact_local_identifiers_and_types() -> None:
    inventory = build_local_inventory(
        SymbolicState("s", "h1 : P ↔ R\nh2 : P → Q\nr : R\n⊢ Q", "lean4", ()),
        structure_for_iff_application(),
        fixed_target="R → Q",
    )
    assert [(item.inventory_id, item.identifier, item.proposition) for item in inventory.locals] == [
        ("local:0", "h1", "P ↔ R"),
        ("local:1", "h2", "P → Q"),
        ("local:2", "r", "R"),
    ]
    assert inventory.active_subgoal == "Q"


def test_shadowed_names_have_distinct_stable_inventory_ids() -> None:
    inventory = build_local_inventory(
        SymbolicState("s", "x : P\nx✝ : Q\n⊢ Q", "lean4", ()),
        structure_with_two_locals(),
        fixed_target="Q",
    )
    assert [item.inventory_id for item in inventory.locals] == ["local:0", "local:1"]
    assert inventory.resolve("local:1").identifier == "x✝"
```

- [ ] **Step 2: Run the new tests and observe RED**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_proof_inventory.py tests/test_lean_verifier.py -k inventory`

Expected: collection fails because `analogy.proof_inventory` and `local_inventory_for` do not exist.

- [ ] **Step 3: Implement immutable inventory records and exact state parsing**

```python
class InventoryKind(str, Enum):
    LOCAL = "local"
    ACTIVE_SUBGOAL = "active_subgoal"
    TARGET_COMPONENT = "target_component"
    CONSTRUCTOR = "constructor"


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    inventory_id: str
    kind: InventoryKind
    identifier: str | None
    proposition: str
    structural_shape: str


@dataclass(frozen=True, slots=True)
class LocalProofInventory:
    state_fingerprint: str
    fixed_target: str
    active_subgoal: str
    locals: tuple[InventoryEntry, ...]
    target_entries: tuple[InventoryEntry, ...]

    def resolve(self, inventory_id: str) -> InventoryEntry:
        matches = tuple(item for item in self.locals + self.target_entries
                        if item.inventory_id == inventory_id)
        if len(matches) != 1:
            raise InventoryError(f"unknown inventory id {inventory_id!r}")
        return matches[0]
```

Parse only the declaration block before `⊢`; preserve Unicode identifiers and exact propositions, assign IDs by verified declaration order, and derive target component/constructor entries from `ProofStructure`. Store the caller-supplied fixed theorem target and `proof_state_fingerprint(state.expression)` so later parsers can reject stale inventories.

- [ ] **Step 4: Expose the inventory through `LeanSearchAdapter`**

Add `local_inventory_for` next to `proof_structure_for`. It must call `proof_structure_for(state)` and `build_local_inventory`, without another Lean subprocess. Add a test proving one structure extraction supplies both structure and inventory when the caller reuses the returned structure.

- [ ] **Step 5: Run focused and verifier tests**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_proof_inventory.py tests/test_lean_verifier.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the inventory boundary**

```bash
git add src/analogy/proof_inventory.py src/analogy/lean_gym/verifier.py tests/test_proof_inventory.py tests/test_lean_verifier.py
git commit -m "feat: inventory exact local proof declarations"
```

### Task 2: Define strict grounded bindings and inference plans

**Files:**
- Create: `src/analogy/grounded_plan.py`
- Create: `tests/test_grounded_plan.py`
- Modify: `src/analogy/analogy_transfer_generation.py`
- Modify: `tests/test_analogy_transfer_generation.py`

**Interfaces:**
- Consumes: `LocalProofInventory`, `ProofReasoningTransfer`, and strict model JSON.
- Produces: `GroundedRoleBinding`, `GroundedInferenceStep`, `GroundedTacticPlan`, `GroundingOperator`, `parse_grounded_tactic_plan(raw, inventory, transfer, plan_id)`, and `GroundedPlanGenerator.generate(...) -> GeneratedGroundedPlan`.

- [ ] **Step 1: Write failing schema, stale-state, and coverage tests**

```python
def test_plan_binds_every_consumed_premise_to_exact_inventory_entry() -> None:
    plan = parse_grounded_tactic_plan(valid_plan_json(), inventory(), transfer(), plan_id="plan-1")
    assert plan.bindings[0].inventory_id == "local:0"
    assert plan.bindings[0].proposition == inventory().resolve("local:0").proposition


def test_plan_rejects_stale_inventory_fingerprint() -> None:
    payload = valid_plan_payload()
    payload["state_fingerprint"] = "old-state"
    with pytest.raises(GroundedPlanError, match="current verified state"):
        parse_grounded_tactic_plan(json.dumps(payload), inventory(), transfer(), plan_id="plan-1")


def test_plan_rejects_unbound_consumed_premise() -> None:
    payload = valid_plan_payload()
    payload["bindings"] = []
    with pytest.raises(GroundedPlanError, match="consumed premise"):
        parse_grounded_tactic_plan(json.dumps(payload), inventory(), transfer(), plan_id="plan-1")
```

- [ ] **Step 2: Run the focused tests and observe RED**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_grounded_plan.py`

Expected: collection fails because `analogy.grounded_plan` does not exist.

- [ ] **Step 3: Implement enums and immutable records**

```python
class GroundingOperator(str, Enum):
    EXACT_LOCAL = "exact_local"
    INTRODUCE = "introduce"
    APPLY_IMPLICATION = "apply_implication"
    IFF_FORWARD = "iff_forward"
    IFF_BACKWARD = "iff_backward"
    REWRITE_FORWARD = "rewrite_forward"
    REWRITE_BACKWARD = "rewrite_backward"
    SPLIT = "split"
    SELECT_LEFT = "select_left"
    SELECT_RIGHT = "select_right"
    CHOOSE_LEFT = "choose_left"
    CHOOSE_RIGHT = "choose_right"
    CASES = "cases"
    CONTRADICTION = "contradiction"
    COMPOSE = "compose"


@dataclass(frozen=True, slots=True)
class GroundedRoleBinding:
    binding_id: str
    proof_role: str
    inventory_id: str
    proposition: str
    source_role: str | None
    justification: str


@dataclass(frozen=True, slots=True)
class GroundedInferenceStep:
    step_id: str
    operator: GroundingOperator
    inputs: tuple[str, ...]
    input_propositions: tuple[str, ...]
    output_proposition: str
    transfer_step_index: int
    closes_subgoal: bool


@dataclass(frozen=True, slots=True)
class GroundedTacticPlan:
    plan_id: str
    motivating_transfer_id: str
    state_fingerprint: str
    bindings: tuple[GroundedRoleBinding, ...]
    steps: tuple[GroundedInferenceStep, ...]
    expected_next_subgoal: str
    completion_expected: bool
    surface_hints: tuple[str, ...]
```

Use exact-key JSON, caller-owned `plan_id`, canonical unique IDs, topological input references, exact inventory proposition equality, complete premise coverage, and exact transfer citation. Reject cycles, future-step references, mismatched input/output propositions, and incompatible completion fields.

- [ ] **Step 4: Add the grounded-plan prompt and generator**

Add `GROUNDING_PROMPT_VERSION = "grounding-v1"`. The prompt renders the full inventory, transfer, and allowed operators. It must explicitly forbid raw Lean and require inventory IDs copied exactly. `GroundedPlanGenerator` uses ordinary generation with `delta=None`, preserves ordered attempts and prompt version, and receives the existing cancellation/budget limit.

- [ ] **Step 5: Add the Review Focus regressions**

Test that a plan generated for state A is rejected against state B even when both contain a declaration named `h`; test Unicode/shadowed identifiers through inventory IDs; test one repair after an unknown ID and no downstream compiler call after persistent failure.

- [ ] **Step 6: Run schema and legacy-generation preservation tests**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_grounded_plan.py tests/test_analogy_transfer_generation.py`

Expected: all selected tests pass and existing source/alignment/transfer/raw-plan behavior is unchanged.

- [ ] **Step 7: Commit the grounded schema**

```bash
git add src/analogy/grounded_plan.py src/analogy/analogy_transfer_generation.py tests/test_grounded_plan.py tests/test_analogy_transfer_generation.py
git commit -m "feat: ground transferred reasoning in local proofs"
```

### Task 3: Compile grounded plans into safe Lean tactics

**Files:**
- Create: `src/analogy/tactic_compiler.py`
- Create: `tests/test_tactic_compiler.py`

**Interfaces:**
- Consumes: validated `GroundedTacticPlan` and its `LocalProofInventory`.
- Produces: `CompiledTactic`, `CompilationDiagnostic`, `CompilationResult`, and `GroundedTacticCompiler.compile(plan, inventory, candidate_limit) -> CompilationResult`.

- [ ] **Step 1: Write failing exact-syntax and provenance tests**

```python
def test_compiler_uses_exact_iff_direction_and_identifiers() -> None:
    result = GroundedTacticCompiler().compile(iff_backward_plan(), inventory(), candidate_limit=4)
    assert [item.text for item in result.candidates] == [
        "exact h2 (h1.mpr r)",
        "apply h2",
    ]
    assert result.candidates[0].step_ids == ("step-1", "step-2")


def test_rewrite_compiler_owns_brackets_and_reverse_arrow() -> None:
    result = GroundedTacticCompiler().compile(rewrite_backward_plan(), inventory(), candidate_limit=2)
    assert result.candidates[0].text == "rw [← h1]"


def test_unknown_composition_returns_diagnostic_without_candidate() -> None:
    result = GroundedTacticCompiler().compile(unsupported_composition(), inventory(), candidate_limit=4)
    assert result.candidates == ()
    assert result.diagnostics[0].code == "unsupported_composition"
```

- [ ] **Step 2: Run compiler tests and observe RED**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_tactic_compiler.py`

Expected: collection fails because `analogy.tactic_compiler` does not exist.

- [ ] **Step 3: Implement compiler result records and canonicalization**

```python
@dataclass(frozen=True, slots=True)
class CompiledTactic:
    text: str
    step_ids: tuple[str, ...]
    binding_ids: tuple[str, ...]
    predicted_next_subgoal: str


@dataclass(frozen=True, slots=True)
class CompilationDiagnostic:
    code: str
    step_id: str | None
    message: str


@dataclass(frozen=True, slots=True)
class CompilationResult:
    candidates: tuple[CompiledTactic, ...]
    diagnostics: tuple[CompilationDiagnostic, ...]
```

Canonicalize whitespace and deduplicate before `candidate_limit`. Reject multiline output and any identifier not resolved from the supplied inventory. Never consume `surface_hints` as code.

- [ ] **Step 4: Implement the supported operator templates**

Implement exact-local, introduce, apply-implication, iff forward/backward, rewrite forward/backward, split, select, choose, cases, contradiction, and supported two-step composition. Emit explicit terms before partial tactics when all required inputs are available. For example, `IFF_BACKWARD(h1, r)` followed by `APPLY_IMPLICATION(h2, step-1)` emits `exact h2 (h1.mpr r)`.

- [ ] **Step 5: Add compiler soundness table tests**

Use parameterized tests for every operator, Unicode identifiers, bracket syntax, direction, canonical deduplication, candidate limit, stale inventory fingerprint, and unsupported multi-step composition. For supported examples, execute each candidate with the fake evaluator contract and at least five representative candidates with the pinned Lean verifier.

- [ ] **Step 6: Run compiler and Lean integration tests**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_tactic_compiler.py tests/test_lean_verifier.py`

Expected: all selected tests pass.

- [ ] **Step 7: Commit the deterministic compiler**

```bash
git add src/analogy/tactic_compiler.py tests/test_tactic_compiler.py tests/test_lean_verifier.py
git commit -m "feat: compile grounded plans into Lean tactics"
```

### Task 4: Integrate grounded decoding and bounded diagnostic repair

**Files:**
- Modify: `src/analogy/analogy_transfer_loop.py`
- Modify: `src/analogy/training.py`
- Modify: `src/analogy/config.py`
- Modify: `src/analogy/analogy_transfer_generation.py`
- Modify: `config.yaml`
- Modify: `config.mlx-8b-fast.yaml`
- Modify: `tests/test_analogy_transfer_loop.py`
- Modify: `tests/test_training.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: inventory builder, `GroundedPlanGenerator`, compiler, Lean diagnostics, existing budgets and cancellation token.
- Produces: `analogy.decoder: raw | grounded`, grounded iteration records, and one bounded plan-repair path that cannot emit raw Lean.

- [ ] **Step 1: Write failing grounded-loop tests**

```python
def test_grounded_loop_compiles_before_spending_lean_calls() -> None:
    runner, recorder = grounded_runner(valid_plan(), compiled=("exact h",))
    outcome = runner.search(state(), goal(), (), (), evaluator(), cancellation())
    iteration = outcome.rollouts[0].analogy_trace.iterations[0]
    assert iteration.inventory.state_fingerprint == fingerprint(state())
    assert iteration.grounded_plan.motivating_transfer_id == "transfer-0"
    assert [item.text for item in iteration.compiled_tactics] == ["exact h"]
    assert recorder.lean_candidates == ["exact h"]


def test_compile_failure_uses_no_lean_call_and_records_diagnostic() -> None:
    runner, recorder = grounded_runner(unsupported_plan(), compiled=())
    outcome = runner.search(state(), goal(), (), (), evaluator(), cancellation())
    assert recorder.lean_calls == 0
    assert outcome.rollouts[0].analogy_trace.iterations[0].failed_stage == "compilation"


def test_lean_repair_can_only_replace_grounded_plan() -> None:
    runner, recorder = runner_with_rejected_compiled_tactic_then_repaired_plan()
    runner.search(state(), goal(), (), (), evaluator(), cancellation())
    assert recorder.model_stages[-1] == "grounding_repair"
    assert recorder.raw_tactic_generation_calls == 0
```

- [ ] **Step 2: Run loop/config tests and observe RED**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_analogy_transfer_loop.py tests/test_config.py -k grounded`

Expected: failures for missing decoder configuration and grounded iteration fields.

- [ ] **Step 3: Add strict decoder configuration**

Add `AnalogyConfig.decoder` with allowed values `raw` and `grounded`, defaulting existing profiles to `raw`. Add `grounding_repair_calls` constrained to `0` or `1`, default `1`. Create a later experiment profile with `decoder: grounded`; do not silently change old benchmark profiles.

- [ ] **Step 4: Insert inventory → grounded plan → compiler in the coordinator**

When `decoder == "grounded"`, replace the raw tactic-plan stage with:

```python
inventory = inventory_builder(current, structure)
grounded = generate("grounding", grounded_plan_generator,
                     observation, need, source, alignment, transfer, inventory).plan
compiled = compiler.compile(grounded, inventory, self.tactics_per_iteration)
if not compiled.candidates:
    raise _CompilationFailure(compiled.diagnostics)
verified = self._verify(current, tuple(item.text for item in compiled.candidates),
                        grounded.expected_next_subgoal, evaluator, cancellation,
                        resources, seen, "grounded_compiler")
```

Persist inventory, plan, compiled provenance, diagnostics, and prompt/compiler versions on partial and complete iterations. Raw mode must retain byte-compatible behavior.

- [ ] **Step 5: Implement one plan-only diagnostic repair**

After all compiled candidates are Lean-rejected, and only when model/Lean/cancellation budgets permit, render the unchanged inventory, plan, candidate provenance, and diagnostics. Parse a replacement `GroundedTacticPlan`, revalidate it against the same current inventory, compile it, and verify it. Count every model and Lean call. Persistent schema/compile failure terminates without raw fallback.

- [ ] **Step 6: Add lifecycle and reward regressions**

Test stale repaired bindings, cancellation before repair, no repair after completion, canonical candidate deduplication before Lean charging, repeated-state discard, infrastructure-failure training exclusion, and a verified nonterminal successor refreshing the next inventory and observation.

- [ ] **Step 7: Run focused and full preservation tests**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_analogy_transfer_loop.py tests/test_training.py tests/test_config.py tests/test_grounded_plan.py tests/test_tactic_compiler.py`

Then run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q`

Expected: all selected and full tests pass; historical raw/legacy paths remain green.

- [ ] **Step 8: Commit grounded execution**

```bash
git add src/analogy/analogy_transfer_loop.py src/analogy/training.py src/analogy/config.py src/analogy/analogy_transfer_generation.py config.yaml config.mlx-8b-fast.yaml tests/test_analogy_transfer_loop.py tests/test_training.py tests/test_config.py
git commit -m "feat: execute grounded analogy tactics"
```

### Task 5: Render and audit grounded causal traces

**Files:**
- Modify: `src/analogy/trace_render.py`
- Modify: `src/analogy/run_audit.py`
- Modify: `src/analogy/progress_judge.py`
- Modify: `tests/test_trace_render.py`
- Modify: `tests/test_run_audit.py`
- Modify: `tests/test_progress_judge.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: new optional grounded iteration fields plus historical raw and legacy rows.
- Produces: connected grounded trace sections and audit normalization that never changes rewards or solver behavior.

- [ ] **Step 1: Write failing grounded trace snapshot and audit tests**

```python
def test_grounded_trace_shows_binding_compilation_and_lean_result() -> None:
    rendered = render_theorem_trace(grounded_trace_fixture())
    assert "LOCAL PROOF INVENTORY" in rendered
    assert "GROUNDED ROLE BINDINGS" in rendered
    assert "TYPED INFERENCE STEPS" in rendered
    assert "COMPILED TACTICS" in rendered
    assert "LEAN RESULTS" in rendered
    assert "local:1 → h2 : P → Q" in rendered


def test_audit_normalizes_partial_grounding_failure() -> None:
    row = normalize_iteration(grounding_failure_row(), problem())
    assert row.stage_payloads["grounding"]["status"] == "failed"
    assert row.tactics == ()
```

- [ ] **Step 2: Run renderer/audit tests and observe RED**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_trace_render.py tests/test_run_audit.py tests/test_progress_judge.py -k grounded`

Expected: failures because grounded sections and normalization are absent.

- [ ] **Step 3: Extend rendering without reconstructing causality**

Render only persisted inventory, bindings, steps, compiled candidates, diagnostics, and actual Lean results. A compiled candidate is an edge only if an attempt record exists. Historical rows omit grounded sections rather than synthesizing them.

- [ ] **Step 4: Extend audit normalization and prompts**

Normalize raw and grounded generated iterations explicitly. Include bindings, operators, compiler diagnostics, candidate provenance, and repair history. The judge remains observer-only and receives objective Lean statuses. Missing or partial stages are represented as unavailable/failed, never as zero-quality proof attempts.

- [ ] **Step 5: Add version and compatibility tests**

Test historical raw fixtures, grounded complete/failure/repair rows, duplicate proof-path isolation, unknown future schema rejection, and that audit execution cannot mutate training or reward artifacts.

- [ ] **Step 6: Run focused and full tests**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_trace_render.py tests/test_run_audit.py tests/test_progress_judge.py`

Then run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 7: Commit grounded visibility**

```bash
git add src/analogy/trace_render.py src/analogy/run_audit.py src/analogy/progress_judge.py tests/test_trace_render.py tests/test_run_audit.py tests/test_progress_judge.py README.md
git commit -m "feat: trace and audit grounded tactics"
```

### Task 6: Build the split-safe verified transition curriculum

**Files:**
- Create: `src/analogy/grounded_curriculum.py`
- Create: `tests/test_grounded_curriculum.py`
- Create: `config.grounded-curriculum.yaml`
- Modify: `src/analogy/config.py`
- Modify: `src/analogy/__main__.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `problems.json`, `reference_proofs.json`, an explicit theorem-level split manifest, pinned Lean verifier, inventory builder, plan derivation, and compiler.
- Produces: versioned `transitions.jsonl`, `summary.json`, `manifest.json`, `CurriculumSummary`, `OnlineCurriculumState`, `derive_grounded_plan(reference_tactic, inventory, before, after) -> GroundedTacticPlan`, `load_online_curriculum_states(manifest, split) -> tuple[OnlineCurriculumState, ...]`, and `build_grounded_curriculum(config, verifier) -> CurriculumSummary`.

- [ ] **Step 1: Write failing prefix extraction and split-leakage tests**

```python
def test_curriculum_replays_every_reference_prefix_and_keeps_nonterminal_steps() -> None:
    summary = build_grounded_curriculum(config(), fake_verifier())
    records = read_jsonl(summary.transitions_path)
    assert [(r["problem_id"], r["prefix_index"]) for r in records] == [
        ("train_a", 0), ("train_a", 1), ("train_a", 2),
    ]
    assert records[0]["outcome"] == "novel_successor"
    assert records[-1]["outcome"] == "complete"


def test_all_derivatives_of_one_theorem_remain_in_one_split() -> None:
    records = build_with_augmentations(analogies_per_transition=3)
    by_problem = groupby_problem(records)
    assert all(len({item.split for item in items}) == 1 for items in by_problem.values())


def test_online_view_omits_reference_next_tactic_and_successor() -> None:
    item = load_online_curriculum_state(record())
    assert not hasattr(item, "reference_tactic")
    assert not hasattr(item, "verified_successor")


def test_duplicate_state_text_from_distinct_prefixes_keeps_distinct_transition_ids() -> None:
    records = build_from_two_prefixes_with_same_normalized_state()
    assert len(records) == 2
    assert records[0].transition_id != records[1].transition_id
```

- [ ] **Step 2: Run curriculum tests and observe RED**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_grounded_curriculum.py`

Expected: collection fails because `analogy.grounded_curriculum` does not exist.

- [ ] **Step 3: Add strict curriculum configuration**

Add paths for problems, reference proofs, split manifest, output directory, and `analogies_per_transition`; require seed, schema version, and pinned Lean project. Reject overlapping/missing theorem IDs before replay. The default config uses theorem-level train/validation/test groups and never derives splits from extracted states.

- [ ] **Step 4: Implement replay and grounded-plan derivation**

For each theorem in split order and every reference tactic:

```python
before = verifier.state_after(problem, tactics[:index])
after = verifier.try_tactic(problem, tactics[:index], tactics[index])
structure = verifier.proof_structure_after(problem, tactics[:index])
inventory = build_local_inventory(symbolic(before), structure, fixed_target=problem.target)
derived = derive_grounded_plan(reference_tactic=tactics[index], inventory=inventory,
                               before=before, after=after)
compiled = compiler.compile(derived, inventory, candidate_limit=8)
```

Retain only accepted transitions for which at least one compiled candidate reproduces an accepted successor/completion. Count unsupported tactic, compile mismatch, verifier error, repeated state, and structure failure separately. Prefer nonterminal examples in summary and sampling weights; do not drop completions.

- [ ] **Step 5: Persist immutable identities and online-safe views**

Each full record stores theorem/source hashes, split, prefix index/hash, before/after hashes, reference tactic, grounded plan, compiler version, matching candidate, Lean version, and outcome. `load_online_curriculum_states` returns only problem ID, accepted prefix, current verified state hash, split, and opaque transition ID—never the next tactic, successor, or grounded answer.

- [ ] **Step 6: Add augmentation identity without inference retrieval**

When `analogies_per_transition > 0`, call the existing source/alignment/transfer/grounding stages offline with an explicit unsteered zero latent action and retain only records whose compiled tactic verifies. Store variants under the parent transition ID. The online runner samples scheduled transition states by split and never searches variants by embedding, theorem similarity, or target text.

- [ ] **Step 7: Add CLI and interruption safety**

Add `python -m analogy build-grounded-curriculum --config config.grounded-curriculum.yaml`. Journal completed transition IDs, resume only under identical source/config/model hashes, and atomically finalize summary/manifest. A partial final JSONL row must be recoverable without losing prior complete rows.

- [ ] **Step 8: Run focused, CLI, and full tests**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_grounded_curriculum.py tests/test_config.py tests/test_cli.py`

Then run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q`

Expected: all tests pass; no test split record appears in a train/validation online view.

- [ ] **Step 9: Commit the curriculum builder**

```bash
git add src/analogy/grounded_curriculum.py src/analogy/config.py src/analogy/__main__.py config.grounded-curriculum.yaml tests/test_grounded_curriculum.py tests/test_config.py tests/test_cli.py
git commit -m "feat: build verified proof transition curriculum"
```

### Task 7: Train and benchmark from curriculum states

**Files:**
- Modify: `src/analogy/training.py`
- Modify: `src/analogy/rl.py`
- Modify: `src/analogy/config.py`
- Create: `config.mlx-8b-grounded-analogy.yaml`
- Create: `tests/test_grounded_training.py`
- Modify: `tests/test_training.py`
- Modify: `tests/test_rl.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`
- Create: `docs/benchmarks/2026-10-15-grounded-transition-smoke.md`

**Interfaces:**
- Consumes: online-safe curriculum states, grounded generated runner, existing latent policy/checkpoint system, and matched evaluation budgets.
- Produces: curriculum-aware training episodes, grounded checkpoint metadata, funnel metrics, immutable traces, and a matched raw-vs-grounded smoke report.

- [ ] **Step 1: Write failing curriculum-training isolation tests**

```python
def test_training_replays_prefix_but_never_passes_reference_answer_to_runner() -> None:
    trainer, recorder = curriculum_trainer(curriculum_state(prefix=("intro h",)))
    trainer.run_epoch()
    assert recorder.started_prefix == ("intro h",)
    assert recorder.generator_payload_has_reference_tactic is False
    assert recorder.generator_payload_has_verified_successor is False


def test_nonterminal_verified_successor_can_seed_second_analogy() -> None:
    result = run_two_step_grounded_episode()
    assert len(result.rollout.analogy_trace.iterations) == 2
    assert result.rollout.analogy_trace.iterations[1].observation.actual_transition
    assert result.samples[0].reward < result.samples[1].reward
```

- [ ] **Step 2: Run training tests and observe RED**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_grounded_training.py tests/test_training.py -k curriculum`

Expected: failures because training cannot load or replay curriculum states.

- [ ] **Step 3: Add curriculum state scheduling**

Load only the configured split through `load_online_curriculum_states`. Replay the accepted prefix through `LeanSearchAdapter`, verify the current state hash, then call the normal generated runner. Schedule states deterministically by seed and epoch; do not use similarity retrieval. Persist transition ID and proof path ID for every episode.

- [ ] **Step 4: Preserve verifier-only optimization**

Use existing `discounted_iteration_samples`. Eligible actions require a verified novel successor or completion; malformed schema, compiler failure, repeated state, and infrastructure failure produce no positive progress sample. Add tests for one successful first iteration followed by compilation/runtime failure so only the first action remains trainable.

- [ ] **Step 5: Add schema/checkpoint metadata and incompatibility errors**

Increment the policy/training artifact schema for grounded curriculum metadata. Save curriculum manifest hash, inventory schema, grounded-plan schema, compiler version, decoder mode, and prompt versions. Refuse a raw-only or different-curriculum checkpoint with a clear saved-versus-expected diagnostic.

- [ ] **Step 6: Produce funnel metrics and connected traces**

Add per-condition counts for source, alignment, transfer, binding, plan, compiler, Lean acceptance, novel successor, second analogy, completion, and calls. Denominators must be attempts at that stage. Trace output uses the Task 5 grounded sections and links every candidate to actual Lean status.

- [ ] **Step 7: Add the grounded MLX profile and fake-backend smoke**

Copy the existing generated 8B profile, set `analogy.decoder: grounded`, point training to a built curriculum manifest, preserve matched call/Lean/wall-time budgets, and use at least 512 output tokens. The fake-backend CLI test must exercise a verified nonterminal successor followed by a second analogy and completion.

- [ ] **Step 8: Run focused and full tests before inference**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q tests/test_grounded_training.py tests/test_training.py tests/test_rl.py tests/test_cli.py`

Then run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 9: Build the curriculum and run the matched smoke**

Build from train/validation reference proofs, verify manifest hashes, then compare conventional, legacy, raw generated, grounded unsteered, grounded random, grounded learned, and compatible grounded shuffled conditions on held-out curriculum states. Preserve immutable raw calls, Lean results, checkpoints, JSONL, summaries, and readable traces. Do not overwrite the October 15 generated-domain artifacts.

- [ ] **Step 10: Write the evidence-backed benchmark report**

Lead with relative completion, accepted-tactic, novel-successor, second-analogy, and completion-per-call metrics. Then show the schema funnel and compiler diagnostics. Explicitly state whether the initial gates were met and whether learned exceeds random/unsteered. Separate in-sample curriculum behavior, held-out behavior, infrastructure failures, and model interpretation.

- [ ] **Step 11: Run final verification and commit**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q`

Run: `git diff --check`

```bash
git add src/analogy/training.py src/analogy/rl.py src/analogy/config.py config.mlx-8b-grounded-analogy.yaml tests/test_grounded_training.py tests/test_training.py tests/test_rl.py tests/test_cli.py README.md docs/benchmarks/2026-10-15-grounded-transition-smoke.md
git commit -m "test: benchmark grounded analogy transitions"
```

### Task 8: Whole-branch verification and review

**Files:**
- Review: all files changed by Tasks 1–7

**Interfaces:**
- Consumes: completed branch, spec, plan, curriculum artifacts, and benchmark provenance.
- Produces: fresh full-suite evidence, whole-branch review, and explicit integration choice.

- [ ] **Step 1: Run the complete suite on the final head**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q`

Expected: zero failures; any environment skip is named and unchanged.

- [ ] **Step 2: Verify repository and artifact hygiene**

Run: `git diff --check <base>..HEAD`

Run: `git status --short --branch`

Verify curriculum and benchmark manifests, source hashes, checkpoint metadata, and trace links without rerunning expensive inference.

- [ ] **Step 3: Request whole-branch review**

The reviewer checks cross-task type contracts, stale-inventory safety, compiler soundness, budget/cancellation accounting, split leakage, reference-answer isolation, reward eligibility, historical compatibility, audit/trace normalization, and evidence claims.

- [ ] **Step 4: Fix every Critical or Important finding and re-review**

Use the original owning implementer for each finding, add a focused regression, rerun the affected suite, and obtain an addressed verdict.

- [ ] **Step 5: Run one final fresh full suite**

Run: `PYTHONPATH=src /Users/laurenmoos/analogy/.venv/bin/python -m pytest -q`

Expected: zero failures on the exact reviewed head.
