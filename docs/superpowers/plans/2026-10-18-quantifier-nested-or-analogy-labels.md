# Quantifier, nested disjunction, and analogy labels implementation plan

> Execute this plan in the current workspace with test-first changes and preserve unrelated edits.

**Goal:** Remove tactic-generation/reporting ambiguity, add deterministic nested-disjunction recovery,
measure analogy predictions, and rerun one matched corpus-only comparison.

**Architecture:** Keep the ordinary and analogy arms on the same search controller. Extend the shared
pre-Lean boundary and trace records, keep semantic recovery deterministic, and classify each valid
analogy proposal against the exact focused state before/after Lean. The benchmark runner loads a fixed
manifest from the checked-in corpus and emits a single two-arm report.

---

### Task 1: Quantifier filtering and context-aware card

1. Add failing parser and prompt-card tests for `intro x`/`intros` on `∀` and visible context names.
2. Extend shape filtering and the allowlist.
3. Generate bounded binary/three-way `rcases` examples from the focused context.
4. Run parser/prompt tests.

### Task 2: Explicit early-recovery skips

1. Add a failing episode/report test for remaining candidates after both goal disjuncts are refuted.
2. Record skipped candidates with the deterministic early-recovery reason.
3. Render the record in detailed traces.
4. Run episode/report tests.

### Task 3: Three-way forced move

1. Add failing semantic tests for `A ∨ B ∨ C`, fresh names, and a three-way split.
2. Flatten exactly three disjuncts in the countermodel table and forced-move selector.
3. Keep binary and longer-disjunction behavior stable.
4. Run semantic/episode tests.

### Task 4: Analogy prediction labels and deterministic retry feedback

1. Add failing rescue tests for predicted no-change, matched, mismatched, and duplicate feedback.
2. Add the three-label record contract for valid novel generations.
3. Compare normalized full focused states; bypass Lean for no-change predictions.
4. Maintain a bounded ordered state-local ledger and inject it into retries.
5. Update prompt wording and trace rendering.
6. Run rescue/prompt/report tests.

### Task 5: Corpus-only matched experiment

1. Add failing protocol tests for exact corpus provenance, fixed problem IDs, and two matched arms.
2. Replace the synthetic/five-run experiment files with one corpus-backed manifest and runner.
3. Remove repeated-run terminology and random-generation controls from persisted reports/docs.
4. Split report sections by observed countermodel evidence.

### Task 6: Verification and report generation

1. Run focused tests, the full suite, and compile checks.
2. Request a fresh read-only code review and fix material findings.
3. Run the same three corpus problems once in each arm.
4. Generate and verify the combined reasoning/trace report.
