# Curiosity-Conditioned Tactic Selection

**Date:** 2026-10-13  
**Status:** Approved

## Purpose

Build a prototype in which a conventional tactic policy supplies a credible top-\(k\) candidate pool and a cheap, iterative Daydream process searches relations from poetry and physics before an LLM chooses among those candidates.

The analogy is conditioning context. It is not translated into a formal tactic transformation, and it cannot introduce a tactic outside the conventional policy's pool. Lean remains the only authority on whether a selected tactic is executable and useful.

The control flow should read as:

```text
policy proposes top-k tactics
    → try the top candidate normally
    → if locally stuck, sample strange relations
    → refine and gate the relations iteratively
    → choose an untried tactic from the original pool
    → verify it with Lean
    → return verified progress to ordinary search
```

## Scope

This milestone includes:

- one conventional top-\(k\) tactic pool per expanded proof state;
- an ordinary attempt of only the policy's top candidate;
- stuck-only entry into Daydream;
- initial relation sampling from external domains such as poetry and physics;
- iterative relation refinement with prior outcomes available as feedback;
- pertinence assessment and abstention;
- LLM-style selection constrained to untried tactics in the original pool;
- Lean verification of every tactic application;
- immutable traces for relation populations, assessments, choices, and results;
- fixed-point, candidate-exhaustion, progress, completion, and cancellation exits.

This milestone excludes:

- generating tactics outside the conventional policy's pool;
- changing the policy's original tactic probabilities;
- crossover or composition of relations;
- evolving tactic sequences or executable programs;
- training a genetic algorithm, tactic policy, LLM, embedding model, or gate;
- a concrete Lean process adapter;
- global rescue after the ordinary frontier is exhausted;
- parallel relation or tactic evaluation.

## Design Principles

### Competence, curiosity, and judgment remain separate

The conventional policy supplies competence by defining the credible action region. Relation search supplies curiosity by surfacing high-entropy perspectives. The constrained chooser supplies judgment by deciding whether a pertinent perspective changes which credible tactic should be tried.

No component silently takes over another component's role.

### The candidate pool is a hard boundary

For a proof state \(x\) and goal \(g\), the conventional policy returns:

\[
C=\operatorname{TopK}(\pi_{\mathrm{tactic}}(\cdot\mid x,g)).
\]

The ordinary path attempts only \(C_0\). If that attempt completes the proof or produces a frontier-eligible successor, search continues normally. Otherwise, Daydream may select only from the remaining canonical tactic identities in \(C\).

The chooser does not create new tactic text. Its result must match exactly one supplied pool member after tactic canonicalization. An out-of-pool result is an integration error rather than a recoverable Lean rejection.

The chosen tactic retains the probability assigned by the conventional policy. The analogy affects selection, not calibrated tactic likelihood.

### Relations are readable proposals, not required formal operators

A relation proposal contains text that can condition an LLM, provenance that identifies its source, and optional embedding data that may support retrieval. The textual proposal is the transferable hypothesis; the embedding is retrieval metadata rather than the definition of the analogy.

A proposal need not have a fixed natural-language relation label. It may be a metaphor, contrast, transformation description, or other strange perspective derived from poetry or physics.

### Relation search is iterative but not yet compositional

The first Daydream round samples a population. Later rounds refine the current population by reframing, specializing, inverting, or otherwise mutating proposals. Refinement receives the proof state, goal, fixed tactic pool, current population, and completed Daydream trace, including Lean results.

This is evolutionary in the modest sense of repeated variation and selection. It does not yet define crossover, multi-relation composition, chromosomes, or program synthesis.

## Core Records

All public records are frozen, slotted dataclasses. Public collections normalize to tuples.

### `RelationProposal`

- `id: str` — stable identity for tracing.
- `text: str` — the actual conditioning material presented to the chooser.
- `source_domain: str` — for example `poetry` or `physics`.
- `source_reference: str` — corpus or item provenance.
- `generation: int` — zero for sampled proposals and increasing for refinements.
- `parent_ids: tuple[str, ...]` — empty for initial samples; otherwise the proposals refined to create this one.
- `embedding: Embedding | None` — optional retrieval representation.

IDs identify trace records. Novelty is based on normalized proposal content, not ID alone, so endlessly assigning new IDs to the same text cannot keep Daydream alive.

### `PertinenceAssessment`

- `proposal: RelationProposal`
- `score: float` — a finite value in \([0,1]\).
- `is_pertinent: bool`
- `rationale: str | None` — optional inspectable model output.

The gate may reject every proposal. Rejection is ordinary abstention, not an error.

### `DaydreamRound`

Each round records:

- proof-state identity and round index;
- whether the population was initially sampled or refined;
- the full relation population;
- every pertinence assessment;
- the pertinent survivor beam;
- the fixed available tactic pool passed to the chooser;
- the selected tactic, if any;
- the Lean result, if a tactic was selected;
- any reason the accepted result was discarded from the frontier.

The trace distinguishes failure to find a pertinent analogy, chooser abstention, Lean rejection, repeated state, and no-progress state.

### `ExpansionOutcome`

The existing record gains `candidate_pool: tuple[TacticCandidate, ...]`. It preserves the complete deduplicated conventional-policy pool even though `attempts` contains at most the ordinary top-one result. This makes the stuckness decision and later Daydream input refer to the same observable expansion.

## Interfaces

### `TacticRanker`

The existing conventional policy interface continues to return an ordered sequence of `TacticCandidate` values. The outer loop materializes it once per proof state, canonicalizes duplicate tactic applications, and treats the result as the fixed top-\(k\) pool.

### `RelationSampler`

```python
def sample(
    state: SymbolicState,
    goal: SymbolicState,
    tactic_pool: tuple[TacticCandidate, ...],
) -> Sequence[RelationProposal]: ...
```

It supplies a high-entropy initial population. Concrete sampling strategy is outside this milestone.

### `RelationRefiner`

```python
def refine(
    state: SymbolicState,
    goal: SymbolicState,
    tactic_pool: tuple[TacticCandidate, ...],
    population: tuple[RelationProposal, ...],
    prior_rounds: tuple[DaydreamRound, ...],
) -> Sequence[RelationProposal]: ...
```

It produces the next population. Returning no content-novel proposal declares a local relation fixed point.

### `PertinenceGate`

```python
def assess(
    state: SymbolicState,
    goal: SymbolicState,
    tactic_pool: tuple[TacticCandidate, ...],
    population: tuple[RelationProposal, ...],
) -> Sequence[PertinenceAssessment]: ...
```

It must return exactly one assessment for every proposal in the supplied population and no assessment for an unknown proposal. Structural mismatches are integration errors.

### `ConstrainedTacticChooser`

```python
def choose(
    state: SymbolicState,
    goal: SymbolicState,
    candidates: tuple[TacticCandidate, ...],
    analogies: tuple[RelationProposal, ...],
    prior_rounds: tuple[DaydreamRound, ...],
) -> TacticCandidate | None: ...
```

The candidates argument contains only canonical, untried members of the original pool. Returning `None` is abstention. Returning anything outside that argument is an integration error.

Existing `LeanVerifier`, `StucknessPolicy`, `StateFingerprinter`, `TacticCanonicalizer`, `ProgressPolicy`, and `CancellationToken` interfaces remain.

## Control Flow

### Ordinary expansion

For each frontier node:

1. Ask the conventional policy for an ordered candidate sequence once.
2. Remove canonical duplicates while preserving order.
3. Save the result as the fixed pool for this proof state.
4. Attempt only the first candidate.
5. If Lean completes the proof, return it.
6. If Lean produces a novel, progress-making state, enqueue it and do not Daydream at this node.
7. Otherwise, record local stuckness and pass the pool plus ordinary result into Daydream.

An empty pool is locally stuck but cannot be rescued by constrained selection, so Daydream returns immediately without sampling relations.

### Daydream relation search

```python
population = sampler.sample(state, goal, fixed_pool)

while content_novel(population):
    assessments = gate.assess(state, goal, fixed_pool, population)
    analogies = pertinent(assessments)
    candidates = untried_members(fixed_pool)

    if not candidates:
        return candidate_exhausted

    choice = None
    if analogies:
        choice = chooser.choose(state, goal, candidates, analogies, trace)
    record_round(population, assessments, analogies, candidates, choice)

    if choice is not None:
        result = lean.run_tactic(state, choice)
        record_result(result)
        if result completes or yields frontier-eligible progress:
            return result

    population = refiner.refine(state, goal, fixed_pool, population, trace)

return relation_fixed_point
```

The gate is always evaluated before choice. If no analogy survives, the chooser is not called and the refiner may still produce a better next population. If analogies survive, the chooser may still abstain. A rejected or no-progress tactic becomes attempted and cannot be selected again from the same state.

There is no fixed refinement-round limit in the scaffold. Cancellation is checked between rounds. Concrete runtime integrations may provide a cancellation policy or enclosing budget.

### Proposal novelty

Proposal content is normalized by a `RelationCanonicalizer` interface. The default canonicalizer collapses whitespace and case-folds text.

A refinement round is actionable only if it contains at least one canonical proposal not seen earlier in the same local rescue session. Duplicate proposals may remain visible in raw dependency output, but the population passed to the gate is deduplicated and contains only content-novel proposals.

### Exit conditions

Local Daydream ends when:

- Lean completes the proof;
- Lean returns a novel, progress-making state;
- every member of the fixed tactic pool has been attempted;
- refinement yields no content-novel relation proposal;
- the sampler yields no proposal;
- the enclosing search is cancelled.

On verified progress, control returns immediately to the ordinary best-first frontier. Daydream never continues on the successor state without a fresh conventional candidate pool.

## Validation and Failure Semantics

- Lean tactic errors are expected `TacticResult` data.
- Gate and chooser abstention are expected control flow.
- Duplicate relation text is filtered by canonical content.
- Duplicate tactics are filtered by canonical tactic identity.
- A gate omitting, duplicating, or inventing proposal assessments raises `ValueError`.
- A chooser returning a tactic outside the supplied candidate tuple raises `ValueError`.
- A verifier returning a result for a different tactic raises `ValueError`.
- Exceptions raised by external components propagate; the scaffold does not disguise unavailable models or processes as abstention.

## Trace and Evaluation

The scaffold does not train the gate or relation search. It records enough evidence to evaluate them later.

For each stuck state, experiments can compare:

- the original policy ordering;
- the chosen tactic with and without analogy conditioning;
- gate scores and abstention frequency;
- relation generations required before selection;
- Lean rejection, repeated-state, no-progress, progress, and completion outcomes;
- improvement over the ordinary top-one attempt.

The eventual operational gate target is whether analogy conditioning improves expected Lean-verified progress over the unconditioned ranking. That learning procedure is future work.

## Package Changes

The implementation should keep the public search entry point readable and split the growing relation-search concepts from generic proof-search records:

```text
daydream/src/daydream/
├── __init__.py
├── models.py
├── search_types.py
├── outer_loop.py
└── curiosity.py

daydream/tests/
├── test_models.py
├── test_outer_loop.py
└── test_curiosity.py
```

- `search_types.py` owns the tactic, verifier-result, successor, expansion, and stuckness records shared by the other two modules.
- `curiosity.py` owns relation proposals, assessments, Daydream traces, relation canonicalization, curiosity protocols, and the local relation-search orchestration.
- `outer_loop.py` owns best-first proof search, ordinary top-one expansion, Lean-result classification, and integration with curiosity search. It imports shared records and the curiosity engine without a reverse dependency.
- `__init__.py` re-exports the supported prototype API.

## Acceptance Criteria

1. The conventional policy is called once per expanded proof state and its deduplicated ordered result becomes a fixed pool.
2. Ordinary expansion attempts only the first pool member.
3. Daydream runs only when that ordinary attempt produces no frontier-eligible progress.
4. Initial relations are sampled, later populations are refined, and every population is gated before tactic choice; an empty pertinent beam never reaches the chooser.
5. The chooser can select only an untried member of the original pool.
6. A selected tactic preserves its conventional-policy probability and is verified by Lean.
7. Duplicate relation content and duplicate tactic applications do not keep search alive.
8. Empty sampling, gate abstention, chooser abstention, relation fixed point, tactic exhaustion, and cancellation terminate predictably and remain visible in traces.
9. Verified proof completion returns the proof; verified progress returns a successor to ordinary best-first search.
10. Existing non-Daydream search behavior, model records, and `daydream` independence from `lean_gym` remain covered by tests.

