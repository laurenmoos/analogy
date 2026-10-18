# PropL OOD Logic Benchmark Design

## Goal

Replace the saturated 25-problem `lean4game-logic` curriculum with 30 harder,
online-sourced intuitionistic propositional-logic problems whose reference
proofs are accepted by the repository's Lean verifier. Retire the old
curriculum and `hardest3` experiment artifacts, and remove prompt wording that
assumes the shape of the old corpus.

## Source and provenance

Use the PropL dataset from `KomeijiForce/PropL`, dataset revision
`62a13de410999c20f60ec2f3be93457dcaac0d62`, and specifically the paired
`normal/test_ood.csv` and `backtrack/test_ood.csv` files. Preserve every source
`data_id`, the exact source target, the exact normal proof trace, source URLs,
revision, split, and MIT attribution in repository metadata.

The checked-in corpus is a deterministic subset, not newly authored logic
problems. Selection order is:

1. require all of `∧`, `∨`, and `→` in the target;
2. require a flat PropL proof trace (exclude nested `case` tactics and
   `have ... := by` blocks, which the harness's one-line tactic protocol cannot
   represent faithfully);
3. require the exact source trace to complete under the local Lean verifier;
4. exclude any target completed by a single `simp` tactic;
5. prefer records with failed attempts in the paired backtracking trace, then
   longer normal proofs and longer targets; fill the remainder by proof length;
6. set a minimum accepted reference length of 15 tactics and retain 30 records.

Store structural selection metadata outside the runtime corpus so the model
does not see difficulty labels, trace lengths, or backtracking counts.

## Runtime behavior

The default benchmark selection is exhaustive. The search budget must admit
the longest checked-in reference proof. Existing analogy lifecycle behavior is
unchanged.

## Prompt audit

All model-facing prompt text remains centralized in `prompts.yaml`. Remove
instructions that assume a countermodel-side table is always available and
replace the fixed old-curriculum constructor footer with a compact,
fragment-general Lean tactic reminder. Do not expose source IDs, selection
scores, reference proofs, or metadata to the model.

## Retirement scope

Delete the old corpus, curriculum split file, dedicated `hardest3` configuration,
scripts, problem copy, and tests. Update current README and benchmark
documentation. Historical dated design/plan documents may continue to describe
past work; they are not active benchmark assets.

