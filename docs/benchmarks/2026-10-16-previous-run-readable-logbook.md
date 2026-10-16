# Previous training and inference: prompts, responses, and outcomes

This is a retrospective execution record of the saved training and inference
runs through October 16, 2026. It records what the model was told, what it
returned, what code derived from the response, and what Lean accepted. The
newest ablation responses are copied byte-for-byte from `attempts.jsonl`.
Rendered current prompts were reconstructed with the same committed prompt
catalog, fixture, proof-state adapter, and inventory renderer used by the run.

Three labels matter throughout this document:

- **Saved raw** means the exact text is present in a run artifact.
- **Reconstructed exact** means the role messages were deterministically
  rendered again from the committed code, configuration, and fixture. This is
  the text handed to the tokenizer, before Qwen's chat-template wrapper.
- **Parsed only** means the smoke command retained the validated fields but did
  not retain the original response bytes. Those fields must not be mistaken for
  a raw transcript.

## Current one-call prompt contract

The production call used prompt version `story-operator-v2`. Qwen thinking was
disabled. This system message is **reconstructed exact**:

```text
Choose one configured operation through a short everyday story. Return only strict JSON. The story must use ordinary objects and actions: never copy formulas, propositions, inventory IDs, proof terminology, tactic names, variable letters, or mathematical symbols into the story.
```

Every production user message had this exact layout:

```text
Neutral need: <need JSON>
Available items: <formula-free inventory JSON>
Allowed operators: ["exact_local", "introduce", "apply_implication", "iff_forward", "iff_backward", "rewrite_forward", "rewrite_backward", "split", "select_left", "select_right", "choose_left", "choose_right", "cases", "contradiction"]
Return exactly story, operator, and bindings. Only bindings may contain inventory IDs. bindings must be one JSON object mapping short everyday-role keys to listed inventory-ID values, for example {"key":"local:0"}.
```

The operation computed by need analysis was not included in these messages. It
was retained only as `expected_operator` in the evaluation record.

### Rendered prompt: `component_or_apply`

The system message is the production message above. The **reconstructed exact**
user message was:

```text
Neutral need: {"desired_focus_after": "the part matching the goal", "focus_before": "one available item made of two parts", "information_flow": ["select", "discard"], "input_arity": "two", "output_arity": "one", "proof_roles": ["active_subgoal", "structured_hypothesis"], "summary": "There is one item made of two parts, and the goal matches one part."}
Available items: [{"inventory_id":"local:0","kind":"local","structural_shape":{"children":[],"root":"opaque"}},{"inventory_id":"local:1","kind":"local","structural_shape":{"children":[],"root":"opaque"}},{"inventory_id":"local:2","kind":"local","structural_shape":{"children":[{"children":[],"root":"target"},{"children":[],"root":"atom"}],"root":"and"}},{"inventory_id":"local:3","kind":"local","structural_shape":{"children":[{"children":[],"root":"atom"},{"children":[],"root":"target"}],"root":"implication"}},{"inventory_id":"active_subgoal","kind":"active_subgoal","structural_shape":{"children":[],"root":"atom"}}]
Allowed operators: ["exact_local", "introduce", "apply_implication", "iff_forward", "iff_backward", "rewrite_forward", "rewrite_backward", "split", "select_left", "select_right", "choose_left", "choose_right", "cases", "contradiction"]
Return exactly story, operator, and bindings. Only bindings may contain inventory IDs. bindings must be one JSON object mapping short everyday-role keys to listed inventory-ID values, for example {"key":"local:0"}.
```

The production smoke retained this **parsed-only** decision:

```json
{
  "story": "A person finds a broken key made of two parts. They try to use one part to open a door, but it doesn't work. They then try the other part and it works.",
  "operator": "split",
  "bindings": {
    "key": "local:2",
    "working_part": "local:1",
    "non_working_part": "local:0"
  }
}
```

Code result: `split` had zero valid applications in this state. No candidate was
compiled and Lean was not called. The log-only expected operation was
`select_left`.

### Rendered prompt: `component_or_exact`

The system message, allowed-operator line, and output instruction were identical
to the preceding prompt. Its **reconstructed exact** variable payload was:

```text
Neutral need: {"desired_focus_after": "the need fulfilled", "focus_before": "a needed item and a matching available item", "information_flow": ["preserve"], "input_arity": "one", "output_arity": "one", "proof_roles": ["active_subgoal", "matching_item"], "summary": "An available item matches what is needed."}
Available items: [{"inventory_id":"local:0","kind":"local","structural_shape":{"children":[],"root":"opaque"}},{"inventory_id":"local:1","kind":"local","structural_shape":{"children":[],"root":"opaque"}},{"inventory_id":"local:2","kind":"local","structural_shape":{"children":[{"children":[],"root":"atom"},{"children":[],"root":"target"}],"root":"and"}},{"inventory_id":"local:3","kind":"local","structural_shape":{"children":[],"root":"target"}},{"inventory_id":"active_subgoal","kind":"active_subgoal","structural_shape":{"children":[],"root":"atom"}}]
```

The production smoke retained this **parsed-only** decision:

```json
{
  "story": "A person needed a tool to fix a broken chair. They found a hammer in the workshop. The hammer matched the needed tool.",
  "operator": "exact_local",
  "bindings": {"key": "local:3"}
}
```

Code grounded this to `exact hq`; Lean returned `complete`. The log-only expected
operation was `exact_local`.

### Rendered prompt: `equality_or_apply`

The **reconstructed exact** variable payload was:

```text
Neutral need: {"desired_focus_after": "its required input", "focus_before": "an available conditional item", "information_flow": ["preserve", "select"], "input_arity": "one", "output_arity": "one", "proof_roles": ["active_subgoal", "conditional_hypothesis"], "summary": "An available item can produce the goal once its input is supplied."}
Available items: [{"inventory_id":"local:0","kind":"local","structural_shape":{"children":[],"root":"opaque"}},{"inventory_id":"local:1","kind":"local","structural_shape":{"children":[],"root":"opaque"}},{"inventory_id":"local:2","kind":"local","structural_shape":{"children":[{"children":[],"root":"atom"},{"children":[],"root":"atom"},{"children":[],"root":"target"}],"root":"equality"}},{"inventory_id":"local:3","kind":"local","structural_shape":{"children":[],"root":"atom"}},{"inventory_id":"local:4","kind":"local","structural_shape":{"children":[{"children":[],"root":"atom"},{"children":[],"root":"target"}],"root":"implication"}},{"inventory_id":"active_subgoal","kind":"active_subgoal","structural_shape":{"children":[],"root":"atom"}}]
```

The production smoke retained this **parsed-only** decision:

```json
{
  "story": "A person finds a key that fits a lock. They use the key to open the lock and retrieve a hidden item.",
  "operator": "exact_local",
  "bindings": {
    "key": "local:2",
    "lock": "local:1",
    "hidden_item": "active_subgoal"
  }
}
```

Code found zero valid `exact_local` applications, so Lean was not called. The
log-only expected operation was `apply_implication`.

## Direct-versus-story ablation prompts and raw responses

The plain arm used prompt version `ablation-plain-v1`:

```text
SYSTEM
Choose one configured proof operation directly. Return only strict JSON.

USER
Neutral need: <the same need JSON shown above>
Allowed operators: <the same fixed operator list shown above>
Return exactly operator.
```

The story arm used `ablation-story-v2`:

```text
SYSTEM
Write a short everyday story, then choose one configured operation. Return only strict JSON. The story must use ordinary objects and actions: never copy formulas, propositions, inventory IDs, proof terminology, tactic names, variable letters, or mathematical symbols into the story.

USER
Neutral need: <the same need JSON shown above>
Available items: <the matching formula-free inventory shown above>
Allowed operators: <the same fixed operator list shown above>
Return exactly story, operator, and bindings. Only bindings may contain inventory IDs. bindings must be one JSON object mapping short everyday-role keys to listed inventory-ID values, for example {"key":"local:0"}.
```

The following are **saved raw** Qwen responses. Samples 0 and 1 were separate
calls with different latent deltas and seeds, but the response text was
identical within every fixture and arm.

### `component_or_apply`

Plain samples 0 and 1:

```json
{"operator": "select_left"}
```

Story samples 0 and 1:

```json
{
  "story": "A carpenter had a wooden plank and a nail. He needed to build a shelf, so he decided to use the plank as the base and the nail to secure it to the wall.",
  "operator": "select_left",
  "bindings": {
    "key": "local:2"
  }
}
```

Both arms grounded one candidate and both samples passed Lean.

### `component_or_exact`

Plain samples 0 and 1:

```json
{"operator": "exact_local"}
```

Story samples 0 and 1:

```json
{
  "story": "A person needed a specific item to complete a task. They found a matching item in their local storage, which was exactly what they required.",
  "operator": "exact_local",
  "bindings": {
    "key": "local:2"
  }
}
```

Both arms grounded one candidate and both samples passed Lean. Notice that the
story binding points to `local:2`, while deterministic grounding—not the model
binding—found the valid exact match. This is an important distinction when
interpreting these records.

### `equality_or_apply`

Plain samples 0 and 1:

```json
{"operator": "apply_implication"}
```

Story samples 0 and 1:

```json
{
  "story": "A person found a box with a red key inside. They used the key to open a locked drawer, revealing a small, glowing stone. The stone was the only item they needed to complete their task.",
  "operator": "exact_local",
  "bindings": {
    "key": "local:2"
  }
}
```

The plain choice generated two candidates and made two Lean calls per sample;
its first candidate succeeded. The story choice had zero valid applications and
made no Lean call. These paired calls are the concrete evidence behind the
reported 6/6 versus 4/6 result.

### Per-call ablation record

| Fixture | Arm | Sample / seed | Prompt tokens | Output tokens | Model seconds | Candidates | Lean calls | First pick |
|---|---|---:|---:|---:|---:|---:|---:|---|
| component_or_apply | plain | 0 / 0 | 182 | 7 | 2.185 | 1 | 1 | pass |
| component_or_apply | story | 0 / 0 | 405 | 69 | 9.329 | 1 | 1 | pass |
| component_or_apply | plain | 1 / 1 | 182 | 7 | 2.136 | 1 | 1 | pass |
| component_or_apply | story | 1 / 1 | 405 | 69 | 9.607 | 1 | 1 | pass |
| component_or_exact | story | 0 / 2 | 376 | 59 | 7.343 | 1 | 1 | pass |
| component_or_exact | plain | 0 / 2 | 168 | 7 | 1.895 | 1 | 1 | pass |
| component_or_exact | story | 1 / 3 | 376 | 59 | 7.445 | 1 | 1 | pass |
| component_or_exact | plain | 1 / 3 | 168 | 7 | 1.940 | 1 | 1 | pass |
| equality_or_apply | plain | 0 / 4 | 174 | 8 | 2.107 | 2 | 2 | pass |
| equality_or_apply | story | 0 / 4 | 426 | 72 | 10.084 | 0 | 0 | fail |
| equality_or_apply | plain | 1 / 5 | 174 | 8 | 2.776 | 2 | 2 | pass |
| equality_or_apply | story | 1 / 5 | 426 | 72 | 9.971 | 0 | 0 | fail |

Model: `mlx-community/Qwen3-8B-4bit`. Prompt catalog SHA-256:
`b4c4e782f7a69ce2011377e25134eed15780c9a12367fddc71e879a8715877da`.
The complete saved rows are in
`/tmp/analogy-real-qwen-20261016-v6/attempts.jsonl` on the machine that ran the
benchmark.

## Historical staged-model prompt/response example

The October 15 implementation used four model calls after ordinary search got
stuck. The following **saved raw** chain is from learned inference on
`imp_intro_05`. Full messages and outputs are in
[`calls.jsonl`](../../runs-fast/generated-domain-20261015/primary-256-gpu/calls.jsonl),
lines 449–452. Unlike the current pipeline, those prompts included the Lean
state, rejected tactics, and an explicit operation in the proof need.

### Call 1: source analogy

```text
SYSTEM
Generate a concrete non-proof source situation as strict JSON.

USER INPUTS
Prompt version: source-analogy-v1
Accepted tactics: apply h5, apply h3, apply h1
Current active subgoal: P
Fixed theorem target: U
Rejected tactics: apply h2, apply h4, apply h1, with full Lean diagnostics
Proof need operation: reuse
Proof need summary: Use an available item identical to the required item.
Required output: domain, before/after situation, source roles, relation chain,
transformation, and rationale as one strict JSON object.
```

Raw response:

```json
{
  "domain": "Home Cooking",
  "source_before": "A recipe requiring 2 cups of flour and 1 cup of sugar",
  "source_after": "A recipe requiring 2 cups of flour and 1 cup of sugar, with an additional note to use almond flour for the flour requirement",
  "source_roles": ["ingredient_specifier", "cooking_instruction"],
  "relation_chain": ["requirement", "specification", "adjustment"],
  "transformation": {
    "operation": "reuse",
    "input_arity": "one",
    "output_arity": "one",
    "permutation": "none",
    "information_flow": ["preserve"],
    "focus_before": "required item and matching available item",
    "focus_after": "requirement fulfilled",
    "domain_neutral_summary": "Use an available item identical to the required item."
  },
  "rationale": "The transformation maintains the original requirement but adds a specific adjustment, reusing the existing ingredient specification without altering its core function."
}
```

### Call 2: alignment

```text
SYSTEM
Align declared source and proof roles as strict JSON.

USER INPUTS
Prompt version: analogy-alignment-v1
The same trajectory observation and proof need
The complete source-analogy JSON from call 1
Required output: mapping, invariant, source_operation, proof_operation,
limitation, and confidence.
```

Raw response:

```json
{
  "mapping": {
    "ingredient_specifier": "active_subgoal",
    "cooking_instruction": "matching_hypothesis"
  },
  "invariant": "The required item (P) is directly fulfilled by an identical available item (P), maintaining the core function without adjustment.",
  "source_operation": "reuse",
  "proof_operation": "apply",
  "limitation": "The analogy assumes direct reuse, but the proof requires chaining multiple hypotheses to reach the target (U), which is not reflected in the source analogy.",
  "confidence": 0.8
}
```

### Call 3: proof-reasoning transfer

```text
SYSTEM
Translate the alignment into tactic-free proof reasoning as strict JSON.

USER INPUTS
Prompt version: proof-reasoning-transfer-v1
The trajectory observation, proof need, complete source analogy, and complete
alignment
Required output: active_subgoal, unique premises, ordered reasoning steps,
predicted_next_subgoal, and completion_expected; no Lean tactics.
```

Raw response:

```json
{
  "active_subgoal": "P",
  "premises": ["P", "P → Q", "Q → T", "T → U"],
  "reasoning_steps": [
    "The active subgoal is P, which is directly given as a premise.",
    "The goal is to derive U, and there is a chain of implications from P to U.",
    "Since P is already available, it can be used to derive Q via P → Q.",
    "From Q, we can derive T using Q → T.",
    "Finally, from T, we can derive U using T → U."
  ],
  "predicted_next_subgoal": "Q",
  "completion_expected": false
}
```

### Call 4: tactic decoding

```text
SYSTEM
Decode the cited proof reasoning transfer into Lean 4 tactics as strict JSON.

USER INPUTS
Prompt version: transferred-tactic-v1
The full current Lean state and structural signature
The complete source analogy, alignment, and transferred reasoning
Required output: predicted_state, exactly four distinct single-line Lean 4
tactics, and the motivating transfer ID.
```

Raw response:

```json
{
  "predicted_state": "P",
  "tactics": ["exact p", "apply h1", "apply h2", "apply h3"],
  "motivating_transfer_id": "transfer-0"
}
```

Lean tried the first decoded candidate, `exact p`, and completed the theorem.
This example also makes the speed motivation for the current design concrete:
the old analogy path took four model calls after ordinary search; the new path
asks for story, operator, and bindings in one call, then grounds and compiles in
code.

## Historical failed response example

Not all valid-looking JSON passed schema validation. This **saved raw** example
is the learned-direct call for `iff_intro_05`, recorded at line 121 of the same
`calls.jsonl`.

```text
SYSTEM
Predict the immediate next proof state and propose bounded Lean 4 rescue tactics.

USER
Overall target:
¬((A → C ∨ ¬L) ∧ (L ∨ A → ¬C) → (L → C)) ↔ A ∧ C ∧ ¬L

Current goals:
A C L P : Prop
h1 : L ↔ P
h2 : ¬((A → C ∨ ¬P) ∧ (P ∨ A → ¬C) → P → C) ↔ A ∧ C ∧ ¬P
⊢ ¬((A → C ∨ ¬L) ∧ (L ∨ A → ¬C) → L → C) ↔ A ∧ C ∧ ¬L

Accepted prefix:
(none)

Failed tactics:
apply h1
unfold h2
apply iff.intro

Return one JSON object with exactly the keys "predicted_state" and "tactics". predicted_state must describe the expected next Lean goals; tactics must be a non-empty list of single-line Lean tactics.
```

Raw response:

```json
{
  "predicted_state": "The goal is to prove the biconditional statement involving the negation of an implication and a conjunction of propositions. The current state likely involves simplifying the left-hand side of the biconditional using the equivalence h1 (L ↔ P) and the given h2, which provides a similar structure with P instead of L.",
  "tactics": [
    "unfold h1 at h2",
    "apply iff.intro",
    "assume h : ¬((A → C ∨ ¬L) ∧ (L ∨ A → ¬C) → L → C)",
    "apply h2",
    "assume h : A ∧ C ∧ ¬L",
    "apply h2"
  ]
}
```

The parser rejected it before Lean because `apply h2` appeared twice after
canonicalization. It also demonstrates the Lean 3-style `assume` syntax that
motivated the later Lean 4 prompt examples and rewrite compatibility fix.

## What the training call actually optimized

The historical training run used the same four-stage prompt chain. The saved
training calls for the representative successful `imp_intro_05` episode are
lines 31–34 of the same `calls.jsonl`; their raw contents match the four response
shapes above. The latent delta was applied only to source-analogy generation;
alignment, transfer, and tactic decoding were downstream validation calls.

The training update consumed the rollout reward after Lean execution. It did
not train on a textual “correct answer” response. In the newer training code,
the advantage used for the update is the attempt reward minus the mean reward
of matched attempts on that same problem. Consequently, a uniformly easy or
uniformly failed problem contributes little or no relative learning signal.

The remainder of this document keeps the concise run-level results so the exact
prompt/response records above can be interpreted in context.

The main conclusion is simple: the current one-call implementation executes end
to end, but the story-mediated choice did not beat direct operator selection in
the bounded real-Qwen comparison. Earlier training runs established integration
and exposed useful failure modes, but none demonstrated a learned held-out
advantage.

## October 16 current one-call runs

### Real-Qwen production inference

Configuration: `mlx-community/Qwen3-8B-4bit`, Metal, one model call per state,
thinking disabled, `story-operator-v2`, deterministic grounding, and real Lean.

#### Problem 1 of 3: component or apply

```text
Expected operation: select_left
Story: A person finds a broken key made of two parts. They try to use one part
       to open a door, but it does not work. They then try the other part and
       it works.
Model operation: split
Grounding: 0 valid applications
Compiled candidates: 0
Lean checks: 0
Result: not solved
Reason: split cannot be applied to this current proof state
Model time: 7.763 seconds
```

#### Problem 2 of 3: component or exact

```text
Expected operation: exact_local
Story: A person needed a tool to fix a broken chair. They found a hammer in the
       workshop. The hammer matched the needed tool.
Model operation: exact_local
Grounding: 1 valid application
Candidate: exact hq
Lean: exact hq -> complete
Result: SOLVED
Model time: 5.227 seconds
```

#### Problem 3 of 3: equality or apply

```text
Expected operation: apply_implication
Story: A person finds a key that fits a lock. They use the key to open the lock
       and retrieve a hidden item.
Model operation: exact_local
Grounding: 0 valid applications
Compiled candidates: 0
Lean checks: 0
Result: not solved
Reason: no local item exactly matches the goal
Model time: 6.574 seconds
```

#### Production result

```text
Problems: 3
Model calls: 3
Valid model outputs: 3
Repair calls: 0
Grounded candidates: 1
Lean checks: 1
Solved: 1 of 3
```

The two failed choices stopped before Lean. Deterministic grounding did not
invent a tactic for an inapplicable operation.

### Real-Qwen direct choice versus story-mediated choice

Each of three fixtures was run twice with matched latent nudges. Both arms made
six model calls.

#### component or apply

```text
Direct choice, samples 1 and 2
Operation: select_left
Result: 2 of 2 successful

Story choice, samples 1 and 2
Story: A carpenter had a wooden plank and a nail. He needed to build a shelf,
       so he decided to use the plank as the base and the nail to secure it to
       the wall.
Operation: select_left
Result: 2 of 2 successful
Story diversity: the two stories were identical
```

#### component or exact

```text
Direct choice, samples 1 and 2
Operation: exact_local
Result: 2 of 2 successful

Story choice, samples 1 and 2
Story: A person needed a specific item to complete a task. They found a matching
       item in their local storage, which was exactly what they required.
Operation: exact_local
Result: 2 of 2 successful
Story diversity: the two stories were identical
```

#### equality or apply

```text
Direct choice, samples 1 and 2
Operation: apply_implication
Result: 2 of 2 successful

Story choice, samples 1 and 2
Story: A person found a box with a red key inside. They used the key to open a
       locked drawer, revealing a small, glowing stone. The stone was the only
       item they needed to complete their task.
Operation: exact_local
Grounding: 0 valid applications in both samples
Result: 0 of 2 successful
Story diversity: the two stories were identical
```

#### Ablation result

| Measure | Direct choice | Story-mediated choice |
|---|---:|---:|
| Successful first picks | 6 / 6 | 4 / 6 |
| Successful candidate sets | 6 / 6 | 4 / 6 |
| Invalid model outputs | 0 | 0 |
| Zero-application choices | 0 | 2 |
| Prompt tokens | 1,048 | 2,414 |
| Output tokens | 44 | 400 |
| Model time | 13.039 s | 53.779 s |
| Lean calls | 8 | 4 |

```text
Story/direct prompt-token ratio: 2.30x
Story/direct output-token ratio: 9.09x
Story/direct model-time ratio: 4.12x
Mean unique stories per fixture: 1.0
Fixtures with multiple stories: 0 of 3
```

Source: [single-call grounding smoke](2026-10-16-single-call-grounding-smoke.md).

## October 15 historical training and inference

These runs used older staged or legacy paths. They remain useful diagnostic
evidence, but their checkpoints and trace schemas are intentionally incompatible
with the current one-call implementation.

### Analogy repair training

```text
TRAINING RUN
Epochs: 1
Training implication problems solved: 1 of 2
Validation problems solved: 0 of 2

Learning update 1
Mean reward: -0.100
Loss: +1963.697
Gradient norm: 4291.837

Learning update 2
Mean reward: +1.000
Loss: -21367.852
Gradient norm: 47434.973

Checkpoint: epoch-0001.pt
TRAINING COMPLETE
```

### Analogy repair matched inference

| Condition | Solved | Lean checks | What happened |
|---|---:|---:|---|
| Conventional | 0 / 2 | 12 | No rescue path |
| Learned direct | 0 / 2 | 13 | No accepted rescue tactic |
| Analogy loop | 0 / 2 | 28 | Some verified progress, no completion |
| Shuffled analogy | 0 / 2 | 27 | Similar progress to the unshuffled analogy |

```text
Total wall time: 635 seconds
Training episodes: 4
Evaluation episodes: 8
Episode-reported Lean checks: 117
Generation attempts: 27
Analogy iterations: 9
Final held-out result: 0 of 2 in every condition
```

The analogy and shuffled conditions both produced a verified successor, so this
run crossed an earlier mechanical bottleneck. Because shuffled analogy improved
similarly, it did not establish that meaningful analogy selection caused the
progress. Full source: [analogy repair smoke](2026-10-15-analogy-repair-ablation.md).

### Generated-domain training variants

#### Primary 256-token training

```text
Epochs: 1
Training implications solved: 1 of 2
Validation negations solved: 0 of 1

Learning update 1: mean reward -0.050; loss +991.531; gradient norm 2358.026
Learning update 2: mean reward +1.000; loss -20394.598; gradient norm 50088.668
Checkpoint: epoch-0001.pt
```

#### Repaired 256-token training

```text
Epochs: 1
Training implications solved: 0 of 2
Validation negations solved: 0 of 1

Learning update 1: mean reward -0.050; loss +991.531; gradient norm 2358.026
Learning update 2: mean reward -0.050; loss +19.414; gradient norm 0.108
Main failure: outputs repeatedly reached or approached the token cap before a
              usable staged result was produced
```

#### Repaired 512-token training

```text
Epochs: 1
Training implications solved: 0 of 2
Validation negations solved: 0 of 1
Token-cap bottleneck: removed in the observed outputs
Remaining failures: source-language constraints, duplicate tactics, and one
                    structure-extraction timeout
```

#### Held-out inference

```text
Held-out problems: 2 iff theorems
Primary run: 0 of 2 solved in every condition
Repaired 256 run: 0 of 2 solved in every condition
Repaired 512 run: 0 of 2 solved in every condition
Novel open rescue successors: 0
Second analogies after verified progress: 0
```

The primary all-five diagnostic solved one in-sample implication with `exact p`,
but that was not held-out evidence. Full source and linked per-theorem traces:
[generated-domain analogy smoke](2026-10-15-generated-domain-analogy-smoke.md).

### Grounded transition training

#### Default retained-state run

```text
Training states solved conventionally: 2 of 5
Validation states solved: 0 of 1
Policy updates: 0
Grounded held-out model calls: censored by generation timeouts
Real grounded compiler candidates reaching Lean: 0
```

#### Curated bounded run

```text
Training states solved conventionally: 2 of 2
Validation states solved: 1 of 2
Policy updates: 0
Held-out states: 2
Matched inference conditions: 8
Solved: 0 of 2 in every condition
Direct rescue: one verified novel open successor
Generated analogy conditions: zero verified novel open successors
Second analogies: 0
```

The real-model grounded generations repeatedly timed out before validated plans
could reach the compiler. This measured a latency/schema-throughput limit, not a
0% compiler acceptance rate. Full source: [grounded transition smoke](2026-10-15-grounded-transition-smoke.md).

## Earlier direct-rescue pilot

```text
TRAINING RUN
Epochs: 1
Learning updates: 3
Mean rewards: +1.000, 0.000, 0.000
Checkpoint: epoch-0001.pt

INFERENCE RUN
Held-out problems: 1
Conventional: 0 of 1 solved; 4 Lean checks
Unperturbed direct rescue: 0 of 1 solved; 4 Lean checks
Random direct rescue: 0 of 1 solved; 4 Lean checks
Learned direct rescue: 0 of 1 solved; 4 Lean checks
```

Artifacts: [`runs/rescue-benchmark-pilot/rescue`](../../runs/rescue-benchmark-pilot/rescue).

## Where to inspect full historical traces

- [Primary generated-domain training traces](../../runs-fast/generated-domain-20261015/primary-256-gpu/rescue/train/readable_traces.md)
- [Primary generated-domain matched inference traces](../../runs-fast/generated-domain-20261015/primary-256-gpu/rescue/matched-five-diagnostic/readable_traces.md)
- [Repaired 256-token training traces](../../runs-fast/generated-domain-20261015/repaired-256-gpu/rescue/train/readable_traces.md)
- [Repaired 256-token held-out traces](../../runs-fast/generated-domain-20261015/repaired-256-gpu/rescue/matched-heldout-retry/readable_traces.md)
- [Repaired 512-token training traces](../../runs-fast/generated-domain-20261015/repaired-512-gpu/rescue/train/readable_traces.md)
- [Repaired 512-token held-out traces](../../runs-fast/generated-domain-20261015/repaired-512-gpu/rescue/matched-heldout-retry/readable_traces.md)
- [Separate timeout diagnostic trace](../../runs-fast/generated-domain-20261015/repaired-512-timeout-diagnostic/rescue/timeout-diagnostic/readable_traces.md)

Future training and `evaluate_rescue` runs write the same plain-English live
output directly to `run.log`; this document is the one-time retrospective bridge
for runs completed before that feature existed.
