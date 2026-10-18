# Search Hygiene and SAT Pruning Design

## Intent

Improve tactic quality before Lean is called, make both experiment arms search under the same
ordinary budget, and use deterministic propositional checks to avoid expanding branches that
cannot prove their focused goal. Analogy remains a stuck-only recovery mechanism. The requested
three-arm ablation is explicitly out of scope.

## Candidate hygiene

Every generated tactic passes through one shared normalization and validation boundary before any
Lean call, regardless of whether it came from ordinary ranking or analogy:

- rewrite a bare `rw h` to `rw [h]`, `split` to `constructor`, and `not.intro` to `Not.intro`;
- reject `tauto`, `itauto`, and unknown pseudo-tactics such as `modus_tollens`;
- reject incomplete heads such as bare `exact`;
- reject `left`/`right` unless the focused target is a disjunction, `constructor` unless it is a
  conjunction, equivalence, or disjunction, and `intro` unless it is an implication or negation;
- deduplicate against canonical tactics already tried at that exact proof state.

Removed ordinary candidates cause another model request for replacements, up to three total model
calls. Analogy duplicates also consume this same bounded retry allowance, but are recorded as
duplicates rather than invalid syntax. No filtered tactic is submitted to Lean.

Prompts show only the focused goal. The compact, generic Lean card includes disjunction and
conjunction destructuring/building syntax, explicitly bans `tauto`/`itauto`, and contains no
problem-specific solution recipe. Analogy feedback labels OPEN successors separately from REJECTED
tactics and Lean diagnostics.

## Proactive semantic pruning and stuck recovery

Every OPEN successor in the supported propositional fragment is checked before entering the
frontier. A countermodel prunes that successor and is retained under its parent state. Unsupported
syntax, including quantifiers, produces `unknown` and is never pruned. Lean remains the final proof
judge.

When all attempted disjunct choices from a node are countermodel-refuted, recovery triggers
immediately rather than waiting for the remaining candidates to error. Recovery otherwise still
uses the ordinary stuckness policy. Analogy is only invoked during recovery.

For each rescue state, the controller aggregates sibling countermodels into a deterministic table.
For every disjunctive hypothesis it records whether the left side, right side, both sides, or neither
side is true in each model. If at least one model requires the left side and another requires the
right side, the controller first applies a deterministic fresh-name `rcases` forced move. If that
does not complete or advance the proof, analogy receives the same table and must describe how its
proposal maps to it.

## Search priority

Frontier priority is best-first by the latest tactic score minus `0.02 * depth`; diversity novelty is
only a deterministic tie-breaker. Scores are not multiplied along the path.

## Experiment protocol

The ordinary ranker samples at temperature `0.7`. Both analogy and no-analogy arms use identical
ordinary settings and run paired seeds `0..4`. Reports retain all retries/failures and compute the
primary comparison on problem/seed pairs where the baseline emitted at least one stuck event.

After candidate-hygiene changes alone, rerun `or_intro_06` without analogy. If it solves, remove it
from the analogy evaluation set. Add harder nested-disjunction and quantified examples; quantified
successors may be `unknown` to the propositional checker but must never be falsely pruned.

## Acceptance criteria

1. No banned, unknown, duplicate, incomplete, or goal-shape-incompatible tactic reaches Lean.
2. Removed candidates trigger bounded replacement sampling and every generation attempt is logged.
3. The card is generic, includes safe destructuring/building syntax, and bans automation tactics.
4. Analogy feedback distinguishes OPEN from REJECTED tactics and uses the focused goal only.
5. Every supported OPEN successor is checked and countermodel-refuted successors never enter the
   frontier; unsupported successors remain eligible.
6. Sibling countermodels produce a stable dependency table and can trigger a fresh-name forced
   disjunction split.
7. Analogy remains stuck-only and receives the dependency table at recovery.
8. Frontier priority uses latest-step score with the configured depth penalty.
9. Five paired seeds use sampling in both arms, and reports include baseline-stuck-subset metrics.
10. `or_intro_06` is tested after hygiene alone and retired if the baseline solves it; harder nested
    disjunction and quantifier cases replace easy retired examples.

