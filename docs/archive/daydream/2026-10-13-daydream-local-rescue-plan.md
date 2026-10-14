# Daydream Local Rescue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build readable, dependency-injected scaffolding for best-first Lean proof search that enters an open-ended local Daydream session only when ordinary tactic expansion is stuck.

**Architecture:** A single `outer_loop.py` module owns immutable search records, small dependency protocols, the observable stuckness policy, and a linear `LocalRescueSearch` orchestrator. Ordinary expansion and analogical rescue share the same Lean verifier; tactic novelty keeps a Daydream session active, while a verified novel proof state returns control to the ordinary frontier.

**Tech Stack:** Python 3.11+, standard-library dataclasses, enums, protocols, `heapq`, and pytest 8.

**Spec:** `docs/superpowers/specs/2026-10-13-daydream-local-rescue-design.md`

## Global Constraints

- Keep `daydream` independent of `lean_gym`; all Lean behavior enters through protocols.
- Use no runtime dependency outside the Python standard library.
- Keep `LocalRescueSearch.search` small and linear; move each phase into a plainly named helper.
- Use frozen, slotted dataclasses and tuple-valued public collections.
- Treat Lean tactic rejection as expected data; propagate integration exceptions.
- Invoke Daydream only after one complete ordinary expansion produces no frontier-eligible successor.
- Allow any number of local Daydream rounds while they produce untried tactic applications.
- End local rescue at completion, a novel frontier-eligible state, cancellation, or an actionable fixed point.
- Do not implement global rescue, always-on analogy, concrete models, Lean adapters, or program synthesis.
- The workspace currently has no Git repository, so task checkpoints cannot be committed unless Git metadata is added separately by the user.

## Review Focus

- A verifier returning two equivalent successor states in one expansion must enqueue only the first and trace the second as repeated.
- Different tactic formatting that canonicalizes to the same application must be attempted only once from a state.
- A Daydream retriever that returns new relation IDs forever but no untried tactics must stop at the first non-actionable round.
- Cancellation during a multi-round Daydream session must return `CANCELLED` with all completed trace records preserved.
- A custom progress policy rejecting a globally novel state must record `NO_PROGRESS` and allow local rescue to begin.

---

## File Structure

- Create `daydream/src/daydream/outer_loop.py` — records, protocols, default policies, priority frontier, and the readable orchestration loop.
- Create `daydream/tests/test_outer_loop.py` — scenario-style tests using deterministic fakes.
- Modify `daydream/src/daydream/__init__.py` — re-export the supported outer-loop API.
- Modify `README.md` — replace conceptual-only language with a short link to the implemented scaffold and its boundary.

The feature remains in one focused module because all types exist to explain one control loop. If the concrete Lean or embedding adapters later arrive, they belong in separate adapter modules rather than this file.

### Task 1: Immutable Search Records

**Files:**
- Create: `daydream/src/daydream/outer_loop.py`
- Create: `daydream/tests/test_outer_loop.py`

**Interfaces:**
- Consumes: `daydream.models.Embedding`, `daydream.models.SymbolicState`.
- Produces: `CandidateOrigin`, `TacticStatus`, `DiscardReason`, `StuckReason`, `SearchStatus`, `TacticCandidate`, `TacticResult`, `SearchNode`, `DiscardedSuccessor`, `ExpansionOutcome`, `StucknessDecision`, `RelationCandidate`, `DaydreamRound`, and `SearchResult`.

- [ ] **Step 1: Write failing construction and invariant tests**

Create `daydream/tests/test_outer_loop.py` with imports and these initial tests:

```python
from __future__ import annotations

import math

import pytest

from daydream.models import Embedding, SymbolicState
from daydream.outer_loop import (
    CandidateOrigin,
    DaydreamRound,
    DiscardReason,
    DiscardedSuccessor,
    ExpansionOutcome,
    RelationCandidate,
    SearchNode,
    SearchResult,
    SearchStatus,
    StuckReason,
    StucknessDecision,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)


def state(identifier: str) -> SymbolicState:
    return SymbolicState(identifier, f"goal {identifier}", "lean4")


def candidate(source: str = "intro", score: float = 0.8) -> TacticCandidate:
    return TacticCandidate(source, score, CandidateOrigin.ORDINARY)


def test_search_records_preserve_a_readable_trace() -> None:
    tactic = candidate()
    result = TacticResult(tactic, TacticStatus.ACCEPTED, state("b"))
    node = SearchNode(state("a"), 1.0)
    successor = SearchNode(state("b"), 0.8, "a", "intro")
    discarded = DiscardedSuccessor(result, DiscardReason.REPEATED_STATE)
    expansion = ExpansionOutcome(node, (result,), (successor,), (discarded,))
    decision = StucknessDecision("a", True, (StuckReason.CANDIDATES_EXHAUSTED,))
    relation = RelationCandidate("rel-1", Embedding("relations", (0.1, 0.2)), 0.9, "poetry", "poem:1")
    round_ = DaydreamRound("a", 0, (relation,), (tactic,), (result,))
    search = SearchResult(SearchStatus.EXHAUSTED, None, (expansion,), (decision,), (round_,))

    assert search.ordinary_expansions[0].node.state.id == "a"
    assert search.daydream_rounds[0].relations[0].source_domain == "poetry"


@pytest.mark.parametrize("score", [True, math.nan, math.inf, -0.01, 1.01])
def test_tactic_candidate_requires_a_probability(score: object) -> None:
    with pytest.raises(ValueError, match="score"):
        TacticCandidate("intro", score, CandidateOrigin.ORDINARY)  # type: ignore[arg-type]


def test_tactic_result_enforces_status_payloads() -> None:
    tactic = candidate()

    with pytest.raises(ValueError, match="state"):
        TacticResult(tactic, TacticStatus.ACCEPTED)
    with pytest.raises(ValueError, match="proof"):
        TacticResult(tactic, TacticStatus.COMPLETE)
    with pytest.raises(ValueError, match="must not"):
        TacticResult(tactic, TacticStatus.ERROR, state("b"))


def test_stuckness_decision_requires_reasons_exactly_when_stuck() -> None:
    with pytest.raises(ValueError, match="reasons"):
        StucknessDecision("a", True, ())
    with pytest.raises(ValueError, match="reasons"):
        StucknessDecision("a", False, (StuckReason.NO_PROGRESS,))


def test_search_result_matches_its_status() -> None:
    with pytest.raises(ValueError, match="proof"):
        SearchResult(SearchStatus.COMPLETE, None)
    with pytest.raises(ValueError, match="proof"):
        SearchResult(SearchStatus.EXHAUSTED, "by exact h")
```

- [ ] **Step 2: Run the record tests and confirm the missing module failure**

Run:

```bash
cd daydream
python -m pytest tests/test_outer_loop.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'daydream.outer_loop'`.

- [ ] **Step 3: Implement the enums, validation helpers, and records**

Create `daydream/src/daydream/outer_loop.py`. Use the existing model validation style, normalize iterable fields to tuples, and implement these exact public shapes:

```python
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from numbers import Real
from typing import Iterable

from daydream.models import Embedding, SymbolicState


class CandidateOrigin(str, Enum):
    ORDINARY = "ordinary"
    ANALOGICAL = "analogical"


class TacticStatus(str, Enum):
    ERROR = "error"
    ACCEPTED = "accepted"
    COMPLETE = "complete"


class DiscardReason(str, Enum):
    REPEATED_STATE = "repeated_state"
    NO_PROGRESS = "no_progress"


class StuckReason(str, Enum):
    ALL_TACTICS_ERRORED = "all_tactics_errored"
    ONLY_REPEATED_STATES = "only_repeated_states"
    NO_PROGRESS = "no_progress"
    CANDIDATES_EXHAUSTED = "candidates_exhausted"


class SearchStatus(str, Enum):
    COMPLETE = "complete"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"
```

Add private `_require_text`, `_finite_float`, `_probability`, and `_tuple` helpers. `_probability` rejects booleans and values outside `[0.0, 1.0]`.

Implement the dataclasses with the field order below so tests and later interfaces remain consistent:

```python
@dataclass(frozen=True, slots=True)
class TacticCandidate:
    source: str
    score: float
    origin: CandidateOrigin


@dataclass(frozen=True, slots=True)
class TacticResult:
    candidate: TacticCandidate
    status: TacticStatus
    state: SymbolicState | None = None
    proof: str | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchNode:
    state: SymbolicState
    score: float
    parent_state_id: str | None = None
    tactic: str | None = None


@dataclass(frozen=True, slots=True)
class DiscardedSuccessor:
    result: TacticResult
    reason: DiscardReason


@dataclass(frozen=True, slots=True)
class ExpansionOutcome:
    node: SearchNode
    attempts: tuple[TacticResult, ...] = ()
    successors: tuple[SearchNode, ...] = ()
    discarded: tuple[DiscardedSuccessor, ...] = ()
    completed_proof: str | None = None


@dataclass(frozen=True, slots=True)
class StucknessDecision:
    state_id: str
    is_stuck: bool
    reasons: tuple[StuckReason, ...] = ()


@dataclass(frozen=True, slots=True)
class RelationCandidate:
    id: str
    embedding: Embedding
    score: float
    source_domain: str
    source_ref: str


@dataclass(frozen=True, slots=True)
class DaydreamRound:
    state_id: str
    index: int
    relations: tuple[RelationCandidate, ...] = ()
    proposed_tactics: tuple[TacticCandidate, ...] = ()
    attempts: tuple[TacticResult, ...] = ()
    discarded: tuple[DiscardedSuccessor, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchResult:
    status: SearchStatus
    proof: str | None
    ordinary_expansions: tuple[ExpansionOutcome, ...] = ()
    stuckness_decisions: tuple[StucknessDecision, ...] = ()
    daydream_rounds: tuple[DaydreamRound, ...] = ()
```

Validate all required text and enum fields, finite scores, non-negative round indexes, record instance types, tuple normalization, and the payload invariants asserted by the tests. Permit any finite `RelationCandidate.score`; only tactic probabilities and node path scores are constrained to `[0, 1]`.

- [ ] **Step 4: Run the record tests**

Run:

```bash
cd daydream
python -m pytest tests/test_outer_loop.py -v
```

Expected: all Task 1 tests pass.

- [ ] **Step 5: Record the checkpoint**

Run:

```bash
cd /Users/laurenmoos/analogy
git status --short
```

Expected in the current workspace: `fatal: not a git repository`. Record Task 1 as complete in this plan; do not initialize Git as part of this feature.

### Task 2: Protocols and Observable Stuckness

**Files:**
- Modify: `daydream/src/daydream/outer_loop.py`
- Modify: `daydream/tests/test_outer_loop.py`

**Interfaces:**
- Consumes: the records from Task 1.
- Produces: `TacticRanker`, `LeanVerifier`, `StucknessPolicy`, `RelationRetriever`, `AnalogyTacticRanker`, `StateFingerprinter`, `TacticCanonicalizer`, `ProgressPolicy`, `CancellationToken`, `ObservableStucknessPolicy`, `StateIdFingerprinter`, `WhitespaceTacticCanonicalizer`, `AcceptNovelState`, and `NeverCancelled`.

- [ ] **Step 1: Write failing default-policy tests**

Append:

```python
from daydream.outer_loop import (
    AcceptNovelState,
    NeverCancelled,
    ObservableStucknessPolicy,
    StateIdFingerprinter,
    WhitespaceTacticCanonicalizer,
)


def outcome_with(*results: TacticResult, discarded: tuple[DiscardedSuccessor, ...] = ()) -> ExpansionOutcome:
    return ExpansionOutcome(SearchNode(state("a"), 1.0), results, (), discarded)


def test_observable_stuckness_reports_all_errors() -> None:
    error = TacticResult(candidate(), TacticStatus.ERROR, diagnostics=("unknown tactic",))

    decision = ObservableStucknessPolicy().evaluate(outcome_with(error))

    assert decision.is_stuck
    assert decision.reasons == (
        StuckReason.ALL_TACTICS_ERRORED,
        StuckReason.CANDIDATES_EXHAUSTED,
    )


def test_observable_stuckness_reports_repetition_and_no_progress() -> None:
    accepted = TacticResult(candidate(), TacticStatus.ACCEPTED, state("b"))
    repeated = DiscardedSuccessor(accepted, DiscardReason.REPEATED_STATE)
    stalled = DiscardedSuccessor(accepted, DiscardReason.NO_PROGRESS)

    repeated_decision = ObservableStucknessPolicy().evaluate(outcome_with(accepted, discarded=(repeated,)))
    stalled_decision = ObservableStucknessPolicy().evaluate(outcome_with(accepted, discarded=(stalled,)))

    assert StuckReason.ONLY_REPEATED_STATES in repeated_decision.reasons
    assert StuckReason.NO_PROGRESS in stalled_decision.reasons


def test_successor_prevents_local_stuckness() -> None:
    successor = SearchNode(state("b"), 0.8, "a", "intro")
    expansion = ExpansionOutcome(SearchNode(state("a"), 1.0), successors=(successor,))

    assert ObservableStucknessPolicy().evaluate(expansion) == StucknessDecision("a", False)


def test_default_identity_and_control_policies_are_explicit() -> None:
    before = state("a")
    after = state("b")

    assert StateIdFingerprinter().fingerprint(before) == "a"
    assert WhitespaceTacticCanonicalizer().canonicalize("  exact   h  ") == "exact h"
    assert AcceptNovelState().accepts(before, after, state("goal"))
    assert not NeverCancelled().is_cancelled()
```

- [ ] **Step 2: Run the new tests and confirm missing-name failures**

Run:

```bash
cd daydream
python -m pytest tests/test_outer_loop.py -v
```

Expected: collection fails because the policy classes are not defined.

- [ ] **Step 3: Add exact protocol signatures**

Add imports for `Protocol` and `Sequence`, then define:

```python
class TacticRanker(Protocol):
    def rank(self, state: SymbolicState, goal: SymbolicState) -> Sequence[TacticCandidate]: ...


class LeanVerifier(Protocol):
    def run_tactic(self, state: SymbolicState, candidate: TacticCandidate) -> TacticResult: ...


class StucknessPolicy(Protocol):
    def evaluate(self, outcome: ExpansionOutcome) -> StucknessDecision: ...


class RelationRetriever(Protocol):
    def retrieve(
        self,
        state: SymbolicState,
        goal: SymbolicState,
        ordinary: ExpansionOutcome,
        prior_rounds: tuple[DaydreamRound, ...],
    ) -> Sequence[RelationCandidate]: ...


class AnalogyTacticRanker(Protocol):
    def rank(
        self,
        state: SymbolicState,
        goal: SymbolicState,
        relations: tuple[RelationCandidate, ...],
        prior_attempts: tuple[TacticResult, ...],
    ) -> Sequence[TacticCandidate]: ...


class StateFingerprinter(Protocol):
    def fingerprint(self, state: SymbolicState) -> str: ...


class TacticCanonicalizer(Protocol):
    def canonicalize(self, source: str) -> str: ...


class ProgressPolicy(Protocol):
    def accepts(self, before: SymbolicState, after: SymbolicState, goal: SymbolicState) -> bool: ...


class CancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...
```

- [ ] **Step 4: Implement the transparent default policies**

Implement the policies directly from visible search evidence:

```python
class ObservableStucknessPolicy:
    def evaluate(self, outcome: ExpansionOutcome) -> StucknessDecision:
        if outcome.completed_proof is not None or outcome.successors:
            return StucknessDecision(outcome.node.state.id, False)

        reasons: list[StuckReason] = []
        if outcome.attempts and all(
            result.status is TacticStatus.ERROR for result in outcome.attempts
        ):
            reasons.append(StuckReason.ALL_TACTICS_ERRORED)

        discarded_reasons = {item.reason for item in outcome.discarded}
        accepted = [
            result for result in outcome.attempts
            if result.status is TacticStatus.ACCEPTED
        ]
        if accepted and discarded_reasons == {DiscardReason.REPEATED_STATE}:
            reasons.append(StuckReason.ONLY_REPEATED_STATES)
        if DiscardReason.NO_PROGRESS in discarded_reasons:
            reasons.append(StuckReason.NO_PROGRESS)

        reasons.append(StuckReason.CANDIDATES_EXHAUSTED)
        return StucknessDecision(outcome.node.state.id, True, tuple(reasons))


class StateIdFingerprinter:
    def fingerprint(self, state: SymbolicState) -> str:
        return state.id


class WhitespaceTacticCanonicalizer:
    def canonicalize(self, source: str) -> str:
        return " ".join(source.split())


class AcceptNovelState:
    def accepts(
        self,
        before: SymbolicState,
        after: SymbolicState,
        goal: SymbolicState,
    ) -> bool:
        return True


class NeverCancelled:
    def is_cancelled(self) -> bool:
        return False
```

- [ ] **Step 5: Run Task 1 and Task 2 tests together**

Run:

```bash
cd daydream
python -m pytest tests/test_outer_loop.py -v
```

Expected: all tests pass.

### Task 3: Ordinary Best-First Search

**Files:**
- Modify: `daydream/src/daydream/outer_loop.py`
- Modify: `daydream/tests/test_outer_loop.py`

**Interfaces:**
- Consumes: `TacticRanker`, `LeanVerifier`, `StucknessPolicy`, identity policies, and Task 1 records.
- Produces: `LocalRescueSearch.search(initial_state: SymbolicState, goal: SymbolicState) -> SearchResult` and its `_expand_normally` helper.

- [ ] **Step 1: Add deterministic fakes and failing ordinary-search scenarios**

Append fakes whose inputs are dictionaries keyed by state ID:

```python
class FakeTacticRanker:
    def __init__(self, candidates: dict[str, tuple[TacticCandidate, ...]]) -> None:
        self.candidates = candidates
        self.calls: list[str] = []

    def rank(self, current: SymbolicState, goal: SymbolicState) -> tuple[TacticCandidate, ...]:
        self.calls.append(current.id)
        return self.candidates.get(current.id, ())


class FakeVerifier:
    def __init__(self, results: dict[tuple[str, str], TacticResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, str]] = []

    def run_tactic(self, current: SymbolicState, tactic: TacticCandidate) -> TacticResult:
        key = (current.id, tactic.source)
        self.calls.append(key)
        return self.results[key]


class FailingRetriever:
    def retrieve(self, current, goal, ordinary, prior_rounds):
        raise AssertionError("Daydream must not run")


class FailingAnalogyRanker:
    def rank(self, current, goal, relations, prior_attempts):
        raise AssertionError("Daydream must not run")


def make_search(tactic_ranker, verifier, **overrides):
    arguments = {
        "tactic_ranker": tactic_ranker,
        "verifier": verifier,
        "stuckness_policy": ObservableStucknessPolicy(),
        "relation_retriever": FailingRetriever(),
        "analogy_ranker": FailingAnalogyRanker(),
        "state_fingerprinter": StateIdFingerprinter(),
        "tactic_canonicalizer": WhitespaceTacticCanonicalizer(),
        "progress_policy": AcceptNovelState(),
        "cancellation": NeverCancelled(),
    }
    arguments.update(overrides)
    return LocalRescueSearch(**arguments)
```

Add a local `make_search` factory supplying the transparent defaults and the two injected fakes. Then add:

```python
def test_ordinary_completion_never_invokes_daydream() -> None:
    exact = candidate("exact h", 0.9)
    verifier = FakeVerifier({
        ("start", "exact h"): TacticResult(exact, TacticStatus.COMPLETE, proof="by exact h"),
    })
    search = make_search(FakeTacticRanker({"start": (exact,)}), verifier)

    result = search.search(state("start"), state("goal"))

    assert result.status is SearchStatus.COMPLETE
    assert result.proof == "by exact h"
    assert result.daydream_rounds == ()


def test_ordinary_novel_successor_returns_to_frontier() -> None:
    intro = candidate("intro h", 0.8)
    exact = candidate("exact h", 0.7)
    verifier = FakeVerifier({
        ("start", "intro h"): TacticResult(intro, TacticStatus.ACCEPTED, state("introduced")),
        ("introduced", "exact h"): TacticResult(exact, TacticStatus.COMPLETE, proof="by intro h; exact h"),
    })
    ranker = FakeTacticRanker({"start": (intro,), "introduced": (exact,)})

    result = make_search(ranker, verifier).search(state("start"), state("goal"))

    assert result.status is SearchStatus.COMPLETE
    assert ranker.calls == ["start", "introduced"]


def test_priority_queue_uses_path_score_then_insertion_order() -> None:
    left = candidate("left", 0.4)
    right = candidate("right", 0.9)
    finish = candidate("finish", 1.0)
    verifier = FakeVerifier({
        ("start", "left"): TacticResult(left, TacticStatus.ACCEPTED, state("low")),
        ("start", "right"): TacticResult(right, TacticStatus.ACCEPTED, state("high")),
        ("high", "finish"): TacticResult(finish, TacticStatus.COMPLETE, proof="high path"),
    })
    ranker = FakeTacticRanker({"start": (left, right), "high": (finish,)})

    result = make_search(ranker, verifier).search(state("start"), state("goal"))

    assert result.proof == "high path"
    assert ranker.calls == ["start", "high"]
```

- [ ] **Step 2: Run each scenario and confirm `LocalRescueSearch` is missing**

Run:

```bash
cd daydream
python -m pytest tests/test_outer_loop.py -k 'ordinary or priority' -v
```

Expected: collection fails because `LocalRescueSearch` is undefined.

- [ ] **Step 3: Implement the priority frontier and constructor**

Use `heapq` entries `(-node.score, insertion_index, node)` with an `itertools.count()` tie-breaker. Define `LocalRescueSearch.__init__` with keyword-only dependencies:

```python
class _PriorityFrontier:
    def __init__(self) -> None:
        self._items: list[tuple[float, int, SearchNode]] = []
        self._order = count()

    def __bool__(self) -> bool:
        return bool(self._items)

    def push(self, node: SearchNode) -> None:
        heappush(self._items, (-node.score, next(self._order), node))

    def extend(self, nodes: Iterable[SearchNode]) -> None:
        for node in nodes:
            self.push(node)

    def pop_best(self) -> SearchNode:
        return heappop(self._items)[2]
```

Import `heappop`, `heappush`, and `count` from their standard-library modules.

```python
def __init__(
    self,
    *,
    tactic_ranker: TacticRanker,
    verifier: LeanVerifier,
    stuckness_policy: StucknessPolicy,
    relation_retriever: RelationRetriever,
    analogy_ranker: AnalogyTacticRanker,
    state_fingerprinter: StateFingerprinter,
    tactic_canonicalizer: TacticCanonicalizer,
    progress_policy: ProgressPolicy,
    cancellation: CancellationToken,
) -> None:
```

Store dependencies under names matching the constructor. Keep `_frontier` local to each `search` call so the object can be reused safely.

Add this private return record next to the internal frontier helper:

```python
@dataclass(frozen=True, slots=True)
class _RescueOutcome:
    completed_proof: str | None = None
    successors: tuple[SearchNode, ...] = ()
    rounds: tuple[DaydreamRound, ...] = ()
    cancelled: bool = False
```

- [ ] **Step 4: Implement ordinary expansion and the linear outer loop**

`search` initializes the visited fingerprint set with the initial state, pops the best node, checks cancellation, calls `_expand_normally`, stores its trace, returns on completion, pushes successors, evaluates stuckness, and invokes `_daydream_locally` only for a stuck decision. For Task 3, `_daydream_locally` returns an empty exhausted rescue result; Task 4 replaces that minimal body.

Use this linear method shape:

```python
def search(self, initial_state: SymbolicState, goal: SymbolicState) -> SearchResult:
    frontier = _PriorityFrontier()
    frontier.push(SearchNode(initial_state, 1.0))
    visited = {self._state_fingerprinter.fingerprint(initial_state)}
    expansions: list[ExpansionOutcome] = []
    decisions: list[StucknessDecision] = []
    rounds: list[DaydreamRound] = []

    while frontier:
        if self._cancellation.is_cancelled():
            return SearchResult(
                SearchStatus.CANCELLED, None,
                tuple(expansions), tuple(decisions), tuple(rounds),
            )

        node = frontier.pop_best()
        ordinary = self._expand_normally(node, goal, visited)
        expansions.append(ordinary)
        if ordinary.completed_proof is not None:
            return SearchResult(
                SearchStatus.COMPLETE, ordinary.completed_proof,
                tuple(expansions), tuple(decisions), tuple(rounds),
            )

        frontier.extend(ordinary.successors)
        decision = self._stuckness_policy.evaluate(ordinary)
        decisions.append(decision)
        if not decision.is_stuck:
            continue

        rescue = self._daydream_locally(node, goal, ordinary, visited)
        rounds.extend(rescue.rounds)
        if rescue.cancelled:
            return SearchResult(
                SearchStatus.CANCELLED, None,
                tuple(expansions), tuple(decisions), tuple(rounds),
            )
        if rescue.completed_proof is not None:
            return SearchResult(
                SearchStatus.COMPLETE, rescue.completed_proof,
                tuple(expansions), tuple(decisions), tuple(rounds),
            )
        frontier.extend(rescue.successors)

    return SearchResult(
        SearchStatus.EXHAUSTED, None,
        tuple(expansions), tuple(decisions), tuple(rounds),
    )
```

For Task 3 only, define the rescue hook with its final signature and an inert body:

```python
def _daydream_locally(
    self,
    node: SearchNode,
    goal: SymbolicState,
    ordinary: ExpansionOutcome,
    visited: set[str],
) -> _RescueOutcome:
    return _RescueOutcome()
```

Task 4 replaces only this body, so the outer loop does not change when analogical rescue becomes active.

`_expand_normally` must:

1. call the ordinary ranker once;
2. verify candidates in ranked order;
3. return immediately on `COMPLETE`;
4. classify accepted states as repeated, rejected by progress, or successor;
5. mark accepted successors visited before checking the next candidate, preventing duplicates within one expansion;
6. create successor nodes with `node.score * candidate.score`.

Its exact signature is:

```python
def _expand_normally(
    self,
    node: SearchNode,
    goal: SymbolicState,
    visited: set[str],
) -> ExpansionOutcome:
```

Implement it without iterator tricks so every attempted tactic remains visible:

```python
def _expand_normally(
    self,
    node: SearchNode,
    goal: SymbolicState,
    visited: set[str],
) -> ExpansionOutcome:
    attempts: list[TacticResult] = []
    successors: list[SearchNode] = []
    discarded: list[DiscardedSuccessor] = []

    for candidate in self._tactic_ranker.rank(node.state, goal):
        result = self._verifier.run_tactic(node.state, candidate)
        attempts.append(result)
        completed, successor, rejected = self._classify_result(
            node, goal, result, visited
        )
        if rejected is not None:
            discarded.append(rejected)
        if completed is not None:
            return ExpansionOutcome(
                node, tuple(attempts), tuple(successors),
                tuple(discarded), completed,
            )
        if successor is not None:
            successors.append(successor)

    return ExpansionOutcome(
        node, tuple(attempts), tuple(successors), tuple(discarded)
    )
```

Use a shared helper with this return shape:

```python
def _classify_result(
    self,
    node: SearchNode,
    goal: SymbolicState,
    result: TacticResult,
    visited: set[str],
) -> tuple[str | None, SearchNode | None, DiscardedSuccessor | None]:
```

The tuple contains `(completed_proof, successor, discarded)`, with at most one non-`None` value. Errors return three `None` values. Using this helper in both ordinary and analogical expansion keeps fingerprinting and progress-policy behavior identical.

Implement the classification order explicitly:

```python
def _classify_result(self, node, goal, result, visited):
    if result.status is TacticStatus.ERROR:
        return None, None, None
    if result.status is TacticStatus.COMPLETE:
        return result.proof, None, None

    assert result.state is not None
    fingerprint = self._state_fingerprinter.fingerprint(result.state)
    if fingerprint in visited:
        return None, None, DiscardedSuccessor(
            result, DiscardReason.REPEATED_STATE
        )

    visited.add(fingerprint)
    if not self._progress_policy.accepts(node.state, result.state, goal):
        return None, None, DiscardedSuccessor(
            result, DiscardReason.NO_PROGRESS
        )

    successor = SearchNode(
        result.state,
        node.score * result.candidate.score,
        node.state.id,
        result.candidate.source,
    )
    return None, successor, None
```

Adding the fingerprint before the progress check ensures a rejected state is still recognized as previously explored if another tactic reaches it.

- [ ] **Step 5: Add and pass the duplicate-successor and progress-policy review tests**

Add one fake progress policy returning false and one test where two tactics return states sharing the same fingerprint. Assert the first is a successor, the second is `DiscardReason.REPEATED_STATE`, and a rejected globally novel state is `DiscardReason.NO_PROGRESS`.

Run:

```bash
cd daydream
python -m pytest tests/test_outer_loop.py -v
```

Expected: all Task 1–3 tests pass.

### Task 4: Open-Ended Local Daydream Rescue

**Files:**
- Modify: `daydream/src/daydream/outer_loop.py`
- Modify: `daydream/tests/test_outer_loop.py`

**Interfaces:**
- Consumes: failed `ExpansionOutcome`, retriever, analogy ranker, verifier, canonicalizer, fingerprinter, progress policy, and cancellation token.
- Produces: `_daydream_locally` behavior, `DaydreamRound` traces, fixed-point termination, and successful return to the ordinary frontier.

- [ ] **Step 1: Add deterministic sequence fakes**

Add a `SequenceRetriever` returning one configured relation tuple per call and a `SequenceAnalogyRanker` returning one configured tactic tuple per call. Both record call arguments. Add a `ToggleCancellation` whose `is_cancelled` result follows a configured Boolean sequence and then retains its final value.

```python
class SequenceRetriever:
    def __init__(self, rounds: list[tuple[RelationCandidate, ...]]) -> None:
        self.rounds = rounds
        self.calls = 0

    def retrieve(self, current, goal, ordinary, prior_rounds):
        index = min(self.calls, len(self.rounds) - 1)
        self.calls += 1
        return self.rounds[index]


class SequenceAnalogyRanker:
    def __init__(self, rounds: list[tuple[TacticCandidate, ...]]) -> None:
        self.rounds = rounds
        self.calls = 0

    def rank(self, current, goal, relations, prior_attempts):
        index = min(self.calls, len(self.rounds) - 1)
        self.calls += 1
        return self.rounds[index]
```

- [ ] **Step 2: Write the failing multi-round rescue test**

Construct an ordinary tactic that errors, then two analogical tactics. The first errors and the second returns a novel state. The retriever may return the same unnamed `RelationCandidate` both rounds. Assert:

```python
assert result.status is SearchStatus.COMPLETE
assert [round_.index for round_ in result.daydream_rounds[:2]] == [0, 1]
assert verifier.calls[:3] == [
    ("start", "ordinary"),
    ("start", "analogy one"),
    ("start", "analogy two"),
]
```

Configure the novel rescue state with an ordinary finishing tactic so the full search proves that rescue returned to the normal frontier.

- [ ] **Step 3: Run the multi-round test and confirm it fails at the minimal Task 3 rescue body**

Run:

```bash
cd daydream
python -m pytest tests/test_outer_loop.py -k multi_round -v
```

Expected: FAIL because no Daydream round is executed.

- [ ] **Step 4: Implement tactic-attempt identity and iterative rescue**

Use this identity function:

```python
def _attempt_key(self, state: SymbolicState, candidate: TacticCandidate) -> tuple[str, str]:
    return (
        self._state_fingerprinter.fingerprint(state),
        self._tactic_canonicalizer.canonicalize(candidate.source),
    )
```

`_daydream_locally` maintains `attempted_keys`, `prior_attempts`, and `rounds_for_state`. Seed `attempted_keys` and `prior_attempts` from the ordinary outcome so Daydream cannot retry ordinary tactics with different whitespace.

Use this exact signature and loop shape:

```python
def _daydream_locally(
    self,
    node: SearchNode,
    goal: SymbolicState,
    ordinary: ExpansionOutcome,
    visited: set[str],
) -> _RescueOutcome:
    attempted_keys = {
        self._attempt_key(node.state, result.candidate)
        for result in ordinary.attempts
    }
    prior_attempts = list(ordinary.attempts)
    rounds: list[DaydreamRound] = []

    while True:
        if self._cancellation.is_cancelled():
            return _RescueOutcome(rounds=tuple(rounds), cancelled=True)

        relations = tuple(self._relation_retriever.retrieve(
            node.state, goal, ordinary, tuple(rounds)
        ))
        proposed = tuple(self._analogy_ranker.rank(
            node.state, goal, relations, tuple(prior_attempts)
        ))
        untried = tuple(
            tactic for tactic in proposed
            if self._attempt_key(node.state, tactic) not in attempted_keys
        )

        if not untried:
            rounds.append(DaydreamRound(
                node.state.id, len(rounds), relations, proposed, ()
            ))
            return _RescueOutcome(rounds=tuple(rounds))

        round_attempts: list[TacticResult] = []
        round_discarded: list[DiscardedSuccessor] = []
        for tactic in untried:
            attempted_keys.add(self._attempt_key(node.state, tactic))
            result = self._verifier.run_tactic(node.state, tactic)
            round_attempts.append(result)
            prior_attempts.append(result)

            completed, successor, discarded = self._classify_result(
                node, goal, result, visited
            )
            if discarded is not None:
                round_discarded.append(discarded)
            if completed is not None or successor is not None:
                rounds.append(DaydreamRound(
                    node.state.id, len(rounds), relations,
                    proposed, tuple(round_attempts), tuple(round_discarded),
                ))
                return _RescueOutcome(
                    completed_proof=completed,
                    successors=() if successor is None else (successor,),
                    rounds=tuple(rounds),
                )

        rounds.append(DaydreamRound(
            node.state.id, len(rounds), relations,
            proposed, tuple(round_attempts), tuple(round_discarded),
        ))
```

Each round:

1. checks cancellation;
2. retrieves latent relation candidates;
3. asks the analogy ranker for candidates;
4. filters candidates by `_attempt_key` while preserving order;
5. records a final empty-attempt `DaydreamRound` and returns fixed-point exhaustion if no untried candidate remains;
6. verifies all untried candidates until completion or the first frontier-eligible novel state;
7. records errors, repeated states, and no-progress states as attempts;
8. appends the round trace;
9. returns immediately on completion or a novel successor;
10. otherwise begins another round with all prior attempts available to retrieval and ranking.

The retriever may return endlessly changing relation IDs; relation novelty alone never bypasses step 5.

- [ ] **Step 5: Add fixed-point and canonicalization tests**

Add scenarios proving:

```python
assert verifier.calls.count(("start", "exact   h")) == 0
assert result.status is SearchStatus.EXHAUSTED
assert result.daydream_rounds[-1].attempts == ()
```

The first assertion belongs to a case where ordinary search already tried canonical `"exact h"` and Daydream proposes `" exact   h "`. The second and third belong to a retriever returning new relation IDs while the ranker returns only already-attempted tactics.

- [ ] **Step 6: Add repeated-state, rescue-completion, and cancellation tests**

Cover these exact outcomes:

- a novel tactic leading to a visited state is attempted once and Daydream continues;
- an analogical `COMPLETE` result immediately returns its proof;
- cancellation between rounds returns `SearchStatus.CANCELLED` and retains the completed first round;
- a verifier exception propagates unchanged.

- [ ] **Step 7: Run the entire outer-loop test file**

Run:

```bash
cd daydream
python -m pytest tests/test_outer_loop.py -v
```

Expected: all outer-loop scenarios pass.

### Task 5: Public API, Documentation, and Full Verification

**Files:**
- Modify: `daydream/src/daydream/__init__.py`
- Modify: `README.md`
- Modify: `daydream/tests/test_outer_loop.py`

**Interfaces:**
- Consumes: the complete outer-loop API.
- Produces: stable package-root imports and documentation pointing researchers to the readable loop.

- [ ] **Step 1: Write the failing package-export test**

Append:

```python
def test_package_root_exports_local_rescue_api() -> None:
    from daydream import (
        LocalRescueSearch as PublicLocalRescueSearch,
        ObservableStucknessPolicy as PublicObservableStucknessPolicy,
        SearchResult as PublicSearchResult,
        StucknessDecision as PublicStucknessDecision,
    )
    from daydream.outer_loop import LocalRescueSearch

    assert PublicLocalRescueSearch is LocalRescueSearch
    assert PublicObservableStucknessPolicy is ObservableStucknessPolicy
    assert PublicSearchResult is SearchResult
    assert PublicStucknessDecision is StucknessDecision
```

- [ ] **Step 2: Run the export test and confirm the import failure**

Run:

```bash
cd daydream
python -m pytest tests/test_outer_loop.py::test_package_root_exports_local_rescue_api -v
```

Expected: FAIL with an import error for `LocalRescueSearch` from `daydream`.

- [ ] **Step 3: Export the supported API**

Import and list in `__all__` the public enums, records, protocols, defaults, and `LocalRescueSearch`. Update the package docstring from “analogical program synthesis” to “stuck-only analogical tactic discovery.” Do not export the internal priority-frontier helper.

- [ ] **Step 4: Update the README implementation status**

Under “Stuck-only tactic discovery,” add a short paragraph linking `daydream/src/daydream/outer_loop.py` and stating that it provides dependency-injected scaffolding rather than concrete embedding, model, or Lean adapters. Preserve the compact equation formatting and the explicit exclusion of multi-step program synthesis.

- [ ] **Step 5: Run focused and full test suites**

Run:

```bash
cd daydream
python -m pytest tests/test_outer_loop.py -v
python -m pytest -v
```

Expected: every outer-loop test and every existing model test passes.

- [ ] **Step 6: Run static readability checks**

Run:

```bash
cd /Users/laurenmoos/analogy
python -m compileall -q daydream/src/daydream
rg -n "class LocalRescueSearch|def search|def _expand_normally|def _daydream_locally" daydream/src/daydream/outer_loop.py
```

Expected: compilation exits zero and the four orchestration entry points are easy to locate in one file.

- [ ] **Step 7: Perform a final requirements audit**

Read the implementation beside the spec and confirm:

- ordinary success never invokes Daydream;
- local stuckness is based on completed expansion outcomes;
- open-ended rescue stops on actionable fixed point rather than round count;
- tactic novelty controls continuation and state novelty controls success;
- every tactic is verified by the injected Lean interface;
- traces preserve ordinary outcomes, decisions, and Daydream rounds;
- global rescue and program synthesis remain absent.

Record any mismatch as a failing test before changing implementation.
