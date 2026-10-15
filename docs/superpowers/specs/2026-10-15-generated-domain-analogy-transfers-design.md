# Generated Domain Analogy Transfers

## Purpose

Replace proof-paraphrase “analogy” cards with explicit, corpus-free analogical
transfers. At each stuck proof state, the system generates a concrete situation
from another domain, extracts its relational transformation, aligns that
transformation with the active proof obligation, translates the alignment into
proof reasoning, and only then generates Lean tactics.

The learned policy chooses how to steer source-analogy generation. It does not
directly choose a tactic. Lean remains the only correctness authority.

The change must also make every rollout readable as one connected proof trace:

```text
fixed theorem target
→ accepted tactic
→ active subgoal
→ generated source-domain analogy
→ structural alignment
→ transferred proof reasoning
→ proposed tactic
→ actual Lean successor or unchanged state
```

## Current Failure

The current `AnalogyHop` schema asks for a domain label, situation, relation
chain, mapping, and continuation, but it does not require two distinct systems.
The model can therefore relabel the Lean problem as “Logical Deduction” and
return a proof-plan paraphrase. Validation only checks that mapping entries are
nonblank.

The policy observation is also a snapshot rather than an explicit trajectory.
It concatenates embeddings of:

- the overall target, current proof state, accepted tactics, and failed tactics;
- the current target and hypothesis shapes.

The analogy-generating model sees prior hop summaries, but the trainable policy
does not directly observe the previous proof state, verified transition, latest
analogy transfer, surprise, repetition, or remaining resources. This makes
directional failures such as oscillating between `⊢ P` and `⊢ R` difficult to
distinguish.

## Standalone Experiment Concepts to Reuse

The production package must not import
`scripts/standalone_analogy_recovery.py`. It should reimplement only these
generic ideas behind package-owned types:

1. **Name-free proof need.** Describe the required structural change without
   copying identifiers or naming Lean tactics.
2. **Typed transformation record.** Record operation, arity, permutation,
   information flow, focus before/after, and a domain-neutral summary.
3. **Separate domain and transformation.** A concrete source-domain transition
   is evidence for a domain-neutral operation; it is not itself a proof tactic.
4. **Strict stage boundaries.** Source generation, structural transfer, proof
   reasoning, tactic decoding, and Lean verification have separate schemas and
   logs.
5. **Embeddings as observations, not explanations.** Frozen-model embeddings
   condition the learned policy, while readable records explain what was
   generated and verified.

Unlike the standalone experiment, this design does not retrieve from a fixed
poetry or physics corpus. The source situation is generated online, giving
unbounded rollout variety while keeping each individual transfer inspectable.

## Data Model

### Proof trajectory observation

`AnalogyObservation` records the information available before one policy action:

- fixed theorem target;
- current Lean state;
- previous Lean state, when one exists;
- tactic that produced the current state, when one exists;
- ordered accepted tactic prefix;
- tactics rejected at the current state with diagnostics;
- latest generated transfer, when one exists;
- latest predicted and actual transitions;
- latest surprise value and change;
- repeated-state flag;
- analogy iteration index;
- remaining model and Lean calls.

The readable observation is embedded into four channels:

1. fixed target;
2. current proof state and structural signature;
3. previous-to-current verified transition;
4. latest source analogy, alignment, and transferred proof reasoning.

The transition and analogy channels are zero vectors before they exist. Scalar
features are normalized and concatenated after the four embeddings.

### Structural proof need

`ProofNeed` is derived before source-domain generation:

- active subgoal shape;
- relevant hypothesis shapes;
- candidate operation class;
- input and output arity;
- permutation;
- information flow;
- focus before;
- desired focus after;
- one short domain-neutral description of the needed transformation.

The operation, arity, permutation, and information-flow enums follow the
standalone experiment. The proof need may be generated with strict validation,
but deterministic Lean structure remains authoritative for fields it can derive.

### Source-domain analogy

`SourceAnalogy` must describe a concrete transition outside formal proof syntax:

- `domain`: a specific domain such as navigation, mechanics, scheduling,
  electrical circuits, or safety systems;
- `source_before`: the concrete situation before the source transition;
- `source_after`: the situation after it;
- `relation_chain`: two to four source-domain relations;
- `transformation`: a typed domain-neutral transformation record;
- `rationale`: why this source transition exhibits the proof need.

The source fields may not contain Lean syntax, tactic names, theorem identifiers,
or verbatim proposition expressions. Generic logical vocabulary is allowed only
inside the domain-neutral transformation.

### Structural alignment

`AnalogyAlignment` explicitly connects the two systems:

- two to six source-role/proof-role mappings;
- the invariant relation shared by both systems;
- the source operation being transferred;
- the corresponding proof operation;
- any known mismatch or limitation;
- a confidence value used for diagnostics, never as proof reward.

Validation requires mappings to reference roles declared in both records. A
domain label mapped to itself is not sufficient. The alignment must cite the
active subgoal and at least one relevant hypothesis or constructor.

### Transferred proof reasoning

`ProofReasoningTransfer` is tactic-free:

- current active subgoal;
- premises consumed;
- ordered reasoning steps;
- expected next subgoal or completion condition;
- motivating source analogy and alignment IDs.

Only after this record is valid does `AnalogyTacticGenerator` receive the current
Lean state and generate tactics. Every tactic plan cites the transfer ID rather
than merely a hop ID.

## Generation Pipeline

One analogy iteration performs these bounded stages:

1. Build `AnalogyObservation` from the fixed target and verified trajectory.
2. Derive and validate `ProofNeed`.
3. Sample a policy action from the observation.
4. Generate one `SourceAnalogy` under the latent action.
5. Generate `AnalogyAlignment` between the source and proof need.
6. Generate `ProofReasoningTransfer` in tactic-free proof language.
7. Decode a bounded tactic set from the transfer and current Lean state.
8. Verify tactics with Lean.
9. Record the actual state transition, surprise, repetition, and stop decision.
10. If continuing, rebuild the complete observation from the verified successor
    before sampling the next action.

Malformed output at one stage triggers at most one schema repair for that stage.
If repair fails, the rollout terminates without calling later stages. Model and
Lean calls share explicit per-rollout budgets.

## Associative Policy

The policy input changes from two embeddings to four embeddings plus scalar
features. A small MLP produces the existing norm-bounded latent delta. The first
implementation retains controller-based stopping; a learned stop action can be
evaluated later after multi-step traces actually exist.

The latent delta conditions only source-domain analogy generation. Alignment,
proof-reasoning translation, and tactic decoding receive ordinary frozen-model
generation so the causal interpretation is clear: the policy selected a source
lens, and deterministic downstream stages attempted to transfer it.

The policy checkpoint schema version must increase. Old checkpoints fail with a
clear incompatibility message rather than silently loading into the enlarged
observation head.

## Continuation and Surprise

An accepted successor must first receive a cheap conventional finishing pass.
If it remains open and the analogy loop continues, the next observation includes
the exact previous-to-current transition and the latest transfer. This makes the
next generated analogy a response to verified progress rather than a fresh
paraphrase of the theorem.

Low surprise alone must not stop immediately when the successor is conventionally
solvable or when the configured minimum number of analogy steps has not been
reached. Repeated-state transitions are explicit negative trajectory evidence.

The initial experiment keeps existing verifier rewards unchanged. Source prose,
alignment confidence, and LLM-judge scores never become rewards.

## Human-Readable Traces

Every run writes the existing machine-readable JSONL plus a rendered Markdown
trace. The renderer groups interventions by theorem and proof path rather than by
JSONL row.

Each theorem trace uses this notation:

```text
THEOREM TARGET (fixed): R → Q

[State 0] ACTIVE SUBGOAL: R → Q
    │ intro hR                         [ordinary search: accepted]
    ▼
[State 1] ACTIVE SUBGOAL: Q
    │ apply hPQ                        [ordinary search: accepted]
    ▼
[State 2] ACTIVE SUBGOAL: P
    │
    ├─ SOURCE ANALOGY: reversible bridge
    │  East → West by reverse traversal
    │
    ├─ ALIGNMENT
    │  East ↔ R; West ↔ P; reverse traversal ↔ reverse direction of hPR
    │
    ├─ TRANSFERRED PROOF REASONING
    │  reduce P to the available R using the reverse equivalence direction
    │
    └─ apply hPR.mpr                    [Lean accepted]
       ▼
[State 3] ACTIVE SUBGOAL: R
    │ assumption                       [conventional finisher]
    ▼
[COMPLETE: theorem target proved]
```

Rejected tactics point back to the unchanged state. Repeated accepted states are
shown as candidate states followed by an explicit discard edge. The renderer
uses one-based analogy numbering and never displays internal `iteration_index`
as the analogy number.

The five existing theorem traces are fixed readability fixtures:

- `imp_intro_04`: expose the mismatch between fixed target `C → S` and active
  subgoal `C`;
- `imp_intro_05`: show ordinary reduction from `U` to `P` before completion;
- `not_intro_05`: show a safety-system source analogy translated into
  contradiction reasoning and then tactics;
- `iff_intro_04`: show two analogy interventions on one connected proof path,
  including the useful `P → R` subgoal transition and repeated-state discard;
- `iff_intro_05`: show a concrete structural-transfer analogy, all failed Lean
  attempts, and budget termination.

## Example: Negation Introduction

For the state:

```lean
h1 : B → S
h2 : ¬S
⊢ ¬B
```

the stages should look like:

```text
Proof need:
  introduce the negated assumption, follow a causal implication, derive a
  forbidden state, and close by contradiction

Source domain — safety interlock:
  before: activating control B triggers alarm S; alarm S is forbidden
  after: control B cannot be activated safely

Alignment:
  control activation ↔ B
  alarm ↔ S
  trigger rule ↔ h1
  forbidden alarm ↔ h2

Transferred proof reasoning:
  assume B; derive S through h1; contradict h2

Lean tactics:
  intro hB
  exact h2 (h1 hB)
```

This is an actual analogy because the safety-system transition and the proof
state are distinct relational systems connected by an explicit alignment.

## Logging and Audit

Each analogy-iteration row records:

- the complete observation rendering and embedding provenance;
- proof need;
- raw and parsed source analogy;
- alignment;
- transferred proof reasoning;
- tactic plan and every actual Lean attempt;
- predicted and actual transitions;
- surprise, repetition, controller state, resources, and stop reason;
- latent-action statistics and generation seeds.

Large embedding vectors remain optional; hashes, dimensions, model revision,
layer, and pooling position are always recorded. The post-hoc LLM judge remains a
separate observer and cannot change these records or the experiment.

## Evaluation

Run the same training/validation and held-out theorem examples first so the new
traces can be compared directly with the six existing cards. Report:

- source-domain validity and diversity;
- alignment-schema validity;
- proof-reasoning-transfer validity;
- Lean-valid tactic rate;
- novel-successor and completion rates;
- mean analogies per rescue invocation;
- multi-analogy continuation rate;
- repeated-state rate;
- model and Lean calls per completion;
- readability fixture snapshots.

Required conditions under matched budgets:

1. conventional solver;
2. current proof-paraphrase card loop, preserved as a legacy control;
3. generated source analogy without learned latent steering;
4. randomly steered generated source analogy;
5. learned-policy generated source analogy;
6. shuffled source analogies before alignment and tactic decoding.

The primary comparison is learned generated-domain analogy versus unsteered and
random generated-domain analogy. The current legacy card loop is diagnostic, not
the desired endpoint.

## Testing and Compatibility

Unit tests cover strict parsing and rejection of:

- source cards that repeat Lean syntax or simply restate the proof;
- mappings that do not reference both source and proof roles;
- transfers that cite the fixed theorem target instead of the active subgoal;
- tactic plans that do not cite their transfer;
- observation tensors with missing or mismatched channels;
- checkpoint schema mismatches;
- repeated-state and failed-tactic trace edges.

Integration tests use deterministic fake generators to exercise the complete
pipeline, then use the pinned Lean project for the five readability fixtures.
Existing conventional, direct-rescue, standalone-experiment, audit, and source
immutability suites remain preservation tests.

## Non-Goals

- importing or depending on the standalone script;
- requiring a persistent external analogy corpus;
- rewarding analogy prose for sounding plausible;
- treating generated alignment confidence as correctness;
- letting unverified transfers mutate the Lean frontier;
- claiming generated examples contain unlimited independent information;
- training or fine-tuning the frozen language model in this stage.
