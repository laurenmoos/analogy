# Activation-Selected Analogy Evaluation Design

## Intent

Test whether cross-domain analogies selected by their causal effect on a frozen
small model's internal reasoning improve Lean proof discovery beyond both direct
prompting and the repository's existing prompted-analogy condition.

The experiment is deliberately standalone. It does not train Qwen, update the
latent policy, or change the production solver. Its purpose is to establish
whether activation-based analogy selection carries enough signal to justify a
later training objective.

The configured frozen model is `mlx-community/Qwen3-8B-4bit`. Lean remains the
sole judge of tactic validity, progress, and proof completion.

## Research questions

1. Do explicit cross-domain analogies change the model's internal computation
   beyond variation caused by decoding, prompt length, or analogy vocabulary?
2. Can a pre-Lean activation score select analogies that produce more useful
   tactics than analogies generated inside the tactic prompt?
3. Does an analogy-induced activation shift causally change tactic preference,
   or is it merely correlated with different output text?
4. How much of a useful tactic's likelihood is explained by the preceding proof
   state and transition rather than the current state alone?
5. Does any observed benefit survive when total model computation, rather than
   only tactic and Lean budgets, is held fixed?

## Goals

- Compare neutral reasoning, current prompted analogies, activation-selected
  analogies, and a shuffled-analogy control on the same proof states.
- Extract a token-aligned activation signature by teacher-forcing identical
  candidate tactics under neutral and analogy contexts.
- Validate the activation signature with causal patching into a neutral run.
- Keep reasoning-trace displacement, proof-state diversity, and Lean utility as
  distinct reported quantities.
- Produce a machine-readable run record and a concise human-readable report with
  per-problem and aggregate results.
- Make the experiment deterministic and resumable enough to rerun selected
  problem/seed/condition cells.

## Non-goals

- Training Qwen or the repository's latent policy.
- Treating activation distance as proof progress or proof-state diversity.
- Claiming that a recorded residual-stream trajectory is the model's unique or
  complete chain of reasoning.
- Optimizing on Lean outcomes before selecting an analogy in the same trial.
- Establishing a broad benchmark result from six propositional problems.
- Replacing the existing matched direct-versus-analogy solver experiment.

## Benchmark selection

Use these six fixed corpus problems:

| Problem | Role in the experiment |
|---|---|
| `imp_intro_05` | Difficulty-5 positive control; prior analogy-only success |
| `iff_intro_03` | Lower-difficulty positive control; prior analogy-only success |
| `iff_intro_05` | Difficulty-5 hard negative; both matched conditions failed |
| `not_intro_05` | Difficulty-5 hard negative; both matched conditions failed |
| `or_intro_05` | Difficulty-4 hard negative; both matched conditions failed |
| `or_intro_06` | Difficulty-5 hard negative; both matched conditions failed |

The set is preselected before the new metric is run. Per-problem results must be
reported so the positive controls cannot conceal failure on the hard negatives.

Run 16 paired seeds initially. The experiment configuration may request a
confirmatory 32-seed run only when the initial confidence intervals or paired
outcomes are inconclusive. The 16-seed result remains preserved and reported.

Before running any experimental condition, create an immutable anchor manifest.
For every problem and seed, run only the current conventional policy until the
first state that satisfies the repository's observable stuckness rule. Record
the theorem, current state, accepted prefix, ordinary failed tactics and Lean
diagnostics, predecessor state and accepted transition when one exists, hashes,
and the ordinary-search seed. All four conditions start from this exact anchor;
they do not independently replay divergent ordinary searches. If conventional
search completes the theorem before becoming stuck, record the cell as
`no_stuck_anchor` and exclude it from rescue-condition denominators while still
reporting its frequency. No reference proof is available during anchoring.

## Experimental conditions

Every problem/seed cell runs four conditions:

1. **Neutral** — direct tactic generation with proof context and feedback, but no
   analogy.
2. **Prompted analogy** — the current control in which the generation prompt asks
   Qwen to invent an analogy and translate it into tactics in one condition.
3. **Activation-selected analogy** — generate analogy candidates separately,
   score them without Lean, select one, and generate tactics conditioned on it.
4. **Shuffled analogy** — use a structurally compatible analogy belonging to a
   different problem, or deterministically scramble the selected analogy's role
   mapping when no compatible donor exists.

The shuffled condition preserves the extra context and approximate format while
breaking the proof-specific explanation. Shuffling must be deterministic from
the problem and seed and must never use the reference proof.

An optional equal-length neutral-filler diagnostic may be recorded for a subset
of cells to estimate prompt-length effects. It is a diagnostic, not a fifth
headline condition.

## Standalone trial pipeline

For one proof state and seed:

1. Reconstruct the same stuck state, accepted prefix, failed tactics, and budget
   for all four conditions.
2. Generate four cross-domain analogy candidates using the existing
   source, alignment, and proof-transfer schemas. Candidate generation may use
   multiple domains, but candidates must contain an explicit role mapping and a
   stated limitation.
3. Build a tactic slate for each candidate and a neutral tactic slate. Form the
   canonicalized union of these slates, capped at eight tactics by stable
   generation order. Each individual slate contains at most four tactics. The
   union is used only for offline scoring; no candidate is sent to Lean during
   analogy selection.
4. For each analogy and tactic in the union, teacher-force the identical tactic
   completion under the neutral and analogy contexts.
5. Capture residual-stream activations for the tactic tokens at a small,
   configured set of middle-to-late decoder layers. The initial default is three
   evenly spaced layers ending at the configured generation-intervention layer;
   exact layer indices are resolved from model depth and recorded in artifacts.
6. Compute the analogy's whitened displacement, causal patching effect, and
   preceding-state explanatory value.
7. Select the analogy using only those pre-Lean measurements.
8. Run the selected condition's bounded tactic generation and submit its tactics
   to Lean.
9. Record generated tactics, verifier results, successor states, proof-state
   diversity, activation metrics, call counts, tokens, and elapsed time.

## Activation measurement

Let `y` be a candidate tactic and let `Z_0(s, y)` and `Z_A(s, y)` be pooled
residual activations over the identical teacher-forced tactic tokens under the
neutral and analogy contexts. Define

```text
delta_A(y) = Z_A(s, y) - Z_0(s, y)
```

Pooling is performed per layer over tactic tokens, followed by concatenation or
an explicitly recorded aggregate across layers. Prompt tokens are not pooled
with tactic tokens.

Raw Euclidean norm is not the headline distance. Estimate neutral covariance
from neutral problem/seed/tactic observations and use a regularized whitened
distance:

```text
D_reason(A, y) = delta_A(y)^T (Sigma_0 + epsilon I)^-1 delta_A(y)
```

Use diagonal covariance for the initial experiment. Calibration is
leave-one-problem-out: for every target problem, estimate neutral mean and
variance using pre-Lean observations from the other five problems across all 16
seeds and the capped tactic unions. The target problem contributes nothing to
its own covariance or scale estimates. Variance uses a configured positive
floor, recorded in the run configuration. This cross-fit rule is fixed before
Lean evaluation.

Aggregate candidate-tactic distances with a declared robust statistic such as a
trimmed mean. Do not select by the single largest displacement, which would favor
pathological outliers.

## Causal patching score

Use the model provider's existing single-layer activation injection as the first
causal test. Convert the measured analogy-minus-neutral displacement at the
configured intervention layer into a bounded patch and inject it at the neutral
prompt boundary. The patch norm is clipped to the same configured maximum used
for existing latent interventions.

For analogy-derived tactics, compute:

```text
C(A, y) = log p(y | neutral context, patched delta_A)
          - log p(y | neutral context, zero delta)
```

The experiment records both the likelihood effect and whether patching changes
the candidate ranking. A large descriptive activation distance with negligible
patching effect is evidence of correlation, not causal reasoning change.

Because the current provider injects at one final prefill token, this score is a
local causal proxy rather than a reconstruction of the full analogy-conditioned
trajectory. The report must state that limitation.

## Explanatory value of the preceding state

For a verified or proposed tactic `y`, score it under two otherwise matched
contexts:

```text
E_prev(A, y) = log p(y | previous state, current state, analogy)
               - log p(y | current state, analogy)
```

When no preceding accepted state exists, record this metric as unavailable; do
not manufacture a predecessor. The metric measures conditional contribution of
the observed proof trajectory, not semantic similarity between proof-state
embeddings.

## Analogy selection score

The initial selector is a configured, normalized linear combination:

```text
S(A) = alpha * robust_mean(D_reason(A, y))
       + beta * robust_mean(C(A, y))
       + gamma * robust_mean(E_prev(A, y))
```

Each component is standardized using neutral/calibration statistics fixed
without the evaluated problem. Use the same leave-one-problem-out partition as
activation whitening. The experiment has two phases: first collect all pre-Lean
metrics, then freeze each problem's calibration statistics and selections, and
only then expose selected tactics to Lean. Calibration never reads Lean outcomes
or reference tactics. Missing `E_prev` contributes neither reward nor penalty.
Component values, normalized values, weights, and the winning margin are all
recorded.

No Lean result, reference tactic, proof completion label, or successor-state
distance may enter `S(A)`.

The initial experiment is a measurement study, not a hyperparameter search.
Weights and robust aggregation are specified before the 16-seed evaluation and
are not tuned on its solve outcomes. A sensitivity table may recompute rankings
post hoc, clearly labeled as exploratory.

## Utility and diversity measurements

Keep these measurements separate:

- **Reasoning displacement:** whitened teacher-forced activation distance.
- **Causal influence:** patched-minus-neutral tactic log likelihood and ranking
  change.
- **Previous-state explanatory value:** conditional tactic likelihood gain.
- **Proof-state diversity:** the repository's existing frozen-embedding novelty
  over Lean-accepted successor states.
- **Lean utility:** rejected, accepted-progressing, or completed.

Activation displacement is never itself proof progress. Proof-state diversity is
never used as a proxy for internal reasoning orthogonality.

## Budgets and fairness

Report two comparisons:

### Equal tactic/Lean budget

All headline conditions receive the same number of final tactic-generation
samples, candidate tactics submitted to Lean, Lean calls, decoding parameters,
and paired seeds. Analogy candidate generation and activation scoring are extra
work in this view. This isolates the quality of the selected analogy.

### Equal total compute budget

The authoritative proxy is **processed model token positions**. Prefill and
teacher-forced calls charge every non-padding sequence token in every batch row;
cached autoregressive generation charges its prefill tokens once plus one unit
per decoded token. Analogy generation, activation capture, completion scoring,
and patching are all charged. Raw provider calls, batch rows, generated tokens,
wall time, and peak memory are also reported but are not the parity rule.

For each problem/seed cell, first measure the activation-selected condition's
actual token-position cost `C`. Neutral and prompted conditions may then make
additional deterministic tactic-generation calls, without exceeding `C`; a call
that would cross the budget is not started. Extra generated candidates may be
ranked before the same fixed Lean-call cap, but no condition receives additional
Lean calls. The selected condition does not receive unused-budget top-ups. This
cell-specific replay is separate from the equal-tactic-budget result and its
extra generations use a disjoint, deterministic seed range.

Both views are required. Wall-clock time and peak-memory observations are
diagnostics because MLX caching and batching can make them order-dependent.
Condition execution order should be deterministically rotated across seeds.

## Primary outcomes

1. Solved problems and paired solved@budget rate.
2. Accepted-progressing tactics per Lean call.
3. Problems rescued by activation selection beyond both neutral and prompted
   analogy conditions.
4. Causal patching effect on verified-tactic likelihood.
5. Association between the pre-Lean selection score and later Lean utility.

Secondary outcomes include tactic validity, unique accepted successor states,
existing proof-state novelty, model forwards, generated tokens, Lean calls,
elapsed time, and activation-selected analogy stability across seeds.

With only six problems, show paired seed-level and per-problem data. Use bootstrap
confidence intervals or exact paired counts as descriptive uncertainty; avoid a
broad population claim based on asymptotic significance.

## Falsification criteria

- High displacement without Lean improvement indicates disruption rather than
  useful reasoning diversification.
- No advantage over shuffled analogies indicates that the metric is not
  proof-specific.
- No causal patching effect indicates that the measured difference is not a
  useful intervention signature.
- Improvement only under equal tactic budget, but not equal total compute,
  indicates scientific signal without practical efficiency.
- Prompted analogies matching activation-selected analogies indicates that the
  additional activation search is unnecessary.
- Positive-control success without improvement on any hard negative is not
  evidence of expanded proof-discovery capability.

## Artifacts

The standalone command writes a new immutable run directory under
`.artifacts/runs/activation_analogy/`. It contains:

- resolved experiment configuration and model revision;
- one JSONL trial record per problem/seed/condition;
- analogy candidates and explicit mappings;
- activation summary statistics and hashes, not unbounded full tensors by
  default;
- selection components and winner;
- tactic generations and completion likelihoods;
- Lean outcomes and accepted successor states;
- budget accounting;
- `summary.json` and `report.md` with per-problem tables.

An opt-in diagnostic flag may persist compressed activation tensors for a small
specified subset. Default runs avoid large raw activation artifacts.

## Failure handling

- A malformed analogy is recorded and excluded without replacement beyond the
  configured candidate-generation budget.
- A failed activation capture, patch, or completion score marks that candidate
  unscorable and preserves diagnostics.
- If all activation candidates are unscorable, the selected condition records a
  failed selection and does not silently fall back to prompted analogy.
- Lean infrastructure failures remain distinct from tactic rejection.
- Partial artifacts are flushed incrementally so long MLX runs can be resumed.

## Constraints and builder discretion

- Preserve the user's existing large, uncommitted package reorganization and do
  not restore or rewrite unrelated deleted files.
- Reuse the current `src/analogy/inference`, `solver`, and `transfer` package
  boundaries rather than resurrecting deleted legacy modules.
- Extend provider protocols narrowly so fake providers remain inexpensive to
  test.
- Unit tests should use deterministic fake activations and verifiers; the real
  8B run is a separate integration experiment.
- The builder may choose exact record and module names, batching strategy,
  covariance implementation, and report layout, provided the experimental
  separation and artifact requirements above remain intact.

## Completion boundary

Implementation is complete when the standalone command, unit tests, and fake
end-to-end experiment pass; the resolved Qwen3-8B experiment configuration can
run the six problems; and its output distinguishes all four conditions and both
budget views. Running all 16 real-model seeds is a separate, potentially long
execution step and must not be represented as completed unless its artifacts
actually exist.
