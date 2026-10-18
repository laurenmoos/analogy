# Mental-Model Revision Scoring Design

## Intent

An analogy is a persistent working mental model, not a report and not a tactic
decoration. It is present throughout analogy-assisted reasoning and is revised
when search is stuck. Candidate revisions are ranked by how well their
relational structure fits the solver's current belief about the proof.

## Mental model

A mental model has one domain and an ordered, nonempty list of
correspondences. Each correspondence contains a domain-side transition:

- state before;
- transition or action;
- state after.

It may also contain any known or predicted proof-side state before, tactic or
transition, and state after. Those proof-side fields are optional: partial
future correspondences are valid. They guide model reasoning but do not fix
the mathematical alignment.

The prompt protocol uses one exact `<mental_model>` JSON block. Internal code
uses `MentalModel` and `MentalModelCorrespondence`; analogy remains the name of
the solver feature and harness.

## Proof belief

At a stuck state, the proof belief is the accepted proof-search state machine:
all accepted search nodes currently known and the tactics connecting parent
states to child states, including the rescue node. Rejected and
countermodeled transitions do not become nodes in this structure.
Countermodels instead remain revision guidance and force domain replacement at
the last valid ancestor.

## Structural representation

Each mental-model domain state and transition and each proof state and tactic
is embedded independently by the existing frozen embedding provider. Text is
prefixed with its role and the mental-model domain where applicable.

Each side becomes a directed graph of alternating state and transition
elements. Within one mental model, equal normalized state descriptions denote
the same state node. Each correspondence adds state-before → transition →
state-after edges. List order never invents an edge between mismatched adjacent
correspondences, so partial and disconnected revisions remain representable.

Its internal distance matrix combines:

- cosine distance between independently embedded elements; and
- normalized undirected shortest-path distance in that side's transition
  graph; and
- normalized distance between each element's earliest correspondence position.

The initial experimental blend gives all three components equal weight. Graph
direction remains available in the typed structure and prompts, while the
first scoring experiment uses undirected path distance plus order distance.
These are explicit experimental constants, not learned claims.

## Candidate scoring

The rescue call budget becomes the candidate revision budget. For one stuck
event the model independently proposes up to `analogy.max_model_calls`
revisions from the same current mental model and proof evidence, using distinct
deterministic seeds. Invalid, duplicate-tactic, or invalid-transition outputs
are recorded and excluded.

For every valid candidate, the solver computes an entropically regularized
Gromov–Wasserstein coupling with uniform mass. Lower distance is better. The
coupling derives structural correspondence; generated proof-side fields never
act as fixed anchors. Ties preserve generation order.

Candidates are submitted to Lean in score order. Only a revision whose tactic
completes the proof or produces an accepted successor becomes the active
mental model. If a candidate is rejected, the next ranked candidate is tried.
This keeps Lean authoritative and prevents unselected or failed revisions from
polluting persistent context.

## Lifecycle

- Initialization generates complete or partial mental models.
- The active mental model guides ordinary tactic generation globally.
- Ordinary stuckness permits either correspondence revision or domain change.
- A countermodel forces domain change and supplies update guidance, but is not
  inserted into the mental model or its structural score.
- Harness tracks remain isolated.

## Constraints

- Use the existing embedding provider; add no learned scorer or model.
- Implement the small entropic solver locally without adding a dependency.
- Keep all Lean calls in the existing submission boundary.
- Do not change vanilla search, general tactic scoring, or proof-state novelty.
- Preserve the two visible functions in `run.py`.
- Leave untracked `docs/paper/` untouched.

## Verification

Tests cover strict mental-model parsing with partial correspondences,
proof-belief graph construction, graph/embedding relational matrices,
Gromov–Wasserstein preference for an isomorphic narrative over a shuffled one,
stable ties, candidate generation without scratchpad mutation, best-first Lean
submission, failed-candidate isolation, and countermodel-forced domain change.
