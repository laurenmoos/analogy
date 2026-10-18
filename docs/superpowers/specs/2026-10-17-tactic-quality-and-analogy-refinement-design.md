# Tactic Quality and Analogy Refinement Design

## Goal

Remove avoidable tactic-generation failures from both experimental arms, while letting stuck-only analogy use its existing call allowance to correct a Lean-rejected proposal. The matched rerun should measure analogy rather than prompt syntax mistakes or accidental hypothesis shadowing.

## Candidate hygiene

The ordinary tactic parser will discard only tactics that are certainly incomplete because their command head requires an operand. The initial guarded heads are `exact`, `apply`, `refine`, `rw`, `cases`, `rcases`, `have`, `show`, and `rename_i`. Valid standalone tactics such as `intro`, `constructor`, `left`, `right`, `simp`, `assumption`, and `rfl` remain eligible.

Filtering happens before `top_k` truncation so later valid entries backfill removed entries, and scores are then renormalized. Only an output whose entire candidate pool is incomplete raises a dedicated incomplete-pool error and receives one repair generation with a short diagnostic and concrete complete examples; unrelated JSON/schema errors retain their existing behavior. This repair is conditional, so it costs no tokens on valid outputs and is identical in both arms. Traces retain both generations and the all-incomplete parser diagnostic. Partial filtering is observable through a separate discarded-candidate trace field rather than attaching diagnostics to a valid generation.

The same exact-head completeness check applies to analogy output before Lean evaluation. A tactic is incomplete only when its entire normalized source equals a guarded head; prefix forms such as `cases h`, `exact?`, and `apply?` remain eligible. An incomplete analogy response consumes one of the existing analogy model calls and receives the normal schema-repair feedback.

## Deterministic destructuring names

Candidate normalization accepts the first active proof-state block and becomes state-aware for simple top-level conjunction and disjunction destructuring. It runs before ordinary deduplication and analogy duplicate checks. For a visible hypothesis, the harness derives semantic fresh names from each operand:

- `hB : H ∧ U` becomes `rcases hB with ⟨hH, hU⟩`.
- `h : G ∨ H ∧ U` becomes `rcases h with hG | hHU`.
- Existing names are protected with deterministic numeric suffixes.

For conjunction destructuring, later token-bounded references to the binders on the same line are renamed consistently. Disjunction canonicalization is limited to a bare split with no continuation because its binders live in separate branches; parenthesized disjunction patterns with continuations pass through unchanged rather than being rewritten unsafely. Rewriting is otherwise limited to simple two-binder `rcases` patterns whose subject and root connective can be resolved from the first active proof state; unfamiliar patterns pass through unchanged rather than being guessed at.

Semantic names concatenate visible proposition atoms (`H` becomes `hH`, `H ∧ U` becomes `hHU`). Operands that cannot be conservatively reduced to visible identifiers use `hLeft` and `hRight`. Existing names are protected with deterministic numeric suffixes.

The old Lean-3 normalization that erased names from `cases h with x y` is removed. Such legacy syntax is translated only when the state supplies enough information to produce a valid semantic `rcases` pattern.

## Compact tactic card

The card remains deterministic, state-specific, and capped at 200 characters. It prioritizes no more than two useful recipes:

1. A directly matching hypothesis: `target=h2: exact h2`.
2. A constructor or introduction appropriate to the current goal.
3. A semantic conjunction-destructuring recipe when useful.
4. An iff rewrite recipe when useful.

The card explicitly says that operand-taking tactics must be complete. The `analogy_left | analogy_right` recipe is removed entirely. Disjunction destructuring is not advertised by the card because it encouraged invalid one-line branch syntax; state-aware normalization still makes a model-generated simple disjunction split safe.

## Analogy correction loop

Analogy keeps its configured maximum of three model calls per stuck event. Schema errors, duplicates, incomplete tactics, and Lean-rejected tactics all consume calls from that same limit.

After a valid tactic is evaluated:

- completion returns immediately;
- retained progress returns immediately;
- an ordinary Lean rejection triggers another generation when budget remains, with the rejected tactic and diagnostic;
- an accepted but discarded/no-progress successor also triggers another generation, with a concise no-progress diagnostic.

Typed Lean timeout/internal-error results terminate under the existing infrastructure-failure behavior and are never presented as model mistakes. Correction prompts reuse the original proof context and add only the latest failed tactic plus the first nonblank, whitespace-normalized Lean diagnostic, capped at 500 characters with an explicit truncation marker. They do not repeat the prior analogy prose or full raw JSON, keeping token overhead small. Every generation and every Lean evaluation remains in the rollout log.

## Experimental fairness

Both arms use the same model, seed policy, ordinary step limit, top-k, completeness filtering, repair behavior, semantic naming, and tactic card. Analogy remains disabled in the control arm and stuck-only in the treatment arm. Analogy correction calls do not count as ordinary reasoning steps and cannot fire before stuckness.

The same three hardest examples are rerun through the existing comparison driver. The detailed report must show each problem, every ordinary reasoning step, every analogy generation/retry, each submitted tactic, and all Lean diagnostics. Its summary and conclusions are derived from the new run records rather than hard-coded old outcomes. The conclusion will report solve counts and runtimes without treating extra analogy calls as equal-cost ordinary steps; sequential wall-clock times are descriptive because model warm-up/order is not counterbalanced.

## Verification

Unit tests cover incomplete filtering and all-invalid repair, semantic freshening and same-line reference rewriting, removal of the misleading card hint, analogy retry after both Lean rejection and discarded progress, retry exhaustion, and trace completeness. The full test suite must pass before the matched GPU rerun and report regeneration.
