# Final-review integrated fix report

Date: 2026-10-16. Base commit: `c1765d29219612451c04409452fac667b61a3529`.

Implementation commit: `725fc3c` (`fix: enforce activation evaluation compute and recovery contracts`). This report is committed separately after the implementation so it can identify that commit. No subagents or additional reviewers were launched for this wave.

## Status and scope

All six Important findings and the requested local minors are addressed. The final activation-focused run passed 226 tests. The final full-suite run passed 389 tests. No real-model 16-seed experiment was run, and the earlier diagnostic smoke is not evidence for these changed semantics.

The existing large package reorganization remains unstaged and untouched. In particular, this wave did not edit or stage the user's pre-existing changes to `README.md`, `prompts.yaml`, `__main__.py`, `config.yaml`, or unrelated tests. New documentation is in `docs/benchmarks/activation-evaluation-v3-semantics.md` rather than interleaving with the dirty README.

## Scientific and execution semantics

### 1. Total-compute continuation

`_GenerationMeter` now checks a conservative next-generation allowance before conventional continuation starts each call in the equal-compute view. The remaining budget is `C - rescue/selection/diagnostic usage - previous continuation usage`. An inadmissible call does not invoke the model or Lean, and the outcome records `continuation_status="budget_exhausted"`. Actual continuation positions are included in equal-compute `processed_token_positions` and remain separately visible in `continuation_processed_token_positions`.

Immediate rescue utility, immediate solves, continuation solves, and total completion are distinct. Equal-tactic continuation retains its prior search/Lean cap and separately metered model usage. Unknown executed usage remains invalid evidence. A shuffled slate above C is unavailable to equal-compute verification rather than contributing a solve. Observational proof-state embeddings do not feed continuation or selection and remain outside solve-compute accounting.

Regression: `test_continuation_cannot_cross_total_compute_ceiling` gives rescue cost 12 and C=13, then verifies that continuation stops before generation and Lean despite rescue usage being below C.

### 2. Actual generation usage

The narrow provider API adds `GenerationResult(text, processed_token_positions, diagnostic)` and `generate_with_usage(messages, seed=...)`. Existing `TextGenerator.generate` still returns plain text, preserving ordinary solver callers. The activation path requires the measured result; it no longer reconstructs generation cost by decoding then retokenizing. A legacy text-only generator cannot silently supply fabricated exact usage to activation evaluation.

Transformers uses a temporary first-decoder pre-hook, accepting positional or keyword hidden states. MLX temporarily wraps the first decoder layer. Both count batch width times actual sequence width for every decoder invocation: chunked prefill, cached steps, and invisible special tokens when those tokens actually enter a forward. A final predicted EOS that never enters another forward does not invent an additional processed input position. The hooks/wrappers are removed in `finally`. No weights are trained or changed.

`estimate_generation_positions` uses each backend's actual generation encoder and special-token policy plus the maximum decode allowance. These conservative estimates govern admission; teacher-forced counting is unchanged. Unknown usage preserves generated text/attempt evidence and marks the charge unknown.

Regressions exercise non-round-tripping output with invisible processed tokens on both backends, generation/capture cleanup on exceptions, and Transformers tuple-valued block output with preserved cache metadata.

### 3. Experiment-owned sampled decoding

The activation profile adds strict `decoding.temperature` and `decoding.top_p` values (0.8 and 0.95). Positive finite temperature and top-p in `(0, 1]` are validated; unknown decoding keys fail strict parsing. `ActivationEvalConfig.generation_config` creates a new sampled AppConfig. The CLI builds its own runtime/provider from that config, without writing to or mutating the user's base config.

Existing deterministic seed namespaces now affect sampling. The two backend regressions run repeated top-ups through actual backend generation orchestration and show distinct canonical tactics and reproducibility for the same seeds. There is still no training or parameter update.

### 4. Coherent shuffled controls

The runner deterministically selects an available donor from another problem with matching input/output arity and different reasoning. The donor's source, alignment and transfer travel together. If no donor is available, the fallback rotates proof-role assignments and rebuilds the proof operation, invariant, limitation, premises, reasoning steps and predicted next subgoal from the rotation. It does not retain the selected target's solution text.

The source package and target prompt are constructed exclusively from frozen pre-Lean material. Reference proofs, Lean verification labels, and successor outcomes never select or construct donors. Prompt-content regressions verify removal of the intact selected operation/transfer and coherent use of the donor package.

### 5. Interruption recovery

Artifact schema is now `activation-analogy-artifacts-v3`. Old v1/v2 artifacts are deliberately rejected because their generation, decoding and continuation semantics differ; they are not rewritten or silently upgraded. Start a new v3 run.

After the anchor manifest is sealed, structures are saved and bound to the identity. Phase A writes atomic per-model-operation working results and atomic completed-cell checkpoints. Resuming a partial cell checks the operation request, identity and result hash and replays durable results; resuming completed cells skips their model work. Remaining cells finish before cross-fit calibration and winner freezing. The resume path does not call Lean for structure extraction. A completed model call whose result has not yet been durably published can still need repeating after process death; no local filesystem mechanism can commit a result it has not received.

Working recovery checkpoints temporarily retain the pooled vectors required for unfinished calibration. They are private execution state, not the published default diagnostic payload. Once the frozen checkpoint is durably published, those working files are removed; finalization repeats cleanup in case publication was interrupted. This cleanup removes only task-created working checkpoints. Published default activation traces and patch deltas retain summaries/hashes, while opt-in full diagnostic persistence uses `persist_full_activations`.

Phase B validates and commits the whole eight-row cell with a flushed/fsynced temporary file and atomic replacement. An interruption before commit leaves the prior journal intact; the uncommitted cell can be evaluated again from the already frozen selection. A published cell is never appended again. Existing process locking, identity validation, ordered scope checks, frozen-file hashes and completed-artifact immutability remain enforced. Collection mutation is rejected after freeze.

Regressions cover interruption inside Phase B publication, per-operation reuse inside a partial Phase A cell, completed-cell reuse, a real CLI mid-collection resume, absence of Lean structure work during that resume, and deletion of working vector checkpoints after freeze. Parent-directory fsync remains optional and was not added.

### 6. Primary submitted/verified-tactic causal outcome

`MatchedConditionOutcome.verified_tactic_causal_effects` records `(submitted tactic, patched-minus-neutral log likelihood or unavailable)` for actual immediate-rescue submissions. The primary measured population is the selected arm; rejected submissions remain part of the population, and coverage is explicit. Conventional continuation tactics are outside this metric.

After the winner is frozen, the final selected slate reuses pre-scored union effects where available. New final-generation tactics receive neutral/contextual captures plus a patch score, metered as `verified_tactic_neutral_capture`, `verified_tactic_analogy_capture`, and `verified_tactic_patch_score`. These charges enter the selected condition's cost C before equal-compute replay. The diagnostics never enter selection or change the frozen winner. Unknown diagnostic usage invalidates the comparison rather than silently granting free model work.

Summary and Markdown report separate `verified_tactic_causal_effect`, coverage, measured/submitted counts, and immediate/continuation solves from the union-wide `patch_likelihood_effect` used during selection. Tests cover reused union effects and a new final tactic requiring 60 extra diagnostic positions and a total C of 252, while preserving the winner.

## Local minor repairs

- String/bytes role-mapping entries are rejected instead of becoming character pairs.
- Default serialization removes `patch_deltas` vectors and retains dimension, norm and SHA-256 summaries; full diagnostic serialization remains opt-in.
- Progress-per-call uses eligible immediate-rescue progress and eligible immediate-rescue Lean calls for both numerator and denominator. Continuation and ineligible calls cannot dilute it.
- Transformers repository-name/unknown provenance uses the explicit `unresolved:` convention.
- Added two-layer intervention-selection coverage: large displacement at an earlier layer does not substitute for the configured intervention layer's patch.
- Added tuple-output, capture-failure and generation-hook cleanup coverage.
- Extreme-float variance changes and parent-directory fsync were optional and remain unchanged.

## Exact red/green evidence

1. `uv run pytest -q tests/test_activation_final_contracts.py` could not start because the sandbox denied access to `/Users/laurenmoos/.cache/uv/sdists-v9/.git`. Used the existing virtualenv directly; no dependency installation or cache change was necessary.
2. `.venv/bin/python -m pytest -q tests/test_activation_final_contracts.py` — **7 failed**, all for missing contracts: continuation exhaustion, generation result usage, sampling config, intact shuffled transfer, collection resume, atomic trial commit, verified-tactic causal outcome. These failures preceded implementation.
3. `.venv/bin/python -m pytest -q tests/test_activation_final_contracts.py tests/test_activation_runner.py tests/test_activation_generation.py tests/test_activation_report.py tests/test_activation_end_to_end.py` — **2 failed, 86 passed**. Both remaining failures compared full in-memory patch vectors against intentionally summarized reloaded records; changed those assertions to compare published evidence.
4. `.venv/bin/python -m pytest -q tests/test_activation_final_contracts.py tests/test_activation_provider.py` — **77 passed**.
5. `.venv/bin/python -m pytest -q tests/test_activation_final_contracts.py::test_interrupted_collection_replays_durable_model_calls_in_partial_cell` — **1 failed** because the operation checkpoint adapter did not exist, before adding it.
6. `.venv/bin/python -m pytest -q tests/test_activation_provider.py tests/test_activation_metrics.py tests/test_activation_final_contracts.py` — **112 passed**.
7. `.venv/bin/python -m pytest -q tests/test_activation_end_to_end.py` — **7 passed**.
8. `.venv/bin/python -m pytest -q tests/test_activation*.py` — **226 passed in 4.38s** after the additional regressions.
9. `.venv/bin/python -m pytest -q` — **389 passed in 8.10s** after the final production changes. An earlier full run also passed 385 tests before the last four regression cases were added.
10. `git diff --cached --check` — exit 0, no whitespace errors. Inspected cached path list/stat before committing; only the 17 fix-owned implementation, test, profile and documentation paths below were staged.

## Staged implementation paths

```text
config.activation-analogy.yaml
docs/benchmarks/activation-evaluation-v3-semantics.md
src/analogy/experiments/activation_cli.py
src/analogy/experiments/activation_generation.py
src/analogy/experiments/activation_metrics.py
src/analogy/experiments/activation_records.py
src/analogy/experiments/activation_report.py
src/analogy/experiments/activation_runner.py
src/analogy/inference/mlx_model.py
src/analogy/inference/model_provider.py
tests/test_activation_end_to_end.py
tests/test_activation_final_contracts.py
tests/test_activation_generation.py
tests/test_activation_metrics.py
tests/test_activation_provider.py
tests/test_activation_report.py
tests/test_activation_runner.py
```

Git staging initially failed because the sandbox marks `.git` read-only. The authorized scoped stage and commit succeeded through the escalation mechanism; there was no automatic approval rejection. The implementation commit contains 17 paths, 749 insertions and 113 deletions. This report is the only path staged for the follow-up report commit.

## Remaining boundaries and concerns

There are no known failing tests or unresolved Important findings in this wave. Real installed MLX/Transformers integration against the 8B model was not rerun; tensor/provider tests use tiny real Torch blocks and controlled backend boundaries. The new seeded profile and usage semantics require a fresh real-model experiment before making empirical claims. Recovery begins at the sealed collection manifest; interruption during earlier conventional anchor preparation still requires a new preparation run. V3 cannot resume old v1/v2 experiments. Private working vector journals can consume disk during collection, then are removed after freeze. The causal patch remains a local single-boundary intervention proxy, and six benchmark problems do not justify broad population claims.
