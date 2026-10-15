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
| Cold model load | 2.67 s |
| Ordinary or latent-steered generation | 2.95 s |
| Proof embedding | 0.11 s |
| Two-candidate batched scoring | 5.32 s |
| Lean tactic check | 1.58 s |

The operation-count projection is about 2.4 minutes for the six-problem smoke
and 15.8 minutes for a 40-episode run. The configured hard bounds are 36 minutes
and 4 hours respectively. These bounds are deliberately conservative: eight
model calls at the full 45-second ceiling per episode.

Probe artifacts:

- `runs-fast/probes/507c0d1bc7344854a1f7098dac0b8c2c/runtime.json`
- `runs-fast/probes/09477c3110fa44d4b3e0b732320abcf8/runtime.json`
- `runs-fast/probes/5225d868e5a8410980a151ca922dd1fd/runtime.json`

## Six-problem smoke

The smoke used two hard problems from each requested connective:
`imp_intro_04`, `imp_intro_05`, `not_intro_04`, `not_intro_05`,
`iff_intro_04`, and `iff_intro_05`. Training took about 233 seconds from artifact
timestamps; the final four-condition evaluation took 139.39 seconds, for about
6.2 minutes total. Long proof-state prompts explain the gap from the simple
operation-count projection.

The completed run recorded:

- 12 problem episodes across training and matched evaluation;
- 20 model generations, 63 Lean calls, 10 rescue rollouts;
- 7 logged analogy iterations;
- valid-generation rates of 1.0 for both the matched analogy and shuffled-analogy
  conditions;
- zero solved problems in every evaluation condition.

The zero solve count is not a positive or negative quality conclusion at this
sample size. The useful result is that ordinary search, direct rescue, iterative
analogy rescue, shuffled analogy control, latent deltas, proof embeddings, Lean
checks, checkpointing, and immutable thought-stream logs all executed end to
end with the exact 8B model.

Smoke artifacts:

- `runs-fast/smoke-6-20261015T065317Z/smoke-report.json`
- `runs-fast/smoke-6-20261015T065317Z/rescue/train/`
- `runs-fast/smoke-6-20261015T065317Z/rescue/evaluate-epoch-0001/`

## Evidence-backed tuning

The first pass showed that a 96-token cap truncated valid-looking JSON before
its closing fields. Raising the cap to 256 exposed a second issue: unconstrained
cards spent most tokens on prose. The final profile therefore keeps 256 tokens,
allows 45 seconds for long iff prompts, and asks for a 120-word structured card
with explicit per-field limits. This both completed the strict schema and made
the associative stream easier to inspect.
