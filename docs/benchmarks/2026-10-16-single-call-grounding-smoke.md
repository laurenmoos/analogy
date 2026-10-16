# Single-call grounding smoke report — 2026-10-16

## Scope and provenance

This report verifies the production story/operator path and the standalone
direct-choice versus story-mediated ablation. The implementation base was commit
`f2fffa4` on
`codex/single-call-grounding`.

- Host: Apple Silicon (`arm64`, Darwin `24.4.0`, macOS `15.4.1`).
- Python environment: repository `.venv`, invoked with `PYTHONPATH=src`.
- Production model profile: MLX Qwen3-8B configuration.
- Real-model runtime: `mlx 0.32.4`, `mlx-lm 0.29.1`, locally cached
  `mlx-community/Qwen3-8B-4bit`, Metal, hidden size 4096.
- Lean: the repository's pinned subprocess verifier.
- Prompt catalog hash:
  `b4c4e782f7a69ce2011377e25134eed15780c9a12367fddc71e879a8715877da`.
- Production prompt: `story-operator-v2`.
- Ablation prompts: `ablation-plain-v1` and `ablation-story-v2`.

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
119 passed in 7.37s
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
| Plain | 40 | 0 | 16 | 16 | 24 | 26,584 | 1,040 | 0.000721 | 16 |
| Story | 40 | 0 | 16 | 16 | 24 | 72,248 | 3,560 | 0.000715 | 16 |

The fixed fake backend always selected `exact_local`, so paired first-pick and
any-candidate deltas were all zero. This checks experiment symmetry and artifact
accounting, not story quality. It also explains the 24 zero-application outcomes
per arm. The story prompt was 2.718× longer, its output was 3.423× longer, and
candidate counts were equal. Fake model timing is too small to compare. These ratios make prompt
dominance visible instead of hiding it behind aggregate accuracy.

The fake backend produced one unique story per fixture and therefore zero
fixtures with multiple stories. The metric is wired correctly, but only a real
model run can test whether distinct latent nudges yield distinct narratives.

## Real-Qwen and production execution

The first sandboxed attempt reported that the MLX extra was not installed. That
diagnosis was false: Metal device initialization raises `ImportError` when the
sandbox hides the GPU, and the backend collapsed every such error into the
missing-extra message. The diagnostic now distinguishes a missing package from
an unavailable Metal runtime. Running with explicit host GPU access loaded the
cached model successfully.

The actual production `story_operator` prompt, parser, grounding enumerator,
compiler, and Lean verifier were then run on three hard fixtures:

```text
PYTHONPATH=src .venv/bin/python scripts/smoke_story_operator_production.py \
  --config config.mlx-8b-grounded-analogy.yaml \
  --fixtures tests/fixtures/story_operator_ambiguous.json \
  --max-fixtures 3
```

Each state made exactly one Qwen call and no repair call. All three outputs were
valid strict JSON with formula-free everyday stories. One state selected
`exact_local`, produced one candidate, and Lean completed the proof. Two states
selected operators with zero valid applications, so no tactic was fabricated and
Lean was not called. Production success was therefore 1/3 on this deliberately
ambiguous sample. This is a model-choice limitation, not a runtime or schema
failure.

The final matched real-model command used three fixtures, two latent samples,
crossed order, and real Lean verification:

```text
PYTHONPATH=src .venv/bin/python scripts/benchmark_story_operator_ablation.py \
  --config config.mlx-8b-grounded-analogy.yaml \
  --fixtures tests/fixtures/story_operator_ambiguous.json \
  --output-dir /tmp/analogy-real-qwen-20261016-v6 \
  --max-fixtures 3 --latent-samples 2
```

| Arm | Calls | Invalid | First-pick | Any-candidate | Zero applications | Prompt tokens | Output tokens | Model seconds | Lean calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Plain | 6 | 0 | 6 | 6 | 0 | 1,048 | 44 | 13.039 | 8 |
| Story | 6 | 0 | 4 | 4 | 2 | 2,414 | 400 | 53.779 | 4 |

On this bounded smoke, direct selection beat story-mediated selection on both
first-pick and any-candidate success. The story arm used 2.30× as many prompt
tokens, 9.09× as many output tokens, and 4.12× model time. Both story failures
were paired failures on `equality_or_apply`, where the story chose `exact_local`
instead of `apply_implication` and deterministic grounding correctly returned
zero applications.

The two distinct latent nudges produced the same story for every fixture:
mean unique stories per fixture was 1.0 and zero fixtures had multiple stories.
Thus the requested diversity check is operational, and its observed result is
negative for this deterministic Qwen configuration. The story prompt dominates
cost and does not improve accuracy on this sample; a larger run is needed before
changing the production research condition, but these results do not support a
story benefit.

## Compatibility boundary

Current curriculum artifacts use `grounded-curriculum-v2` and
`grounded-application-v1`. Old staged generated-domain checkpoints, v1
curriculum manifests, grounded-plan traces, and staged prompt schemas are
intentionally incompatible. Historical sealed reports remain documentation only.
