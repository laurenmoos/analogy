# Analogy-guided Lean proof search

This repository has one proof-search runtime. Conventional tactic generation owns the frontier,
verification, progress checks, budgets, and backtracking. When a conventional expansion is stuck,
the runtime may generate one analogy and translate it into exactly one Lean tactic.

The only behavior switch is `analogy.enabled` in `config.yaml`:

```bash
uv run python -m analogy solve --analogy
uv run python -m analogy solve --no-analogy
```

Without either CLI override, the configured boolean is used. Both settings execute the same
controller; disabling analogy causes stuck states to follow ordinary failure/backtracking with no
analogy model call. `analogy.max_model_calls` limits malformed-output repair attempts per stuck
state. Once one response satisfies the schema, at most one analogy-guided tactic reaches Lean.

Each run writes `run.json`, per-problem JSON, and a readable `report.md` beneath
`.artifacts/runs/solve/<run-id>/`. `analogy_fallback_count` counts analogy tactics actually submitted
to Lean, not malformed generations.

Historical activation-evaluation artifacts under `.artifacts/` are retained as evidence, but their
former batch-selection and multi-arm runtime is not part of the package.
