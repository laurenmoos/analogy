# Single-Call Story Operator Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the four-call generated grounding pipeline with one story/operator call followed by exhaustive deterministic grounding, while centralizing prompts and adding a standalone matched ablation.

**Architecture:** `derive_proof_need` produces separate private diagnostics and a neutral public description. A strict `StoryOperatorDecision` generator makes one un-repaired model call, then `enumerate_applications` and a simplified compiler generate every valid candidate from exact inventory data and the runner verifies all of them. Prompt text is loaded from `prompts.yaml`; experiment-only plain-versus-story logic lives only in a benchmark script.

**Tech Stack:** Python 3.11, frozen dataclasses, PyYAML, PyTorch policy gradients, pytest, pinned Lean 4 subprocess verifier, Transformers/MLX Qwen backends.

**Spec:** `docs/superpowers/specs/2026-10-16-single-call-story-operator-grounding-design.md`

## Global Constraints

- Production generated grounding makes exactly one model call per attempt and zero repair calls.
- Qwen thinking remains disabled for the call.
- The model never receives raw Lean state text, rejected tactics, or the hidden expected operator.
- The enabled operator list is explicit in every experiment config and contains unique real `GroundingOperator` values.
- Code, not the model, owns state hashes, formulas, identifiers, successors, and completion.
- Every unique deterministic candidate is sent to Lean unless cancellation or infrastructure failure intervenes.
- All model-facing instructional prose is stored in repository-root `prompts.yaml`.
- Compatibility with old generated-domain schemas and checkpoints is not required.
- The direct-versus-story ablation remains a standalone script, not a production runner branch.

## Review Focus

- A prompt template containing literal JSON braces or a missing named variable must render safely or fail before a model call; Task 1 adds literal-brace and missing-variable tests.
- A selected valid operator with stale or duplicate inventory IDs must be rejected without enumeration; Task 2 adds exact-ID validation tests.
- Exhaustive verification must continue after the first completed candidate and retain every result; Task 4 adds a completion-first multi-candidate test.
- Reward centering must group attempts by problem and state rather than mixing unrelated problems; Task 5 adds two-state, unequal-reward tests.
- Arm order, cache warmth, or differing call counts must not bias the ablation; Task 7 adds crossover-order and exactly-one-call tests.

---

### Task 1: Add the prompt catalog and explicit operator configuration

**Files:**
- Create: `prompts.yaml`
- Create: `src/analogy/prompts.py`
- Modify: `src/analogy/config.py`
- Modify: `config.yaml`
- Modify: `config.mlx-8b-fast.yaml`
- Modify: `config.mlx-8b-generated-analogy.yaml`
- Modify: `config.mlx-8b-audit.yaml`
- Modify: `config.mlx-8b-grounded-analogy.yaml`
- Modify: `config.grounded-curriculum.yaml`
- Modify: `src/analogy/model.py`
- Modify: `src/analogy/rescue.py`
- Modify: `src/analogy/proof_views.py`
- Modify: `src/analogy/analogy_generation.py`
- Modify: `src/analogy/analogy_stream.py`
- Modify: `src/analogy/progress_judge.py`
- Modify: `src/analogy/run_audit.py`
- Modify: `src/analogy/training.py`
- Test: `tests/test_prompts.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `PromptCatalog.load(path: Path) -> PromptCatalog`
- Produces: `PromptCatalog.messages(name: str, **values: object) -> tuple[Message, ...]`
- Produces: `PromptCatalog.version(name: str) -> str` and `PromptCatalog.content_hash: str`
- Produces: `PromptConfig(path: Path)` at `AppConfig.prompts`
- Produces: `AnalogyConfig.grounding_operators: tuple[GroundingOperator, ...]`

- [ ] **Step 1: Write failing catalog and configuration tests**

Add tests that load a temporary YAML catalog, render system/user messages with `{{name}}` placeholders, preserve literal JSON braces, reject missing prompts/variables/unknown fields, and return a stable SHA-256 hash. Add config tests asserting relative `prompts.path` resolution and rejection of empty, duplicate, or unknown `analogy.grounding_operators`.

```python
def test_prompt_catalog_renders_named_values_and_literal_json(tmp_path):
    path = tmp_path / "prompts.yaml"
    path.write_text("""prompts:
  story:
    version: story-v1
    system: 'Return JSON: {\"operator\": \"exact_local\"}'
    user: 'Need: {{need}}'
""")
    catalog = PromptCatalog.load(path)
    assert catalog.messages("story", need="one item")[1].content == "Need: one item"

def test_config_rejects_duplicate_grounding_operators(tmp_path):
    payload = yaml.safe_load((ROOT / "config.yaml").read_text())
    payload["analogy"]["grounding_operators"] = ["exact_local", "exact_local"]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload))
    with pytest.raises(ConfigError, match="must not contain duplicates"):
        load_config(path)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `PYTHONPATH=src pytest tests/test_prompts.py tests/test_config.py -q`

Expected: collection/import failure because `analogy.prompts`, `PromptConfig`, and `grounding_operators` do not exist.

- [ ] **Step 3: Implement strict prompt loading and config parsing**

Use a non-executable renderer that replaces only doubled-brace placeholders and does not call `str.format`:

```python
_VARIABLE = re.compile(r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}")

@dataclass(frozen=True, slots=True)
class PromptTemplate:
    version: str
    system: str
    user: str

@dataclass(frozen=True, slots=True)
class PromptCatalog:
    templates: Mapping[str, PromptTemplate]
    content_hash: str

    @classmethod
    def load(cls, path: Path) -> "PromptCatalog":
        raw = path.read_bytes()
        payload = yaml.safe_load(raw)
        if not isinstance(payload, dict) or set(payload) != {"prompts"}:
            raise PromptError("prompt catalog must contain only prompts")
        prompts = payload["prompts"]
        if not isinstance(prompts, dict) or not prompts:
            raise PromptError("prompts must be a nonempty mapping")
        templates = {}
        for name, value in prompts.items():
            if (not isinstance(name, str) or not isinstance(value, dict)
                    or set(value) != {"version", "system", "user"}
                    or any(not isinstance(value[key], str) or not value[key].strip()
                           for key in ("version", "system", "user"))):
                raise PromptError(f"invalid prompt template: {name!r}")
            templates[name] = PromptTemplate(**value)
        return cls(MappingProxyType(templates), hashlib.sha256(raw).hexdigest())

    def messages(self, name: str, **values: object) -> tuple[Message, ...]:
        template = self.templates[name]
        supplied = {key: str(value) for key, value in values.items()}
        required = set(_VARIABLE.findall(template.system + template.user))
        if required != set(supplied):
            raise PromptError(f"prompt variables differ: required={sorted(required)} supplied={sorted(supplied)}")
        def render(text: str) -> str:
            return _VARIABLE.sub(lambda match: supplied[match.group(1)], text)
        return Message("system", render(template.system)), Message("user", render(template.user))

    def version(self, name: str) -> str:
        return self.templates[name].version
```

Add `prompts` to the root config schema and parse operator strings with `GroundingOperator(value)`. Configure all profiles with `prompts.path: prompts.yaml` and the spec’s explicit default list. Set `grounding_repair_calls: 0` wherever the obsolete field still exists before its later removal. For generated grounding profiles, set `max_model_calls` equal to `max_iterations` because each iteration can now make only one call, and set `rl.rollouts_per_state: 4` so every update has several same-state attempts.

- [ ] **Step 4: Populate `prompts.yaml` and migrate prompt renderers**

Create named entries for all remaining model-facing paths discovered by `rg 'Message\(|prompt =|render_.*prompt' src/analogy`: conventional ranking, capability, direct rescue, rescue repair, proof language/structure embeddings, legacy analogy paths that have not yet been deleted, story operator decision, tactic reranking, progress audit, audit repair, and both standalone ablation arms. Move system and user instructional prose verbatim first; interpolation values remain Python-owned serialized data.

Each entry uses this exact shape:

```yaml
prompts:
  story_operator:
    version: story-operator-v1
    system: >-
      Choose one configured proof operation through a short everyday story. Return only strict JSON.
    user: |-
      Neutral need: {{need_json}}
      Available items: {{inventory_json}}
      Allowed operators: {{operators_json}}
      Return exactly story, operator, and bindings. Bindings map short story roles to listed inventory IDs.
```

Inject `PromptCatalog` at composition roots instead of loading YAML inside hot loops. Remove instructional string literals from Python after each caller uses the catalog.

- [ ] **Step 5: Run catalog/config tests and a prompt-text audit**

Run: `PYTHONPATH=src pytest tests/test_prompts.py tests/test_config.py -q`

Expected: PASS.

Run: `rg -n 'Message\([^,]+,\s*["'"']|Generate exactly|Return exactly|You are an observer|You produce correct' src/analogy`

Expected: no model-facing prose; remaining matches are validation errors, docstrings, or catalog key lookups.

- [ ] **Step 6: Commit**

```bash
git add prompts.yaml config*.yaml src/analogy/prompts.py src/analogy/config.py src/analogy/model.py src/analogy/rescue.py src/analogy/proof_views.py src/analogy/analogy_generation.py src/analogy/analogy_stream.py src/analogy/progress_judge.py src/analogy/run_audit.py src/analogy/training.py tests/test_prompts.py tests/test_config.py
git commit -m "refactor: centralize model prompts and grounding operators"
```

### Task 2: Replace staged transfer records with a neutral need and strict one-call decision

**Files:**
- Create: `src/analogy/story_operator.py`
- Modify: `src/analogy/analogy_transfer.py`
- Modify: `src/analogy/proof_inventory.py`
- Delete: obsolete source/alignment/transfer generation portions of `src/analogy/analogy_transfer_generation.py`
- Test: `tests/test_story_operator.py`
- Test: `tests/test_proof_need.py`

**Interfaces:**
- Produces: `NeedAnalysis(description: NeutralProofNeed, expected_operator: GroundingOperator | None)`
- Produces: `derive_need_analysis(structure: ProofStructure, inventory: LocalProofInventory) -> NeedAnalysis`
- Produces: `StoryBinding(role: str, inventory_id: str)`
- Produces: `StoryOperatorDecision(story: str, operator: GroundingOperator, bindings: tuple[StoryBinding, ...])`
- Produces: `parse_story_operator_decision(raw: str, inventory: LocalProofInventory, enabled: tuple[GroundingOperator, ...]) -> StoryOperatorDecision`
- Produces: `StoryOperatorGenerator.generate(..., action: PolicyAction) -> GeneratedStoryOperatorDecision`

- [ ] **Step 1: Write failing neutralization and parser tests**

Cover every need-analysis branch. Hand-check exact neutral summaries, including conjunction selection:

```python
analysis = derive_need_analysis(structure, inventory)
assert asdict(analysis.description) == {
    "input_arity": "two",
    "output_arity": "one",
    "information_flow": ["select", "discard"],
    "focus_before": "one available item made of two parts",
    "desired_focus_after": "the part matching the goal",
    "proof_roles": ["active_subgoal", "structured_hypothesis"],
    "summary": "There is one item made of two parts, and the goal matches one part.",
}
assert analysis.expected_operator is GroundingOperator.SELECT_LEFT
assert "operator" not in json.dumps(asdict(analysis.description))
```

Parser tests accept only `{story, operator, bindings}`, reject duplicate keys, disabled operators, unknown IDs, duplicate roles, repeated IDs, Lean/tactic leakage in the story, and any model-supplied formula/hash/completion field. Backend tests spy on Qwen chat-template arguments and require `enable_thinking=False` for this call.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `PYTHONPATH=src pytest tests/test_story_operator.py tests/test_proof_need.py -q`

Expected: FAIL because the new module and records do not exist.

- [ ] **Step 3: Implement neutral need analysis**

Split the current `ProofNeed` responsibility:

```python
@dataclass(frozen=True, slots=True)
class NeutralProofNeed:
    input_arity: Arity
    output_arity: Arity
    information_flow: tuple[InformationFlow, ...]
    focus_before: str
    desired_focus_after: str
    proof_roles: tuple[str, ...]
    summary: str

@dataclass(frozen=True, slots=True)
class NeedAnalysis:
    description: NeutralProofNeed
    expected_operator: GroundingOperator | None
```

Map structural branches directly to concrete operators: target match → `EXACT_LOCAL`; implication/not target → `INTRODUCE`; conjunction target → `SPLIT`; disjunction target → the first structurally viable `CHOOSE_LEFT`/`CHOOSE_RIGHT` diagnostic or `None`; conjunction component → `SELECT_LEFT`/`SELECT_RIGHT`; implication hypothesis → `APPLY_IMPLICATION`; iff direction → `IFF_FORWARD`/`IFF_BACKWARD`; equality → rewrite direction when determinable; disjunction hypothesis → `CASES`; contradiction pair → `CONTRADICTION`. Keep the expected value private from `NeutralProofNeed` serialization.

- [ ] **Step 4: Implement strict decision parsing and one-call generation**

```python
@dataclass(frozen=True, slots=True)
class StoryBinding:
    role: str
    inventory_id: str

@dataclass(frozen=True, slots=True)
class StoryOperatorDecision:
    story: str
    operator: GroundingOperator
    bindings: tuple[StoryBinding, ...]

class StoryOperatorGenerator:
    def generate(self, description, inventory, action, *, analogy_index, cancellation=None):
        messages = self.prompts.messages(
            "story_operator",
            need_json=json.dumps(asdict(description), sort_keys=True),
            inventory_json=render_model_inventory(inventory),
            operators_json=json.dumps([item.value for item in self.enabled]),
        )
        raw = self.generator.generate_with_delta(messages, delta=action.delta.detach(),
                                                 layer=self.hidden_layer,
                                                 seed=self.base_seed + analogy_index)
        return GeneratedStoryOperatorDecision(
            parse_story_operator_decision(raw, inventory, self.enabled),
            (GenerationAttempt(self.base_seed + analogy_index, raw,
                               GenerationStatus.VALID, repair=False),),
        )
```

Catch runtime failure once, record it once, and never feed invalid output back to the model. `render_model_inventory` includes inventory IDs, kinds, proposition text, and structural shapes so the model can map story roles to available items. It omits Lean identifiers and the raw state block. The decision repeats only IDs; code copies exact proposition text and identifiers from the inventory afterward.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `PYTHONPATH=src pytest tests/test_story_operator.py tests/test_proof_need.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/analogy/story_operator.py src/analogy/analogy_transfer.py src/analogy/analogy_transfer_generation.py src/analogy/proof_inventory.py tests/test_story_operator.py tests/test_proof_need.py
git commit -m "feat: add one-call story operator decision"
```

### Task 3: Enumerate and compile every deterministic operator application

**Files:**
- Create: `src/analogy/grounding.py`
- Rewrite: `src/analogy/tactic_compiler.py`
- Delete: `src/analogy/grounded_plan.py`
- Modify: `src/analogy/proof_inventory.py`
- Test: `tests/test_grounding.py`
- Test: `tests/test_tactic_compiler.py`

**Interfaces:**
- Produces: `GroundedApplication(application_id, operator, state_fingerprint, inventory_ids, input_propositions, output_proposition, expected_next_subgoal, completion_expected)`
- Produces: `enumerate_applications(operator: GroundingOperator, inventory: LocalProofInventory) -> tuple[GroundedApplication, ...]`
- Consumes: existing conservative `_proposition_root` parsing, moved to `proof_inventory.py` as public package helpers
- Produces: `GroundedTacticCompiler.compile_all(applications, inventory) -> CompilationResult`

- [ ] **Step 1: Write exhaustive failing operator tests**

Use literal inventories and expected tuples for all configured operators. Include multiple matching locals to prove enumeration is exhaustive and ordered by inventory ID. Required cases:

| Operator | Valid application rule | Expected tactic family |
|---|---|---|
| `exact_local` | local proposition equals goal | `exact <id>` |
| `introduce` | goal root is implication/not | `intro analogy_h[N]` |
| `apply_implication` | local consequent equals goal | `apply <id>` plus exact term when antecedent local exists |
| `iff_forward/backward` | iff side plus matching local derives goal | `exact h.mp x` / `exact h.mpr x` |
| `rewrite_forward/backward` | equality/iff local has requested direction | `rw [h]` / `rw [← h]` |
| `split` | goal root is and/iff | `constructor` |
| `select_left/right` | local conjunction component equals goal | `exact h.left/right` |
| `choose_left/right` | selected disjunction branch becomes next goal | `left` / `right` |
| `cases` | local root is disjunction | `cases h` |
| `contradiction` | `False` local or `P` plus `¬P` | `exact False.elim (not_p p)` |

Also test opaque syntax, quoted identifiers, stable IDs, exact state fingerprints, outer-parenthesis equivalence, global candidate deduplication, and completion only when output equals the active goal.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `PYTHONPATH=src pytest tests/test_grounding.py tests/test_tactic_compiler.py -q`

Expected: FAIL because `GroundedApplication`, `enumerate_applications`, and `compile_all` do not exist.

- [ ] **Step 3: Implement immutable deterministic applications**

```python
@dataclass(frozen=True, slots=True)
class GroundedApplication:
    application_id: str
    operator: GroundingOperator
    state_fingerprint: str
    inventory_ids: tuple[str, ...]
    input_propositions: tuple[str, ...]
    output_proposition: str
    expected_next_subgoal: str
    completion_expected: bool

def enumerate_applications(operator, inventory):
    builders = {
        GroundingOperator.EXACT_LOCAL: _exact_local,
        GroundingOperator.INTRODUCE: _introduce,
        GroundingOperator.APPLY_IMPLICATION: _apply_implication,
        GroundingOperator.IFF_FORWARD: partial(_iff, forward=True),
        GroundingOperator.IFF_BACKWARD: partial(_iff, forward=False),
        GroundingOperator.REWRITE_FORWARD: partial(_rewrite, forward=True),
        GroundingOperator.REWRITE_BACKWARD: partial(_rewrite, forward=False),
        GroundingOperator.SPLIT: _split,
        GroundingOperator.SELECT_LEFT: partial(_select, index=0),
        GroundingOperator.SELECT_RIGHT: partial(_select, index=1),
        GroundingOperator.CHOOSE_LEFT: partial(_choose, index=0),
        GroundingOperator.CHOOSE_RIGHT: partial(_choose, index=1),
        GroundingOperator.CASES: _cases,
        GroundingOperator.CONTRADICTION: _contradiction,
        GroundingOperator.COMPOSE: lambda _inventory: (),
    }
    return tuple(replace(item, application_id=f"application:{index}")
                 for index, item in enumerate(builders[operator](inventory)))
```

All builders derive exact formulas from inventory entries. They sort operand tuples lexicographically by inventory ID and never consume story bindings.

- [ ] **Step 4: Simplify the compiler around `GroundedApplication`**

Delete model-plan validation, bindings, step graphs, transfer IDs, and repair diagnostics. Reuse safe-identifier, proposition-root, canonicalization, and tactic templates. `compile_all` validates the current fingerprint for every application, emits all supported syntax for each, canonicalizes, and deduplicates by text while preserving first occurrence and application provenance.

```python
def compile_all(self, applications, inventory):
    emitted: list[CompiledTactic] = []
    diagnostics: list[CompilationDiagnostic] = []
    for application in applications:
        result = self.compile(application, inventory)
        emitted.extend(result.candidates)
        diagnostics.extend(result.diagnostics)
    unique = {candidate.text: candidate for candidate in emitted}
    return CompilationResult(tuple(unique.values()), tuple(diagnostics))
```

Do not apply `candidate_limit` or `tactics_per_iteration` to deterministic output.

- [ ] **Step 5: Run focused and real-Lean tests**

Run: `PYTHONPATH=src pytest tests/test_grounding.py tests/test_tactic_compiler.py tests/test_lean_end_to_end.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/analogy/grounding.py src/analogy/tactic_compiler.py src/analogy/proof_inventory.py tests/test_grounding.py tests/test_tactic_compiler.py tests/test_lean_end_to_end.py
git rm src/analogy/grounded_plan.py
git commit -m "feat: enumerate deterministic grounding applications"
```

### Task 4: Replace the generated runner with the single-call all-candidate path

**Files:**
- Rewrite: `src/analogy/analogy_transfer_loop.py`
- Modify: `src/analogy/training.py`
- Modify: `config.mlx-8b-grounded-analogy.yaml`
- Modify: `config.mlx-8b-generated-analogy.yaml`
- Modify: `src/analogy/trace_render.py`
- Modify: `src/analogy/progress_judge.py`
- Test: `tests/test_story_operator_loop.py`
- Test: `tests/test_trace_render.py`

**Interfaces:**
- Consumes: `StoryOperatorGenerator`, `derive_need_analysis`, `enumerate_applications`, `GroundedTacticCompiler.compile_all`
- Produces: `StoryOperatorIteration` with decision, expected operator, applications, compiled candidates, all Lean attempts, and call/timing diagnostics
- Produces: `StoryOperatorTrace`
- Preserves: `GeneratedDomainAnalogyLoopRunner.run(...) -> GenericRescueOutcome` caller contract until renamed callers are updated in the same task

- [ ] **Step 1: Write failing one-call and exhaustive-verification tests**

Build a fake generator that raises if called twice and an evaluator with three candidates: first completes, second errors, third yields a novel successor. Assert one generation call, three Lean calls, three retained attempts, completion chosen for the iteration, and `expected_operator` absent from captured messages.

```python
outcome = runner.run(state, goal, ordinary_attempts=(), evaluator=evaluator)
iteration = outcome.rollouts[0].analogy_trace.iterations[0]
assert generator.calls == 1
assert [a.result.status for a in iteration.attempts] == [COMPLETE, ERROR, ACCEPTED]
assert iteration.model_calls == 1
assert iteration.lean_calls == 3
assert "expected_operator" not in generator.calls[0].messages[1].content
```

Add cancellation and infrastructure-failure tests proving those are the only early-stop paths.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `PYTHONPATH=src pytest tests/test_story_operator_loop.py tests/test_trace_render.py -q`

Expected: FAIL because the existing runner performs staged calls and stops verification on success.

- [ ] **Step 3: Rewrite the iteration body**

Replace stage dictionaries and source/alignment/transfer/grounding repair state with:

```python
analysis = derive_need_analysis(structure, inventory)
action = self.action_sampler(representation, index)
generated = self.decision_generator.generate(
    analysis.description, inventory, action,
    analogy_index=index, cancellation=cancellation,
)
applications = enumerate_applications(generated.decision.operator, inventory)
compilation = self.compiler.compile_all(applications, inventory)
verified, resources = self._verify_all(
    current, compilation.candidates, evaluator, cancellation, resources, seen,
)
```

Record `expected_operator` only when building the iteration after generation. Make invalid JSON, zero applications, and zero compiled candidates explicit non-infrastructure terminations.

- [ ] **Step 4: Implement `_verify_all` without success short-circuiting**

```python
for candidate in candidates:
    if cancellation.is_cancelled() or resources.remaining_lean_calls <= 0:
        break
    evaluation = evaluate(candidate)
    attempts.append(to_attempt(candidate, evaluation))
    completed.append(evaluation.completed_proof)
    successors.append(evaluation.successor)
return VerifiedAll(
    attempts=tuple(attempts),
    completed_proof=next((item for item in completed if item is not None), None),
    successor=next((item for item in successors if item is not None), None),
)
```

Do not treat an exhausted configured Lean budget as successful exhaustive verification. Production profiles must set `max_lean_calls` and `max_rescue_verifier_calls` high enough for the emitted set; logs expose any truncation.

- [ ] **Step 5: Update composition, traces, funnels, and identities**

Construct only the new decision generator/compiler in `_new_generated_runner`. Remove source/alignment/transfer/grounding prompt identities and replace them with `story_operator`, prompt-catalog hash, enabled operators, decision schema, and compiler version. Render story, operator, bindings, deterministic applications, compiled tactics, and all Lean outcomes. Update funnel names to `decision`, `application`, `compiled_candidate`, `lean_attempt`, and `completion`.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `PYTHONPATH=src pytest tests/test_story_operator_loop.py tests/test_trace_render.py tests/test_grounded_training.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/analogy/analogy_transfer_loop.py src/analogy/training.py src/analogy/trace_render.py src/analogy/progress_judge.py tests/test_story_operator_loop.py tests/test_trace_render.py tests/test_grounded_training.py
git commit -m "refactor: run single-call exhaustive grounded analogies"
```

### Task 5: Center rewards within same-problem attempts and report story diversity

**Files:**
- Modify: `src/analogy/rl.py`
- Modify: `src/analogy/training.py`
- Test: `tests/test_rl.py`
- Test: `tests/test_grounded_training.py`

**Interfaces:**
- Changes: `reinforce_step(..., samples)` always uses sibling mean and requires at least two samples
- Removes: `RewardBaseline` and checkpoint `reward_baseline`
- Produces: `StoryDiversityMetrics(states, exact_distinct_ratio, mean_pairwise_distance, operator_diversity_ratio)`
- Produces: `story_diversity(samples, embed: Callable[[str], Embedding]) -> StoryDiversityMetrics`

- [ ] **Step 1: Write failing grouped-advantage and diversity tests**

Create two problem/state groups with rewards `(1.0, 0.0, -1.0)` and `(0.4, 0.2)`. Assert separate centered advantages `(1, 0, -1)` and `(0.1, -0.1)`, one optimizer step per group, and no update for a singleton. Add stories where two latent samples normalize to the same text and one differs; assert exact ratio and hand-computed pairwise cosine distance.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `PYTHONPATH=src pytest tests/test_rl.py tests/test_grounded_training.py -q`

Expected: FAIL because singleton baselines still update and story metrics do not exist.

- [ ] **Step 3: Remove singleton baselines and enforce grouped updates**

```python
def reinforce_step(policy, optimizer, samples, entropy_weight, max_gradient_norm):
    normalized = tuple(samples)
    if len(normalized) < 2:
        raise ValueError("same-state policy updates require at least two attempts")
    state_ids = {sample.rollout.state_id for sample in normalized}
    if len(state_ids) != 1:
        raise ValueError("samples must belong to one problem state")
    rewards = torch.tensor([sample.reward for sample in normalized], dtype=torch.float32)
    advantages = rewards - rewards.mean()
    log_probabilities = torch.stack([sample.action.log_probability for sample in normalized])
    entropies = torch.stack([sample.action.entropy for sample in normalized])
    loss = -(advantages.to(log_probabilities.device).detach() * log_probabilities).mean()
    loss = loss - float(entropy_weight) * entropies.mean()
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    gradient_norm = torch.nn.utils.clip_grad_norm_(policy.parameters(), max_gradient_norm)
    optimizer.step()
    return TrainingMetrics(float(loss.detach()), float(rewards.mean()), float(gradient_norm))
```

Buffer attempts until all configured `rollouts_per_state` siblings for the same curriculum transition are available, then call `reinforce_step` once. Validate `rollouts_per_state >= 2` when generated analogy RL is enabled. Remove reward baseline save/load fields.

- [ ] **Step 4: Add real-model story diversity diagnostics**

Group decisions by `(problem_id, transition_id or state_id)`, normalize whitespace/case for exact distinct counts, embed unique stories once through the configured model, and compute mean upper-triangle cosine distance. Persist metrics per epoch and in `summary.json`; report zero rather than NaN when fewer than two stories are available, while separately reporting the count of eligible states.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `PYTHONPATH=src pytest tests/test_rl.py tests/test_grounded_training.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/analogy/rl.py src/analogy/training.py tests/test_rl.py tests/test_grounded_training.py
git commit -m "feat: center same-state rewards and measure story diversity"
```

### Task 6: Normalize ordinary Lean 3 `cases` output conservatively

**Files:**
- Modify: `src/analogy/model.py`
- Modify: `prompts.yaml`
- Test: `tests/test_model.py`

**Interfaces:**
- Produces: `normalize_lean4_tactic(text: str) -> str`
- Changes: `parse_tactic_candidates` normalizes before deduplication and stores normalized tactic text

- [ ] **Step 1: Write failing syntax-normalization tests**

```python
@pytest.mark.parametrize("source, expected", [
    ("cases h with hP hQ", "cases h"),
    (" cases   h   with   hP   hQ ", "cases h"),
    ("cases «h with x»", "cases «h with x»"),
    ("cases h with | inl hp => exact hp", "cases h with | inl hp => exact hp"),
])
def test_normalize_lean3_cases_only_when_standalone(source, expected):
    assert normalize_lean4_tactic(source) == expected
```

Also assert that normalized duplicates collapse before weight normalization.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `PYTHONPATH=src pytest tests/test_model.py -q`

Expected: FAIL because no normalizer exists.

- [ ] **Step 3: Implement the anchored rewrite and update the prompt example**

```python
_LEAN3_CASES = re.compile(
    r"^cases\s+(?P<subject>«[^»]+»|[A-Za-z_][\w'✝]*)\s+with\s+"
    r"(?P<left>[A-Za-z_][\w'✝]*)\s+(?P<right>[A-Za-z_][\w'✝]*)$"
)

def normalize_lean4_tactic(text: str) -> str:
    canonical = " ".join(text.split())
    match = _LEAN3_CASES.fullmatch(canonical)
    if match is None:
        return canonical
    return f"cases {match['subject']}"
```

Add explicit Lean 4 branch and standalone examples to the ordinary-ranking and capability templates in `prompts.yaml`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `PYTHONPATH=src pytest tests/test_model.py tests/test_lean_end_to_end.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/analogy/model.py prompts.yaml tests/test_model.py
git commit -m "fix: normalize standalone Lean 3 cases tactics"
```

### Task 7: Build the standalone matched story ablation

**Files:**
- Create: `scripts/benchmark_story_operator_ablation.py`
- Create: `tests/fixtures/story_operator_ambiguous.json`
- Create: `tests/test_story_operator_ablation.py`
- Modify: `prompts.yaml`
- Modify: `README.md`

**Interfaces:**
- Produces CLI: `python scripts/benchmark_story_operator_ablation.py --config PATH --fixtures PATH --output-dir PATH`
- Produces: `attempts.jsonl`, `summary.json`, and `report.md`
- Imports production inventory, neutral need, decision parser, enumeration, compiler, model backend, and Lean verifier
- Does not modify production runner selection or `experiment.stage`

- [ ] **Step 1: Write failing pairing, call-count, and reporting tests**

Use a fake generator that records prompt name, seed, delta fingerprint, start order, tokens, and latency. Assert each `(state, latent_sample)` receives exactly one `ablation_plain` and one `ablation_story` call; even-index states run plain first and odd-index states run story first; both arms use identical enabled operators and verifier behavior; the plain message contains the neutral description and operator enum but no inventory; output includes first-pick success and paired deltas.

- [ ] **Step 2: Run the script tests and verify RED**

Run: `PYTHONPATH=src pytest tests/test_story_operator_ablation.py -q`

Expected: FAIL because the script and fixtures do not exist.

- [ ] **Step 3: Add ambiguous Lean fixtures**

Create at least ten reconstructable theorem/prefix fixtures across the five ambiguity families in the spec. Each fixture contains exactly `id`, `binders`, `target`, and `accepted_prefix`; it contains no reference tactic or expected operator. Validate all prefixes through the pinned verifier before including them.

- [ ] **Step 4: Implement paired one-call arms in the script**

Define script-local arm adapters:

```python
def run_arm(name, catalog, generator, description, inventory, operators, action, seed):
    values = {
        "need_json": json.dumps(asdict(description), sort_keys=True),
        "operators_json": json.dumps([item.value for item in operators]),
    }
    if name == "story":
        values["inventory_json"] = render_model_inventory(inventory)
    messages = catalog.messages(
        f"ablation_{name}",
        **values,
    )
    raw = generator.generate_with_delta(messages, delta=action.delta.detach(), layer=-1, seed=seed)
    return parse_ablation_output(name, raw, inventory, operators)
```

The plain parser accepts only `operator`; the story parser reuses `StoryOperatorDecision`. Both feed the same production enumeration/compiler/verifier helper. Run crossed order by fixture index, capture tokenizer counts when the backend exposes them, and always capture UTF-8 character counts as a backend-neutral fallback.

- [ ] **Step 5: Write raw and summarized artifacts**

`summary.json` includes per-arm call count, first-pick successes, any-candidate successes, invalid outputs, zero applications, prompt/output tokens, model/Lean latency, candidate/Lean-call counts, order-stratified metrics, per-problem paired rows, and operator confusion matrices. `report.md` prints these side by side and flags any call-count mismatch or order effect.

- [ ] **Step 6: Run script tests and a fake-backend smoke benchmark**

Run: `PYTHONPATH=src pytest tests/test_story_operator_ablation.py -q`

Expected: PASS.

Run: `PYTHONPATH=src python scripts/benchmark_story_operator_ablation.py --config config.mlx-8b-grounded-analogy.yaml --fixtures tests/fixtures/story_operator_ambiguous.json --output-dir /tmp/story-operator-ablation --fake-backend`

Expected: exit 0; two calls per fixture/sample total, one per arm; three output artifacts.

- [ ] **Step 7: Commit**

```bash
git add scripts/benchmark_story_operator_ablation.py tests/fixtures/story_operator_ambiguous.json tests/test_story_operator_ablation.py prompts.yaml README.md
git commit -m "feat: add matched story operator ablation"
```

### Task 8: Remove obsolete staged code and update documentation/configuration

**Files:**
- Delete: unused staged transfer generator modules or classes identified by `rg`
- Modify: `src/analogy/__init__.py`
- Modify: `src/analogy/grounded_curriculum.py`
- Modify: `src/analogy/run_audit.py`
- Modify: `README.md`
- Modify: `docs/benchmarks/end-to-end-analogy-trace.md` only if its “current” wording conflicts; retain it as a historical artifact otherwise
- Test: `tests/test_dependencies.py`
- Test: `tests/test_grounded_curriculum.py`

**Interfaces:**
- Removes: `SourceAnalogyGenerator`, `AlignmentGenerator`, `ProofTransferGenerator`, `GroundedPlanGenerator`, their prompt versions, and model-generated grounded plans
- Preserves: offline curriculum reconstruction and historical artifact reading only where still needed by sealed runs

- [ ] **Step 1: Write a failing dependency-boundary test**

Assert production modules do not import obsolete staged classes/modules and current run metadata contains only the new decision/compiler identities. Test the actual import graph with `modulefinder` or AST imports rather than grepping source text.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `PYTHONPATH=src pytest tests/test_dependencies.py tests/test_grounded_curriculum.py -q`

Expected: FAIL while staged imports remain.

- [ ] **Step 3: Delete dead compatibility paths and repair imports**

Use `rg 'SourceAnalogy|AnalogyAlignment|ProofReasoningTransfer|GroundedTacticPlan|GroundedPlanGenerator' src tests` to enumerate consumers. Delete production-only staged code, update curriculum identity to the new prompt/operator schema, and leave historical JSON readers isolated from current execution. Do not add adapters that recreate old runtime behavior.

- [ ] **Step 4: Update user documentation**

Document the one-call path, explicit config list, exhaustive Lean behavior, centered sibling training, prompt catalog, and standalone ablation command. State that old generated-domain checkpoints and trace schemas are incompatible.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `PYTHONPATH=src pytest tests/test_dependencies.py tests/test_grounded_curriculum.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/analogy/__init__.py src/analogy/grounded_curriculum.py src/analogy/run_audit.py tests/test_dependencies.py tests/test_grounded_curriculum.py README.md config*.yaml docs/benchmarks/end-to-end-analogy-trace.md
git add -u src/analogy
git commit -m "refactor: remove staged analogy grounding compatibility"
```

### Task 9: Verify correctness and measure the speedup

**Files:**
- Create: `docs/benchmarks/2026-10-16-single-call-grounding-smoke.md`
- Modify only if verification reveals a defect: files owned by Tasks 1–8, with a new failing regression test first

**Interfaces:**
- Verifies the spec’s production and experiment contracts
- Produces a benchmark report with model/enumeration/compiler/Lean timing separated

- [ ] **Step 1: Run the complete test suite**

Run: `PYTHONPATH=src pytest -q`

Expected: all tests PASS with no warnings or errors. Record any pre-existing failure by exact test name before changing code.

- [ ] **Step 2: Run static prompt/config audits**

Run: `rg -n 'Message\([^,]+,\s*["'"']|Generate exactly|Return exactly|You produce correct|You are an observer' src/analogy`

Expected: no model-facing instructional prose in Python.

Run: `for f in config*.yaml; do PYTHONPATH=src python -c 'import sys; from pathlib import Path; from analogy.config import load_config; load_config(Path(sys.argv[1]))' "$f"; done`

Expected: every experiment config loads.

- [ ] **Step 3: Run real Lean integration tests**

Run: `PYTHONPATH=src pytest tests/test_lean_end_to_end.py tests/test_grounding.py tests/test_tactic_compiler.py -q`

Expected: PASS for representative configured operators.

- [ ] **Step 4: Run the timed fake-backend production benchmark**

Execute a fixed set of at least 100 reconstructed states through both the recorded old call-count model and the new runner. Measure total calls and wall time for model, enumeration, compilation, and Lean separately. Assert the new valid production attempt uses one model call and zero repairs. Do not combine Lean time with model-call speedup in the headline.

- [ ] **Step 5: Attempt a bounded real-Qwen smoke run**

Run: `PYTHONPATH=src python scripts/benchmark_story_operator_ablation.py --config config.mlx-8b-grounded-analogy.yaml --fixtures tests/fixtures/story_operator_ambiguous.json --output-dir runs-grounded/story-operator-ablation-smoke`

Expected when MLX model is locally available: both arms finish with exactly one call per arm/sample and emit story-diversity diagnostics. If unavailable, record the exact error and command without weakening fake-backend verification.

- [ ] **Step 6: Write the benchmark report**

Record commit SHA, hardware/backend, fixture count, prompt versions/hash, enabled operators, test commands, call-count reduction, latency breakdown, candidate/Lean-call counts, story diversity, and paired ablation results. Clearly separate verified facts from unavailable real-model measurements.

- [ ] **Step 7: Run final diff and suite checks**

Run: `git diff --check`

Expected: no output.

Run: `PYTHONPATH=src pytest -q`

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add docs/benchmarks/2026-10-16-single-call-grounding-smoke.md
git commit -m "test: verify single-call grounding speedup"
```
