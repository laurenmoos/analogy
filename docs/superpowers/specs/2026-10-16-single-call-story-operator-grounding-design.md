# Single-Call Story Operator Grounding Design

## Intent

Speed up grounded analogy experiments while making the causal question sharper: does writing an everyday story help a model choose the next useful proof operation?

The production path must use one model call per analogy attempt. The model chooses one configured `GroundingOperator` and binds story roles to exact proof-inventory IDs. Deterministic code then enumerates every valid application of that operator, fills all proof-state-owned data, compiles every unique candidate, and sends every candidate to Lean. Compatibility with the previous source, alignment, transfer, and model-grounding stages is explicitly out of scope.

Success means:

- one model call replaces source generation, alignment, transfer, and grounding calls;
- no model repair call is made for an invalid analogy decision;
- the model never receives the hidden operation inferred by need analysis;
- model output can contain only a configured operator and current inventory IDs;
- deterministic code owns hashes, propositions, completion claims, and tactic construction;
- all unique compiled candidates are attempted in Lean, even after one succeeds;
- every model-facing prompt string lives in `prompts.yaml`;
- training uses within-problem centered rewards and measures whether latent changes produce different stories;
- a standalone matched ablation compares direct operator choice with story-mediated choice without adding experiment-only branches to production execution.

## Non-goals

- Preserving old generated-domain trace schemas or checkpoint compatibility.
- Keeping model-generated proof-reasoning, alignment, or grounded-plan records.
- Letting the model emit Lean syntax.
- Making the standalone ablation part of the normal training or evaluation commands.
- Adding a general theorem prover beyond the configured finite operator vocabulary.

## Production Data Flow

```text
Lean proof state
  -> deterministic proof inventory
  -> deterministic need analysis
       -> neutral model-facing description
       -> hidden expected operator in logs only
  -> one Qwen call, thinking disabled, no repair
       -> short everyday story
       -> one configured GroundingOperator
       -> story-role to inventory-ID bindings
  -> strict validation
  -> deterministic exhaustive application enumeration
  -> deterministic tactic compilation
  -> deduplicate exact tactic text
  -> attempt every compiled candidate in Lean
  -> record all results and whether any candidate completed the proof
```

### Need analysis

Need analysis continues to infer an expected operation for diagnostic comparison, but its model-facing record excludes that field. The model-facing record retains:

- input and output arity;
- information flow;
- focus before and desired focus after;
- proof roles;
- a situation summary.

Summaries describe shapes without naming the move. For example, selection from a conjunction becomes: “There is one item made of two parts, and the goal matches one part.” The inferred expected operation is stored beside the iteration log as `expected_operator`; it is never serialized into the prompt payload.

The old broad `OperationClass` may be retained only as an internal feature if another subsystem needs it. The new generated path logs and compares concrete `GroundingOperator` values.

### Analogy decision

The sole model-produced production record contains exactly:

- `story`: short everyday prose;
- `operator`: one enabled `GroundingOperator` value;
- `bindings`: a bounded mapping from short story-role labels to current inventory IDs.

The parser rejects unknown keys, duplicate JSON keys, non-finite constants, disabled operators, unknown inventory IDs, duplicate role labels, and duplicate exclusive bindings. It does not accept hashes, propositions, tactic strings, completion flags, predicted goals, or caller-owned IDs.

The prompt receives only the neutral need description, a compact inventory view, and the configured operator enum. It does not receive raw Lean state text, rejected tactics, old analogy stages, or the hidden expected operator. Qwen thinking is disabled at the tokenizer boundary. A schema failure ends the attempt; there is no repair prompt or second model call.

### Configuration

Each experiment YAML exposes:

```yaml
prompts:
  path: prompts.yaml

analogy:
  grounding_operators:
    - exact_local
    - introduce
    - apply_implication
    - iff_forward
    - iff_backward
    - rewrite_forward
    - rewrite_backward
    - split
    - select_left
    - select_right
    - choose_left
    - choose_right
    - cases
    - contradiction
```

The list must be nonempty, unique, and contain only real `GroundingOperator` values. `compose` is not model-selectable because it combines deterministic intermediate results rather than representing a first operation on the current state. Code may create internal composition steps while enumerating a selected operator.

The resolved operator list, prompt-catalog hash, and prompt versions are written to run metadata and checkpoints.

### Deterministic application enumeration

A new enumerator consumes `(operator, inventory)` and returns every valid grounded application for that current state. The story bindings are validated and logged as alignment evidence, but they do not suppress other valid applications: exhaustive enumeration ranges over the full current inventory.

For each operator, code uses exact inventory propositions and structural shapes to enumerate type-compatible operands and directions. It owns:

- stable application and binding IDs;
- the current state fingerprint;
- exact input and output propositions;
- exact inventory identifiers;
- the expected next subgoal for structural operations;
- the completion flag.

Completion is true only when the derived output proposition matches the active subgoal under the compiler’s conservative outer-parenthesis equivalence. Structural operations such as introduction, splitting, branch choice, rewriting, and cases derive their successor obligation from the current proposition structure; they never trust a model prediction.

Unsupported, opaque, or ambiguous proposition syntax fails closed for that candidate. Enumeration returns zero applications when no valid application exists. This is a normal negative result, not an infrastructure error.

### Compilation and Lean verification

The compiler consumes deterministic applications rather than a model-authored multi-step plan. It emits all supported tactic forms for each application, preserving exact current-state identifiers. Candidate text is canonicalized and globally deduplicated for the iteration.

The loop attempts every unique compiled candidate. It does not stop after the first accepted successor or completed proof. Every attempt and outcome is logged; the iteration result chooses completion if any candidate completes, otherwise the highest-priority novel successor under a deterministic ordering. Lean remains the only correctness oracle.

`tactics_per_iteration` no longer truncates deterministic grounded candidates. Resource accounting must reserve or report one Lean call per emitted unique candidate. Cancellation and genuine infrastructure failure may stop execution; ordinary acceptance may not.

## Prompt Catalog

All model-facing prose moves to repository-root `prompts.yaml`. This includes every model-backed path that remains after obsolete generated-domain stages are deleted: production story selection, embeddings, ordinary tactic generation, capability evaluation, rescue, audit, schema-error text, and the standalone ablation templates. Python renderers provide named values and structural serialization but contain no instructional prompt prose.

The catalog stores version, system template, and user template per prompt. A small loader:

- loads and validates the catalog once per process;
- rejects missing names and missing template variables;
- renders only explicitly supplied named variables;
- exposes stable per-prompt versions and a whole-file content hash;
- supports an explicit path from `config.yaml` with paths resolved relative to that config file.

Templates use a deliberately small named-placeholder renderer rather than executable templating. Literal JSON examples are YAML block scalars and do not require evaluation.

## Ordinary Tactic Syntax

The ordinary tactic generator’s catalog prompt includes Lean 4 `cases` examples. A small conservative normalizer also rewrites only the unambiguous standalone Lean 3 form:

```text
cases h with hP hQ
```

to the valid Lean 4 next-tactic form:

```text
cases h
```

This leaves the resulting branch goals to later search instead of inventing branch bodies. The normalizer must not rewrite arbitrary `cases` programs or text inside quoted identifiers. Normalization occurs before deduplication and Lean submission and is recorded in diagnostics.

## Training

Training collects several analogy attempts for the same problem/state before one optimizer update. For each sibling group, advantage is:

```text
attempt reward - mean reward of attempts for that problem/state
```

No exponential moving baseline substitutes for a missing sibling group. A group with fewer than two valid sampled attempts produces no policy-gradient update because it cannot establish relative performance. Entropy regularization remains configurable and may still contribute only when an actual grouped update occurs.

The configured `rollouts_per_state` must therefore be at least two for analogy-policy training. Checkpoints no longer persist the obsolete singleton reward baseline.

For real-model runs, each sibling record includes normalized story text and a story embedding or stable semantic fingerprint. Training summaries report at minimum:

- number of states with at least two latent samples;
- exact distinct-story ratio;
- mean pairwise story-embedding distance;
- fraction of states with more than one selected operator.

This diagnostic verifies whether different latent deltas change the visible story rather than merely changing hidden activations.

## Standalone Story Ablation

`scripts/benchmark_story_operator_ablation.py` owns the experiment. Production runners contain no “plain versus story” branch.

The script imports inventory, neutral need analysis, strict operator validation, deterministic enumeration, compilation, model generation, and Lean verification. It defines two one-call arms using templates from `prompts.yaml`:

- **plain:** select one operator from the neutral description and inventory;
- **story:** write a short story, then select one operator and bindings in the same response.

Both arms use the same problems, saved states, enabled operators, model configuration, latent deltas, seeds, call count, maximum output budget, Lean candidate handling, and scoring. Arm order is deterministically randomized or crossed over per state to control warm-cache and order effects. Neither arm gets a repair call.

The script records:

- first selected operator success;
- whether any enumerated candidate succeeds;
- selected and hidden expected operators;
- operator confusion matrix;
- prompt/input tokens and output tokens;
- model latency and Lean latency;
- invalid-output and zero-application rates;
- candidate count and Lean-call count;
- story diversity across latent samples;
- paired per-problem deltas between arms.

It writes raw JSONL, a machine-readable summary JSON, and a Markdown report. The report highlights whether one prompt dominates by prompt length, output length, invalid rate, latency, candidate count, or ordering.

### Ambiguous benchmark fixtures

The script owns a separate fixture set of harder states where multiple operators are plausible. Examples include:

- a goal matching a conjunction component while an implication can also derive it;
- an equality that enables rewriting alongside a directly applicable implication;
- a biconditional plus matching directional hypotheses;
- a disjunction where case analysis and a direct local route compete;
- conjunction goals where splitting, rewriting, or an existing exact local may work.

Fixtures include only theorem statements and accepted prefixes needed to reconstruct the state. They do not expose a reference tactic to either arm. Lean outcomes define success.

## Error Handling and Observability

Expected failures are explicit stage outcomes:

- invalid one-call JSON;
- disabled or unknown operator;
- invalid inventory binding;
- selected operator with zero valid applications;
- compiler produced zero tactics;
- all Lean candidates rejected.

Infrastructure failures remain distinct and include model runtime errors, Lean process errors, cancellation, and corrupt configuration. Partial iteration logs retain the decision raw text, parsed decision when available, deterministic applications, compiled candidates, every Lean attempt, call counts, token counts when available, and elapsed time.

The iteration log compares `selected_operator` with `expected_operator`, but the latter is added only after the model call has completed.

## Testing Strategy

Implementation follows test-driven development.

Unit tests cover:

- neutral need records never serialize the hidden operation;
- summaries do not name an operator or tactic;
- configuration accepts only unique real operators and exposes the resolved list;
- prompt catalog completeness, rendering, path resolution, versions, and hash;
- strict decision parsing against enabled operators and exact inventory IDs;
- exhaustive applications for every enabled operator;
- exact hashes, formulas, identifiers, and completion decisions;
- compilation and global tactic deduplication;
- verification attempts all candidates after an early success;
- Lean 3 standalone `cases` normalization without broad rewrites;
- grouped reward centering and no singleton update;
- story-diversity metrics over multiple latent samples.

Integration tests use fake generation only at the external model boundary and real deterministic inventory/enumerator/compiler code. Lean end-to-end tests cover representative operators against the pinned project.

Script tests verify that the ablation:

- makes exactly one model call per arm and no repair call;
- pairs identical states, seeds, latent deltas, operator sets, and Lean handling;
- randomizes or crosses over arm order deterministically;
- scores first picks independently of later candidates;
- reports token, latency, invalidity, and per-problem paired metrics;
- runs the ambiguous fixture set without modifying the production corpus.

The full existing test suite and a timed local fake-backend benchmark must pass before completion. A real-Qwen smoke run is attempted when the configured model is available; otherwise the exact command and limitation are reported.

## Performance Contract

For one valid production attempt:

- model calls: exactly 1;
- model repair calls: 0;
- model-generated stages: exactly 1;
- deterministic enumeration and compilation: in-process and bounded by current inventory combinations;
- Lean calls: exactly the number of unique compiled candidates unless cancellation or infrastructure failure intervenes.

Benchmarks compare the old recorded multi-stage call count with the new call count and report wall-clock breakdown by model, enumeration, compilation, and Lean. The primary speed result is model-call and model-latency reduction; exhaustive Lean verification is reported separately so it cannot be hidden by aggregate timing.
