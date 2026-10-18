# Multi-Analogy Harness Design

## Intent

Restore multiple generated analogies and an explicit selection step inside the
single live solver runtime. The harness owns isolated analogy tracks; search,
semantic recovery, Lean verification, and the shared frontier remain
authoritative. The scoring mechanism is a replaceable black box whose real
definition will be specified separately.

## Visible analogy-assisted recipe

`run_analogy_assisted` must show the analogy lifecycle directly. Before search,
it initializes multiple tracks, scores them through the opaque scorer, selects
one, and starts search with that track's model policy and fallback. When search
is stuck, it updates the cohort lifecycle, scores and selects again, activates
the selected isolated track, generates its rescue, submits the rescue to Lean,
and returns the verified result to the shared search engine.

The visible stuck-state sequence is:

```python
analogies = update_analogies(search, analogies)
scored_analogies = score_analogies(search, analogies)
analogy = select_analogy(scored_analogies)
activate_analogy(search, analogy)
rescue = generate_analogy_rescue(search, analogy.scratchpad)
result = submit_analogy_rescue_to_lean(proof, rescue)
update_search_with_scored_proof_states(search, result)
```

The vanilla recipe is unchanged.

## Harness and isolation

Create `solver/analogy/harness.py`. An `AnalogyHarness` owns a fixed ordered
cohort of `AnalogyTrack` objects. Each track owns its own:

- `AnalogyScratchpad`, including current state, abandoned domains, rebuild
  status, and bounded history;
- ordinary `TacticRanker` bound to that scratchpad;
- `AnalogyFallback` bound to that same scratchpad;
- stable track identifier and initialization trace.

No scratchpad, history list, model policy, or fallback instance is shared
between tracks. Selecting a track changes which policy/fallback the existing
search session uses; it never copies one track's context into another.

## Initialization

The harness generates a cohort at problem initialization, before ordinary
search begins. The cohort size is `analogy.max_model_calls`, reusing the
existing positive configured bound rather than adding another knob. Each track
gets one deterministic seed and one independent initialization prompt. Later
prompts name already accepted domains as exclusions so the cohort is diverse
without sharing conversation history.

Initialization returns exactly one validated `<analogy>` block per successful
track. Invalid or failed generations are traced and skipped. Initialization
fails the problem if it cannot produce any valid track; it never silently falls
back to a shared or empty analogy in an analogy-assisted run.

The opaque scorer receives the initialized tracks. Until its semantics are
specified, the default scorer assigns equal finite scores and stable selection
chooses the first generated track. This makes the selection boundary real and
replaceable without inventing scoring behavior.

## Stuck-state updates

Semantic recovery already chooses the rescue node, including the last valid
ancestor after a countermodel.

- With no countermodel, tracks remain intact until selected. The selected
  fallback may revise steps or change domain using its own history.
- With a countermodel, every track destructively abandons its active analogy
  and enters pending-rebuild state before scoring. The selected track generates
  a new domain, mapping, and story at `search.rescue_node`, which is the last
  semantically valid ancestor. Abandoned-domain memory remains local to that
  track.

Only the selected track performs the model update and Lean rescue attempt for a
stuck event. Other tracks remain isolated candidates for later selection. A
rejected or invalid selected update cannot mutate or pollute another track.

## Scoring black box

Define an `AnalogyScorer` protocol and typed `ScoredAnalogy` result. The scorer
accepts one track plus the optional current `SearchSession` and returns a finite
real score. `score_analogies` preserves cohort order. `select_analogy` chooses
the highest score and uses cohort order as the deterministic tie-breaker.

No causal, orthogonality, activation, SAT, novelty, or Lean-outcome formula is
implemented in this pass. Countermodels govern lifecycle validity and rebuilds,
not the placeholder numeric score. The protocol is the seam for the later
scoring design.

## Search integration

`activate_analogy(search, track)` replaces only the immutable search context's
`tactic_ranker` and `analogy_fallback` fields. It verifies that both belong to
the track's scratchpad. Frontier contents, visited states, semantic caches,
budgets, cancellation, scoring, and reports are unchanged.

## Constraints

- Maintain a single search runtime and one Lean frontier.
- Keep all Lean calls in the existing submission functions.
- Do not submit unselected analogy candidates to Lean.
- Preserve ordinary tactic scoring and proof-state diversity behavior.
- Preserve countermodel pruning, forced moves, and last-valid-ancestor recovery.
- Keep `run.py` limited to the two visible run functions.
- Do not restore the retired activation experiment runtime.
- Leave untracked `docs/paper/` untouched.

## Verification

Tests cover multiple distinct initialization calls and stable IDs, isolated
scratchpads and histories, deterministic equal-score selection, later
replacement by a custom scorer, non-countermodel preservation, destructive
countermodel rebuild preparation, activation of exactly one track, and the
visible loop in `run_analogy_assisted`. Existing rescue, semantic, reporting,
and full-suite tests remain green.
