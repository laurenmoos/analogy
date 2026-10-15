# Analogy Repair Smoke Benchmark

Date: 2026-10-15 (America/Los_Angeles)

This is a directional six-problem smoke test of the combined low-hanging-fruit
changes: a Lean-4-specific decoder prompt, four candidates instead of two, one
diagnostic repair pass, same-state refinement when budget remains, and new
verifier-backed metrics. It uses the same problem IDs, Qwen3-8B 4-bit model,
seed, search width, and per-episode model/Lean limits as the previous smoke.
The local MLX package was 0.32.0 rather than the prior recorded 0.32.4, so this
is not a statistically controlled quality claim.

## Result

Artifact timestamps span 212 seconds for training and 423 seconds for matched
evaluation: 635 seconds total (10.58 minutes), versus 291.60 seconds (4.86
minutes) for the prior two-candidate run. The wider pool and fuller use of the
fixed verifier budget cost about 2.2x wall time.

Training still solved 1/2 implication problems and 0/2 validation problems.
All four evaluation conditions remained at 0/2 solves.

| Evaluation condition | Lean-accepted tactic rate | Successor rate | Repair successes / attempts | Non-null surprise rate | Lean calls |
|---|---:|---:|---:|---:|---:|
| conventional | 0.000 | 0.000 | 0 / 0 | 0.000 | 12 |
| learned direct | 0.000 | 0.000 | 0 / 0 | 0.000 | 13 |
| analogy loop | 0.200 | 0.333 | 0 / 1 | 0.333 | 28 |
| shuffled analogy | 0.222 | 0.333 | 0 / 1 | 0.333 | 27 |

The prior analogy and shuffled conditions produced no successor and therefore
no surprise value. The new run produced verified successors in both conditions,
making surprise measurable (mean first observed surprise 0.091 for analogy and
0.096 for shuffled). This confirms that the prompt/candidate changes crossed
the first mechanical bottleneck. Because shuffled analogy improved similarly,
this smoke does not show that meaningful analogy selection caused the gain.

Diagnostic repair executed but did not itself produce a successor. The logs
show a remaining concrete syntax target: the model emitted `rw h1` and
`rw h1 at h2`, while Lean 4 expects bracketed rewrite rules such as `rw [h1]`.
The current repair loop also frequently reaches the six-call Lean cap after
checking four initial candidates, leaving no budget for another same-state
iteration. Those are the clearest next ablations; neither should be described
as solved by this run.

Across training and evaluation the run recorded 12 episodes, 117
episode-reported Lean calls, 12 rescue rollouts, 27 generation attempts, and 9
analogy iterations. The increased work is visible rather than hidden by the
new metrics.

## Artifacts

- Training: `runs-fast/repair-ablation-20261015/rescue/2641bdf832c14fb6b278f94325735f7b/`
- Evaluation: `runs-fast/repair-ablation-20261015/rescue/evaluate-epoch-0001/`
- Prior baseline: `runs-fast/smoke-6-20261015T153554Z/smoke-report.json`

The benchmark exposed an unrelated metadata bug: a shuffled-problem loop
variable overwrote the evaluation `run_id` in `summary.json`. The artifact
directory and all condition measurements are unaffected. A regression test now
pins the intended `evaluate-epoch-0001` identifier and the source variable has
been renamed.

