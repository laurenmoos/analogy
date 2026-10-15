# Rescue Quality and Analogy Stream Design

## Purpose

Turn the generic rescue checkpoint into a scientifically useful analogy experiment. The five-problem pilot established that training, checkpointing, Lean verification, and matched evaluation run end to end, but exposed three immediate failures: all held-out rescue generations were opaque `generation_error` records, 4096-dimensional policy actions saturated the maximum norm, and only three training theorems produced almost no learning signal.

This design implements the next stages of the approved associative-rescue architecture without adding an external corpus or proof retrieval. Success means that failures are explainable, policy actions explore a meaningful range, failed generations teach the policy, many reproducible stuck states can be sampled, and an interpretable analogy stream can be compared fairly with direct rescue.

## Delivery Order

The work lands in five independently runnable checkpoints:

1. observable and repairable structured generation;
2. non-saturating latent actions;
3. denser policy rewards for valid failures;
4. persisted stuck-state replay and curriculum sampling;
5. one-to-three-hop analogy streams with matched direct and shuffled controls.

Each checkpoint preserves conventional-only behavior and has its own tests and benchmark metrics. A later checkpoint may use an earlier one, but no benchmark result is reported as an analogy gain unless all matched controls run successfully.

## 1. Observable Structured Generation

Every generation attempt records:

- prompt version and generation seed;
- raw model text, bounded to the configured output limit;
- whether the attempt was original or repair;
- parse status;
- stable error kind and diagnostic message.

`RescueRollout` receives a tuple of immutable generation-attempt records. `GENERATION_ERROR` is reserved for model/runtime failures. A new `INVALID_OUTPUT` termination represents text that was generated but did not satisfy the schema. Empty or duplicate-only valid outputs remain `EXHAUSTED`.

On invalid output, the runner makes at most one repair call. The repair prompt contains the invalid text and parser diagnostic and asks only for a corrected JSON object. Repair consumes a model-call budget but no Lean-call budget. Runtime errors and timeouts are never retried automatically. Original and repaired text remain in the trace.

The direct schema stays intentionally small:

```json
{"predicted_state":"...","tactics":["..."]}
```

The parser remains strict after extracting an optional fenced JSON object. It does not guess tactics from prose.

## 2. Non-Saturating Latent Actions

Replace hard clipping of a high-dimensional diagonal Gaussian with separate stochastic direction and radius:

1. sample a raw direction vector from a diagonal Gaussian whose scale is dimension-normalized;
2. normalize the sampled direction;
3. sample a scalar radius logit from a Gaussian;
4. map the radius through `sigmoid` and multiply by `max_delta_norm`;
5. define the policy log probability as the sum of the raw-direction and raw-radius log probabilities.

The initial radius mean targets ten percent of the maximum norm. The policy records radius, raw direction norm, final delta norm, and saturation ratio. Entropy includes both distributions. Zero-delta evaluation remains an explicit control and bypasses sampling.

This parameterization gives REINFORCE a stochastic magnitude variable while preventing dimensionality alone from forcing every action to the boundary. Checkpoints include a policy schema version; incompatible old direct-rescue checkpoints fail with a clear message rather than partially loading.

## 3. Dense but Grounded Rewards

Positive task reward remains exclusively Lean-grounded:

- completion reward for a verified proof;
- progress reward for a verified novel frontier state.

Valid sampled actions also receive bounded negative feedback:

- invalid structured output after repair;
- valid output containing no fresh tactic;
- all proposed tactics rejected by Lean;
- optional per-hop cost for analogy streams.

Cancellation and infrastructure failures are excluded from optimization. Model output that fails the declared schema is not an infrastructure failure and is trainable. Reward records store each component separately.

Prediction alignment is logged for every accepted tactic by embedding the predicted and actual next states. It becomes an optional intrinsic reward only in a named `surprise` condition. The signal is clipped, zero for unaccepted tactics, and cannot outweigh verified progress. No reward is given merely for fluent analogy prose or parseable JSON.

State-level sibling baselines remain the default. A single sampled action uses an exponential moving reward baseline rather than its raw return, avoiding the extremely large one-sample update observed in the pilot. Gradient clipping remains a final safety boundary, not the primary scale-control mechanism.

## 4. Stuck-State Replay and Curriculum

Conventional and training searches persist a `StuckStateRecord` containing:

- problem identity and structural split;
- theorem binders and target;
- accepted tactic prefix;
- exact current proof state and state fingerprint;
- ordinary candidates and their verified results;
- difficulty and connective;
- prompt/model provenance.

The record contains enough information to reconstruct the Lean session from the theorem and accepted prefix. Replay must re-run Lean before rescue; recorded proof states are evidence, not trusted environment state.

Training supports two sources behind one episode interface:

- online theorem search, which discovers new stuck states;
- deterministic replay sampling from previously verified stuck-state records.

Sampling is stratified by connective and difficulty, begins with states where at least one short verified rescue exists, and gradually admits harder or entirely unsuccessful states. The replay store deduplicates by theorem, accepted prefix, and proof-state fingerprint. Train, validation, and test split identities remain immutable, preventing replay leakage.

The first useful benchmark uses at least 30 training stuck states, five validation states, five held-out test states, and three seeds. It reports rescue opportunity count and valid-generation rate in addition to theorem completion.

## 5. Interpretable Analogy Streams

An analogy is generated as a reasoning lens, never as a tactic or fixed operator. Each `AnalogyHop` contains:

- domain or imagined setting;
- source situation;
- ordered relation chain;
- explicit mapping from source roles to proof roles;
- next source-side continuation;
- predicted consequence in the proof state;
- a natural-language reason to continue or stop.

The first hop is conditioned on the proof trajectory. Each later hop is conditioned on the proof trajectory plus prior hops. The policy consumes proof language/structure embeddings, the latest hop language/structure embeddings, and normalized hop index. A stochastic stop action ends the stream after at least one valid hop and before the configured maximum of three.

After stopping, a separate tactic-decoding call receives the proof trajectory and complete readable stream and emits the existing strict tactic schema. Lean remains the only authority that can complete a proof or add a successor to the frontier. Final verified return, prediction reward, and per-hop cost propagate to every policy action in the stream with configurable discounting.

Invalid hops receive one bounded repair attempt, then terminate only that stream. Later hops may naturally continue, reframe, or remap by changing their explicit text and mapping; there is no hand-authored operator vocabulary.

## Matched Controls

Evaluation uses the same theorem set, seeds, wall-clock ceiling, model-call budget, Lean-call budget, and tactic limit for:

1. conventional only;
2. zero-delta direct rescue;
3. random direct rescue;
4. learned direct rescue;
5. learned analogy stream;
6. shuffled analogy stream;
7. direct deliberation control with the same number of model calls as the analogy stream.

The shuffled condition preserves generated hops but assigns them to another compatible stuck state before tactic decoding. The direct deliberation control replaces analogy hops with additional proof-focused reflection calls. These controls distinguish analogy structure from extra tokens or model calls.

## Configuration and Artifacts

Strict configuration adds:

- model-call and repair-call budgets;
- invalid-output and empty-output reward weights;
- policy radius initialization and schema version;
- replay path, mixture ratio, curriculum thresholds, and minimum state counts;
- analogy enablement, stream count, maximum hops, hop discount, and per-hop cost;
- explicit evaluation condition list and wall-clock ceiling.

Run artifacts remain non-overwriting and add:

- `generations.jsonl` for raw/repair attempts;
- `stuck_states.jsonl` for replay records;
- analogy hops inside rollout traces;
- valid-output, repair-success, saturation, rescue-opportunity, and prediction metrics;
- newly solved and regressed theorem IDs by condition and seed.

Raw output is local experimental data and may contain arbitrary model text. It is never executed, interpreted as an instruction, or interpolated into Lean source except through the validated single-line tactic parser.

## Failure Handling

- A repair failure terminates one rollout and remains trainable as invalid output.
- Model load, device, timeout, or internal runtime failures are infrastructure failures and receive no policy reward.
- Reconstruction mismatch invalidates a replay record and records the mismatch; it never silently substitutes the recorded state.
- Non-finite actions, rewards, losses, or gradients abort the optimizer step before mutation.
- A failed analogy stream cannot erase earlier completed streams or alter conventional search.
- Budget exhaustion is explicit and never triggers another model or Lean call.

## Acceptance Criteria

- Every invalid held-out generation exposes raw text and a stable diagnostic.
- A deterministic malformed output demonstrates one repair attempt and a persisted trace.
- In a seeded initial-policy batch, fewer than ten percent of actions lie within one percent of `max_delta_norm`.
- Invalid and empty model outputs produce configured finite negative rewards and policy gradients; cancellation and runtime failure do not.
- Replay reconstructs Lean states exactly and rejects cross-split or stale records.
- Every analogy rollout can be read from proof state through hops to tactic and Lean result.
- Multi-action returns update every action in the stream.
- Matched controls enforce identical model-call, Lean-call, tactic, and wall-clock budgets.
- Conventional-only search remains unchanged when rescue and analogy are disabled.
- The complete preservation suite and a real-model/real-Lean smoke benchmark pass before a larger benchmark is started.

## Non-Goals

- external analogy or proof retrieval;
- fine-tuning Qwen;
- rewarding prose quality;
- trusting replayed states without Lean reconstruction;
- claiming improvement from a single theorem or seed;
- treating parser reliability improvements as evidence for analogy reasoning.
