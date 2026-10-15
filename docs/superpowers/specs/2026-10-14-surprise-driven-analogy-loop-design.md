# Surprise-Driven Analogy Loop Design

## Purpose

When conventional Lean search is stuck, generate an interpretable analogy, use it to condition tactic generation, verify the tactic with Lean, and continue refining the analogy while the verified proof transition remains surprising. The loop must remain easy to inspect and modify: its control flow belongs in one readable coordinator rather than being spread across model, reward, and training code.

Success means that every generated tactic can be traced to a readable analogy hop, every accepted tactic produces a predicted-versus-actual transition comparison, and every continuation or stopping decision is explained in immutable logs. Lean remains the only authority for completion and proof-state progress.

## Control Loop

The coordinator will live in `src/analogy/analogy_loop.py`. Its main method will read linearly:

1. Build language and structural representations of the current proof state.
2. Sample a latent analogy action.
3. Generate and validate one readable analogy card.
4. Generate tactics conditioned on the proof state and the complete analogy stream.
5. Verify tactics with Lean until one completes, advances the frontier, or the per-iteration tactic budget is exhausted.
6. For an accepted tactic, compare the predicted and actual proof transitions.
7. Log the complete iteration and its stopping decision.
8. Continue from the verified successor when surprise remains useful; otherwise return control to conventional search.

The loop terminates on proof completion, cancellation, invalid-output exhaustion, model or Lean budget exhaustion, maximum iterations, sustained low surprise, insufficient surprise improvement, or failure to obtain a verified successor.

## Analogy Generation

Each analogy hop is conditioned on:

- the current Lean proof state and overall target;
- accepted and failed tactic history;
- a language-view embedding;
- a Lean-derived structural-view embedding and structural signature;
- every previous analogy card;
- earlier generated tactics and Lean outcomes;
- predicted and actual transition summaries;
- the current surprise value and its trend.

The model emits exactly one strict analogy card containing `domain`, `situation`, `relation_chain`, `mapping`, `continuation`, `predicted_consequence`, `rationale`, and `stop`. The relation vocabulary remains open-ended. A latent direction/radius policy biases card generation but does not replace the readable card.

The first iteration has no previous transition. Later iterations are refinements of the accumulated reasoning trace rather than independent metaphors.

## Proof Representation Reused from the Standalone Experiment

The implementation will promote the strongest standalone logic into package components:

- Lean-extracted goal count, target expression shape, hypothesis shapes, arity, and information-flow diagnostics;
- separate language and structural proof renderings;
- normalized final non-padding hidden-state embeddings;
- model revision, resolved layer, token index, text hash, and prompt-version provenance;
- a concise domain-neutral structural-proof-need description;
- optional mean-token-likelihood tactic reranking.

It will not import the standalone script or reuse its poetry/physics corpus, structural shortlist retrieval, or fixed operation taxonomy as the analogy vocabulary.

## Analogy-Conditioned Tactics

Tactic decoding receives the exact current proof state, Lean structural signature, complete analogy stream, previous outcomes, and required `motivating_hop_id`. Generated tactics must use the existing strict single-line tactic schema. The cited hop must belong to the active stream.

Lean tests the decoded tactics. Rejected tactics are logged and may inform the next refinement. Only a Lean-accepted successor may become the next iteration's state.

## Transition Surprise

Surprise compares reasoning moves rather than raw states. For a state `before`, predicted successor `predicted`, and verified successor `actual`:

```text
predicted_transition = E(before -> predicted)
actual_transition    = E(before -> actual)
surprise             = cosine_distance(predicted_transition, actual_transition)
```

Both transition renderings use the same frozen encoder, prompt version, and hidden layer. Distances must be finite and are clipped to `[0, 2]`.

Continuation uses a small explicit state machine:

- always run at least `min_iterations` after obtaining successors;
- count consecutive values at or below `surprise_threshold`;
- stop after `surprise_patience` consecutive low-surprise transitions;
- stop when improvement over the preceding surprise is below `min_surprise_improvement` for `improvement_patience` iterations;
- otherwise continue until `max_iterations` or another budget is exhausted.

Model-proposed `stop` is advisory: it may stop only after `min_iterations` and cannot override a required minimum. Hard budgets always take precedence.

## Hyperparameters

The strict `analogy` configuration section adds:

- `min_iterations`
- `max_iterations`
- `surprise_threshold`
- `surprise_patience`
- `min_surprise_improvement`
- `improvement_patience`
- `tactics_per_iteration`
- `max_model_calls`
- `max_lean_calls`
- `transition_embedding_layer`
- `discount`
- `iteration_cost`
- `rerank_tactics`

Validation requires positive integer budgets, `min_iterations <= max_iterations`, surprise values in `[0, 2]`, discount in `(0, 1]`, and non-negative cost/improvement values.

## Readable Components

`analogy_loop.py` owns only orchestration and stop decisions. It uses small dependencies with narrow interfaces:

- `ProofViewEncoder`: produces language/structure/transition embeddings with provenance.
- `ProofStructureExtractor`: obtains the Lean structural signature.
- `AnalogyGenerator`: samples and parses one card.
- `AnalogyTacticGenerator`: decodes cited tactics from the accumulated stream.
- `SurpriseController`: pure state transition deciding `continue` or a named stop reason.
- `AnalogyTraceSink`: persists one complete iteration at a time.

The coordinator will avoid deeply nested branches. Each iteration produces an immutable `AnalogyIteration` record, then a single pure controller call determines the next action. Model repair, parsing, embedding, and JSONL serialization stay outside the coordinator.

## Logging

Every run creates `analogy_iterations.jsonl`. Each row includes:

- run, theorem, rollout, and iteration identifiers;
- current proof state and accepted prefix;
- language/structure embedding provenance and structural signature;
- raw generation references and parsed analogy card;
- latent action radius, norm, log probability, entropy, and seed;
- motivating hop, generated tactics, and Lean result for every attempt;
- predicted successor, actual successor, transition renderings, and embeddings;
- surprise, surprise delta, low-surprise streak, low-improvement streak;
- decision (`continue` or `stop`) and a stable reason enum;
- model calls, Lean calls, elapsed time, and remaining budgets.

Raw model output remains in `generations.jsonl`; rollout summaries remain in `rollouts.jsonl`. Logs must contain enough information to replay the controller decision without rerunning the model.

## Training and Credit

Every latent action in the accepted stream receives discounted verified return:

```text
return_i = discount ** distance_to_final * final_verified_reward
           - iteration_cost * remaining_iterations
```

Rejected, malformed, empty, cancelled, and infrastructure outcomes retain distinct semantics. Surprise may be an explicitly named clipped intrinsic component, but it cannot exceed verified progress reward and is zero without a Lean-accepted successor.

## Evaluation

Matched conditions share theorem IDs, seeds, model-call limits, Lean-call limits, tactic limits, and wall-clock limits. Required comparisons are conventional search, learned direct rescue, analogy-conditioned loop, and shuffled-analogy control. Report completion, novel successors, Lean calls per completion, valid-generation rate, repair rate, average iterations, stop-reason distribution, surprise trajectories, and newly solved/regressed theorem IDs.

## Testing

Tests will cover strict configuration; proof-view and transition embeddings; tactic prompts containing the complete stream; exact hop citations; high-surprise continuation; patience-based low-surprise stopping; improvement stopping; advisory model stop; every hard budget; invalid generations; rejected tactics; trace completeness and deterministic replay; discounted gradients across multiple iterations; and preservation of the conventional solver path.

A deterministic fake-generator/fake-Lean integration test will exercise at least three iterations with a descending surprise sequence. A real offline Qwen/Lean smoke will use a single replayed stuck state before any larger benchmark.
