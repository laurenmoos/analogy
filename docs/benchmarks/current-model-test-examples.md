# Current-model test examples

This is the small, human-maintained test set for the current model regime. It
tracks examples worth rerunning; it is not a run log.

## Model regime

- Model: `mlx-community/Qwen3-8B-4bit`
- Backend/device: MLX on MPS
- Generation: deterministic, thinking disabled, one model call, no repair call
- Prompt: `story-operator-v2`
- Input: neutral proof need plus formula-free local inventory
- Output: one everyday story, one configured operator, and inventory bindings
- Grounding: code enumerates every valid application of the chosen operator
- Correctness: Lean must accept at least one grounded candidate

If any of these settings changes, start a new section instead of silently
mixing results from two regimes.

## Easy examples

These isolate one obvious structural move. Use them as a fast sanity check for
prompting, parsing, grounding, compilation, and Lean verification.

| ID | Given | Goal | Expected first operator |
|---|---|---|---|
| `and_intro_01` | `todo_list : P` | `P` | `exact_local` |
| `and_intro_02` | `p : P`, `s : S` | `P ∧ S` | `split` |
| `and_intro_04` | `vm : P ∧ S` | `P` | `select_left` |
| `and_intro_05` | `h : P ∧ Q` | `Q` | `select_right` |
| `imp_intro_01` | `p : P`, `bakery_service : P → C` | `C` | `apply_implication` |
| `imp_intro_02` | no local hypotheses | `C → C` | `introduce` |
| `or_intro_01` | `s : S` | `S ∨ O` | `choose_left` |

Source: [`problems.json`](../../src/analogy/lean_gym/corpus/problems.json).

## Hard examples

These deliberately contain distractors or several plausible moves. Do not
require the model to match a hidden reference tactic: the pass condition is
that its selected operator has a valid application and at least one compiled
candidate is accepted by Lean.

| ID | Given | Goal | What makes it hard |
|---|---|---|---|
| `component_or_apply` | `h : P ∧ Q`, `f : Q → P` | `P` | Projection competes with implication application. |
| `component_or_exact` | `h : P ∧ Q`, `hq : Q` | `Q` | Exact evidence competes with projection. |
| `equality_or_apply` | `e : P = Q`, `hp : P`, `f : P → Q` | `Q` | Equality/rewrite cues distract from direct implication application. |
| `reverse_equality_or_apply` | `e : P = Q`, `hq : Q`, `f : Q → P` | `P` | The same ambiguity appears in the reverse direction. |
| `iff_forward_or_apply` | `h : P ↔ Q`, `hp : P`, `f : P → Q` | `Q` | Biconditional transfer competes with a direct implication. |
| `iff_backward_or_apply` | `h : P ↔ Q`, `hq : Q`, `f : Q → P` | `P` | Direction selection and direct application are both plausible. |
| `cases_or_exact` | `h : P ∨ Q`, `hr : R` | `R` | A disjunction invites case analysis even though exact evidence is present. |
| `cases_or_local` | `h : P ∨ Q`, `hp : P` | `P` | The model must ignore an irrelevant case split. |
| `split_or_exact` | `h : P ∧ Q`, `hp : P`, `hq : Q` | `P ∧ Q` | The complete conjunction and its separate components are both available. |
| `split_or_rewrite` | `e : P = Q`, `hp : P`, `hq : Q` | `Q ∧ P` | Splitting is sufficient, while equality suggests unnecessary rewriting. |

Source: [`story_operator_ambiguous.json`](../../tests/fixtures/story_operator_ambiguous.json).

## Test record

Keep only the latest result for this regime so this page stays useful.

| Date | Easy | Hard | Notes |
|---|---:|---:|---|
| _not run_ | — / 7 | — / 10 | Fill in after the next matched test. |

For a failure, note only the example ID, selected operator, and failure boundary
(`invalid JSON`, `zero applications`, `compile failure`, or `Lean rejection`).
Detailed generated artifacts belong in the configured output directory, not in
this file.
