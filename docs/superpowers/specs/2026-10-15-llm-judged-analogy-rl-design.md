# Post-Hoc LLM Audit of Analogy RL

## Goal

Explain where the existing analogy-generating proof process succeeds or fails,
without feeding any LLM judgment back into the experiment.

## Causal boundary

Training and evaluation run exactly as they do now. The auditor runs only after a
run has finished and reads its immutable JSON/JSONL artifacts. It cannot change
analogy generation, tactic selection, stopping, surprise, Lean verification, RL
reward, policy updates, or reported solver outcomes.

## Audit input and output

For each logged analogy iteration, the auditor sees the theorem target, proof
state before the iteration, analogy card, predicted transition, proposed tactics,
Lean statuses and diagnostics, actual successor when present, surprise, and stop
decision. It never sees a reference proof.

It emits strict JSON with:

- `stage`: `analogy`, `tactic_translation`, `lean_execution`, `proof_progress`, or `working`;
- `progress_score`: a diagnostic number in `[-1, 1]`;
- `analogy_quality`: a diagnostic number in `[0, 1]`;
- `confidence`: a number in `[0, 1]`;
- `diagnosis`: a short account of what worked or failed;
- `evidence`: a short citation to the supplied state, tactic, or Lean diagnostic;
- `suggested_fix`: a short experiment-level improvement to investigate later.

Malformed output receives one bounded repair attempt. Persistent failure is
logged as an audit failure and never changes the source run.

## Artifacts and summaries

Each audit writes to a new sibling directory, leaving the source run untouched:

- `config.json` with source path, source hash, model, seed, and call cap;
- `judgments.jsonl` with one row per attempted iteration;
- `summary.json` with coverage, stage counts, score means, confidence, failures,
  and source solver metrics copied verbatim for context.

The human-readable report groups repeated diagnoses and reconstructs concrete
chains of `state -> analogy -> tactic -> Lean result -> judgment`.

## Verification

Tests cover strict parsing, bounded repair, deterministic sampling, source-run
immutability, accepted/rejected/completed iterations, missing or truncated logs,
resumable append-only output, and aggregation. The full suite passes before the
MLX 8B auditor is run over the existing benchmark artifacts.
