# Associative Analogy Rescue RL Design

## Purpose

Add a generic stuck-only RL rescue system first, then introduce explicit analogy behavior in independently testable stages. The final policy explores chains of generated analogies, maps the continuation of a promising chain back into proof space, proposes a bounded number of tactics, and learns from Lean verification. The system must transfer lines of reasoning rather than retrieve similar proofs or depend on an external analogy corpus.

This design supersedes the one-step traversal and paired-reranking approach in `docs/plans/05-latent-traversal.md` and `docs/plans/06-lean-reward-training.md`. It preserves the conventional solver as the default path and the frozen Qwen model as the generator. The merged standalone experiment remains intact as experimental evidence; its production code is not moved into `src/analogy`.

## Relationship to the Standalone Experiment

`scripts/standalone_analogy_recovery.py` proved that bounded analogy context can change tactic recovery and that Lean-derived structural signatures, strict parsing, shared budgets, and auditable run artifacts work end to end. It is intentionally self-contained and corpus-specific.

The RL implementation starts from the smaller reusable package on `main`:

- `LocalRescueSearch` remains the conventional search coordinator;
- the existing injected rescue boundary remains the place where experiments attach;
- `LeanGymAdapter` remains the executable environment;
- shared immutable records and strict configuration remain package conventions.

No production module imports the standalone script. Concepts may be reimplemented behind generic interfaces only when the corresponding stage needs them. The standalone test suite remains a preservation suite and a behavioral reference, not a migration checklist.

## Core Idea

An analogy aligns with the observed proof trajectory through the current stuck state. Its next imagined transition is then mapped back into the Lean domain as a predicted proof state and a candidate tactic:

```text
analogy:  a₀ → a₁ → a₂ → a₃
proof:    s₀ → s₁ → s₂ →  ?
                       alignment
prediction: map a₃ back into proof space → next tactic
```

The analogy is a generative lens, not a tactic name or a fixed symbolic operator. The policy searches over latent interventions; frozen Qwen eventually decodes each intervention into a readable analogy hop. A stream may continue, reframe, remap, or stop. Only after the stream stops does a separate decoder propose tactics.

The first implementation stage deliberately omits analogy semantics: a latent action conditions direct tactic generation plus a predicted next proof state. This establishes rollout collection, Lean rewards, optimization, replay, and fair evaluation before asking whether analogy streams improve them.

## Boundaries

- The conventional prover runs first and invokes rescue only after observable local stuckness.
- Generic rollout and reward records must not require an analogy domain, mapping, or corpus reference.
- No external analogy corpus, proof retrieval system, or hand-authored relation ontology is used.
- Qwen remains frozen. Only the associative policy is trainable.
- The rescue system may propose new tactics because reranking a pool that omits the needed tactic cannot escape local failure.
- Every proposed tactic is checked by Lean before it can affect the frontier or receive positive task reward.
- A successful rescue returns its successor to ordinary best-first proof search immediately.
- Reference proofs are evaluation metadata only and never enter prompts, rewards, or policy inputs.

## Representations

### Proof trajectory

The rescue input contains the overall goal, the ordered accepted tactic/state prefix, the current Lean state, and failed tactics at the current state. A canonical renderer produces:

1. a natural-language proof-need description;
2. a structural description that suppresses identifier spelling and emphasizes relationships, constructors, hypotheses, goals, and unresolved dependencies;
3. normalized embeddings of both descriptions, with model, layer, pooling rule, and source hashes recorded.

### Generic rescue rollout

The initial reusable record contains:

- rollout, step, and optional parent identifiers;
- free-form generated content with an explicit step kind;
- predicted next proof-state description;
- natural-language and structural embedding references;
- generation seed and latent-action provenance;
- proposed tactics and Lean results;
- termination reason and reward components.

No field assumes that the generated content is an analogy. This same record supports direct tactic generation, one-hop lenses, multi-hop analogy streams, random-policy controls, and shuffled-stream ablations.

### Analogy stream

Each hop records:

- a unique ID and optional parent ID;
- domain or imagined world;
- the situation before the imagined transition;
- the continuation being explored;
- the current source-to-proof mapping;
- the predicted next proof-state description;
- natural-language and structural embeddings;
- generation seed and latent-action provenance.

The analogy stage specializes generic rollout steps with the domain, imagined situation, continuation, and mapping fields. The stream is interpretable because every latent action must decode into one of these records. Full hidden activations are not serialized.

### Tactic proposal

In the generic baseline, Qwen receives the proof trajectory and latent intervention directly. In the analogy conditions, it also receives the completed generated stream. Both paths emit a small strict-JSON set of single-line Lean tactics with predicted next proof-state descriptions. Analogy-conditioned proposals cite the motivating hop. All rescue tactics are deduplicated against ordinary and previously attempted tactics.

## Associative Policy

The policy receives the concatenation of:

- proof natural-language embedding;
- proof structural embedding;
- latest generated rescue-step embedding, or zeros before the first step;
- normalized hop index.

A small MLP produces a diagonal Gaussian latent delta and a Bernoulli stop decision. The delta is norm-clipped and applied only to the final prompt token at one configured Qwen layer during prefill. Decode-token activations are not modified. A zero delta must reproduce unperturbed generation under the same seed.

The direct-rescue baseline emits exactly one action. Analogy conditions may emit at most `max_hops` actions; invalid or duplicate hops end that branch without a tactic proposal. At least one decoded hop is required before stop can produce tactics.

## Incremental Delivery

The implementation proceeds in this order:

1. **Generic rescue protocol and traces.** Add domain-neutral rollout, proposal, prediction, outcome, and reward records at the existing stuck-state injection point. A deterministic fake policy proves ordinary search is unchanged.
2. **Direct latent rescue baseline.** A small random Gaussian policy perturbs frozen Qwen once, which directly emits tactics and predicted next states. Lean verifies them. No analogy text or training is involved.
3. **Verifier-only RL.** Train the same policy from completion and novel-state rewards. Compare learned, random, and unperturbed direct rescue under matched budgets.
4. **Prediction and surprise.** Embed predicted and actual accepted states, record their distance, then add the gated intrinsic-reward condition. Verifier-only remains the control.
5. **One-hop analogy lens.** Decode the latent action into one explicit analogy card before tactic generation. Compare it with direct rescue using the same number of model and Lean calls.
6. **Multi-hop associative stream.** Feed each decoded hop back to the policy, add stop actions and parent links, and propagate the final Lean return through the chain.
7. **Refinement actions.** Let later hops continue, reframe, remap, or abandon the current lens through generated natural language rather than a fixed operator vocabulary.

Every stage must be runnable and evaluable before the next begins. A stage that fails its matched control is preserved as a negative result and does not silently become a prerequisite for later claims.

## Final Analogy Rollout

For each stuck proof state in the final analogy condition:

1. Encode the proof trajectory once.
2. Sample `streams_per_state` independent analogy streams.
3. Decode up to `max_hops` explicit hops per stream.
4. Decode up to `tactics_per_stream` tactics from each completed stream.
5. Verify tactics under a shared per-state Lean-call budget.
6. Return the first completed proof or best novel accepted successor to ordinary search.
7. Persist every stream, proposal, Lean result, prediction comparison, and policy statistic.

The same proof state may generate effectively unbounded distinct experience across seeds, but evaluation uses fixed seeds and budgets. The project describes this as unbounded rollout capacity, not infinite independent information.

## Reward

The first training condition is verifier-only and is implemented before analogy generation:

```text
+1.0  theorem completed
+0.1  accepted tactic reached a novel frontier-eligible state
 0.0  repeated/no-progress state or unused stream
-0.1  Lean rejected every tactic from the stream
```

Every latent action in a stream receives the stream's discounted return. A small per-hop cost discourages gratuitously long streams.

Two intrinsic signals are computed and logged from the beginning but enter training only in an explicit curiosity condition:

- **transition surprise:** distance between predicted and actual accepted proof-state embeddings;
- **state novelty:** distance between the actual accepted state and states previously reached in the same proof search.

Intrinsic reward is zero for rejected tactics, errors, repeated states, and no-progress states. It is clipped and weighted below completion reward so nonsense cannot win by being unpredictable. Experiments must compare verifier-only, surprise, random-policy, and conventional-only conditions.

## Training

Use on-policy REINFORCE initially. Qwen generation runs under `no_grad`; the policy retains only action log probabilities and entropies. In the direct-rescue stage a rollout has one action; the same optimizer later accepts several actions from an analogy stream. Normalize returns within the rollouts sampled for one stuck state, apply an entropy bonus, clip policy gradients, and reject non-finite batches.

Training examples are generated online from theorem statements and Lean interaction. Checkpoints contain policy, optimizer, RNG, configuration, prompt versions, and split identity. Model selection uses validation problems; the held-out test split is evaluated once.

## Interpretability and Replay

Each rollout must allow a reader to reconstruct:

```text
stuck proof trajectory
→ latent policy actions
→ decoded rescue steps or analogy hops
→ predicted proof state
→ proposed tactic
→ actual Lean result
→ extrinsic and intrinsic reward components
```

Replay reuses recorded tactics and Lean states without resampling generation. Prompt versions, model revision, seeds, hashes, and resolved configuration make generation experiments auditable even when exact hardware-level model reproduction is unavailable.

## Failure Handling

- Malformed, blank, or duplicate generated steps terminate only their rollout.
- Malformed or multiline tactics are rejected before Lean.
- Generation timeout, Lean timeout, cancellation, and budget exhaustion are distinct terminal reasons.
- One failed stream does not erase completed streams from the same stuck state.
- A rescue failure leaves conventional search behavior unchanged.
- Non-finite embeddings, actions, rewards, losses, or gradients fail the affected episode before optimizer mutation.

## Evaluation

The primary metric is theorem completion under matched verifier-call and wall-clock budgets. Secondary metrics are newly solved/regressed theorem IDs, rescue invocation rate, valid-tactic rate, novel-state rate, stream length, domain diversity, prediction error, state novelty, and completion per verifier call.

Required ablations:

1. conventional solver only;
2. unperturbed direct rescue;
3. random latent direct rescue;
4. verifier-only direct-rescue RL;
5. verifier plus transition surprise;
6. one-hop analogy with matched model and Lean calls;
7. multi-hop analogy stream;
8. shuffled analogy streams before tactic decoding.

The matched direct-rescue and shuffled-stream conditions establish whether the analogy trajectory contributes information beyond extra sampling or extra model calls.

## Non-Goals

- retrieving similar theorems, proofs, poems, or physics examples;
- defining a universal fixed vocabulary of reasoning operators;
- training or fine-tuning Qwen in the first implementation;
- rewarding analogy prose for sounding plausible;
- allowing unverified analogical outputs onto the proof frontier;
- claiming that unlimited generated rollouts are unlimited independent training information.
