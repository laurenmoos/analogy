# Joint Activation Candidates Design

## Intent

Replace the four-call source → alignment → transfer → tactic pipeline with `K`
independent, single-call candidates. Every candidate must contain both the analogy
and its proof reasoning so activation scoring can judge whether the complete idea
causally improves reasoning relative to the neutral trace.

The experiment should obtain diversity through independent sampling, domain cues,
and candidate perturbation—not by hiding the proof from the analogy generator.

## Goals

- Generate one complete candidate per configured domain cue in one model call.
- Give every candidate the theorem, current proof state, failed tactics, structural
  proof need, and exploration cue.
- Require a joint response containing source analogy, explicit mapping, proof
  reasoning, predicted proof state, and bounded Lean tactics.
- Keep proof-state relevance, reasoning diversification, causal utility, and
  predecessor explanatory value as distinct measurements.
- Preserve the existing `GeneratedAnalogyCandidate` downstream structure, adding
  only `predicted_state`, so activation capture, causal patching, matched controls,
  and artifact formats continue to work.
- Preserve bounded schema-aware retries, deterministic seeds, exact prompt/output
  logging, and token-position accounting.
- Select candidates using the existing cross-fitted activation score, but abstain
  when the winner lacks positive causal evidence or fails a configured minimum
  selection score.
- Treat displacement and diversity as exploration information, not evidence of
  proof utility by themselves.

## Non-goals

- Do not use Lean outcomes to choose the pre-Lean candidate.
- Do not replace the neutral, prompted, or shuffled evaluation arms.
- Do not redesign activation capture, causal patching, covariance calibration, or
  equal-budget evaluation.
- Do not add a learned selector in this revision.
- Do not require analogy text to conceal proof identifiers or proof structure.

## Joint candidate contract

Each generation returns exactly these top-level fields:

```json
{
  "analogy": {
    "domain": "...",
    "source_before": "...",
    "source_after": "...",
    "source_roles": ["...", "..."],
    "relation_chain": ["...", "..."],
    "transformation": {
      "operation": "...",
      "input_arity": "...",
      "output_arity": "...",
      "permutation": "...",
      "information_flow": ["..."],
      "focus_before": "...",
      "focus_after": "...",
      "domain_neutral_summary": "..."
    },
    "rationale": "..."
  },
  "mapping": {
    "mapping": {"source role": "proof role"},
    "invariant": "...",
    "source_operation": "...",
    "proof_operation": "...",
    "limitation": "...",
    "confidence": 0.0
  },
  "proof_reasoning": {
    "active_subgoal": "...",
    "premises": ["..."],
    "reasoning_steps": ["..."],
    "predicted_next_subgoal": "...",
    "completion_expected": false
  },
  "predicted_state": "...",
  "tactics": ["..."]
}
```

Field order is validated as part of the generation contract: `analogy`, `mapping`,
`proof_reasoning`, and `predicted_state` precede `tactics`. The model therefore
generates tactics autoregressively with the complete analogy and proof reasoning in
its actual context. There is no later tactic-generation prompt.

The parser assigns stable candidate, alignment, and transfer IDs, then reuses the
existing strict source, mapping, transfer, and tactic validators. A failure anywhere
rejects the entire joint candidate and supplies the precise nested diagnostic to the
shared retry prompt. `GeneratedAnalogyCandidate` gains the parsed `predicted_state`
so the complete explanatory prefix can be reconstructed without a generation call.

## Measurement factors

The selector must not collapse “relevant” and “different” into one notion of
distance. It records four separate pre-Lean factors:

1. **Proof-state relevance.** Embed the complete explanatory object—analogy,
   mapping, proof reasoning, and predicted state—and compare it with the frozen
   language/structure representation of the current proof state. Report cosine
   similarity, where larger means the candidate remains connected to the actual
   proof need. This is not a diversity score.
2. **Reasoning diversification.** Preserve the existing cross-fitted whitened
   squared activation distance between neutral tactic reasoning and candidate-
   conditioned tactic reasoning. Larger means the candidate meaningfully moved the
   model away from its neutral reasoning trace. This is not proof relevance and is
   not automatically useful.
3. **Causal utility/regret.** Preserve the existing bounded activation patch effect
   on the shared tactic union: patched tactic log likelihood minus neutral tactic log
   likelihood. Larger means the candidate-caused movement improved the tested tactic
   distribution; negative values are causal regret.
4. **Previous-state explanatory value.** Preserve the existing matched likelihood
   difference with versus without a real predecessor transition. This remains
   unavailable when there is no predecessor.

All four raw values and their standardized contributions remain visible in artifacts
and reports. A combined score may rank candidates after leave-one-problem-out
calibration, but no individual factor is relabeled as another. Displacement is an
exploration signal; causal utility and the abstention gate prevent novelty alone from
winning.

## Exploration

`analogy_candidates` remains `K`. Each candidate receives a deterministic seed and
one configured domain cue. The prompt describes the cue as an exploration direction,
not a restriction on access to proof information. The full proof is visible in every
call. Different seeds, sampling, and cues provide the initial exploration surface.

Candidate activations are recovered with a measurement pass over the same joint
prompt plus the already-generated explanatory response prefix. The measurement pass
does not generate or revise a candidate. For each tactic score, the conditioning
prefix contains the generated analogy, mapping, proof reasoning, and predicted
state. The tactic being scored is the completion, so it is not duplicated inside its
own prefix. This is ordinary autoregressive conditioning, not an information-hiding
ablation and not a second generation prompt.

The existing displacement measurement quantifies how far this joint context moves
reasoning from neutral. The separate proof-state relevance measurement determines
whether the exploration remains connected to the proof. Existing causal patch
effects determine whether the movement helps the shared tactic distribution.

After selection, the selected arm submits the winning candidate's already-generated
tactics. It does not make another selected-tactic model call. This keeps the object
being evaluated identical to the object that was scored and removes an otherwise
unmatched source of model work. The shuffled control still regenerates tactics from
the perturbed explanatory context because its purpose is to test the mapping.

## Selection and abstention

Cross-fitted scoring remains unchanged up to winner ranking. After ranking,
selection must abstain unless:

- the winner has a finite selection score at or above
  `minimum_selection_score`; and
- the winner has a finite causal effect strictly above `minimum_causal_effect`.

Both thresholds are explicit activation-evaluation configuration values. Their
defaults are `0.0`, so a candidate must provide positive causal evidence rather than
merely be the least bad candidate. An exactly zero causal effect abstains. A failed
threshold produces `failed_selection`; downstream evaluation records the selected
arm as a failed rescue instead of silently substituting another arm.

The ranking margin remains descriptive and is not an abstention substitute.

## Compatibility and artifacts

The downstream candidate object retains `source`, `alignment`, `transfer`, and
`tactics`. Collection JSON therefore remains readable by the current runner. The
candidate's `messages`, `raw_outputs`, and `attempts` now describe one joint prompt
plus any retries instead of four independent stages.

Prompt versioning changes to a new `activation_joint_candidate` template. The legacy
four stage templates may remain in the catalog for historical artifact readability,
but the activation generator no longer invokes them.

## Success criteria

- Exactly `K` initial model calls occur when all responses validate.
- A schema error retries only that candidate and never consumes another candidate's
  seed block.
- The prompt exposes analogy and proof reasoning together.
- Tactic activations use the actual joint-response prefix, with all explanatory
  fields visible before tactic generation.
- Proof-state relevance and neutral-trace displacement are separately recorded and
  separately standardized.
- Existing scoring receives complete candidates through the minimally extended
  downstream interface.
- A batch of entirely negative causal effects produces no winner.
- The full automated test suite passes.
