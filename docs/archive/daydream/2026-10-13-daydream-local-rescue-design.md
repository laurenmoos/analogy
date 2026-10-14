# Daydream Local Rescue Outer Loop

**Date:** 2026-10-13  
**Status:** Ready for review

## Purpose

Add readable scaffolding for a best-first Lean proof-search loop that invokes Daydream only when ordinary tactic expansion is locally stuck.

The first milestone does not implement an embedding model, analogy retrieval, tactic generation, Lean integration, or a goal-distance heuristic. It defines the control flow and the interfaces through which those implementations will later participate.

The loop should be easy to revisit and modify during research. The code should read in the same order as the conceptual process:

```text
expand normally
    → detect local stuckness
    → daydream while novel tactics remain
    → verify every tactic with Lean
    → return a novel state to the ordinary frontier
```

## Scope

This milestone includes:

- best-first frontier orchestration;
- ordinary tactic expansion;
- a precise local-stuckness decision;
- repeated local Daydream rounds without an arbitrary round limit;
- tactic-attempt and proof-state deduplication;
- dependency interfaces for ranking, retrieval, verification, and identity;
- immutable trace records that make behavior inspectable in tests.

This milestone excludes:

- global rescue after the frontier stalls;
- always-on analogical ranking;
- a concrete Lean process adapter;
- an embedding or small-model implementation;
- relation-space clustering algorithms;
- tactic or multi-step program synthesis;
- persistence, parallel search, and a command-line interface.

## Core Terms

### Ordinary expansion

The normal tactic ranker proposes tactic applications for one proof state. Lean checks each proposal. Valid, previously unseen successor states may return to the frontier.

### Local stuckness

A state is locally stuck when one complete ordinary expansion produces no frontier-eligible successor.

This is an observation about search behavior, not a feeling derived from model confidence. A low tactic probability may be recorded, but it does not trigger Daydream by itself.

The decision records one or more explicit reasons:

- `ALL_TACTICS_ERRORED` — Lean rejected every ordinary candidate.
- `ONLY_REPEATED_STATES` — valid candidates led only to states already visited.
- `NO_PROGRESS` — candidates were valid but the configured progress policy rejected every result.
- `CANDIDATES_EXHAUSTED` — the ordinary candidate stream ended without a frontier-eligible successor.

`CANDIDATES_EXHAUSTED` may accompany a more specific reason. Mixed outcomes may have only `NO_PROGRESS` and `CANDIDATES_EXHAUSTED`.

### Tactic novelty

A tactic application is novel when it has not previously been attempted from the same normalized proof state.

Its identity is:

```text
(proof_state_fingerprint, canonical_tactic)
```

The canonical tactic includes normalized arguments. A rejected or errored tactic is still considered attempted and is not retried from the same state.

Tactic novelty determines whether the local Daydream session has another experiment worth running.

### State novelty

A state is novel when Lean returns a normalized proof-state fingerprint that the search has not visited.

State novelty determines whether local rescue succeeded. A tactic can be novel without being useful; only Lean can reveal its result.

### Actionable novelty

Relation retrieval may continue internally, but the outer loop continues a Daydream session only when a round yields at least one untried tactic application.

This prevents an infinite stream of differently worded or slightly different embedding hits from being mistaken for search progress.

## Control Flow

The public search method should remain short. Detailed work belongs in named helpers.

```python
def search(initial_state, goal):
    frontier.push(initial_state)

    while frontier:
        node = frontier.pop_best()
        ordinary = expand_normally(node, goal)

        if ordinary.completed:
            return ordinary.proof

        push_successors(ordinary.successors)

        decision = stuckness_policy.evaluate(ordinary)
        if not decision.is_stuck:
            continue

        rescue = daydream_locally(node, goal, ordinary)
        if rescue.completed:
            return rescue.proof

        push_successors(rescue.successors)

    return SearchExhausted()
```

The local Daydream helper is also explicit:

```python
def daydream_locally(node, goal, ordinary):
    session = start_session(node, goal, ordinary)

    while True:
        analogies = relation_retriever.retrieve(session.query())
        tactics = analogy_ranker.rank(node.state, goal, analogies)
        novel_tactics = session.untried(tactics)

        if not novel_tactics:
            return session.fixed_point()

        outcome = verify(novel_tactics)
        session.observe(analogies, outcome)

        if outcome.completed or outcome.novel_successors:
            return outcome
```

There is no fixed number of Daydream rounds. The session ends at a local fixed point when it can no longer produce an untried tactic application, when Lean completes the proof, when Lean produces a novel frontier-eligible state, or when the enclosing search is cancelled.

Successful rescue returns to ordinary search immediately. It does not remain in a separate analogical search mode.

## Proposed Package Shape

```text
daydream/src/daydream/
├── __init__.py
├── models.py
└── outer_loop.py

daydream/tests/
├── test_models.py
└── test_outer_loop.py
```

The new module keeps orchestration separate from the general domain records in `models.py`.

## Data Records

All records are frozen, slotted dataclasses unless a record must own intentionally mutable session state. Collections exposed by records are tuples.

### `TacticCandidate`

- `source: str` — canonicalizable Lean tactic text.
- `score: float` — ranker score used to order candidates.
- `origin: CandidateOrigin` — `ORDINARY` or `ANALOGICAL`.

### `TacticResult`

- `candidate: TacticCandidate`
- `status: TacticStatus` — `ERROR`, `ACCEPTED`, or `COMPLETE`.
- `state: SymbolicState | None`
- `proof: str | None`
- `diagnostics: tuple[str, ...]`

An accepted non-complete result requires a successor state. A complete result requires proof material. An error has no successor state.

### `SearchNode`

- `state: SymbolicState`
- `score: float`
- `parent_state_id: str | None`
- `tactic: str | None`

Parent and tactic references preserve enough provenance for a future proof reconstruction adapter without making reconstruction part of this milestone.

### `ExpansionOutcome`

- `node: SearchNode`
- `attempts: tuple[TacticResult, ...]`
- `successors: tuple[SearchNode, ...]`
- `discarded: tuple[DiscardedSuccessor, ...]`
- `completed_proof: str | None`

This is the single input to stuckness evaluation and the main trace unit for testing.

### `DiscardedSuccessor`

- `result: TacticResult`
- `reason: DiscardReason` — `REPEATED_STATE` or `NO_PROGRESS`.

This record makes the reason a valid Lean result did not reach the frontier visible to the stuckness policy and trace. Errors remain ordinary `TacticResult` attempts rather than discarded successors.

### `StucknessDecision`

- `state_id: str`
- `is_stuck: bool`
- `reasons: tuple[StuckReason, ...]`

At least one reason is required when `is_stuck` is true. No reasons are allowed when it is false.

### `RelationCandidate`

- `id: str`
- `embedding: Embedding` — a latent higher-order relation representation.
- `score: float`
- `source_domain: str`
- `source_ref: str`

The record requires provenance but does not require a natural-language relation name.

### `DaydreamRound`

- `state_id: str`
- `index: int`
- `relations: tuple[RelationCandidate, ...]`
- `proposed_tactics: tuple[TacticCandidate, ...]`
- `attempts: tuple[TacticResult, ...]`
- `discarded: tuple[DiscardedSuccessor, ...]`

Rounds form the readable local-rescue trace. An empty set of untried proposals ends the session and is represented by the final round rather than an exception.

### `SearchResult`

- `status: SearchStatus` — `COMPLETE`, `EXHAUSTED`, or `CANCELLED`.
- `proof: str | None`
- `ordinary_expansions: tuple[ExpansionOutcome, ...]`
- `stuckness_decisions: tuple[StucknessDecision, ...]`
- `daydream_rounds: tuple[DaydreamRound, ...]`

A complete result requires proof material; an exhausted result does not allow it. The scaffold does not use exceptions to represent ordinary proof failure.

## Dependency Interfaces

The orchestration module depends on small `Protocol` interfaces rather than concrete models or Lean processes.

### `TacticRanker`

Ranks ordinary tactic candidates for a proof state and goal.

### `LeanVerifier`

Runs one tactic from one state and returns a `TacticResult`. Lean remains the authority on validity and completion.

### `StucknessPolicy`

Evaluates one completed ordinary `ExpansionOutcome`. The initial implementation uses the observable rules above; learned policies can implement the same interface later.

### `RelationRetriever`

Uses the current state, goal, failed ordinary attempts, and accumulated Daydream observations to retrieve `RelationCandidate` records. The interface does not require relations to have names.

### `AnalogyTacticRanker`

Uses retrieved relation representations to rank known tactic applications. It does not execute tactics or decide validity.

### `StateFingerprinter`

Returns stable identity for normalized Lean goals and local context. The scaffold treats this as an injected dependency because correct Lean normalization belongs in an adapter.

### `TacticCanonicalizer`

Normalizes tactic syntax and arguments for attempt deduplication.

### `ProgressPolicy`

Decides whether a valid, novel successor is frontier-eligible. The default accepts every globally novel Lean state, matching the vanilla loop. A goal-distance policy may be supplied later.

### `CancellationToken`

Reports whether the enclosing caller has cancelled search. The outer loop checks it before each frontier expansion and Daydream round. Cancellation is represented separately from proof-search exhaustion in the implementation result status.

## Session State

One local Daydream session owns three sets:

```text
seen_relation_regions
attempted_tactic_applications
seen_proof_states
```

Only tactic and state identity affect outer-loop continuation in this milestone. `seen_relation_regions` is passed through to the retriever so a concrete relation-space implementation can avoid redundant neighborhoods; the scaffold does not define a clustering algorithm.

The session also retains prior Lean results. This allows later retrieval rounds to condition on errors, repetitions, and valid-but-unhelpful outcomes.

## Scoring and Frontier Behavior

The ordinary node score follows the vanilla multiplicative form:

```text
successor_score = parent_score × tactic_score
```

Analogically re-ranked tactics use the same field and frontier calculation. The scaffold does not prescribe how an analogy ranker calibrates its score against the ordinary model. That calibration is an experimental concern behind the ranking interface.

The priority queue uses deterministic tie-breaking so tests and traces are reproducible.

## Failure Handling

- A Lean tactic error is expected data and is recorded in `TacticResult`.
- An empty ordinary candidate list produces local stuckness with `CANDIDATES_EXHAUSTED`.
- Empty or redundant analogical results advance the session toward its fixed point.
- An exception from an injected dependency is an integration failure and should propagate rather than be mislabeled as proof-search exhaustion.
- Cancellation stops both ordinary search and local Daydream cleanly.
- Invalid result invariants fail immediately during dataclass construction.

## Readability Requirements

The outer loop is research infrastructure and will be revisited frequently. Its implementation should therefore:

- keep the public `search` method small and linear;
- use names from this document rather than generic names such as `handler` or `processor`;
- separate ordinary expansion, stuckness evaluation, local rescue, and frontier insertion into named methods;
- prefer explicit immutable result records over nested dictionaries;
- keep model calls and Lean calls behind protocols;
- include docstrings that explain *why* a phase exists, not restate its code;
- avoid clever iterator pipelines in orchestration code;
- preserve a complete trace of decisions without logging inside the core loop;
- use deterministic fakes in tests so each control-flow branch reads as a small scenario.

## Testing Strategy

`test_outer_loop.py` will use small fake implementations and cover:

1. ordinary expansion completes without invoking Daydream;
2. ordinary expansion finds a novel state and does not invoke Daydream;
3. all ordinary tactics error and trigger local rescue;
4. repeated states trigger local rescue;
5. an empty ordinary candidate list triggers local rescue;
6. a novel analogical tactic errors, is recorded, and is not retried;
7. several Daydream rounds may run before a tactic reaches a novel state;
8. a novel tactic reaching a repeated state does not count as rescue success;
9. a novel verified state ends rescue and returns to the frontier;
10. proof completion inside rescue returns immediately;
11. no untried tactic ends rescue at a fixed point;
12. equivalent tactic syntax is deduplicated through canonicalization;
13. priority ordering and tie-breaking are deterministic;
14. dependency exceptions propagate;
15. invalid data-record combinations are rejected.

Tests will assert both the final result and the ordered trace of ordinary attempts, stuckness decisions, Daydream rounds, and Lean outcomes.

## Future Extensions

The interfaces leave room for, but do not implement:

- learned stuckness detection;
- semantic relation-region novelty;
- global rescue after frontier stagnation;
- analogy-conditioned tactic generation;
- parallel frontier expansion;
- learned score calibration;
- proof reconstruction from accepted search nodes.

These extensions should not complicate the first local-rescue loop until experiments require them.
