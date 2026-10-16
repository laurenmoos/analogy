# Single-call grounding smoke report — 2026-10-16

## Scope and provenance

This report verifies the production story/operator path and the standalone
direct-choice versus story-mediated ablation. The implementation base was commit
`b746769ebc5be08cedf7d5e19ecf907160a27dec` on
`codex/single-call-grounding`.

- Host: Apple Silicon (`arm64`, Darwin `24.4.0`, macOS `15.4.1`).
- Python environment: repository `.venv`, invoked with `PYTHONPATH=src`.
- Production model profile: MLX Qwen3-8B configuration.
- Real-model availability: unavailable in this environment because the MLX
  optional dependency is not installed.
- Lean: the repository's pinned subprocess verifier.
- Prompt catalog hash:
  `e1e4dbafdd98c3bb44c41f1d3c683abb456cbcf079610633f813384daf0f34f9`.
- Production prompt: `story-operator-v1`.
- Ablation prompts: `ablation-plain-v1` and `ablation-story-v1`.

The enabled operator configuration was:

```text
exact_local, introduce, apply_implication, iff_forward, iff_backward,
rewrite_forward, rewrite_backward, split, select_left, select_right,
choose_left, choose_right, cases, contradiction
```

## Correctness evidence

The complete suite passed:

```text
PYTHONPATH=src .venv/bin/python -m pytest -q
117 passed in 8.18s
```

The focused real-Lean integration suite passed:

```text
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_lean_end_to_end.py tests/test_grounding.py tests/test_tactic_compiler.py
24 passed in 2.92s
```

All six root `config*.yaml` profiles loaded with the appropriate strict loader.
The static source audit found no model-facing instruction phrases or direct
instructional `Message(...)` construction in `src/analogy`.

## Production call-count speedup

The reproducible command was:

```text
PYTHONPATH=src .venv/bin/python scripts/benchmark_single_call_pipeline.py \
  --config config.mlx-8b-grounded-analogy.yaml --states 100
```

For 100 valid reconstructed inventory states, the removed staged design's
recorded source, alignment, transfer, and grounding boundaries require 400 model
calls. The current path made 100 model calls and zero repair calls.

| Metric | Removed staged path | Current path |
|---|---:|---:|
| Model calls | 400 | 100 |
| Calls per valid attempt | 4 | 1 |
| Repair calls | staged path allowed repairs | 0 |

The verified reduction is **75% fewer model calls**, or **4× fewer calls**. This
is the speedup headline; Lean time is intentionally excluded.

The current deterministic pipeline produced 300 compiled candidates and sent
300 results through the fake-Lean boundary. Its separated timings were:

| Stage | Seconds |
|---|---:|
| One-call model render/generate/parse | 0.004806 |
| Application enumeration | 0.002667 |
| Compilation and deduplication | 0.001827 |
| Fake-Lean result boundary | 0.000052 |
| Current pipeline wall time | 0.009752 |

The recorded old four-call boundary took `0.000708` seconds with a trivial fake
backend. It does not render or parse the deleted prompts, so it is retained only
as a call-counter timing and is not a valid latency comparison. Real model
latency should scale primarily with the verified 4:1 call-count change.

## Standalone matched ablation smoke

The fake-backend experiment used 10 ambiguous Lean fixtures, four latent samples
per fixture, crossed arm order, identical seeds/deltas, and one call per arm per
pair. The two arms each made 40 calls; there was no call-count mismatch and the
first-minus-second order success delta was zero for both arms.

| Arm | Calls | Invalid | First-pick successes | Any-candidate successes | Zero applications | Prompt chars | Output chars | Model seconds | Lean calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Plain | 40 | 0 | 16 | 16 | 24 | 26,584 | 1,040 | 0.000539 | 16 |
| Story | 40 | 0 | 16 | 16 | 24 | 63,348 | 3,560 | 0.000596 | 16 |

The fixed fake backend always selected `exact_local`, so paired first-pick and
any-candidate deltas were all zero. This checks experiment symmetry and artifact
accounting, not story quality. It also explains the 24 zero-application outcomes
per arm. The story prompt was 2.383× longer, its output was 3.423× longer, model
latency was 1.106×, and candidate counts were equal. These ratios make prompt
dominance visible instead of hiding it behind aggregate accuracy.

The fake backend produced one unique story per fixture and therefore zero
fixtures with multiple stories. The metric is wired correctly, but only a real
model run can test whether distinct latent nudges yield distinct narratives.

## Real-Qwen attempt

The bounded command was attempted with the real configured backend:

```text
PYTHONPATH=src .venv/bin/python scripts/benchmark_story_operator_ablation.py \
  --config config.mlx-8b-grounded-analogy.yaml \
  --fixtures tests/fixtures/story_operator_ambiguous.json \
  --output-dir /tmp/analogy-real-smoke.*/results
```

It exited with status 2 before loading a model:

```text
story operator ablation failed: ModelConfigurationError: the MLX extra is not installed; install analogy[mlx]
```

Consequently, this report makes no claim about real-Qwen paired accuracy or
latent story diversity. The fake-backend, parser, deterministic grounding,
compiler, and real-Lean verification evidence remain independent of that missing
optional runtime.

## Compatibility boundary

Current curriculum artifacts use `grounded-curriculum-v2` and
`grounded-application-v1`. Old staged generated-domain checkpoints, v1
curriculum manifests, grounded-plan traces, and staged prompt schemas are
intentionally incompatible. Historical sealed reports remain documentation only.
