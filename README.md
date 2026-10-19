# Analogy-guided Lean proof search

This repository has one proof-search session. Conventional tactic generation owns the frontier,
verification, progress checks, budgets, and ordinary backtracking. Every Lean-accepted OPEN
successor in the supported propositional fragment receives a deterministic SAT check before it can
enter the frontier. Countermodel-refuted successors are pruned; quantified or otherwise unsupported
states are marked unknown and remain searchable. Analogy itself remains strictly stuck-only.

After the ordinary controller declares a state stuck, it resolves the nearest propositionally valid
rescue state. Sibling countermodels are summarized by disjunctive-hypothesis side. When different
models require opposite sides of the same hypothesis, the controller first applies a deterministic
fresh-name `rcases`; otherwise analogy receives the table and asks for a different plan. If the root
is false or a stuck state cannot be checked safely, the problem ends as `semantic_failure`.

Before any Lean call, ordinary and analogy candidates share the same syntax and safety boundary:
common syntax is normalized, incomplete/unknown tactics and `tauto`/`itauto` are rejected, tactic
heads are filtered by focused-goal shape, and tactics already tried at that exact state are removed.
Removed ordinary candidates trigger bounded replacement sampling. Prompts show only the focused
goal and a compact generic Lean syntax card without per-problem solution hints.

The proof behavior switch is `analogy.enabled` in `config.yaml`:

```bash
uv run python -m analogy solve --analogy
uv run python -m analogy solve --no-analogy
```

Without either CLI override, the configured boolean is used. Both settings execute the same
ordinary controller, proactive SAT pruning, countermodel tables, and deterministic forced moves.
Disabling analogy records the same recovery decisions but makes no analogy model call.
`analogy.max_model_calls` limits malformed, duplicate, and Lean-feedback correction attempts per
stuck state. Each generation is logged; duplicate proposals are resampled without reaching Lean.

Problem scheduling is controlled by `benchmark.selection`. The default, `exhaustive`, runs the
complete 30-problem PropL OOD benchmark. Use an override when an interrupted experiment should
retry only unresolved or failed problems:

```bash
uv run python -m analogy solve --all-problems
uv run python -m analogy solve --previous-failures
```

At startup the runner creates or loads the checkpoint file `.artifacts/problem-index.json`. The
checkpoint is keyed by the corpus, prompts, model settings, search budgets, and analogy settings. A
changed experiment therefore starts with every problem unresolved instead of inheriting
incompatible results. The checkpoint is updated after each attempted problem, so an interrupted run
can restart with the same MLX model loaded once for only the remaining failures. If every compatible
problem is already complete, the command exits before initializing prompts, MLX, or Lean. If the
checkpoint is missing, compatible `run.json` files are used to reconstruct it.

Each run writes `run.json`, per-problem JSON, and a readable `report.md` beneath
`.artifacts/runs/solve/<run-id>/`. `analogy_fallback_count` counts analogy tactics actually submitted
to Lean, not malformed generations. Semantic recovery records separately show the stuck trigger,
countermodel or validation result, chosen rescue state, abandoned path tactics, dependency table,
and forced moves. Benchmark reports use one matched run of each arm on the same corpus problems.

The active corpus is a deterministic subset of the online
[PropL dataset](https://huggingface.co/datasets/KomeijiForce/PropL), pinned to revision
`62a13de410999c20f60ec2f3be93457dcaac0d62`. Each problem is intuitionistic propositional logic
from `test_ood`, contains conjunction, disjunction, and implication, has a verifier-accepted source
proof of at least 13 tactics, and is not solved by one `simp`. Selection and provenance details live
in `src/analogy/lean_gym/corpus/benchmark_metadata.json`; they are not included in model prompts.
