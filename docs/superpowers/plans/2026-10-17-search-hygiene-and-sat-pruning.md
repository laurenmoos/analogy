# Search Hygiene and SAT Pruning Implementation Plan

**Goal:** Implement the requested tactic-quality, semantic-pruning, search-ordering, and experiment
reporting fixes without adding the proposed ablation arm.

**Spec:** `docs/superpowers/specs/2026-10-17-search-hygiene-and-sat-pruning-design.md`

**Execution note:** Work inline in the existing user-authorized dirty checkout. Do not create commits
that would accidentally capture unrelated pre-existing changes. Each task is gated by focused RED
then GREEN tests, followed by a full-suite verification and one fresh final review.

## Task 1: Candidate normalization, safety, and generic card

**Files:** `src/analogy/inference/parser.py`, `src/analogy/inference/prompts.py`,
`src/analogy/solver/model_policy.py`, `src/analogy/solver/episode.py`, `prompts.yaml`, relevant tests.

- [x] Add failing tests for rewrites, bans, incomplete tactics, focused-goal shape filtering,
  state-local deduplication, replacement sampling, generic card contents, and focused prompts.
- [x] Implement one shared pre-Lean candidate preparation boundary and bounded ordinary refill.
- [x] Run focused parser/model/prompt/episode tests and record results.

## Task 2: Analogy retry and feedback semantics

**Files:** `src/analogy/solver/rescue.py`, `prompts.yaml`, relevant tests.

- [x] Add failing tests proving duplicates resample without being classified as invalid syntax and
  proving OPEN/REJECTED prompt sections remain distinct.
- [x] Implement duplicate status/retry logging and separated focused-goal feedback.
- [x] Run focused rescue and prompt tests and record results.

## Task 3: Proactive semantic pruning and countermodel tables

**Files:** `src/analogy/solver/semantic.py`, `src/analogy/solver/episode.py`,
`src/analogy/records.py`, `src/analogy/solver/inference.py`, relevant tests.

- [x] Add failing tests for supported OPEN-successor pruning, quantified UNKNOWN behavior, early
  all-disjunct recovery, stable sibling tables, and report serialization.
- [x] Implement semantic prune records, cache/recovery aggregation, and the table renderer.
- [x] Run focused semantic/episode/report tests and record results.

## Task 4: Forced disjunction move and table-guided analogy

**Files:** `src/analogy/solver/episode.py`, `src/analogy/solver/rescue.py`, `prompts.yaml`, relevant tests.

- [x] Add failing tests for cross-model side dependency, fresh deterministic names, direct forced
  move evaluation, and table propagation to analogy when forced progress is unavailable.
- [x] Implement the rule before analogy within stuck recovery; do not create another arm.
- [x] Run focused episode/rescue/prompt tests and record results.

## Task 5: Best-first score and experiment protocol

**Files:** `src/analogy/solver/episode.py`, configs, corpus/index fixtures,
`.artifacts/experiment-configs/hardest3/run_comparison.py`, report generator, relevant tests.

- [x] Add failing tests for latest-step/depth priority, paired five-seed execution, stuck-subset
  metrics, and new problem indexing.
- [x] Implement priority, temperature `0.7` sampling, paired seeds, metrics, and harder problems.
- [x] Run the hygiene-only `or_intro_06` baseline checkpoint and retire it if solved.
- [x] Run focused tests and then the paired experiment when local MLX/Lean resources are available.

## Task 6: Verification and review

- [x] Run `pytest -q`, `python -m compileall -q src tests`, and `git diff --check`.
- [x] Generate the detailed trace report with all generations, retries, filters, Lean outcomes,
  semantic prunes, countermodel tables, and stuck events.
- [x] Request one fresh-context whole-change review, fix Critical/Important findings test-first, and
  rerun verification.

## Review focus

- No filtered tactic can bypass the shared boundary through ordinary, analogy, or forced paths.
- State-local dedup does not incorrectly suppress a tactic at a different proof state.
- Quantifiers and unsupported syntax are UNKNOWN, not countermodels.
- Analogy still fires only after stuck recovery and both arms have identical ordinary exploration.
- Multi-seed results are paired and stuck-subset denominators are explicit.
