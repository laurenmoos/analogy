# MLX Qwen3-8B Runtime Benchmark

Date: 2026-10-15 (America/Los_Angeles)

This benchmark verifies that the existing frozen-model analogy-RL pipeline runs
locally with latent activation steering. It is a runtime and integration smoke,
not evidence of proof-quality improvement.

## Runtime

- Hardware: Apple M3 MacBook Air, 8 CPU cores, 24 GB unified memory
- OS: macOS 15.4.1, arm64
- Model: `mlx-community/Qwen3-8B-4bit` (4-bit, 4.3 GiB local cache)
- Model revision: `545dc4251c05440727734bcd94334791f6ab0192`
- Python 3.14.7; MLX 0.32.4; MLX-LM 0.29.1; Transformers 4.57.6;
  PyTorch 2.14.1
- Profile: 256 output tokens, 45-second call ceiling, deterministic decoding,
  3 analogy iterations, 8 model calls, and 6 rescue Lean calls per episode

Three offline probe runs produced these p50 latencies:

| Operation | p50 |
|---|---:|
| Cold model load | 2.81 s |
| Ordinary or latent-steered generation | 3.26 s |
| Proof embedding | 0.13 s |
| Two-candidate batched scoring | 7.06 s |
| Lean tactic check | 1.70 s |

The operation-count projection is about 5.2 minutes for the 12-episode smoke
and 17.2 minutes for a 40-episode run. The configured nominal budget totals are
72 minutes and 4 hours respectively: eight model calls at the full 45-second
ceiling per episode. They are not guaranteed wall-clock upper bounds because
the model timeout is checked after an in-flight call returns.

Probe artifacts:

- `runs-fast/probes/700ea853fbc747dc9e44fbf644348bfd/runtime.json`
- `runs-fast/probes/99898c447f6b42e69317b8df14533ca1/runtime.json`
- `runs-fast/probes/9684ff7197864a3584bb20b117e315f3/runtime.json`

A separate bounded diagnostic used the same prompt and seed with four
deterministic nonzero final-layer directions. All four changed the sampled
output relative to the unsteered baseline, confirming that the intervention
now reaches logits after MLX-LM's split prefill schedule.

## Six-problem smoke

The smoke used two hard problems from each requested connective:
`imp_intro_04`, `imp_intro_05`, `not_intro_04`, `not_intro_05`,
`iff_intro_04`, and `iff_intro_05`. Training took 140.06 seconds and the final
four-condition evaluation took 151.54 seconds, for 291.60 seconds (4.86 minutes)
total.

The completed run recorded:

- 12 problem episodes across training and matched evaluation;
- 18 rescue-generation attempt records, 64 episode-reported Lean calls, and 10
  rescue rollouts (these scoped counters exclude ordinary backend generations
  and verifier initialization calls);
- 8 logged analogy iterations;
- valid-generation rates of 1.0 for both the matched analogy and shuffled-analogy
  conditions;
- zero solved problems in every evaluation condition.

The zero solve count is not a positive or negative quality conclusion at this
sample size. The useful result is that ordinary search, direct rescue, iterative
analogy rescue, shuffled analogy control, latent deltas, proof embeddings, Lean
checks, checkpointing, and immutable thought-stream logs all executed end to
end with the exact 8B model.

Smoke artifacts:

- `runs-fast/smoke-6-20261015T153554Z/smoke-report.json`
- `runs-fast/smoke-6-20261015T153554Z/rescue/train/`
- `runs-fast/smoke-6-20261015T153554Z/rescue/evaluate-epoch-0001/`

## Evidence-backed tuning

The first pass showed that a 96-token cap truncated valid-looking JSON before
its closing fields. Raising the cap to 256 exposed a second issue: unconstrained
cards spent most tokens on prose. The final profile therefore keeps 256 tokens,
allows 45 seconds for long iff prompts, and asks for a 120-word structured card
with explicit per-field limits. This both completed the strict schema and made
the associative stream easier to inspect.

## Follow-up measurement protocol

Future analogy-rescue runs report verifier-backed intermediate behavior in
addition to solved counts:

- Lean-accepted tactic rate counts only `accepted` and `complete` verifier
  results;
- successor rate is the fraction of analogy iterations that produce a new
  proof state;
- diagnostic repair attempts, successes, and success rate measure the bounded
  Lean-feedback repair pass;
- non-null-surprise rate shows how often a verified transition made surprise
  measurement possible; and
- completions per Lean call retains the end-to-end efficiency measure.

The first ablation keeps the same seed, six hard problems, model, search width,
and resource limits. Compare baseline, Lean-4 prompt only, diagnostic repair
only, and both changes. Any later easy/medium mechanics suite must be reported
separately from this hard suite so it supplies debugging signal without
weakening the benchmark.
