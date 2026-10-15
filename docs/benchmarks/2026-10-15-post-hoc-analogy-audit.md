# Post-Hoc Audit of Analogy RL

## Result

The main failure is not a lack of broad analogical ideas. It is grounding those
ideas in the **current residual Lean goal**. Across the six available
analogy-condition examples, the model often described the right theorem-level
relationship but generated tactics for an earlier or imagined goal, used invalid
Lean syntax, or oscillated back to an already visited state.

The most useful held-out transition changed `⊢ P` to `⊢ R` when `R` was already a
local hypothesis. That was one `assumption`/`exact a✝` away from completion, but
low embedding surprise caused the analogy loop to stop instead of handing the
trivial successor back to a conventional finisher. This explains “useful progress
without a solve” more concretely than the aggregate successor metric.

The LLM audit is descriptive only. It did not affect generation, Lean, reward,
policy updates, stopping, or the source metrics.

## Sources and integrity

- Model: `mlx-community/Qwen3-8B-4bit`, frozen and locally cached.
- Judge seed: `0`; one bounded schema-repair attempt; no repair was needed.
- Runtime probe: load `3.07s`, generation `3.38s`, Lean `1.54s`; Lean probe status
  `complete`.
- Training source: `runs-fast/repair-ablation-20261015/rescue/2641bdf832c14fb6b278f94325735f7b`
  (`train` and `validation` rows).
- Held-out source: `runs-fast/repair-ablation-20261015/rescue/evaluate-epoch-0001`
  (`analogy_loop` rows only).
- Training source SHA-256 tree hash:
  `4fa532487a9b4517788ef974caf4380660bbe3776da7f4a0c986de0f26d5931c`.
- Test source SHA-256 tree hash:
  `d7ab4397fb29ba4e3961cb7b7eb50ff8bca310bb8ea41db9cff1011a366bfdbe`.
- Both hashes matched after auditing.
- Final audit artifacts:
  `runs-fast/post-hoc-audit-20261015-v6/train` and
  `runs-fast/post-hoc-audit-20261015-v6/test`.
- Approximate command wall time: `63s` for training/validation and `110s` for
  held-out test, including model loading. The audit-only generation timeout was
  raised from 45s to 120s after the largest structured trace timed out; solver
  settings and source runs were unchanged.

## Coverage

| Source | Eligible | Judged | Parse failures | Mean progress | Mean analogy quality | Mean confidence |
|---|---:|---:|---:|---:|---:|---:|
| Train + validation | 3 | 3 | 0 | -0.33 | 0.17 | 0.43 |
| Held-out test | 3 | 3 | 0 | -0.33 | 0.40 | 0.77 |

The judge labeled training examples as one `working` and two `lean_execution`;
it gave the same distribution on held-out test. These labels are opinions, not
ground truth. In particular,
the judge sometimes blamed Lean when the objective record more precisely showed a
bad analogy orientation or tactic translation. Lean statuses, successor states,
and discard reasons below take precedence.

## Training and validation examples

### 1. `imp_intro_04` — analogy aimed at the old theorem, not the residual goal

- State: `h1 : C → A`, `h2 : A → S`, `a✝ : C`, goal `⊢ C`.
- Analogy: “Apply h1 then h2 to derive S from C.”
- Tactics: `apply h1`, `apply h2`, `exact a✝`, `apply h1`.
- Lean: all four rejected; applications targeted `A`/`S` while the goal was `C`,
  and the generated dagger identifier was parsed as unknown `a`.
- Judge: `lean_execution`, progress `-1.0`, analogy quality `0.0`, confidence
  `0.2`.
- Objective diagnosis: the analogy was stale for the residual goal. The state was
  already solvable by its local hypothesis, but neither the analogy nor tactic
  renderer reliably recognized the binder.

### 2. `imp_intro_05` — solved, but not because the analogy mattered

- State: `p : P`, goal `⊢ P`, after an ordinary prefix reduced the theorem.
- Analogy: an implication chain ending in “Apply the final implication.”
- First tactic: `exact p`.
- Lean: `complete`; the remaining candidates were never needed.
- Judge: `working`, progress `1.0`, quality `0.5`, confidence `0.9`.
- Objective diagnosis: this is a valid solve, but it is weak evidence for analogy
  quality. The direct local hypothesis completed the residual goal; the card's
  implication-chain story was unnecessary.

### 3. `not_intro_05` — correct reasoning, invalid Lean translation

- State: `h1 : B → S`, `h2 : ¬S`, goal `⊢ ¬B`.
- Analogy: derive `¬B` by contradiction from `B → S` and `¬S`.
- Tactics included `apply h2.mp h1`, `apply not.intro`, and `apply h1.mp h2`.
- Lean: all rejected; functions/negations do not expose `.mp`, and
  `not.intro` resolved to no valid Lean constant.
- Judge: `lean_execution`, progress `-1.0`, quality `0.0`, confidence `0.2`.
- Objective diagnosis: the line of reasoning was good, but the decoder could not
  express it as `intro hB; exact h2 (h1 hB)` or equivalent Lean.

Training itself completed 1 of the 4 recorded train/validation episodes. The
three audited rows are every analogy iteration present, not a sampled subset.

## Held-out test examples

### 1. `iff_intro_04` — useful progress stopped one trivial step early

- State: `h1 : P ↔ R`, `h2 : P → Q`, `a✝ : R`, goal `⊢ P`.
- Analogy: use the reverse direction of `h1` to derive `P` from `R`.
- Tactic: `apply h1.mpr`.
- Lean: accepted; successor was `⊢ R`, which exactly matched `a✝ : R`.
- Controller: surprise `0.0909`, then `surprise_converged`.
- Judge: `working`, progress `1.0`, quality `0.8`, confidence `0.9`, but still
  questioned whether the valid successor aligned with the original target.
- Objective diagnosis: the analogy and tactic were useful. The missing behavior
  was a cheap conventional finish on the accepted successor.

### 2. `iff_intro_04` — the next rescue oscillated back to a visited state

- State: same hypotheses, goal `⊢ R`.
- Analogy: “Apply `h1.mpr` to get `P`,” although the active goal was already `R`.
- Tactics: three rejected; `apply h1.mp` was Lean-accepted last.
- Search: the accepted result returned to `⊢ P`, so it was discarded as
  `repeated_state`; no actual successor was admitted.
- Judge: `lean_execution`, progress `-1.0`, quality `0.0`, confidence `0.2`.
- Objective diagnosis: the chain lost direction and oscillated between the two
  sides of the iff instead of closing `R` from the existing hypothesis.

### 3. `iff_intro_05` — structural analogy did not become a valid rewrite

- State: a nested equivalence over `L`, with `h1 : L ↔ P` and a corresponding
  theorem `h2` over `P`.
- Analogy: map `L` and `P`, then apply `h2` to the matching structure.
- Tactics: `apply h2`, `rw h1 at h2`, `exact h2`, `apply h2`, then the repair
  `apply h2 at h1`.
- Lean: direct applications had type mismatches; rewrite syntax was rejected
  (`rw [h1] at h2` would at least be syntactically valid); the six-call Lean
  budget was exhausted.
- Judge: `lean_execution`, progress `-1.0`, quality `0.2`, confidence `0.7`.
- Objective diagnosis: the structural analogy was plausible, but the tactic
  decoder lacked reliable orientation and syntax for rewriting a nested formula.

On the two held-out theorems, conventional, learned-direct, and analogy-loop all
solved `0/2`. Only analogy-loop made a novel successor (`1/3` iterations,
successor rate `0.333`; Lean-accepted tactic rate `0.20`), but it spent 28 Lean
calls versus 12 conventional and 13 learned-direct calls. The audit explains that
single successor as genuinely near-completing rather than merely different.

## What to improve next

1. **Finish trivial successors before another analogy.** After an accepted
   transition, try `assumption`, exact matching hypotheses, and a very small
   conventional beam. This directly targets the observed near-solve.
2. **Generate from the residual goal, not the original theorem story.** Explicitly
   require every analogy card to name the active goal and the hypotheses it will
   consume; reject cards whose predicted consequence does not match that goal.
3. **Use structure-aware tactic templates.** Convert implication, negation, iff
   direction, and rewrite relations into small Lean-safe skeletons before asking
   the model to fill identifiers.
4. **Treat repeated-state discards as directional feedback.** An iff analogy that
   returns to a visited side should be marked as oscillating and prevented from
   proposing the inverse direction again.
5. **Keep LLM judgment visibly separate from facts.** The audit caught useful
   patterns, but it also over-attributed one direct `exact` solve to the analogy
   and called a near-complete successor a detour. Future reports should continue
   showing Lean status, successor, and discard reason beside every judgment.

This six-example audit is enough to identify concrete engineering failures, but
not enough to estimate rates across the full benchmark. A larger run should reuse
the same post-hoc auditor after collecting more analogy-condition rollouts.
