# Current PropL OOD benchmark

The active reasoning benchmark contains 30 intuitionistic propositional-logic
problems sourced from PropL's `test_ood` split. It replaces the saturated
introductory curriculum and is intended to stress proof search,
branch selection, and recovery rather than mathematical background knowledge.

## Pinned source

- Dataset: <https://huggingface.co/datasets/KomeijiForce/PropL>
- Generator: <https://github.com/ucsd-atp/PropL>
- Paper: <https://aclanthology.org/2024.acl-long.45/>
- Dataset revision: `62a13de410999c20f60ec2f3be93457dcaac0d62`
- Files: `normal/test_ood.csv` paired with `backtrack/test_ood.csv`
- License: MIT

## Selection

The checked-in subset is rebuilt by `scripts/import_propl_ood.py`. Candidates
must contain `∧`, `∨`, and `→`, have at least 15 tactics in the source proof,
fit the harness's flat one-line tactic protocol, complete in the local Lean
verifier, and remain incomplete after one `simp`. Ranking prefers more failed
attempts in PropL's paired backtracking trace, then longer proofs, then longer
targets, with the source ID as a deterministic tie-breaker.

Runtime files contain neutral titles and exact source statements/proofs. Detailed
source IDs, selection ranks, trace lengths, backtracking counts, and validation
statuses are stored separately in
[`benchmark_metadata.json`](../../src/analogy/lean_gym/corpus/benchmark_metadata.json)
so they are auditable without leaking difficulty hints into model prompts.

## Evaluation

The default configuration runs all 30 problems with `search.max_steps: 32`,
matching the longest reference proof. Compare analogy-enabled and disabled runs
on the same complete corpus and configuration; do not select a post-hoc subset
based on either arm's failures.
