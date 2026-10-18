# Visible Search Scoring Design

## Intent

Make the live solver's two general scoring mechanisms obvious both in the
source tree and in the vanilla and analogy-assisted run recipes. This pass is
strictly about scoring shared by both runs. Analogy-specific causal or
orthogonality scoring remains a separate follow-up.

## Visible recipes

Both ordinary search loops use the same three-line recipe:

```python
tactics = generate_and_score_tactics_with_model_policy(search)
results = submit_tactics_to_lean(proof, tactics)
update_search_with_scored_proof_states(search, results)
```

An analogy rescue result also enters search through
`update_search_with_scored_proof_states`, so an accepted rescue successor gets
the same proof-state novelty treatment as an ordinary successor.

## Package boundary

Create `solver/scoring/` as the single obvious place for live, general search
score calculations:

- `tactics.py` validates and normalizes the model's nonnegative tactic weights.
- `proof_states.py` embeds accepted Lean proof states and measures novelty as
  mean cosine distance to their nearest previously accepted states.
- `priority.py` combines tactic score, the existing depth penalty, and proof
  state novelty into the frontier ordering key.
- `__init__.py` exposes the small public scoring vocabulary.

The model policy still owns prompting, generation, repair, filtering, and the
construction of the tactic batch. Search still owns result classification,
deduplication, semantic rejection, frontier mutation, and termination. The
scoring package contains calculations and score-bearing observations, not run
or search orchestration.

## Timing and behavior

Tactic scoring happens before Lean. The model-provided weights are validated
and normalized without changing candidate order or width. Their normalized
values remain the successor nodes' primary score.

Proof-state scoring happens only after Lean produces a successor and search has
accepted it as semantically valid, progressive, and non-repeated. Rejected,
repeated, errored, and semantically refuted states must not enter the diversity
archive. Novelty remains a secondary frontier key after tactic score minus the
existing `0.02 * depth` penalty; insertion order remains the final tie-breaker.

The initial state and accepted analogy-rescue successors continue to be
observed exactly as they are today. Diversity observations remain part of
`SearchResult` and therefore remain available to reports.

## Module changes

`proof/diversity.py` is retired after its implementation and record move to
`scoring/proof_states.py`; no compatibility shim remains. Model parsing stops
performing score normalization itself: it returns validated selected weights,
and the model policy calls the shared tactic scorer at the same points needed
to preserve current multi-attempt behavior. Search calls the shared priority
function rather than spelling out the score formula inside its frontier.

The public search transition is renamed from `update_search` to
`update_search_with_scored_proof_states`. The public model-policy operation is
renamed from `generate_tactics_with_model_policy` to
`generate_and_score_tactics_with_model_policy`. Old names are removed rather
than retained as compatibility aliases.

## Constraints

- Preserve candidate order, ordinary width, normalized values, retry behavior,
  successor selection, and frontier ordering.
- Preserve cancellation, budgets, semantic recovery, forced moves, and analogy
  rescue behavior.
- Keep `run.py` limited to `run_vanilla` and `run_analogy_assisted`.
- Keep scoring independent of `search`; dependencies flow from model policy and
  search into scoring, never from scoring back into either orchestrator.
- Do not restore retired activation experiments or add analogy-specific scores
  in this pass.
- Leave the existing untracked `docs/paper/` directory untouched.

## Verification

Focused tests pin tactic normalization, unchanged candidate order and width,
state-diversity caching, primary-versus-secondary frontier ordering, ordinary
and rescue successor observation, and the visible calls in both run recipes.
The full test suite must remain green.
