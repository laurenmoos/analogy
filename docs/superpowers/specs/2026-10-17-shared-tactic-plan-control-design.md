# Shared Tactic-Plan Control Design

## Intent

Measure what analogy adds to proof planning, without confounding the comparison with different
execution or memory mechanisms. The active experiment has exactly two conditions:

- `neutral`: analogy off; the model sees the proof and generates a vanilla tactic plan.
- `activation_selected`: analogy on; the selected proof-aware analogy generates a tactic plan.

The historical `prompted_analogy` and `shuffled_analogy` enum values remain readable for old
artifacts but are not generated, executed, summarized, or paired in new runs.

## Shared controller

Before Lean execution, each active arm is normalized into a durable `TacticPlan` containing the
originating state hash and a bounded ordered sequence of tactic steps. Both arms use the same
cursor and verifier control flow. Prompts are not used as working memory after the plan freezes.

The controller executes the plan in order. Each accepted tactic advances the cursor; rejection,
repeated state, plan exhaustion, or a budget limit ends the frozen-plan phase. Remaining matched
budget may then enter the existing ordinary continuation search. Analogy receives no special
retry, stack, or fallback behavior.

## Measurements

Every outcome records two distinct views:

1. **Frozen-plan quality:** completion, accepted progressing steps, verifier results, status, and
   Lean calls before any replanning.
2. **Recovery quality:** whether recovery was needed, eventual completion, recovery status, model
   positions, and Lean calls under the matched remaining budget.

The existing equal-tactic and equal-compute budgets remain. Pairwise reporting compares only
analogy-on against analogy-off.

## Constraints

- Preserve old enum decoding and anchor reuse.
- Bound both plans by `tactics_per_slate`.
- Freeze plans before the first Lean call and checkpoint them with frozen selections.
- Keep selection metrics (relevance, displacement, causal patch effect, predecessor value)
  unchanged for choosing the analogy.
- Do not hide failed or incomplete plans behind recovery metrics.

