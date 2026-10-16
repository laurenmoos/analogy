# Grounded transition smoke — 2026-10-15

No learned advantage was demonstrated. The bounded curated comparison completed
0/2 held-out theorems in every condition; direct rescue alone produced one novel
open successor. All generated conditions produced zero verified novel successors
and zero second analogies. Both policy checkpoints had zero optimization updates.
Grounding generation timeouts prevent a grounded compiler-quality comparison.

The default curriculum and the separate curated iterative curriculum are distinct
experiments. Neither the deterministic fake backend nor offline reference replay
is evidence of a learned model advantage. All Qwen3-8B weights remain frozen;
only the existing small latent policy is eligible for optimization.

## Default retained-state results

No learned advantage was established. The primary checkpoint had **zero policy
updates**: its optimizer state is empty and every tensor equals the seed-0
initialization. Grounded unsteered, random, learned, and shuffled conditions each
completed 0/1 operationally, but all four are censored by model-generation
timeouts. Raw generated, direct rescue, and legacy cards completed the one
retained held-out projection theorem; conventional search did not. No condition
produced a novel open rescue successor or a second analogy. These results cannot
measure learned source selection or uncensored raw-versus-grounded proof quality.

| Condition | Complete | Accepted rescue tactics / attempts | Novel / second analogy | Actual Lean processes | Model generations | Completion / Lean | Completion / model |
|---|---:|---:|---:|---:|---:|---:|---:|
| Conventional | 0/1 | 0/0 | 0 / 0 | 4 | 1 | 0 | 0 |
| Direct rescue, unchanged policy | 1/1 | 1/1 | 0 / 0 | 6 | 2 | 0.167 | 0.500 |
| Legacy analogy cards | 1/1 | 1/1 | 0 / 0 | 7 | 3 | 0.143 | 0.333 |
| Raw generated, unsteered | 1/1 | 1/2 | 0 / 0 | 8 | 5 | 0.125 | 0.200 |
| Grounded unsteered | 0/1* | 0/0 | 0 / 0 | 5 | 5 | 0 | 0 |
| Grounded random | 0/1* | 0/0 | 0 / 0 | 5 | 5 | 0 | 0 |
| Grounded learned, 0 updates | 0/1* | 0/0 | 0 / 0 | 5 | 2 | 0 | 0 |
| Grounded shuffled | 0/1* | 0/0 | 0 / 0 | 5 | 2 | 0 | 0 |

`*` Unsteered and random timed out at grounding; learned and shuffled timed out
at source generation. These are infrastructure outcomes, not Lean rejections.
The single-theorem default split also cannot supply a different compatible
held-out source for the shuffled control; it did not reach that substitution.
No grounded compiler candidate reached Lean, so compiler acceptance and malformed
identifier/rewrite rates are unmeasured in this primary model run.

| Generated condition | Source valid / calls | Alignment valid / calls | Transfer valid / calls | Binding and plan valid / calls | Compiler covered / plans |
|---|---:|---:|---:|---:|---:|
| Raw unsteered | 1/1 | 1/1 | 1/1 | — | — |
| Grounded unsteered | 1/1 | 1/1 | 1/1 | 0/1* | 0/0 |
| Grounded random | 1/1 | 1/1 | 1/1 | 0/1* | 0/0 |
| Grounded learned | 0/1* | 0/0 | 0/0 | 0/0 | 0/0 |
| Grounded shuffled | 0/1* | 0/0 | 0/0 | 0/0 | 0/0 |

The raw tactic schema was valid in 1/1 calls. Default training completed 2/5
states through conventional search; validation completed 0/1. Training rescue
source calls had 4 schema-invalid outputs and 1 runtime error. Validation passed
source, alignment, and transfer (1/1 each), then grounding timed out. No
in-sample rescue supplied an optimization sample.

Primary raw artifacts are under `runs-grounded/grounded-transition-20261015/gpu-primary/`;
`primary-analysis.json` verifies all 110 hashed raw files, checks prompt answer-field
isolation, and derives the table's corrected all-call denominators. Total runtime
was 1129.89 seconds, with 43 generations and 81 Lean processes across training and
evaluation. The preserved primary source snapshot predates bookkeeping corrections
to phase-only state loading and ordinary-generation counters. Its original
machine summary is retained; the table uses actual raw calls, including prefix
reconstruction and proof validation.

A full verification run overlapped part of the primary GPU run and encountered
deadline failures. Consequently, these wall-time-sensitive primary results are
also subject to resource contention. The bounded curated experiment is run
separately from verification workloads; its results must remain distinct.

## Bounded curated initial-state results

This separate, preselected two-theorem comparison used the same eight conditions,
512-token output cap, shared call/Lean caps, and 45-second generation timeout.
No tests or other verification workloads ran in parallel with its GPU inference.
All conditions completed 0/2. Direct rescue accepted one nonterminal tactic on
`iff_intro_03`; it did not complete the theorem. No analogy condition produced a
verified novel open successor, and no real second analogy occurred.

| Condition | Complete | Accepted rescue tactics / attempts | Novel rescue / second analogy | Actual Lean processes | Model generations | Completion / Lean | Completion / model |
|---|---:|---:|---:|---:|---:|---:|---:|
| Conventional | 0/2 | 0/0 | 0 / 0 | 8 | 2 | 0 | 0 |
| Direct rescue, unchanged policy | 0/2 | 1/6 | 1 / 0 | 17 | 6 | 0 | 0 |
| Legacy analogy cards | 0/2 | 0/4 | 0 / 0 | 14 | 8 | 0 | 0 |
| Raw generated, unsteered | 0/2 | 0/4 | 0 / 0 | 14 | 13 | 0 | 0 |
| Grounded unsteered | 0/2* | 0/0 | 0 / 0 | 10 | 12 | 0 | 0 |
| Grounded random | 0/2* | 0/0 | 0 / 0 | 10 | 12 | 0 | 0 |
| Grounded learned, 0 updates | 0/2* | 0/0 | 0 / 0 | 10 | 12 | 0 | 0 |
| Grounded shuffled | 0/2† | 0/0 | 0 / 0 | 10 | 12 | 0 | 0 |

`*` Both episodes timed out at grounding. `†` One timed out after a grounded-plan
repair; the other failed transfer parsing twice because its reasoning contained
tactic text. Therefore the shuffled denominator includes one infrastructure
outcome and one model-schema outcome. Its first plan was rejected for duplicate
binding IDs/exclusive proof roles before the repair timed out. There were **zero
validated grounded plans, zero compiler candidates, and zero compiled Lean calls**
in this curated model run. This is not evidence that the compiler rejects correct
plans or that grounded tactics have a 0% acceptance rate: that denominator is empty.

| Generated condition | Source valid / calls | Alignment valid / calls | Transfer valid / calls | Binding and plan valid / calls | Compiler covered / plans |
|---|---:|---:|---:|---:|---:|
| Raw unsteered | 2/3 | 2/2 | 2/3 | — | — |
| Grounded unsteered | 2/3 | 2/2 | 2/3 | 0/2* | 0/0 |
| Grounded random | 2/3 | 2/2 | 2/3 | 0/2* | 0/0 |
| Grounded learned | 2/3 | 2/2 | 2/3 | 0/2* | 0/0 |
| Grounded shuffled | 2/3 | 2/2 | 1/3 | 0/2† | 0/0 |

Raw tactic parsing was valid in 1/3 calls. On `iff_intro_03`, all four raw
applications were rejected while the target was still `P → R`: `apply h2`,
`apply h1`, `exact h1`, and `apply h1.mp` never introduced the required input.
On `iff_intro_04`, both raw tactic outputs repeated candidates, so no raw tactic
reached Lean. These are model/schema and logical-sequencing failures; they are
separate from the grounding timeouts. The compatible shuffled control was actually
exercised: alignment/transfer prompts for `iff_intro_03` cited the source generated
for `iff_intro_04`, and conversely. No reference next tactic was substituted.

Curated training completed 2/2 states through ordinary search, with no policy
actions optimized. Validation completed 1/2; its failed rescue produced two
concrete-source schema rejections. The checkpoint optimizer has zero state
entries and every policy tensor equals seed-0 initialization. Thus learned and
random are the same parameter initialization here; this is not a learning
experiment demonstrating improvement, even where a finite-sample rate might differ.

The curated run took 1830.53 seconds, with 85 generations, 34 embedding calls, and
115 actual Lean processes across training and evaluation. Raw artifacts are in
`curated-run/gpu-primary/`; `curated-analysis.json` verifies all 110 artifact hashes
and checks that generator prompts contain none of the offline answer fields.
The full 9-record curriculum and later test prefixes remain separate and were not
counted as online held-out successes.

## Initial gates and interpretation

The real-model grounded gates were not met: no compiled candidate reached Lean,
no novel open grounded successor occurred on either held-out theorem, and no
verified second analogy appeared. Learned selection did not exceed random or
unsteered selection, and zero policy updates make a learned advantage untestable.
The fake CLI establishes two-iteration integration; the fresh 9/9 offline build
establishes compiler-reproducible reference transitions. Neither can substitute
for the missing real-model grounded observations.

The repeated grounding timeout also occurs with verification idle. The existing
MLX backend disables thinking, completes its generation, then checks elapsed time;
it raises when the result took at least 45 seconds. It does not expose the late
response text to the caller. Accordingly the raw logs preserve prompts, timing,
and exceptions, but cannot recover discarded late text. No late answer was used
as a proof, a training sample, or a matched-budget success. These results identify
a latency/schema-throughput limit under this profile, rather than establishing
the quality of completed grounded plans.

This is one seed, two curated initial states, one default retained test state,
and a narrow propositional corpus with theorem-level, not family-level, splits.
Later-prefix performance and broad transfer are unmeasured. A meaningful policy
comparison first needs on-budget grounded outputs, real nonterminal continuations,
and nonzero verified policy updates.

## Dataset and isolation

The unchanged default curriculum retained 7 of 28 attempted reference prefixes:
1 verified nonterminal transition and 6 completions. Twenty-one reference tactics
were outside the conservative derivation subset; there were no verifier,
structure, compiler-match, or repeated-state failures. Its retained state split
is train 5 / validation 1 / test 1. The sole retained held-out theorem is
`and_intro_05`. This denominator cannot establish novel successors on multiple
held-out theorems, regardless of solver performance.

Default artifact: `runs/grounded-curriculum/manifest.json`, build ID
`78265fb7c9e3d6af9e23347e28b8ac20ca0dc546140e957edc7edcce14a16372`.
The existing corpus, split manifest, and reference proofs were not changed.
The Task 7 CLI invocation followed the identical-hash resume path for this
previously verified default artifact; it did not freshly replay all 28 references.
Online scheduled states were separately replayed and hash-checked before solving.

The curated fixture selects four original theorems and inherits their original
theorem-level membership before prefix extraction: `imp_intro_02` for training,
`not_intro_05` for validation, and `iff_intro_03` / `iff_intro_04` for testing.
Its human-authored equivalent decompositions are committed in
[`fixtures/grounded-transition-reference-proofs.json`](fixtures/grounded-transition-reference-proofs.json).
Pinned Lean 4.29.1 and deterministic compiler replay retained all 9/9 prefixes:
5 nonterminal transitions and 4 completions, with zero exclusions or failures.
There are 2 train states, 2 validation states, and 5 held-out states. The
decompositions are benchmark construction, not automatically mined training data.

Before curated inference, a separate initial-state projection selects the empty
accepted prefix on each held-out theorem while retaining all train/validation
states. Thus the bounded curated comparison has **2 held-out states × 8 matched
conditions**, rather than evaluating all five later-prefix states. Its manifest
SHA256 is `6be1d230089c00a4372c0917885e771ac60eda068ea1dbcba3278d9c76e7cbce`;
`curated-initial-projection/selection-frozen.json` records the frozen selection,
and the projected manifest links the full curriculum by hash. Later-prefix
performance is not measured by this bounded smoke.

Curated source hashes, config, journal, before/after states, plans, and manifest
are separate under `runs-grounded/grounded-transition-20261015/curated-input/`
and `curated-curriculum/`. Original default artifacts remain intact.

Both online paths receive only theorem identity, accepted past prefix, current
state hash, split, and transition identity. They replay and hash-check the current
state before solving. Reference next tactics and verified successors stay offline;
there is no theorem/embedding/proof-similarity retrieval.

## Test evidence

The deterministic fake-backend CLI test exercises the actual configuration,
curriculum projection, scheduler, replay adapter, staged generators, compiler,
policy trainer, JSONL writer, and readable-trace renderer. A controlled verifier
accepts an introductory nonterminal step, rejects the conventional finisher, then
accepts a second analogy's compiled completion. Both train and validation episodes
contain two connected iterations. Discounted sample rewards are 0.94 and 1.0
(mean 0.97). This establishes integration behavior with fake external inference;
it does not measure Qwen or Lean proof-search performance.

Regression tests also preserve the first useful action when a later iteration
has compilation, malformed-schema, or runtime failure, and reject samples without
a verifier-accepted novel successor or completion. Checkpoint tests reject
different-curriculum and raw-only metadata before loading policy weights.

The latest focused suite passed 127 tests (1 optional test skipped). A full run
overlapping the primary GPU benchmark had 3 deadline failures / 1218 passes /
1 skip: `test_three_condition_smoke_uses_real_lean_and_shared_pool` (10-second
theorem timeout), `test_launcher_defaults_to_three_mixed_problems_across_all_conditions`
(60-second child timeout), and the first `iff_forward` parameter of
`test_compiled_candidates_with_pinned_lean` (10-second Lean structure timeout).
The failed trial files and failure summary are preserved beside the benchmark.
With GPU work idle, the three exact failures plus an iff-forward control passed
(4 tests, 38.08 seconds), then the unchanged full suite passed **1221 tests /
1 skip in 179.67 seconds**. The contended run took 499.64 seconds. This supports
resource contention rather than a reproducible semantic or test-order regression;
the precise CPU/memory mechanism was not isolated. No timeout was increased and
no production change was made in response to those three failures. The idle
reproduction and full outputs are retained in `idle-failure-reproduction.txt`
and `full-suite-idle.txt`.

After the final measured-generation counter regression and fix, focused
verification returned **127 passed / 1 skip**, and the final complete idle command
returned **1222 passed / 1 skip in 156.27 seconds**. Its unabridged output is
`full-suite-final.txt`. `git diff --check` also passed.

## Reproduction and raw evidence

`config.mlx-8b-grounded-analogy.yaml` uses 512 output tokens, ordinary width 3,
8 ordinary expansions, at most 8 model generations / 6 Lean calls per rescue
rollout, at most 3 analogy iterations, and a nominal 360-second episode deadline
checked between calls. All conditions share these configured caps and seed 0.
Actual call counts may differ with early termination and multiple search states.
The raw decoder is an explicitly separate unsteered ablation. Source shuffling
uses only different held-out theorem sources with the same proof-need signature.
An unavailable compatible donor is reported separately from model rejection.

The primary harness is `runs-grounded/grounded-transition-20261015/smoke.py`;
the curated wrapper is `curated-run/driver.py`. Each creates a fresh output
directory and records prompts, raw outputs, source-lens deltas, actual Lean source
and subprocess results, checkpoints, JSONL, readable traces, resolved config,
source hashes, elapsed counts, and an artifact hash manifest. The earlier
`runs-fast/generated-domain-20261015` evidence is untouched.

The configured model alias is `mlx-community/Qwen3-8B-4bit`. The sole observed
local cache snapshot is `545dc4251c05440727734bcd94334791f6ab0192`;
`model-cache-identity.json` records its reference, blob paths/sizes, small-file
hashes, and package versions. The backend itself reports the repository alias,
so this snapshot attribution comes from cache inspection rather than an immutable
revision returned by the loader. Weight shards were not rehashed. No Qwen weights
were fine-tuned or saved by these runs.

After inference, a failing regression also exposed ordinary-generation exceptions
that occurred before an expansion record and therefore escaped inferred model-call
counts. New episodes now count the generation boundary directly, including such
failures, and persist the measured count. Historical/custom episodes retain the
record-based fallback. This bookkeeping correction changes no prompts, generation
arguments, compiler behavior, or policy optimization; both report tables use the
smoke harness's exact raw-call counters and are unaffected.

Schema counts use attempted calls at that stage, including bounded repair calls.
Binding validity and grounded-plan validity are the same strict validation
transaction; they must not be multiplied as independent success rates. Compiler
coverage uses validated plans as its denominator. Lean acceptance uses actual
rescue tactic attempts (including conventional finishing), and second-analogy
continuation uses prior novel nonterminal iterations as opportunities. Empty
denominators indicate unavailable measurements. Episode budget counters omit
prefix reconstruction and final-proof validation; raw subprocess logs retain them.
