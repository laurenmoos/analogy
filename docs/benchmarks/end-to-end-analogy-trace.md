# End-to-end analogy trace: training and inference

This document follows one proof all the way through the analogy pipeline. It shows what the model sees, what it returns, what deterministic code does next, and where learning happens.

> **Evidence labels**
>
> - **RECORDED** means copied from a saved run artifact.
> - **RECONSTRUCTED PROMPT** means rebuilt from the saved payload with the exact checked-in prompt renderer. The fake backend saved outputs but not prompt strings.
> - **EXPLANATION** is commentary, not model output.

The important separation is:

```text
fixed theorem target
    ↓
current Lean proof state ──→ inferred proof need
    ↓                         ↓
ordinary tactics fail     source-domain analogy  ← RL latent action (training only)
                              ↓
                         role alignment
                              ↓
                      tactic-free proof reasoning
                              ↓
                    verified local proof inventory
                              ↓
                       typed grounded plan
                              ↓
                    deterministic tactic compiler
                              ↓
                         Lean verification
                              ↓
                 new proof state or complete proof
```

The analogy does **not** directly choose a tactic. It proposes a line of reasoning. The grounding stage binds that reasoning to declarations actually present in the current Lean state; the compiler turns only those typed bindings into tactic candidates; Lean is the final judge.

## Prompt contracts used in both traces

Every model call has a short system message and a versioned user prompt.

| Stage | System message | Required output |
|---|---|---|
| Source | `Generate a concrete non-proof source situation as strict JSON.` | A concrete before/after situation, roles, relation chain, and domain-neutral transformation. No Lean syntax or tactic names. |
| Alignment | `Align declared source and proof roles as strict JSON.` | A mapping from source roles to declared proof roles, shared invariant, corresponding operations, limitation, and confidence. |
| Transfer | `Translate the alignment into tactic-free proof reasoning as strict JSON.` | Active subgoal, consumed premises, ordered reasoning steps, predicted next subgoal, and whether completion is expected. No tactics. |
| Grounding | `Bind transferred reasoning to the exact current inventory as strict typed JSON.` | Exact inventory bindings and a typed acyclic inference plan using the allowed operators. No raw Lean. |

The full grounding contract requires exact state fingerprints and inventory IDs, exact proposition text, backward-only step references, and one of these operators:

```text
exact_local, introduce, apply_implication, iff_forward, iff_backward,
rewrite_forward, rewrite_backward, split, select_left, select_right,
choose_left, choose_right, cases, contradiction, compose
```

The compiler accepts only this typed plan. Natural-language surface hints are diagnostic and never executable.

---

## Training trace: a complete two-iteration episode

**RECORDED.** This is the deterministic fake-backend integration run. Its point is to expose the complete data and gradient path, not to estimate model quality.

### 0. Fixed target and initial observation

The theorem target never changes:

```lean
theorem current_problem (P : Prop) (h : P) : P → P := by
  ?_
```

Lean's current state is the part tactics transform:

```text
[State 0]
local context:
  h : P
active subgoal:
  ⊢ P → P
```

The ordinary candidate `ordinary_bad` is rejected, so analogy rescue starts.

### 1. Iteration 0: turn an implication into a local assumption

#### 1a. Controller observation and inferred need

**RECORDED.** The observation contains state-over-state information, not just the goal:

```json
{
  "fixed_target": "P → P",
  "current_state": "h : P\n⊢ P → P",
  "previous_state": null,
  "accepted_tactics": [],
  "rejected_tactics": [["ordinary_bad", ["rejected"]]],
  "predicted_transition": null,
  "actual_transition": null,
  "iteration_index": 0,
  "remaining_model_calls": 8,
  "remaining_lean_calls": 5,
  "repeated_state": false,
  "surprise": null
}
```

The structural analyzer infers:

```json
{
  "active_subgoal": "P → P",
  "operation": "introduce",
  "input_arity": "one",
  "output_arity": "one",
  "information_flow": ["add"],
  "focus_before": "conditional or quantified requirement",
  "desired_focus_after": "local item and remaining requirement",
  "proof_roles": ["active_subgoal", "introduced_assumption"],
  "summary": "Expose a local input and continue with the remaining requirement."
}
```

#### 1b. Source analogy prompt → output

**RECONSTRUCTED PROMPT.** The user prompt is `source-analogy-v2`, with the full observation and need above, followed by:

```text
Generate a concrete source domain outside formal proof, with a distinct before/after
transition that exhibits the needed transformation. Do not restate the proof. Use
specific physical or everyday roles. Never copy proposition expressions, proof
identifiers, Lean syntax, or tactic names into the source text. Every concrete-source
field, including rationale, must avoid the words proof, Lean, or tactic. Explain the
fit using only domain-neutral transformation language and concrete source roles.
Return exactly one JSON object with domain, source_before, source_after, source_roles,
relation_chain, transformation, and rationale. transformation must contain exactly
operation, input_arity, output_arity, permutation, information_flow, focus_before,
focus_after, and domain_neutral_summary.
```

During training only, the sampled policy action injects a latent delta into this source-generation call:

```json
{
  "seed": 0,
  "delta_norm": 0.1528288871049881,
  "log_probability": -0.22882080078125,
  "entropy": -1.8751246929168701
}
```

**RECORDED model output:**

```json
{
  "domain": "warehouse logistics",
  "source_before": "A worker needs a spare wheel; a matching wheel is on the shelf.",
  "source_after": "The worker takes the matching wheel from the shelf.",
  "source_roles": ["requested wheel", "shelved wheel"],
  "relation_chain": [
    "The shelved wheel matches the request.",
    "Taking it fulfills the request."
  ],
  "transformation": {
    "operation": "introduce",
    "input_arity": "one",
    "output_arity": "one",
    "permutation": "none",
    "information_flow": ["preserve"],
    "focus_before": "required item",
    "focus_after": "available item",
    "domain_neutral_summary": "Use the available matching resource."
  },
  "rationale": "An available matching resource meets the outstanding need."
}
```

#### 1c. Alignment prompt → output

**RECONSTRUCTED PROMPT.** `analogy-alignment-v1` receives the same observation and proof need plus the source analogy. Its core task contract is:

```text
Align the two distinct systems using only their declared roles. Return exactly one
JSON object with mapping, invariant, source_operation, proof_operation, limitation,
and confidence. mapping is an object of two to six source-role to proof-role pairs;
every key must be a source_roles entry and every value a proof_roles entry. Cite
active_subgoal and at least one relevant hypothesis or constructor role. Explain the
shared relation, the corresponding operations, and a mismatch or limitation.
confidence is a finite number in [0, 1] for diagnostics only.
```

**RECORDED model output:**

```json
{
  "mapping": {
    "requested wheel": "active_subgoal",
    "shelved wheel": "introduced_assumption"
  },
  "invariant": "Available resource meets the need.",
  "source_operation": "Take the wheel.",
  "proof_operation": "Use the matching premise.",
  "limitation": "Physical movement is incidental.",
  "confidence": 0.75
}
```

#### 1d. Transfer prompt → output

**RECONSTRUCTED PROMPT.** `proof-reasoning-transfer-v1` receives observation + need + source + alignment:

```text
Translate the aligned operation into tactic-free proof reasoning about the current
active subgoal. Return exactly one JSON object with active_subgoal, premises,
reasoning_steps, predicted_next_subgoal, and completion_expected. Copy active_subgoal
from the proof need exactly. Explain which premises are consumed and the expected
next obligation or completion condition. Never emit tactics or Lean programs.
```

**RECORDED model output:**

```json
{
  "active_subgoal": "P → P",
  "premises": [],
  "reasoning_steps": [
    "The matching premise establishes the active requirement."
  ],
  "predicted_next_subgoal": "P",
  "completion_expected": false
}
```

This is not yet executable. It predicts the structural move: expose the implication's input, leaving `P`.

#### 1e. Inventory + grounding prompt → output

**RECORDED inventory:**

```json
{
  "state_fingerprint": "67a76fce4fa937c856a70c065a23f795d21db6c6eb6158ad27febe0bfa5bb8e0",
  "fixed_target": "P → P",
  "active_subgoal": "P → P",
  "locals": [
    {"inventory_id": "local:0", "identifier": "h", "proposition": "P"}
  ],
  "target_entries": [
    {"inventory_id": "active_subgoal", "proposition": "P → P"},
    {"inventory_id": "target:0", "proposition": "P"},
    {"inventory_id": "target:1", "proposition": "P"}
  ]
}
```

**RECONSTRUCTED PROMPT.** `grounding-v1` supplies that exact inventory and the transfer, then requires exact IDs and proposition strings, typed steps, no cycles, no guessed identifiers, and no raw Lean.

**RECORDED model output:**

```json
{
  "motivating_transfer_id": "transfer-0",
  "state_fingerprint": "67a76fce4fa937c856a70c065a23f795d21db6c6eb6158ad27febe0bfa5bb8e0",
  "bindings": [],
  "steps": [{
    "step_id": "step-0",
    "operator": "introduce",
    "inputs": [],
    "input_propositions": [],
    "output_proposition": "P",
    "transfer_step_index": 0,
    "closes_subgoal": false
  }],
  "expected_next_subgoal": "P",
  "completion_expected": false,
  "surface_hints": []
}
```

#### 1f. Compiler → Lean → next observation

**RECORDED deterministic compilation:**

```text
introduce()  ──compiler──>  intro analogy_h
```

**RECORDED Lean result:** accepted.

```text
[State 0]
h : P
⊢ P → P
    │
    └─ intro analogy_h
       ▼
[State 1]
h : P
analogy_h : P
⊢ P
```

The fixed theorem target remains `P → P`; only the active subgoal changed. A conventional finisher, `assumption`, is deliberately rejected by this fake fixture, forcing a second analogy iteration.

### 2. Iteration 1: reuse a matching local fact

#### 2a. New observation and need

**RECORDED.** The next observation carries the prior analogy, predicted state, actual state, accepted tactic, and rejection:

```json
{
  "fixed_target": "P → P",
  "previous_state": "h : P\n⊢ P → P",
  "current_state": "h : P\nanalogy_h : P\n⊢ P",
  "accepted_tactics": ["intro analogy_h"],
  "rejected_tactics": [["assumption", ["rejected"]]],
  "predicted_transition": "P",
  "actual_transition": "h : P\nanalogy_h : P\n⊢ P",
  "iteration_index": 1,
  "surprise": 0.0
}
```

The newly inferred need is:

```json
{
  "active_subgoal": "P",
  "operation": "reuse",
  "input_arity": "one",
  "output_arity": "one",
  "information_flow": ["preserve"],
  "focus_before": "required item and matching available item",
  "desired_focus_after": "requirement fulfilled",
  "proof_roles": ["active_subgoal", "matching_hypothesis"],
  "summary": "Use an available item identical to the required item."
}
```

#### 2b. Source → alignment → transfer

The same four prompt contracts run again with the new observation and need. The second RL source action is:

```json
{
  "seed": 1,
  "delta_norm": 0.20150747895240784,
  "log_probability": 3.17838716506958,
  "entropy": -1.934606671333313
}
```

**RECORDED source output:** the warehouse scene is retained, but its formal transformation is now `reuse`.

```json
{
  "domain": "warehouse logistics",
  "source_before": "A worker needs a spare wheel; a matching wheel is on the shelf.",
  "source_after": "The worker takes the matching wheel from the shelf.",
  "source_roles": ["requested wheel", "shelved wheel"],
  "relation_chain": [
    "The shelved wheel matches the request.",
    "Taking it fulfills the request."
  ],
  "transformation": {
    "operation": "reuse",
    "input_arity": "one",
    "output_arity": "one",
    "permutation": "none",
    "information_flow": ["preserve"],
    "focus_before": "required item",
    "focus_after": "available item",
    "domain_neutral_summary": "Use the available matching resource."
  }
}
```

**RECORDED alignment output:**

```json
{
  "mapping": {
    "requested wheel": "active_subgoal",
    "shelved wheel": "matching_hypothesis"
  },
  "invariant": "Available resource meets the need.",
  "source_operation": "Take the wheel.",
  "proof_operation": "Use the matching premise.",
  "limitation": "Physical movement is incidental.",
  "confidence": 0.75
}
```

**RECORDED transfer output:**

```json
{
  "active_subgoal": "P",
  "premises": ["matching_hypothesis"],
  "reasoning_steps": [
    "The matching premise establishes the active requirement."
  ],
  "predicted_next_subgoal": "No remaining goals.",
  "completion_expected": true
}
```

#### 2c. Grounding → compilation → completion

**RECORDED inventory:**

```text
local:0 → h : P
local:1 → analogy_h : P
active_subgoal → P
```

**RECORDED grounded plan:**

```json
{
  "bindings": [{
    "binding_id": "binding-0",
    "proof_role": "matching_hypothesis",
    "inventory_id": "local:0",
    "proposition": "P",
    "justification": "Available premise."
  }],
  "steps": [{
    "step_id": "step-0",
    "operator": "exact_local",
    "inputs": ["binding-0"],
    "input_propositions": ["P"],
    "output_proposition": "P",
    "transfer_step_index": 0,
    "closes_subgoal": true
  }],
  "expected_next_subgoal": "No remaining goals.",
  "completion_expected": true
}
```

**RECORDED deterministic compilation and Lean result:**

```text
exact_local(binding-0)  ──compiler──>  exact h  ──Lean──>  COMPLETE
```

The final verified proof is:

```lean
theorem current_problem (P : Prop) (h : P) : P → P := by
  intro analogy_h
  exact h
```

### 3. How the terminal reward reaches both analogy choices

**RECORDED outcome:** Lean completion gives the trajectory a verified reward of `1.0`.

The configured return rule is:

```text
return[i] = discount^(remaining steps) × verified_reward
            − iteration_cost × remaining steps
```

With `discount = 0.95`, `iteration_cost = 0.01`, and two analogy actions:

```text
iteration 0: 0.95 × 1.0 − 0.01 = 0.94
iteration 1: 1.00 × 1.0 − 0.00 = 1.00
mean return: (0.94 + 1.00) / 2 = 0.97
```

**RECORDED optimizer metrics:**

```json
{
  "loss": -0.049203209578990936,
  "mean_reward": 0.9700000286102295,
  "gradient_norm": 1.203385829925537
}
```

**EXPLANATION.** The Qwen weights do not change. REINFORCE updates the small latent policy using each sampled source action's log-probability and its propagated return. The source analogy is therefore the learned exploratory action; alignment, transfer, grounding, compilation, and Lean verification determine whether that action was useful.

---

## Inference trace: real Qwen3-8B on a held-out state

**RECORDED.** Model: `mlx-community/Qwen3-8B-4bit`. Condition: `generated_unsteered/grounded`. Problem: `and_intro_05`.

This is a real failure trace. It is especially useful because the analogy itself is sensible; the run fails later at the language-to-typed-plan boundary.

### 0. Fixed target, state, and failed conventional proposals

```text
THEOREM TARGET (fixed): Q

[State 0]
local context:
  P Q : Prop
  h : P ∧ Q
active subgoal:
  ⊢ Q
```

The conventional generator proposed:

```json
[
  {"tactic": "cases h with hP hQ", "score": 9},
  {"tactic": "apply h", "score": 6},
  {"tactic": "apply h.left", "score": 5}
]
```

Lean rejected all three: the first used invalid Lean 4 syntax, `h` has type `P ∧ Q` rather than `Q`, and `h.left` has type `P` rather than `Q`.

The inferred proof need was:

```json
{
  "active_subgoal": "Q",
  "operation": "select",
  "input_arity": "two",
  "output_arity": "one",
  "information_flow": ["select", "discard"],
  "focus_before": "available structured item",
  "desired_focus_after": "required contained component",
  "proof_roles": ["active_subgoal", "structured_hypothesis"],
  "summary": "Extract the required component from an available structured item."
}
```

### 1. Source analogy call

**RECORDED exact call metadata:** `source-analogy-v2`, seed `0`, latent delta all zeros, `34.69 s`.

The prompt contains the complete current observation—including the three rejected tactics—and the proof need above, then the source contract shown earlier.

**RECORDED Qwen output:**

```json
{
  "domain": "Kitchen Cooking",
  "source_before": "A recipe requires both flour and sugar, and the baker has a bag of flour and a box of sugar.",
  "source_after": "The baker uses the sugar for the recipe, as the flour is not needed for this particular step.",
  "source_roles": ["available_ingredient", "required_ingredient"],
  "relation_chain": ["select", "discard"],
  "transformation": {
    "operation": "select",
    "input_arity": "two",
    "output_arity": "one",
    "permutation": "none",
    "information_flow": ["select", "discard"],
    "focus_before": "available_ingredient",
    "focus_after": "required_ingredient",
    "domain_neutral_summary": "Extract the required component from an available structured item."
  },
  "rationale": "The transformation involves identifying and using the necessary item (sugar) from a set of available items (flour and sugar), discarding the unnecessary one (flour) in the process."
}
```

The useful analogy is: **a conjunction is like a stocked set of ingredients; select the required component and ignore the other one.**

### 2. Alignment call

**RECORDED exact call metadata:** `analogy-alignment-v1`, seed `10`, `39.07 s`.

The prompt includes the complete observation, proof need, source object, and alignment contract. Qwen returned:

```json
{
  "mapping": {
    "available_ingredient": "structured_hypothesis",
    "required_ingredient": "active_subgoal"
  },
  "invariant": "The operation involves selecting a specific component from a structured item while discarding the rest, maintaining the focus on the required component.",
  "source_operation": "select",
  "proof_operation": "apply",
  "limitation": "The proof system's `apply` tactic may not directly map to the `select` operation in the analogy due to the need for explicit hypothesis selection in the proof context.",
  "confidence": 0.8
}
```

### 3. Tactic-free transfer call

**RECORDED exact call metadata:** `proof-reasoning-transfer-v1`, seed `20`, `31.72 s`.

The prompt includes observation + need + source + alignment and asks for no tactics. Qwen returned:

```json
{
  "active_subgoal": "Q",
  "premises": ["P ∧ Q"],
  "reasoning_steps": [
    "The current goal is to derive Q from the hypothesis P ∧ Q.",
    "The structured hypothesis P ∧ Q contains two components: P and Q.",
    "The required component Q is present in the structured hypothesis.",
    "The operation involves selecting the required component Q from the structured hypothesis P ∧ Q.",
    "After selecting Q, the next obligation would be to confirm that Q is indeed derived from the hypothesis."
  ],
  "predicted_next_subgoal": "Confirm that Q is derived from the hypothesis P ∧ Q.",
  "completion_expected": false
}
```

This is directionally right but contains the first semantic mistake: selecting `Q` from `P ∧ Q` should complete this proof, not create a prose-shaped follow-up subgoal.

### 4. Grounding call and actual stopping point

**RECORDED inventory supplied to Qwen:**

```json
{
  "active_subgoal": "Q",
  "fixed_target": "Q",
  "state_fingerprint": "1e5445180b628728a537480ab5be943311465f94d7d0bfa949c10e34e0f31800",
  "locals": [
    {"inventory_id": "local:0", "identifier": "P", "proposition": "Prop"},
    {"inventory_id": "local:1", "identifier": "Q", "proposition": "Prop"},
    {"inventory_id": "local:2", "identifier": "h", "proposition": "P ∧ Q"}
  ],
  "target_entries": [
    {"inventory_id": "active_subgoal", "proposition": "Q"}
  ]
}
```

**RECORDED exact call metadata:** `grounding-v1`, seed `40`. The prompt included the full inventory, the transfer object, the exact-ID rules, and the allowed typed operators.

```text
model wall time: 65.46 s
configured generation deadline: 45 s
recorded result: TimeoutError("generation exceeded 45 seconds")
raw output: empty
```

Therefore:

```text
source analogy       VALID
role alignment       VALID
proof transfer       VALID, but predicts a spurious remaining obligation
typed grounding      TIMEOUT
compiled candidates  NONE
Lean analogy calls   NONE
proof result          UNSOLVED
reward                0.0
```

No compiler or Lean failure occurred here: the pipeline never reached them. At inference there is also no reward propagation or optimizer update.

For comparison, the same real run's less-safe **raw tactic decoder** reached the final stage and proposed `exact h.right`, which Lean can verify. That tells us the central problem in this trace is not discovering the “select the right component” idea; it is expressing that idea efficiently and correctly in the strict typed grounding schema.

---

## Train versus inference, in one view

| Property | Training trace | Inference trace |
|---|---|---|
| Backend | Deterministic fake integration backend | Real Qwen3-8B-4bit |
| Proof | `P → P` | derive `Q` from `h : P ∧ Q` |
| Analogy | Matching wheel on a shelf | Select sugar from available ingredients |
| Iterations | 2 | 1 attempted |
| Typed grounding | Valid twice | Timed out |
| Compiler | `intro analogy_h`, then `exact h` | Not reached |
| Lean result | Complete | No analogy candidate reached Lean |
| Learning | Returns `0.94`, `1.00`; latent policy updated | None |

The end-to-end design is working mechanically. The real trace says where to improve it: make transfer completion judgments more reliable and make grounding much shorter/faster—ideally deterministic for obvious structural operators such as `select_right`—while keeping Lean verification as the hard correctness boundary.

## Provenance

Training evidence came from the saved fake-backend run `1aa1f4aa424540f8857b12c0ae11aa83`: `readable_traces.md`, `generations.jsonl`, `analogy_iterations.jsonl`, `rollouts.jsonl`, `metrics.jsonl`, and `summary.json`.

Inference evidence came from the sealed `gpu-primary` run: `calls.jsonl` and `rescue/evaluate-epoch-0001/rollouts.jsonl`. Prompt reconstruction uses the checked-in renderers in `src/analogy/analogy_transfer_generation.py`.
