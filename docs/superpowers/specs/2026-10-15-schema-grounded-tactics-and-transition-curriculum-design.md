# Schema-Grounded Tactics and Verified Transition Curriculum

## Purpose

Improve generated-domain analogy rescue at its two demonstrated bottlenecks:

1. translate valid transferred reasoning into tactics that are grounded in the
   exact local Lean context; and
2. produce enough verified nonterminal transitions to train and evaluate
   iterative analogy selection.

The source analogy remains open-ended and generative. The boundary from proof
reasoning into Lean becomes typed and mostly deterministic. Training examples
come from verified state transitions, not from retrieving a similar proof at
inference time.

## Evidence motivating the change

The October 15 Qwen3-8B smoke established that the staged pipeline can generate
concrete domains, align them with proof structure, produce tactic-free proof
reasoning, and reach Lean. It did not establish a learned advantage.

At 512 output tokens, valid transfers still decoded into tactics such as:

- a rewrite missing Lean's required bracket syntax;
- `apply` or `exact` against a hypothesis whose proposition did not match the
  active target; and
- duplicated candidates rejected before verification.

No held-out generated condition produced a verified novel open successor.
Consequently, no real rollout generated a second analogy conditioned on a
verified transition, and the learned policy received almost no evidence that
distinguished one source lens from another.

## Design principles

1. **Analogies stay generative.** A source situation may come from any concrete
   domain that satisfies the existing strict source schema.
2. **Grounding is explicit.** Every proof role used by transferred reasoning is
   bound to an exact local declaration, constructor, or target component.
3. **Logical intent precedes syntax.** The model selects typed inference
   operations; a package-owned compiler emits ordinary Lean candidates.
4. **Lean remains authoritative.** Static grounding and compilation reduce bad
   calls but never certify a proof.
5. **Only verified transitions train the policy.** LLM judgments, alignment
   confidence, and schema validity remain diagnostics rather than rewards.
6. **Training data is not an inference corpus.** Known proofs may generate
   offline transition examples, but the online solver never retrieves their
   next tactics or completed proofs.

## Grounded tactic schema

### Local proof inventory

Before tactic decoding, build a `LocalProofInventory` from the verified Lean
state and `ProofStructure`:

- fixed theorem target;
- exact active subgoal;
- ordered local declarations with identifier, proposition, and structural
  signature;
- target constructors and components;
- stable declaration IDs used by generated records;
- the latest transferred proof reasoning and its declared premises.

The inventory is readable and serializable. Generated records cite stable IDs;
the package resolves them to current Lean identifiers only for compilation.

### Role bindings

`GroundedRoleBinding` connects one proof role or transferred premise to one
inventory entry:

- `proof_role`;
- `inventory_id`;
- exact proposition copied from the inventory;
- binding kind: local declaration, target, target component, or constructor;
- optional source-role provenance from the alignment;
- a short justification.

Validation rejects unknown IDs, copied propositions that differ from the
inventory, duplicate exclusive bindings, and bindings that do not cover every
premise consumed by the transfer.

### Typed inference steps

`GroundedInferenceStep` contains:

- a stable step ID;
- an operator from a bounded logical vocabulary;
- ordered input binding or prior-step IDs;
- exact input propositions;
- exact output proposition;
- rewrite or iff direction when applicable;
- the transferred-reasoning step it implements;
- whether it is expected to close the active subgoal or produce a successor.

The first vocabulary covers operations already represented by the corpus and
current proof-need enum:

- exact local hypothesis;
- introduce implication or negation assumption;
- apply implication;
- use iff forward or backward;
- rewrite equality or iff forward or backward;
- split conjunction or iff goal;
- select conjunction component;
- choose disjunction branch;
- eliminate disjunction by cases;
- derive contradiction;
- compose previously derived steps.

This vocabulary constrains grounding, not analogy generation. Unsupported
reasoning fails explicitly instead of being silently translated to arbitrary
Lean text.

### Grounded plan

`GroundedTacticPlan` replaces the current raw tactic array as the primary
decoder output. It contains:

- motivating transfer ID;
- active-subgoal fingerprint;
- role bindings;
- ordered inference steps;
- expected next-subgoal fingerprint or completion;
- optional model-suggested surface hints used only for diagnostics.

Strict parsing uses exact keys, caller-owned IDs, finite bounded fields, and the
existing one-repair limit. The plan is rejected if its active subgoal differs
from the verified state or if its chain does not produce the claimed expected
result.

## Deterministic tactic compilation

`GroundedTacticCompiler` translates validated steps into a small, ordered set of
Lean candidates. Examples include:

- `exact h` for exact-hypothesis reuse;
- `apply h` and an explicit term form for implication application;
- `exact h.mp x` / `exact h.mpr x` for directional iff use;
- `rw [h]` / `rw [← h]` for rewrite directions;
- `intro h`, `constructor`, `left`, `right`, and `cases h with` for structural
  operations.

The compiler owns brackets, arrows, direction syntax, and identifier spelling.
It deduplicates candidates canonically before charging Lean calls. Every
candidate records the bindings and inference steps that produced it.

When several steps require an intermediate result, the compiler may emit a
single explicit term or a short `have` block only from typed components. It does
not concatenate arbitrary model text. Unsupported compositions produce a
compiler diagnostic and no Lean call.

For an initial migration period, the old free-form tactic generator may run as
an explicitly labeled fallback condition in evaluation. It is not mixed into
the grounded condition or its training data.

## Diagnostic repair after Lean rejection

A rejected compiled tactic may receive one bounded repair pass. The repairer
sees:

- the unchanged grounded plan;
- the exact candidate and its provenance;
- Lean diagnostics;
- the current inventory.

It may only revise bindings, operator direction, or the typed step chain. It
cannot emit raw Lean. The revised plan is revalidated and recompiled. Model and
Lean calls use the existing shared rollout budgets, and cancellation is checked
before every repair call.

## Verified transition curriculum

### Source transitions

Build offline examples by replaying proof prefixes in the pinned Lean project.
For each accepted transition in the training split:

1. replay the prefix to obtain the exact state before the tactic;
2. execute the next tactic to obtain the verified successor or completion;
3. construct the local inventory;
4. derive the grounded bindings and inference steps from the verified tactic and
   before/after structures;
5. verify that compiling the grounded plan reproduces an accepted transition;
6. generate multiple source analogies, alignments, and transfers for that same
   grounded reasoning relation;
7. retain only complete records whose compiled candidate Lean verifies.

The curriculum emphasizes successful **nonterminal** transitions so later
observations can contain a real previous-to-current transition and motivate a
second analogy. Completions remain useful but do not dominate the dataset.

### Data augmentation without proof retrieval

One verified transition may yield many training records by varying:

- concrete source domain;
- source roles and relation-chain wording;
- structurally valid alignment;
- tactic-free explanation;
- equivalent compiler-emitted Lean candidate;
- surrounding failed candidates used as negatives.

At inference time, the solver receives only the current state, trajectory, and
generated records. It cannot query this transition dataset by theorem,
embedding, or proof similarity.

### Splitting and leakage prevention

Splits occur before prefix extraction. The default split unit is theorem; an
evaluation intended to measure broader transfer additionally groups related
theorem families or source levels. All prefixes, alternative tactics, generated
analogies, and negative candidates derived from one theorem stay in its split.

Every curriculum artifact stores:

- source corpus and theorem hash;
- Lean version and project hash;
- exact before/after state hashes;
- original verified tactic;
- grounded-plan schema and prompt versions;
- compiler version;
- generation seed and model revision.

### Positive and negative records

Positive policy examples require a Lean-verified, non-repeated successor or
completion. Nonterminal successors receive explicit transition labels and may
seed the next curriculum step.

Negative diagnostic examples include:

- invalid role binding;
- wrong operator direction;
- compilable but Lean-rejected candidate;
- repeated state;
- malformed schema;
- infrastructure failure.

Only verifier-derived outcomes affect reward. Infrastructure failures are
excluded from optimization. Schema and compiler failures may train supervised
validity heads or filters but are not treated as proof-progress reward.

## Training sequence

### Phase 1: schema and grounding warm start

Train or prompt-tune the frozen-model-facing components to produce valid role
bindings and grounded inference steps. Track exact-field accuracy, binding
validity, operator/direction accuracy, and compiler coverage.

The first implementation may use supervised examples to evaluate prompt/schema
quality without updating the 8B model itself. The trainable rescue policy remains
the existing small latent policy.

### Phase 2: policy learning on verified transitions

The policy still chooses the latent source lens. The downstream stages use the
grounded schema and compiler. Discounted reward propagates only through eligible
analogy iterations that lead to verified successors or completion.

Training batches deliberately include trajectories with two or more verified
nonterminal steps so the policy observes how a useful analogy changes what
analogy should come next.

### Phase 3: matched evaluation

Compare, under identical budgets:

- conventional solver;
- legacy free-form analogy cards;
- generated analogy with old raw decoder;
- grounded generated analogy, unsteered;
- grounded generated analogy, random policy;
- grounded generated analogy, learned policy;
- grounded generated analogy with compatible source shuffling.

The raw decoder remains an ablation rather than a fallback inside the grounded
condition.

## Metrics and visibility

Report the full funnel per condition:

1. source schema validity;
2. alignment validity;
3. proof-transfer validity;
4. inventory binding validity;
5. grounded-plan validity;
6. compiler coverage and candidate count;
7. Lean attempt acceptance;
8. novel open successors;
9. second-analogy continuation;
10. completion and completion per Lean/model call.

Readable traces add these nested sections after transferred reasoning:

```text
LOCAL PROOF INVENTORY
GROUNDED ROLE BINDINGS
TYPED INFERENCE STEPS
COMPILED TACTICS
LEAN RESULTS
```

Each compiled tactic cites its step and binding provenance. Repairs show the
original plan, Lean diagnostic, revised plan, and result.

## Initial success gates

Before interpreting theorem completion rates, the grounded system must show on
held-out states:

- nonzero compiled candidates reaching Lean;
- nonzero accepted-tactic rate;
- verified novel open successors on multiple theorems;
- at least one real second analogy conditioned on a verified successor;
- no unknown-identifier or malformed-rewrite candidates from the deterministic
  compiler;
- learned source selection exceeding both random and unsteered selection on
  verified-successor rate under matched budgets.

These are integration and causal gates, not claims of generalization. The report
must retain denominators and distinguish schema throughput from proof progress.

## Compatibility and migration

- Introduce a new grounded-plan schema version and reject incompatible saved
  curriculum/checkpoint artifacts clearly.
- Keep legacy and raw-decoder conditions available for ablation.
- Existing generated traces remain readable; new grounding fields are optional
  when loading historical rows and explicit when writing new rows.
- The post-hoc audit supports both raw and grounded generated records.
- Existing reward definitions remain unchanged except that compiler/schema
  failures are explicitly ineligible for proof-progress reward.

## Non-goals

- Training or fine-tuning the Qwen3-8B weights in the first implementation.
- Retrieving similar proof scripts or completed proofs during inference.
- Treating an LLM judge, source quality, or alignment confidence as reward.
- Building a complete Lean metaprogramming tactic synthesizer.
- Claiming domain-general analogy performance from the current five-problem
  smoke benchmark.

## Risks

- A small operator vocabulary may improve correctness while hiding useful novel
  reasoning. The explicit unsupported-operation diagnostic and raw-decoder
  ablation measure this trade-off.
- Automatically deriving grounded plans from arbitrary existing tactics is not
  always possible. The first curriculum should support a clear tactic subset and
  skip unsupported transitions with counted reasons.
- Multiple equivalent Lean states complicate exact successor matching. Lean
  acceptance and normalized structural fingerprints remain authoritative.
- Synthetic analogy volume can create superficial diversity. Report structural
  and domain diversity separately, and group splits before augmentation.
