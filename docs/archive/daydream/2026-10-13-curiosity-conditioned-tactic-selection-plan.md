# Curiosity-Conditioned Tactic Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace tactic-generating analogical rescue with iterative relation search that pertinence-gates poetry and physics proposals before choosing from a fixed conventional-policy top-\(k\) tactic pool.

**Architecture:** Shared proof-search records move to `search_types.py`; `curiosity.py` owns relation proposals, gating traces, and the iterative relation-search engine; `outer_loop.py` retains best-first proof search and supplies a Lean-backed tactic evaluator to curiosity search. The conventional policy is evaluated once per proof state, only its top candidate is tried ordinarily, and Daydream can select only untried members of that same canonicalized pool.

**Tech Stack:** Python 3.11+, standard-library dataclasses, enums, protocols, callables, `heapq`, and pytest 8.

**Spec:** `docs/superpowers/specs/2026-10-13-curiosity-conditioned-tactic-selection-design.md`

## Global Constraints

- Keep `daydream` independent of `lean_gym`; the existing `lean_gym/.venv` may be used only as the test interpreter.
- Add no runtime dependency outside the Python standard library.
- Use frozen, slotted public dataclasses and tuple-valued public collections.
- Call the conventional tactic policy exactly once per expanded proof state.
- Canonicalize the returned sequence into one fixed, ordered tactic pool.
- Attempt only the first pool member on the ordinary path.
- Permit curiosity search to choose only canonical, untried members of that pool.
- Preserve the conventional policy's original candidate object and probability when a chooser returns an equivalent tactic spelling.
- Evaluate the pertinence gate before invoking the constrained chooser; do not call the chooser for an empty survivor beam.
- Stop relation search on verified progress, completion, candidate exhaustion, relation-content fixed point, or cancellation.
- Do not add relation crossover, relation composition, tactic generation, tactic-sequence evolution, or program synthesis.
- Preserve propagated integration exceptions and expected-data treatment of Lean tactic errors.
- Preserve the existing public imports through package-root re-exports where the concept still exists.
- The workspace is not a Git repository, so task checkpoints are recorded in `.superpowers/sdd/curiosity-conditioned-selection/progress.md` instead of commits.

## Review Focus

- A chooser may return a newly constructed candidate with canonical text equal to a pool member but a different score; the engine must execute the original pool member so policy probability cannot be overwritten. Covered in Task 3.
- A gate may return the correct number of assessments while duplicating one proposal and omitting another; the engine must reject the mapping. Covered in Task 3.
- A refiner may emit new IDs for whitespace/case variants forever; canonical relation-content novelty must stop after the first duplicate-only generation. Covered in Task 3.
- A policy may return duplicate tactic spellings around the top candidate; ordinary execution must use the first canonical member once and preserve the remaining unique order. Covered in Task 4.
- A selected tactic may produce a globally new state rejected by the progress policy; the round must trace `NO_PROGRESS`, mark the tactic attempted, and continue refinement. Covered in Task 4.

---

## File Structure

- Create `daydream/src/daydream/search_types.py` — shared enums, validation helpers, tactic/result/search records, and the shared tactic-canonicalizer and cancellation protocols.
- Create `daydream/src/daydream/curiosity.py` — relation records, protocols, canonicalization, iterative search, and immutable Daydream traces.
- Create `daydream/tests/test_curiosity.py` — unit and scenario tests for relation search independent of the frontier.
- Modify `daydream/src/daydream/outer_loop.py` — import shared records, make ordinary expansion top-one, and adapt Lean verification/classification to curiosity search.
- Modify `daydream/tests/test_outer_loop.py` — preserve normal-search tests and replace tactic-generating rescue scenarios with constrained-pool scenarios.
- Modify `daydream/src/daydream/__init__.py` — export the new public prototype API and remove obsolete retrieval/ranking exports.
- Modify `README.md` — describe competence, curiosity, judgment, and the current non-compositional boundary.
- Create `.superpowers/sdd/curiosity-conditioned-selection/progress.md` — Native execution ledger.

### Task 1: Extract Shared Search Types Without Changing Behavior

**Files:**
- Create: `daydream/src/daydream/search_types.py`
- Modify: `daydream/src/daydream/outer_loop.py:1-286`
- Modify: `daydream/tests/test_outer_loop.py:1-130`
- Create: `.superpowers/sdd/curiosity-conditioned-selection/progress.md`

**Interfaces:**
- Consumes: `daydream.models.SymbolicState`.
- Produces: `CandidateOrigin`, `TacticStatus`, `DiscardReason`, `StuckReason`, `SearchStatus`, `TacticCandidate`, `TacticResult`, `SearchNode`, `DiscardedSuccessor`, `ExpansionOutcome`, `StucknessDecision`, `TacticCanonicalizer`, and `CancellationToken` from `daydream.search_types`.
- `ExpansionOutcome` gains the final defaulted field `candidate_pool: tuple[TacticCandidate, ...] = ()`, preserving existing positional construction.

- [ ] **Step 1: Create the ledger and record the green baseline**

Run:

```bash
cd /Users/laurenmoos/analogy/daydream
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest -v
```

Expected: `89 passed`.

Create the ledger with:

```markdown
# SDD ledger — plan: docs/superpowers/plans/2026-10-13-curiosity-conditioned-tactic-selection.md

Setup: Native execution in place because /Users/laurenmoos/analogy is not a Git repository.
Ruling: use lean_gym/.venv only as the pytest interpreter; production daydream remains independent of lean_gym — cost if wrong: test tooling comes from a sibling project's environment.
Baseline: 89 tests passed.
```

- [ ] **Step 2: Write the failing extraction test**

Add to `test_outer_loop.py`:

```python
def test_outer_loop_reexports_shared_search_types() -> None:
    from daydream import search_types
    from daydream import outer_loop

    assert outer_loop.TacticCandidate is search_types.TacticCandidate
    assert outer_loop.ExpansionOutcome is search_types.ExpansionOutcome
    assert outer_loop.StucknessDecision is search_types.StucknessDecision


def test_expansion_records_the_fixed_candidate_pool() -> None:
    first = candidate("first", 0.9)
    second = candidate("second", 0.8)

    expansion = ExpansionOutcome(
        SearchNode(state("a"), 1.0),
        candidate_pool=(first, second),
    )

    assert expansion.candidate_pool == (first, second)
```

- [ ] **Step 3: Run the extraction tests and verify RED**

Run:

```bash
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest tests/test_outer_loop.py -k 'reexports_shared or fixed_candidate_pool' -v
```

Expected: FAIL because `daydream.search_types` does not exist and `ExpansionOutcome` has no `candidate_pool` field.

- [ ] **Step 4: Move shared definitions and add `candidate_pool`**

Move the validation helpers and listed public definitions from `outer_loop.py` into `search_types.py`. Define the updated record with this field order:

```python
@dataclass(frozen=True, slots=True)
class ExpansionOutcome:
    node: SearchNode
    attempts: tuple[TacticResult, ...] = ()
    successors: tuple[SearchNode, ...] = ()
    discarded: tuple[DiscardedSuccessor, ...] = ()
    completed_proof: str | None = None
    candidate_pool: tuple[TacticCandidate, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.node, SearchNode):
            raise ValueError("node must be a SearchNode")
        object.__setattr__(self, "attempts", _typed_tuple(self.attempts, TacticResult, "attempts"))
        object.__setattr__(self, "successors", _typed_tuple(self.successors, SearchNode, "successors"))
        object.__setattr__(self, "discarded", _typed_tuple(self.discarded, DiscardedSuccessor, "discarded"))
        if self.completed_proof is not None:
            _require_text(self.completed_proof, "completed_proof")
        object.__setattr__(
            self,
            "candidate_pool",
            _typed_tuple(self.candidate_pool, TacticCandidate, "candidate_pool"),
        )
```

Keep imports from `daydream.outer_loop` working by importing the moved names there. Do not move `RelationCandidate`, the old `DaydreamRound`, or `SearchResult` in this task.

- [ ] **Step 5: Run focused and full tests, then record the checkpoint**

Run:

```bash
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest tests/test_outer_loop.py -v
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest -v
```

Expected: all tests pass. Append exact counts to the ledger.

### Task 2: Add Relation, Gate, and Trace Records

**Files:**
- Create: `daydream/src/daydream/curiosity.py`
- Create: `daydream/tests/test_curiosity.py`

**Interfaces:**
- Consumes: `Embedding`, `SymbolicState`, and Task 1 search types.
- Produces: `PopulationOrigin`, `RelationProposal`, `PertinenceAssessment`, `TacticEvaluation`, `DaydreamRound`, `CuriosityOutcome`, `RelationSampler`, `RelationRefiner`, `PertinenceGate`, `ConstrainedTacticChooser`, `RelationCanonicalizer`, `WhitespaceRelationCanonicalizer`, and `CuriosityRunner`.

- [ ] **Step 1: Write failing record-invariant tests**

Create `test_curiosity.py` with helpers and these tests:

```python
from dataclasses import FrozenInstanceError

import pytest

from daydream.curiosity import (
    CuriosityOutcome,
    DaydreamRound,
    PertinenceAssessment,
    PopulationOrigin,
    RelationProposal,
    TacticEvaluation,
    WhitespaceRelationCanonicalizer,
)
from daydream.models import Embedding, SymbolicState
from daydream.search_types import (
    CandidateOrigin,
    DiscardReason,
    DiscardedSuccessor,
    SearchNode,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)


def state(identifier: str) -> SymbolicState:
    return SymbolicState(identifier, f"goal {identifier}", "lean4")


def tactic(source: str = "simp", score: float = 0.7) -> TacticCandidate:
    return TacticCandidate(source, score, CandidateOrigin.ORDINARY)


def proposal(identifier: str = "r1", text: str = "uncover what is hidden") -> RelationProposal:
    return RelationProposal(identifier, text, "poetry", f"poem:{identifier}", 0)


def test_relation_proposal_carries_readable_content_and_optional_embedding() -> None:
    item = RelationProposal(
        "r1",
        "uncover what is hidden",
        "poetry",
        "poem:12",
        0,
        embedding=Embedding("relations", (0.1, 0.2)),
    )
    assert item.text == "uncover what is hidden"
    assert item.parent_ids == ()
    with pytest.raises(FrozenInstanceError):
        item.text = "changed"  # type: ignore[misc]


def test_refined_relation_requires_parent_and_initial_relation_forbids_one() -> None:
    with pytest.raises(ValueError, match="parent"):
        RelationProposal("r2", "invert it", "physics", "trip:2", 1)
    with pytest.raises(ValueError, match="parent"):
        RelationProposal("r1", "open", "poetry", "poem:1", 0, ("old",))


def test_pertinence_score_is_a_probability() -> None:
    with pytest.raises(ValueError, match="score"):
        PertinenceAssessment(proposal(), 1.1, True)


def test_round_requires_selected_result_to_refer_to_same_candidate() -> None:
    selected = tactic("simp")
    wrong = tactic("aesop")
    with pytest.raises(ValueError, match="selected"):
        DaydreamRound(
            "s",
            0,
            PopulationOrigin.SAMPLED,
            (proposal(),),
            (PertinenceAssessment(proposal(), 0.8, True),),
            (proposal(),),
            (selected,),
            selected,
            TacticResult(wrong, TacticStatus.ERROR),
        )


def test_default_relation_canonicalizer_ignores_case_and_whitespace() -> None:
    normalizer = WhitespaceRelationCanonicalizer()
    assert normalizer.canonicalize("  Break   OPEN ") == "break open"
```

- [ ] **Step 2: Run the record tests and verify RED**

Run:

```bash
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest tests/test_curiosity.py -v
```

Expected: collection fails because `daydream.curiosity` does not exist.

- [ ] **Step 3: Implement the exact public record shapes**

Create `curiosity.py` with:

```python
class PopulationOrigin(str, Enum):
    SAMPLED = "sampled"
    REFINED = "refined"


@dataclass(frozen=True, slots=True)
class RelationProposal:
    id: str
    text: str
    source_domain: str
    source_reference: str
    generation: int
    parent_ids: tuple[str, ...] = ()
    embedding: Embedding | None = None


@dataclass(frozen=True, slots=True)
class PertinenceAssessment:
    proposal: RelationProposal
    score: float
    is_pertinent: bool
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class TacticEvaluation:
    result: TacticResult
    completed_proof: str | None = None
    successor: SearchNode | None = None
    discarded: DiscardedSuccessor | None = None


@dataclass(frozen=True, slots=True)
class DaydreamRound:
    state_id: str
    index: int
    origin: PopulationOrigin
    population: tuple[RelationProposal, ...]
    assessments: tuple[PertinenceAssessment, ...]
    survivors: tuple[RelationProposal, ...]
    available_candidates: tuple[TacticCandidate, ...]
    selected: TacticCandidate | None = None
    result: TacticResult | None = None
    discarded: DiscardedSuccessor | None = None


@dataclass(frozen=True, slots=True)
class CuriosityOutcome:
    completed_proof: str | None = None
    successor: SearchNode | None = None
    rounds: tuple[DaydreamRound, ...] = ()
    cancelled: bool = False
```

Validate nonblank text, nonnegative integer generation/index, generation/parent consistency, optional embedding type, score range, booleans, typed tuples, and mutual consistency among selected/result/discarded fields. `TacticEvaluation` permits exactly one terminal classification among `completed_proof`, `successor`, and `discarded`; an error result has none. `CuriosityOutcome` permits at most one of completion, successor, and cancellation.

Add the four dependency protocols with the signatures in the spec, plus:

```python
class RelationCanonicalizer(Protocol):
    def canonicalize(self, text: str) -> str:
        raise NotImplementedError


class CuriosityRunner(Protocol):
    def search(
        self,
        state: SymbolicState,
        goal: SymbolicState,
        tactic_pool: tuple[TacticCandidate, ...],
        ordinary_attempts: tuple[TacticResult, ...],
        evaluator: Callable[[TacticCandidate], TacticEvaluation],
        cancellation: CancellationToken,
    ) -> CuriosityOutcome:
        raise NotImplementedError


class WhitespaceRelationCanonicalizer:
    def canonicalize(self, text: str) -> str:
        return " ".join(text.split()).casefold()
```

- [ ] **Step 4: Run focused and full tests, then record the checkpoint**

Run:

```bash
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest tests/test_curiosity.py -v
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest -v
```

Expected: all tests pass. Append exact counts to the ledger.

### Task 3: Implement Iterative, Gated, Constrained Relation Search

**Files:**
- Modify: `daydream/src/daydream/curiosity.py`
- Modify: `daydream/tests/test_curiosity.py`

**Interfaces:**
- Consumes: all Task 2 records/protocols and `TacticCanonicalizer`.
- Produces: `CuriositySearch.search(...) -> CuriosityOutcome` implementing the approved loop.

- [ ] **Step 1: Add deterministic fakes and failing scenario tests**

Add `SequenceSampler`, `SequenceRefiner`, `SequenceGate`, and `SequenceChooser` fakes. Each stores a public `calls` list and returns the next configured tuple/value, clamping at the last configured item. Add a `ToggleCancellation` fake matching the existing outer-loop test helper.

Implement these exact scenarios and assertions:

| Test | Inputs | Required assertions |
|---|---|---|
| `test_rejected_population_is_refined_before_chooser_runs` | Sample poem proposal `r0`; gate rejects it; refiner returns physics proposal `r1`; gate accepts `r1`; chooser returns pool candidate `aesop`; evaluator returns an error. | Chooser calls contain only `(r1,)`; rounds have origins `SAMPLED, REFINED`; first selected value is `None`; second selected value is the pool's `aesop`; refiner saw the completed first round. |
| `test_chooser_selection_is_resolved_to_original_pool_candidate` | Pool has `aesop` score `0.72`; chooser returns canonical-equivalent `aesop` score `0.01`. | Evaluator receives the exact `0.72` pool object; round selection is that same object. |
| `test_out_of_pool_choice_raises` | Available pool is `simp, aesop`; chooser returns `omega`. | `search` raises `ValueError` matching `pool`; evaluator is never called. |
| `test_gate_must_assess_each_population_member_exactly_once` | Population is `r1, r2`; gate returns two assessments for `r1`. | `search` raises `ValueError` matching `assessment`; chooser and evaluator are never called. |
| `test_empty_sample_abstains_without_gate_or_refinement` | Sampler returns an empty tuple while one untried tactic remains. | Outcome is non-cancelled with no rounds; gate, chooser, refiner, and evaluator have no calls. |
| `test_chooser_abstention_can_be_refined` | First pertinent generation makes the chooser return `None`; refiner returns a second novel generation whose choice errors. | First round has `selected is None` and `result is None`; second round contains the selected tactic and error result; both survivor beams are preserved. |
| `test_duplicate_only_refinement_reaches_relation_fixed_point` | Sample text is `Break open`; refinement text is ` break  OPEN ` with a new ID. | Outcome has one sampled round; gate has one call; refiner has one call; chooser result is traced if configured; duplicate refinement never reaches the gate. |
| `test_failed_choices_exhaust_only_the_fixed_pool` | Ordinary attempts contain top-one `simp`; pool also has `aesop, omega`; two pertinent generations choose those remaining tactics and both evaluators return errors. | Evaluator order is `aesop, omega`; outcome has two rounds; refiner is not called after `omega`; no tactic outside the fixed pool is requested. |
| `test_cancellation_between_relation_rounds_preserves_trace` | First pertinent generation chooses `aesop`, whose evaluator errors; cancellation sequence is `False, True`. | Outcome is cancelled with exactly the completed first round and the refiner is not called after cancellation. |

Each test must assert the full relevant `DaydreamRound` fields—population, assessments, survivors, available candidates, selection, result, and discarded value—not only call counts.

- [ ] **Step 2: Run the scenario tests and verify RED**

Run:

```bash
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest tests/test_curiosity.py -k 'refined_before or original_pool or out_of_pool or assess_each or empty_sample or chooser_abstention or fixed_point or exhaust_only or cancellation_between' -v
```

Expected: FAIL because `CuriositySearch` is absent.

- [ ] **Step 3: Implement `CuriositySearch` with small named helpers**

Use this constructor and public method:

```python
class CuriositySearch:
    def __init__(
        self,
        *,
        sampler: RelationSampler,
        refiner: RelationRefiner,
        gate: PertinenceGate,
        chooser: ConstrainedTacticChooser,
        relation_canonicalizer: RelationCanonicalizer,
        tactic_canonicalizer: TacticCanonicalizer,
    ) -> None:
        self._sampler = sampler
        self._refiner = refiner
        self._gate = gate
        self._chooser = chooser
        self._relation_canonicalizer = relation_canonicalizer
        self._tactic_canonicalizer = tactic_canonicalizer

    def search(
        self,
        state: SymbolicState,
        goal: SymbolicState,
        tactic_pool: tuple[TacticCandidate, ...],
        ordinary_attempts: tuple[TacticResult, ...],
        evaluator: Callable[[TacticCandidate], TacticEvaluation],
        cancellation: CancellationToken,
    ) -> CuriosityOutcome:
        return self._search_relations(
            state,
            goal,
            tactic_pool,
            ordinary_attempts,
            evaluator,
            cancellation,
        )
```

Implement helpers with these responsibilities:

```python
def _novel_population(
    self,
    proposals: Sequence[RelationProposal],
    seen: set[str],
) -> tuple[RelationProposal, ...]:
    # Preserve order; keep the first occurrence of each globally unseen
    # canonical text; add retained keys to seen.

def _validate_assessments(
    self,
    population: tuple[RelationProposal, ...],
    assessments: Sequence[PertinenceAssessment],
) -> tuple[PertinenceAssessment, ...]:
    # Require the same proposal IDs exactly once and require each assessment's
    # proposal value to equal the population value for that ID.

def _available_candidates(
    self,
    pool: tuple[TacticCandidate, ...],
    attempted_keys: set[str],
) -> tuple[TacticCandidate, ...]:
    # Preserve pool order and exclude canonical attempted tactics.

def _resolve_choice(
    self,
    choice: TacticCandidate | None,
    available: tuple[TacticCandidate, ...],
) -> TacticCandidate | None:
    # Match canonical tactic text and return the original available object;
    # reject no match or ambiguous canonical matches.
```

The main loop must:

1. Seed attempted tactic keys from `ordinary_attempts`.
2. Return immediately when no untried pool member exists.
3. Sample and canonical-deduplicate generation zero.
4. Check cancellation before each gated round.
5. Validate gate coverage, derive survivors in population order, and skip the chooser when survivors are empty.
6. Resolve a non-null chooser output to the original available pool member.
7. Evaluate at most one tactic per relation generation and append one complete round.
8. Return on completion or successor; otherwise mark the selected tactic attempted.
9. Return on tactic exhaustion before calling the refiner.
10. Refine using the current population and complete prior rounds; stop if no globally content-novel proposal remains.

- [ ] **Step 4: Run curiosity and full tests, then record the checkpoint**

Run:

```bash
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest tests/test_curiosity.py -v
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest -v
```

Expected: all tests pass. Append exact counts to the ledger.

### Task 4: Integrate Top-One Ordinary Search with Curiosity Search

**Files:**
- Modify: `daydream/src/daydream/outer_loop.py`
- Modify: `daydream/tests/test_outer_loop.py`

**Interfaces:**
- Consumes: `CuriosityRunner`, `CuriosityOutcome`, `TacticEvaluation`, and Task 1 search types.
- Produces: `LocalRescueSearch` with constructor dependency `curiosity_search: CuriosityRunner` in place of `relation_retriever` and `analogy_ranker`.
- Preserves: `search(initial_state, goal) -> SearchResult`, ordinary best-first frontier, Lean verification, stuckness policy, state identity, progress policy, tactic canonicalization, and cancellation.

- [ ] **Step 1: Replace old rescue expectations with failing constrained-pool tests**

Keep record, stuckness, completion, verifier-consistency, and public-export tests. Remove tests and fakes whose contract permits analogical tactics outside the policy pool. Remove `test_duplicate_successor_is_traced_as_repeated`, because one ordinary expansion can no longer verify two policy candidates. Replace the integration-style priority test with a direct `_PriorityFrontier` test that pushes `SearchNode(state("first"), 0.5)`, `SearchNode(state("high"), 0.9)`, and `SearchNode(state("second"), 0.5)`, then asserts pop order `high, first, second`. Add:

```python
def test_ordinary_path_attempts_only_top_candidate_and_preserves_pool() -> None:
    first = candidate("first", 0.9)
    second = candidate("second", 0.8)
    verifier = FakeVerifier({("start", "first"): TacticResult(first, TacticStatus.ERROR)})
    curiosity = RecordingCuriosity(CuriosityOutcome())

    result = make_search(
        FakeTacticRanker({"start": (first, second)}),
        verifier,
        curiosity_search=curiosity,
    ).search(state("start"), state("goal"))

    assert verifier.calls == [("start", "first")]
    assert result.ordinary_expansions[0].candidate_pool == (first, second)
    assert curiosity.calls[0].tactic_pool == (first, second)


def test_policy_pool_is_canonicalized_once_in_original_order() -> None:
    first = candidate("  simp ", 0.9)
    duplicate = candidate("simp", 0.2)
    second = candidate("aesop", 0.8)
    verifier = FakeVerifier({("start", "  simp "): TacticResult(first, TacticStatus.ERROR)})
    curiosity = RecordingCuriosity(CuriosityOutcome())
    result = make_search(
        FakeTacticRanker({"start": (first, duplicate, second)}),
        verifier,
        curiosity_search=curiosity,
    ).search(state("start"), state("goal"))
    assert result.status is SearchStatus.EXHAUSTED
    assert verifier.calls == [("start", "  simp ")]
    assert curiosity.calls[0].tactic_pool == (first, second)
    assert curiosity.calls[0].tactic_pool[0].score == 0.9


def test_empty_policy_pool_does_not_start_curiosity() -> None:
    curiosity = RecordingCuriosity(CuriosityOutcome())
    result = make_search(
        FakeTacticRanker({"start": ()}),
        FakeVerifier({}),
        curiosity_search=curiosity,
    ).search(state("start"), state("goal"))
    assert result.status is SearchStatus.EXHAUSTED
    assert curiosity.calls == []


def test_curiosity_completion_is_returned() -> None:
    first = candidate("first", 0.9)
    second = candidate("exact h", 0.8)
    verifier = FakeVerifier({
        ("start", "first"): TacticResult(first, TacticStatus.ERROR),
        ("start", "exact h"): TacticResult(second, TacticStatus.COMPLETE, proof="by exact h"),
    })
    curiosity = EvaluatingCuriosity("exact h")
    result = make_search(
        FakeTacticRanker({"start": (first, second)}),
        verifier,
        curiosity_search=curiosity,
    ).search(state("start"), state("goal"))
    assert result.status is SearchStatus.COMPLETE
    assert result.proof == "by exact h"
    assert verifier.calls == [("start", "first"), ("start", "exact h")]


def test_curiosity_progress_returns_to_ordinary_frontier() -> None:
    first = candidate("first", 0.9)
    escape = candidate("escape", 0.8)
    finish = candidate("finish", 1.0)
    verifier = FakeVerifier({
        ("start", "first"): TacticResult(first, TacticStatus.ERROR),
        ("start", "escape"): TacticResult(escape, TacticStatus.ACCEPTED, state("rescued")),
        ("rescued", "finish"): TacticResult(finish, TacticStatus.COMPLETE, proof="rescued proof"),
    })
    ranker = FakeTacticRanker({"start": (first, escape), "rescued": (finish,)})
    result = make_search(
        ranker,
        verifier,
        curiosity_search=EvaluatingCuriosity("escape"),
    ).search(state("start"), state("goal"))
    assert result.proof == "rescued proof"
    assert ranker.calls == ["start", "rescued"]


def test_no_progress_curiosity_evaluation_is_traced_and_search_continues() -> None:
    first = candidate("first", 0.9)
    wander = candidate("wander", 0.8)
    verifier = FakeVerifier({
        ("start", "first"): TacticResult(first, TacticStatus.ERROR),
        ("start", "wander"): TacticResult(wander, TacticStatus.ACCEPTED, state("elsewhere")),
    })
    curiosity = EvaluatingCuriosity("wander")
    result = make_search(
        FakeTacticRanker({"start": (first, wander)}),
        verifier,
        curiosity_search=curiosity,
        progress_policy=RejectAllProgress(),
    ).search(state("start"), state("goal"))
    assert result.status is SearchStatus.EXHAUSTED
    assert curiosity.evaluations[0].discarded.reason is DiscardReason.NO_PROGRESS
```

Implement `RecordingCuriosity` and `EvaluatingCuriosity` fakes with the exact `CuriosityRunner.search` signature, storing state, goal, pool, ordinary attempts, and evaluator results.

- [ ] **Step 2: Run integration tests and verify RED**

Run:

```bash
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest tests/test_outer_loop.py -k 'top_candidate or canonicalized_once or empty_policy or curiosity_completion or curiosity_progress or no_progress_curiosity' -v
```

Expected: FAIL because `LocalRescueSearch` still verifies every ordinary candidate and accepts the obsolete rescue dependencies.

- [ ] **Step 3: Change ordinary expansion to materialize a fixed pool and try top-one**

Add:

```python
def _candidate_pool(
    self,
    state: SymbolicState,
    goal: SymbolicState,
) -> tuple[TacticCandidate, ...]:
    seen: set[str] = set()
    pool: list[TacticCandidate] = []
    for candidate in self._tactic_ranker.rank(state, goal):
        key = self._tactic_canonicalizer.canonicalize(candidate.source)
        if key in seen:
            continue
        seen.add(key)
        pool.append(candidate)
    return tuple(pool)
```

Rewrite `_expand_normally` so it constructs the pool once, returns `ExpansionOutcome(node, candidate_pool=())` for no candidates, verifies only `pool[0]`, classifies it, and stores the entire pool in every return path.

- [ ] **Step 4: Adapt Lean classification to the curiosity evaluator**

Replace `_daydream_locally` with:

```python
def _evaluate_for_curiosity(
    self,
    node: SearchNode,
    goal: SymbolicState,
    visited: set[str],
    candidate: TacticCandidate,
) -> TacticEvaluation:
    result = self._verify_candidate(node.state, candidate)
    completed, successor, discarded = self._classify_result(node, goal, result, visited)
    return TacticEvaluation(result, completed, successor, discarded)
```

When a stuck decision is reached, skip curiosity if `ordinary.candidate_pool` is empty. Otherwise call:

```python
rescue = self._curiosity_search.search(
    node.state,
    goal,
    ordinary.candidate_pool,
    ordinary.attempts,
    lambda candidate: self._evaluate_for_curiosity(node, goal, visited, candidate),
    self._cancellation,
)
```

Accumulate rounds in `SearchResult`, return completion/cancellation as before, and enqueue `rescue.successor` when present. Replace the old `DaydreamRound` reference in `SearchResult` with `daydream.curiosity.DaydreamRound`. Remove the obsolete outer-loop `RelationCandidate` and `DaydreamRound` records, `RelationRetriever`, `AnalogyTacticRanker`, `_RescueOutcome`, `_daydream_locally`, `_untried_tactics`, and their constructor fields.

- [ ] **Step 5: Run outer-loop and full tests, then record the checkpoint**

Run:

```bash
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest tests/test_outer_loop.py -v
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest -v
```

Expected: all tests pass. Append exact counts to the ledger.

### Task 5: Export and Document the Prototype Boundary

**Files:**
- Modify: `daydream/src/daydream/__init__.py`
- Modify: `daydream/tests/test_curiosity.py`
- Modify: `daydream/tests/test_outer_loop.py`
- Modify: `README.md:49-89`

**Interfaces:**
- Consumes: completed public APIs from Tasks 1-4.
- Produces: package-root imports for the constrained-curiosity prototype and concise repository documentation.

- [ ] **Step 1: Write the failing public API test**

Add:

```python
def test_package_root_exports_curiosity_selection_api() -> None:
    import daydream

    expected = {
        "ConstrainedTacticChooser",
        "CuriosityOutcome",
        "CuriosityRunner",
        "CuriositySearch",
        "DaydreamRound",
        "PertinenceAssessment",
        "PertinenceGate",
        "PopulationOrigin",
        "RelationCanonicalizer",
        "RelationProposal",
        "RelationRefiner",
        "RelationSampler",
        "TacticEvaluation",
        "WhitespaceRelationCanonicalizer",
    }
    assert expected <= set(daydream.__all__)
    for name in expected:
        assert getattr(daydream, name) is not None
```

Update the existing outer-loop export test to expect the surviving API and to stop requiring `AnalogyTacticRanker`, `RelationCandidate`, and `RelationRetriever`.

- [ ] **Step 2: Run the export tests and verify RED**

Run:

```bash
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest tests/test_curiosity.py::test_package_root_exports_curiosity_selection_api tests/test_outer_loop.py::test_package_root_exports_local_rescue_api -v
```

Expected: FAIL because the new curiosity names are not exported and obsolete names remain expected.

- [ ] **Step 3: Update package exports**

Import the Task 2-3 curiosity API and Task 1 search types in `__init__.py`, preserve domain-model exports, and remove obsolete `AnalogyTacticRanker`, `RelationCandidate`, and `RelationRetriever`. Keep `CandidateOrigin.ANALOGICAL` for serialized compatibility, but document that this prototype executes only candidates originating in the conventional pool.

- [ ] **Step 4: Rewrite the README control-flow section**

Replace the current stuck-only flow with:

```text
conventional policy proposes top-k tactics
    → try the top candidate normally
    → when that attempt is locally stuck, sample relations from poetry/physics
    → refine and pertinence-gate the relation population
    → let an LLM choose only among untried members of the original top-k
    → let Lean verify the selected tactic
    → return verified progress to ordinary proof search
```

State explicitly that the prototype searches and refines relations, not programs; the analogy is LLM conditioning context rather than a required formal transformation; embeddings are optional retrieval metadata; relation composition and genetic program synthesis remain later work.

- [ ] **Step 5: Run final verification and record exact evidence**

Run:

```bash
cd /Users/laurenmoos/analogy/daydream
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m pytest -v
/Users/laurenmoos/analogy/lean_gym/.venv/bin/python -m compileall -q src/daydream
rg -n "lean_gym" src/daydream
```

Expected: all tests pass, compileall exits zero, and `rg` prints no production import. Record exact test counts and each design ruling in the ledger.

- [ ] **Step 6: Perform one whole-feature self-review**

Because Native/no-delegation execution has no independent reviewer, inspect the complete diff-equivalent file set for spec coverage, stale obsolete interfaces, trace consistency, and accidental tactic generation. For each important issue found, add a failing regression test, watch it fail, implement the smallest fix, and rerun the full suite once. Record the weaker self-review boundary and any deferred minor findings in the ledger.

