# Generated-domain analogy smoke — 2026-10-15

## Relative performance first

**No learned generated-domain advantage was demonstrated.** In the preserved
primary run, learned, random, and unsteered generated analogies each completed
0/2 held-out iff problems. Direct rescue and legacy cards also completed 0/2.
All methods had zero novel nonterminal rescue successors and zero repeated-state
discards. The repaired 256-token comparison again tied at 0/2, despite advancing
one example farther through the schema stages. This is a five-example integration
smoke test, with only two held-out examples—not an estimate of generalization.
The 512-token retry improved stage throughput but still completed no theorem;
one learned case is infrastructure-censored by a Lean structure-extraction
timeout, so that run cannot support a clean learned-versus-unsteered comparison.

The primary all-five diagnostic is deliberately separate below: it includes the
two training implications and the validation negation. Its one completion cannot
be presented as held-out learning evidence.

### Preserved primary: held-out iff only

| Condition | Completed | Accepted rescue tactics / attempts | Novel open successors | Repeats | Episode Lean / actual subprocesses | Model generations | Completions / actual Lean |
|---|---:|---:|---:|---:|---:|---:|---:|
| Conventional | 0/2 | — (0 attempts) | 0 | 0 | 6 / 8 | 2 | 0/8 |
| Learned direct | 0/2 | 0/3 | 0 | 0 | 9 / 11 | 4 | 0/11 |
| Legacy cards | 0/2 | 0/5 | 0 | 0 | 13 / 15 | 8 | 0/15 |
| Generated unsteered | 0/2 | — (0 attempts) | 0 | 0 | 8 / 10 | 7 | 0/10 |
| Generated random | 0/2 | — (0 attempts) | 0 | 0 | 8 / 10 | 7 | 0/10 |
| Generated learned | 0/2 | — (0 attempts) | 0 | 0 | 8 / 10 | 7 | 0/10 |
| Generated shuffled | 0/2 | — (0 attempts) | 0 | 0 | 8 / 10 | 5 | 0/10 |

### Repaired 256: checkpoint-matched held-out comparison

| Condition | Completed | Accepted rescue tactics / attempts | Novel open successors | Repeats | Episode Lean / actual subprocesses | Model generations | Completions / actual Lean |
|---|---:|---:|---:|---:|---:|---:|---:|
| Conventional (primary reused) | 0/2 | — (0 attempts) | 0 | 0 | 6 / 8 | 2 | 0/8 |
| Learned direct (retrained checkpoint) | 0/2 | 0/3 | 0 | 0 | 9 / 11 | 4 | 0/11 |
| Legacy cards (retrained checkpoint) | 0/2 | 0/5 | 0 | 0 | 13 / 15 | 8 | 0/15 |
| Generated unsteered | 0/2 | — (0 attempts) | 0 | 0 | 8 / 10 | 8 | 0/10 |
| Generated random | 0/2 | — (0 attempts) | 0 | 0 | 8 / 10 | 8 | 0/10 |
| Generated learned | 0/2 | — (0 attempts) | 0 | 0 | 8 / 10 | 8 | 0/10 |
| Generated shuffled | 0/2 | — (0 attempts) | 0 | 0 | 8 / 10 | 5 | 0/10 |

The conventional solver does not consume a policy checkpoint, so its original
result is reused. Direct and legacy rescues were rerun with each repaired run's
checkpoint; their primary reference results remain visible above. Configured
caps are matched; realized call counts differ due to early stage failure.
Zero tactic attempts means no acceptance rate is estimable, not a 0% or 100%
tactic-quality result. Actual Lean subprocess counts include initial-state and
final-proof validation omitted from episode budget counters. Model generations
include ordinary search plus rescue, but exclude embedding calls.
With zero held-out completions, model/Lean calls **per completion** are undefined;
the displayed completion-per-call numerators are all zero.

### Repaired 512: matched run, with an infrastructure failure retained

| Condition | Completed | Accepted rescue tactics / attempts | Novel open successors | Repeats | Episode Lean / actual subprocesses | Model generations | Completions / actual Lean |
|---|---:|---:|---:|---:|---:|---:|---:|
| Conventional (primary reused) | 0/2 | — (0 attempts) | 0 | 0 | 6 / 8 | 2 | 0/8 |
| Learned direct (retrained checkpoint) | 0/2 | 0/3 | 0 | 0 | 9 / 11 | 4 | 0/11 |
| Legacy cards (retrained checkpoint) | 0/2 | 0/5 | 0 | 0 | 13 / 15 | 8 | 0/15 |
| Generated unsteered | 0/2 | 0/4 | 0 | 0 | 12 / 14 | 13 | 0/14 |
| Generated random | 0/2 | 0/4 | 0 | 0 | 12 / 14 | 13 | 0/14 |
| Generated learned (one infrastructure failure) | 0/2 operational | — (0 attempts) | 0 | 0 | 8 / 10 | 9 | 0/10 |
| Generated shuffled | 0/2 | — (0 attempts) | 0 | 0 | 8 / 10 | 5 | 0/10 |

The third run preserves repaired code and changes only the output cap to 512
for all six rerun rescue conditions and training. Conventional is reused because
it is checkpoint-independent and its two ordinary outputs ended below 256.
Generated learned's `iff_intro_05` stopped before source generation:
`ProofViewError: Lean structure extraction failed: Lean exceeded 10 seconds`.
Its 0/2 operational result includes one untested model case; lower calls are
not an efficiency win. A separately labeled same-checkpoint/config/seed/timeout
diagnostic is retained solely to classify this failure, never to replace this
matched-run row.

That one-case diagnostic completed in 142.607 seconds with the same saved
checkpoint and resolved config. Structure extraction succeeded; all four schemas
passed, followed by the same four rejected Lean tactics seen for unsteered/random.
It completed 0/1, used five generations, two embeddings, and nine actual Lean
subprocesses (eight episode-reported calls). The timeout was **not reproduced in
this one retry**; that does not prove it cannot recur. Its result is not pooled
into the matched learned row.

## Primary all-five diagnostic (in-sample)

| Condition | Completed | Accepted rescue tactics / attempts | Episode Lean / actual subprocesses | Model generations | Completions / actual Lean |
|---|---:|---:|---:|---:|---:|
| Conventional | 0/5 | — (0 attempts) | 24 / 29 | 8 | 0/29 |
| Learned direct | 1/5 | 1/6 | 30 / 36 | 13 | 1/36 |
| Legacy cards | 1/5 | 1/19 | 48 / 54 | 23 | 1/54 |
| Generated unsteered | 1/5 | 1/1 | 30 / 36 | 23 | 1/36 |
| Generated random | 1/5 | 1/1 | 30 / 36 | 23 | 1/36 |
| Generated learned | 1/5 | 1/1 | 30 / 36 | 23 | 1/36 |
| Generated shuffled | 0/5 | — (0 attempts) | 29 / 34 | 15 | 0/34 |

Every completion is `imp_intro_05`. Ordinary search accepted the same three
reductions, from `U` through `T` and `Q` to `P`; rescue then completed with
`exact p`. The primary generated conditions each have a 1/1 accepted-tactic
fraction because four other examples failed before emitting any tactics. That
selection effect is not evidence of superior tactic quality. All conditions
have zero novel open rescue successors, repeated states, multi-analogy rollouts,
and conventional-finisher attempts. Training itself completed 1/2 implications;
validation completed 0/1 negations.
For those in-sample completions, model-generation/actual-Lean calls per completion
are 13/36 for direct, 23/54 for legacy, and 23/36 for each nonshuffled generated
condition. Mean analogy iterations per rescue invocation are 1.0 for generated
conditions (all-five and held-out), 0.8 for legacy all-five, and 0.5 for legacy
held-out. None continued to a second analogy.

## Stage throughput and model interpretations

These are schema-validation measurements, not Lean correctness judgments.
“Valid source” means the configured boundary checks passed, not that the analogy
is deep, useful, independent information, or causally responsible for progress.

| Run / scope / condition | Source valid / attempts | Alignment valid / attempts | Reasoning valid / attempts | Tactic-plan valid / attempts |
|---|---:|---:|---:|---:|
| Primary all five, each unsteered/random/learned | 3/7 | 2/4 | 1/3 | 1/1 |
| Primary held-out, each unsteered/random/learned | 1/3 | 0/2 | 0/0 | 0/0 |
| Repaired-256 held-out, each unsteered/random/learned | 1/3 | 1/1 | 0/2 | 0/0 |
| Repaired-512 held-out, each unsteered/random | 2/3 | 2/2 | 2/3 | 1/3 |
| Repaired-512 held-out, learned (one infrastructure failure) | 1/2 | 1/1 | 1/2 | 0/2 |

Attempt denominators include the one allowed repair per stage. Primary source,
alignment, and reasoning repairs per five-case condition were respectively 2,
1, and 1. All three generated conditions produced byte-identical source outputs
(including repairs) for each primary example. Source-level latent steering did
not yield observable source diversity on these deterministic examples.

Primary valid domain labels were `cooking` (two cases) and `Home Cooking` (one);
these are substantively one domain, not two diverse categories. Rejected examples
also mentioned plumbing/home automation. Repaired-256's valid held-out source
was `logistics planning`. Its narrative combined resource and delivery constraints;
that is a concrete non-proof setting, but merely joining constraints does not by
itself establish a bidirectional equivalence. The model's alignment confidence
is diagnostic prose and was never used as reward or correctness evidence.

The primary source failed twice for proof-language leakage in its rationale
(`imp_intro_04`, `iff_intro_05`). `iff_intro_04` had a valid cooking source but
mapped roles to `h1`/`h2` rather than the declared proof roles, so alignment failed.
`not_intro_05` reached contradiction reasoning, but the validator falsely treated
the grammatical noun in “introduced assumption” / “our assumption B” as a tactic.
The primary result is preserved, not retrospectively rescored.

Two separately committed repairs were then tested: a transfer-only grammatical
exception preserving command rejection (`47df206`), and a source prompt requiring
all concrete fields, including rationale, to avoid proof/Lean/tactic language
and explain fit in domain-neutral transformation terms (`11b94b6`). Concrete
source validators, strict schemas, reward values, and Lean authority were not
weakened. The repaired runs use both changes and are not pooled with primary.
Because the source prompt and parser changed together before retraining, a
primary-to-repaired difference cannot isolate either repair's causal effect.

Repaired-256 training completed 0/2 and validation 0/1: all failed at source.
The held-out simple iff source truncated; the complex iff source and alignment
passed, but both reasoning attempts truncated. Of 53 total generated outputs,
20 re-tokenize to the 256-token cap: 16 malformed stage outputs and four complete
source objects exactly at the cap. Thus reaching the cap alone is not equated
with truncation. Primary had 0/144 at cap (maximum 254 tokens); its observed
stage failures were not token truncation.

Repaired-512 had 0/64 outputs at its cap (maximum 324 tokens), eliminating the
observed truncation bottleneck. Training and validation remained 0/2 and 0/1,
with concrete-source violations. On `iff_intro_04`, all three nonshuffled
generated conditions repaired the source's proof-language rationale, then
repaired reasoning that said “apply modus ponens.” The declarative repaired
reasoning passed; both tactic plans repeated `apply h2` and failed the uniqueness
schema. This is a model/schema boundary: future prompting could request
declarative inference language, but this benchmark did not relax `apply` or any
other tactic guard.

On `iff_intro_05`, unsteered and random passed all four schemas. Lean then
rejected `rw h1 at h2` and `rw h1` for missing bracket syntax, and `apply h2` /
`exact h2` because the P-based hypothesis did not match the L-based target.
No tactic changed the verified state. Both valid domains were logistics
planning—still one substantive domain. Learned never reached these stages in
the matched run because of the recorded extraction timeout.

### What this smoke actually answers

1. **Concrete domains?** Some schema-valid cooking and logistics situations were
   generated, not just proof cards. Diversity was limited, and source leakage
   remained a practical failure mode.
2. **Grounded tactic-free reasoning?** The primary `imp_intro_05` transfer passed
   and led to a Lean-checked completion. The negation reasoning was blocked by a
   lexical false positive; repaired held-out reasoning was token-truncated at
   256. At 512, valid reasoning was produced, but no rescue tactic was accepted.
   A valid alignment alone is not evidence of a correct proof transition.
3. **A genuinely new second analogy after a verified successor?** Not observed
   in any matched run: no accepted open rescue successor occurred.
4. **Did the finisher close the prior `⊢ R` near-solve?** Not demonstrated by
   any matched run. Their ordinary search stopped earlier and no rescue reached that
   open state. Fake integration coverage exercises the finisher, but does not
   substitute for a real-model observation.
5. **Better than unsteered/random under matched budgets?** No observed advantage:
   learned, random, and unsteered tied at 256 on completion and novel-successor
   metrics. All remained unsolved at 512, with one learned infrastructure failure
   preventing a clean condition comparison there.

The shuffled condition is especially limited: a donor must come from another
theorem in the same split and match operation, arities, permutation, information
flow, and declared proof roles. These two iff examples have incompatible needs
(introduce versus combine), so available valid sources could not be substituted.
Primary had three all-five donor-unavailable terminations; repaired-256 had one
held-out donor-unavailable termination and one source failure. The two
donor-unavailable terminations at 512 are not successful source substitutions.
Do not interpret the shuffled result as a successful causal ablation or evidence
that alignment helped.

## Reproduction and immutable evidence

Worktree: `/private/tmp/analogy-generated-domain-20261015`.
Stable artifact root:
[/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015).
Only finalized directories were copied there, without modifying their manifests
or original absolute-path provenance. The original temporary-worktree copies
remain. Existing October 15 legacy runs were not overwritten. Runtime artifacts
are ignored by repository convention and are not committed.

The driver calls package-owned `train_direct_rescue`, `_execute_problem`,
`RescueRunWriter`, `_persist_episode`, and the connected-trace renderer; it does
not use the standalone analogy experiment. It verifies checkpoint schema,
configuration hash, and split before loading. Raw model messages and outputs,
seeds, per-call elapsed time, every actual Lean source/result, checkpoints,
JSONL episodes/iterations, and readable traces are retained. `provenance.json`
hashes the profile, corpus, driver, package sources, Lean toolchain, and manifest;
`artifact_sha256.json` hashes every finalized artifact file except itself.

Exact example IDs and split:

- Train: `imp_intro_04`, `imp_intro_05`.
- Validation: `not_intro_05`.
- Held-out: `iff_intro_04`, `iff_intro_05`.

The profile copies `config.mlx-8b-fast.yaml`, changing only generated strategy
and minimum iterations to two. Seed is 0; greedy generation; one epoch, one
rollout; ordinary width 3, maximum expansions 8; analogy samples 1, iterations
2–3 when continuation is possible; four tactics per iteration, eight rescue
model calls, six rescue Lean calls. The 45-second model setting yields a nominal
360-second episode deadline checked between calls, not a strict interruptible
wall-clock bound. Minimum iterations cannot force continuation after malformed
generation, completion, missing shuffle donor, or exhausted budget.

Model: local `mlx-community/Qwen3-8B-4bit`, cache revision
`545dc4251c05440727734bcd94334791f6ab0192`; MLX 0.32.4, mlx-lm 0.29.1,
transformers 4.57.6, torch 2.14.1, Python 3.14.7. Lean 4.29.1 is explicitly
pinned by the verifier. `HF_HUB_OFFLINE=1` uses the existing local cache.

```bash
cd /private/tmp/analogy-generated-domain-20261015
PYTHONPATH=src HF_HUB_OFFLINE=1 /Users/laurenmoos/analogy/.venv/bin/python runs-fast/generated-domain-20261015/smoke.py --output runs-fast/generated-domain-20261015/primary-256-gpu --tokens 256
PYTHONPATH=src HF_HUB_OFFLINE=1 /Users/laurenmoos/analogy/.venv/bin/python runs-fast/generated-domain-20261015/smoke_retry.py --output runs-fast/generated-domain-20261015/repaired-256-gpu --tokens 256
PYTHONPATH=src HF_HUB_OFFLINE=1 /Users/laurenmoos/analogy/.venv/bin/python runs-fast/generated-domain-20261015/smoke_retry.py --output runs-fast/generated-domain-20261015/repaired-512-gpu --tokens 512
PYTHONPATH=src HF_HUB_OFFLINE=1 /Users/laurenmoos/analogy/.venv/bin/python runs-fast/generated-domain-20261015/timeout_retry.py
```

Each output directory is created with `exist_ok=False`; reproductions must choose
a new path. Primary used commit `fb2bcd935279c70ef8ed8998fc003d13ca149cbe`
plus the new profile/CLI tests/README. Both repairs use benchmark commit
`11b94b6e39327f4d78745c0d621df36ddd09ac90` with that same uncommitted profile/CLI documentation addition. Source
hashes identify the actual code rather than relying on a clean-tree assumption.
Profile SHA-256:
`88123f751674e459f1bb8b0d5cf5f81335f2c988f9b99c99d7bc41b234ecbff8`.
Primary resolved configuration hash:
`21f74787cc179478dfd9db18fd1f50677fca63bab250302be0ef961dce0d6070`.
Repaired-256 resolved configuration hash:
`77795de9a34a4448f3303bc59374b3d463183018e70433c71969192adfd68bac`.
Repaired-512 resolved configuration hash:
`eefb5c8e8c11b26aff65ddc48d8249c0ae14bb438b6d8d70063b71037d0f4dff`.
Resolved configs retain run-specific corpus/output paths, so their hashes differ
even where substantive settings match.

| Run | Episodes | Estimated typical time | Actual time | Generation calls | Embedding calls | Actual Lean subprocesses |
|---|---:|---:|---:|---:|---:|---:|
| Primary-256 | 38 (3 train/validation + 35 diagnostic) | 31.28 min | 40.37 min (2422.053 s) | 144 | 66 | 287 |
| Repaired-256 | 15 (3 train/validation + 12 held-out) | 12.35 min | 18.89 min (1133.349 s) | 53 | 30 | 90 |
| Repaired-512 | 15 (3 train/validation + 12 held-out) | 12.35 min | 22.27 min (1336.451 s) | 64 | 28 | 98 |
| Separate timeout diagnostic | 1 (no retraining) | about 2.3 min from the same case | 2.38 min (142.607 s) | 5 | 2 | 9 |

The prior estimate used 3.0525 seconds/generation and 1.5722 seconds/Lean call,
ten generations plus twelve Lean subprocesses per typical episode. Longer
structured JSON made actual latency higher. The coarse deadline envelope was
38 × 8 × 45 = 13,680 seconds for primary, 5,400 seconds for each retry—not a
hard bound. A sandbox-only startup failed before inference because Metal was
unavailable; `primary-256/failure.json` is preserved. The subsequent approved
local GPU executions completed; model failures inside an episode remain data.
The matched-512 run also retains the one Lean extraction timeout described above.

Metrics are extracted read-only by `extract.py` from `problems.jsonl`,
`rollouts.jsonl`, `analogy_iterations.jsonl`, and `calls.jsonl`. Completion is the
package's Lean-validated `completed` flag. Accepted rescue tactics have Lean
status `accepted` or `complete`; novel open successors require `accepted` and no
discard flag. Training actual-call instrumentation is aggregate, not attributed
separately to train versus validation. Re-tokenized lengths use the exact local
model tokenizer. No post-hoc model judgment changes these measurements.

### Post-benchmark hardening and verification

After all inference and artifact finalization, commit
`fd3fd07fc04d8b95410db82039bf76876421f559` narrowed the grammatical assumption
exception so explicit “assumption tactic/command” references remain forbidden.
Nine parser regressions failed before the change; the focused suite then passed
229 tests and the full suite passed **868 tests, 1 skipped (94.51 seconds)**.
The source-domain guards and `apply` guard were not relaxed. None of the raw
benchmark outputs contains the newly rejected phrase, and all 10 previously
valid transfer records still validate as identical structured records. Therefore
this post-benchmark hardening is recorded separately, without rerunning or
rewriting the benchmark evidence.

The initial pre-inference suite passed 850 tests, 1 skipped. After the two
pre-retry fixes it passed 859 tests, 1 skipped. An intervening full run performed
during GPU inference hit the standalone launcher's 60-second subprocess timeout;
the isolated test and subsequent idle-GPU full suite passed. This resource-related
test failure is not omitted from the task report. Final artifact verification
checked 105 bundle files, unchanged per-run manifests, matched repaired source
hashes/settings, exact diagnostic checkpoint reuse, and all local report links.
The [bundle hash index](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/bundle_sha256.json)
also covers the copied drivers and derived metrics.

## Exact connected traces

Primary generated-learned interventions, including ordinary search, rejected
tactics returning to the unchanged state, raw stage output, and parser failure:

- [imp_intro_04: fixed target versus active subgoal](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/primary-256-gpu/rescue/matched-five-diagnostic/readable_traces.md:899)
- [imp_intro_05: reductions and completion](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/primary-256-gpu/rescue/matched-five-diagnostic/readable_traces.md:1807)
- [not_intro_05: contradiction reasoning rejected by the original guard](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/primary-256-gpu/rescue/matched-five-diagnostic/readable_traces.md:3193)
- [iff_intro_04: alignment failure](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/primary-256-gpu/rescue/matched-five-diagnostic/readable_traces.md:78)
- [iff_intro_05: source failure](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/primary-256-gpu/rescue/matched-five-diagnostic/readable_traces.md:513)
- [Primary machine-readable connected iterations](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/primary-256-gpu/rescue/matched-five-diagnostic/analogy_iterations.jsonl)
- [Repaired-256 held-out connected traces](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/repaired-256-gpu/rescue/matched-heldout-retry/readable_traces.md)
- [Repaired-512 learned iff_intro_04: duplicate tactic output](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/repaired-512-gpu/rescue/matched-heldout-retry/readable_traces.md:41)
- [Repaired-512 learned iff_intro_05: extraction timeout](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/repaired-512-gpu/rescue/matched-heldout-retry/readable_traces.md:466)
- [Repaired-512 unsteered iff_intro_05: valid stages, rejected Lean attempts](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/repaired-512-gpu/rescue/matched-heldout-retry/readable_traces.md:673)
- [Separate learned iff_intro_05 timeout diagnostic](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/repaired-512-timeout-diagnostic/rescue/timeout-diagnostic/readable_traces.md)
- [Primary derived metrics](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/primary-final-metrics.json)
- [Repaired-256 derived metrics](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/repaired-256-metrics.json)
- [Repaired-512 derived metrics](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/repaired-512-metrics.json)
- [Separate timeout diagnostic metrics](/Users/laurenmoos/analogy/runs-fast/generated-domain-20261015/timeout-diagnostic-metrics.json)

These are observed traces, not fabricated versions of the desired design
fixtures. The real smoke did not reproduce the earlier two-intervention iff
trajectory or the `⊢ R` finisher scenario.
