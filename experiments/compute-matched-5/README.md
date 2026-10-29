# Frozen five-trial comparison

This directory contains the exact five problem-seed trials behind the result reported in the main
README. The category-selected suite contains four unique PropL OOD theorems; OOD 26 appears under
two seeds. Both conditions use `mlx-community/Qwen3-14B-4bit` for every generation and embedding
role. The packaged layer-12 calibration records model revision
`a4d9b2df59d2c150bef02fcbe0d91046b7ca33a4`.

[`manifest.json`](manifest.json) records the immutable file hashes, run IDs, trial outcomes, and
intervention metrics.

Run the four configurations from the repository root:

```bash
uv run python -m analogy solve --config experiments/compute-matched-5/deliberation-seed0.yaml --deliberation --all-problems
uv run python -m analogy solve --config experiments/compute-matched-5/analogy-seed0.yaml --analogy --all-problems
uv run python -m analogy solve --config experiments/compute-matched-5/deliberation-seed1.yaml --deliberation --all-problems
uv run python -m analogy solve --config experiments/compute-matched-5/analogy-seed1.yaml --analogy --all-problems
```

## Recorded result

| Trial | Seed | Direct deliberation | Analogy |
|---|---:|---|---|
| PropL OOD 09 | 0 | Exhausted | Solved |
| PropL OOD 19 | 1 | Exhausted | Solved |
| PropL OOD 26 | 0 | Exhausted | Exhausted |
| PropL OOD 26 | 1 | Solved | Solved |
| PropL OOD 18 | 0 | Solved | Solved |

Analogy solved 4/5 trials and direct deliberation solved 2/5. The `results/` directory preserves the
complete per-problem JSON, prompts, responses, search traces, proofs, run summaries, and readable
reports from the four recorded runs. These artifacts predate seed persistence inside individual model
events; the four frozen YAML files preserve the run seeds, and current runs persist each generation
seed directly.
