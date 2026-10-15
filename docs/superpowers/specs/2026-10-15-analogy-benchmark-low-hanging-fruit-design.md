# Analogy Benchmark Low-Hanging-Fruit Design

## Goal

Improve proof-rescue benchmark performance by converting otherwise useful analogy cards into valid Lean 4 tactics, without hiding the reasoning or weakening the hard benchmark.

## Approved changes

1. Add one bounded semantic repair pass after Lean rejects every tactic in an analogy-conditioned plan. The repair prompt receives the current proof state, the analogy stream, the rejected tactics, and their exact Lean diagnostics.
2. Make the tactic prompt explicitly Lean-4-specific: prefer `intro`, `fun`, `.mp`, `.mpr`, and `rw`; forbid pseudo-Lean forms observed in the smoke run (`assume`, `iff.elim`, `iff.elim_left`, and comma-separated tactic programs).
3. Request and verify four distinct candidates per generation in the fast 8B profile, while preserving the existing global model and Lean budgets.
4. When a plan and its repair produce no successor, permit another bounded analogy refinement on the same proof state. Prior rejected tactics and diagnostics must be visible to the next hop. Resource limits and `max_iterations` remain hard stops.
5. Report behavioral intermediate metrics: Lean-accepted tactic rate, successor rate, diagnostic repair attempts and successes, non-null-surprise rate, and completions per Lean call. Keep existing hard-suite results separate from any future stratified suite.

## Architecture

`AnalogyTacticGenerator` owns both initial decoding and diagnostic-guided repair because both share strict parsing, latent steering, and generation accounting. `AnalogyLoopRunner` owns the bounded control flow: initial verification, at most one semantic repair, and same-state refinement if no transition is found. Each iteration records the initial plan, repair plan/attempts, all Lean attempts, and whether it refined without progress.

Benchmark metrics are derived only from persisted rollout records; no result is inferred from model text. A tactic is Lean-accepted only when its `TacticResult` status is accepted or complete, repair succeeds only when a repaired tactic completes or produces a successor, and surprise availability is measured from actual transition comparisons.

## Constraints

- Preserve strict JSON schemas and corpus-free operation.
- Do not add a model reranking call in the default fast profile.
- Never exceed configured model-call, Lean-call, iteration, cancellation, or wall-clock limits.
- Do not retry runtime model failures as semantic repairs.
- Keep the loop readable and keep every new decision visible in JSONL artifacts.

