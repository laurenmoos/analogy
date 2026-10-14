"""Iterative relation search for constrained tactic selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol, Sequence

from .records import (
    CancellationToken,
    DiscardedSuccessor,
    SearchNode,
    TacticCandidate,
    TacticCanonicalizer,
    TacticResult,
    TacticStatus,
    Embedding,
    SymbolicState,
    _probability,
    _require_text,
    _text_tuple,
    _typed_tuple,
)


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

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.text, "text")
        _require_text(self.source_domain, "source_domain")
        _require_text(self.source_reference, "source_reference")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 0:
            raise ValueError("generation must be a non-negative integer")
        parents = _text_tuple(self.parent_ids, "parent_ids")
        object.__setattr__(self, "parent_ids", parents)
        if self.generation == 0 and parents:
            raise ValueError("initial relations must not include parent_ids")
        if self.generation > 0 and not parents:
            raise ValueError("refined relations require at least one parent_id")
        if self.embedding is not None and not isinstance(self.embedding, Embedding):
            raise ValueError("embedding must be an Embedding or None")


@dataclass(frozen=True, slots=True)
class PertinenceAssessment:
    proposal: RelationProposal
    score: float
    is_pertinent: bool
    rationale: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, RelationProposal):
            raise ValueError("proposal must be a RelationProposal")
        object.__setattr__(self, "score", _probability(self.score, "score"))
        if not isinstance(self.is_pertinent, bool):
            raise ValueError("is_pertinent must be a bool")
        if self.rationale is not None:
            _require_text(self.rationale, "rationale")


@dataclass(frozen=True, slots=True)
class TacticEvaluation:
    result: TacticResult
    completed_proof: str | None = None
    successor: SearchNode | None = None
    discarded: DiscardedSuccessor | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.result, TacticResult):
            raise ValueError("result must be a TacticResult")
        if self.successor is not None and not isinstance(self.successor, SearchNode):
            raise ValueError("successor must be a SearchNode or None")
        if self.discarded is not None and not isinstance(self.discarded, DiscardedSuccessor):
            raise ValueError("discarded must be a DiscardedSuccessor or None")
        classifications = sum(
            value is not None
            for value in (self.completed_proof, self.successor, self.discarded)
        )
        if self.result.status is TacticStatus.ERROR:
            if classifications:
                raise ValueError("error evaluations must not include an outcome")
            return
        if classifications != 1:
            raise ValueError("accepted and complete evaluations require one outcome")
        if self.result.status is TacticStatus.COMPLETE:
            _require_text(self.completed_proof, "completed_proof")
            if self.successor is not None or self.discarded is not None:
                raise ValueError("complete evaluations require only completed_proof")
        elif self.completed_proof is not None:
            raise ValueError("accepted evaluations must not include completed_proof")
        elif self.successor is not None and self.successor.state != self.result.state:
            raise ValueError("successor state must match the accepted result")
        elif self.discarded is not None and self.discarded.result != self.result:
            raise ValueError("discarded result must match the accepted result")


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

    def __post_init__(self) -> None:
        _require_text(self.state_id, "state_id")
        if isinstance(self.index, bool) or not isinstance(self.index, int) or self.index < 0:
            raise ValueError("index must be a non-negative integer")
        if not isinstance(self.origin, PopulationOrigin):
            raise ValueError("origin must be a PopulationOrigin")
        object.__setattr__(self, "population", _typed_tuple(self.population, RelationProposal, "population"))
        object.__setattr__(self, "assessments", _typed_tuple(self.assessments, PertinenceAssessment, "assessments"))
        object.__setattr__(self, "survivors", _typed_tuple(self.survivors, RelationProposal, "survivors"))
        object.__setattr__(self, "available_candidates", _typed_tuple(self.available_candidates, TacticCandidate, "available_candidates"))
        if self.selected is not None and not isinstance(self.selected, TacticCandidate):
            raise ValueError("selected must be a TacticCandidate or None")
        if self.result is not None:
            if not isinstance(self.result, TacticResult):
                raise ValueError("result must be a TacticResult or None")
            if self.selected is None or self.result.candidate != self.selected:
                raise ValueError("result must reference the selected candidate")
        elif self.selected is not None:
            raise ValueError("a selected candidate requires a result")
        if self.discarded is not None:
            if self.result is None or self.discarded.result != self.result:
                raise ValueError("discarded must reference the round result")


@dataclass(frozen=True, slots=True)
class CuriosityOutcome:
    completed_proof: str | None = None
    successor: SearchNode | None = None
    rounds: tuple[DaydreamRound, ...] = ()
    cancelled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.cancelled, bool):
            raise ValueError("cancelled must be a bool")
        if self.successor is not None and not isinstance(self.successor, SearchNode):
            raise ValueError("successor must be a SearchNode or None")
        object.__setattr__(self, "rounds", _typed_tuple(self.rounds, DaydreamRound, "rounds"))
        outcomes = sum(value is not None for value in (self.completed_proof, self.successor))
        if outcomes > 1 or (self.cancelled and outcomes):
            raise ValueError("curiosity outcome must have at most one terminal result")
        if self.completed_proof is not None:
            _require_text(self.completed_proof, "completed_proof")


class RelationSampler(Protocol):
    def sample(self, state: SymbolicState, goal: SymbolicState, tactic_pool: tuple[TacticCandidate, ...]) -> Sequence[RelationProposal]: ...


class RelationRefiner(Protocol):
    def refine(self, state: SymbolicState, goal: SymbolicState, tactic_pool: tuple[TacticCandidate, ...], population: tuple[RelationProposal, ...], prior_rounds: tuple[DaydreamRound, ...]) -> Sequence[RelationProposal]: ...


class PertinenceGate(Protocol):
    def assess(self, state: SymbolicState, goal: SymbolicState, tactic_pool: tuple[TacticCandidate, ...], population: tuple[RelationProposal, ...]) -> Sequence[PertinenceAssessment]: ...


class ConstrainedTacticChooser(Protocol):
    def choose(self, state: SymbolicState, goal: SymbolicState, candidates: tuple[TacticCandidate, ...], analogies: tuple[RelationProposal, ...], prior_rounds: tuple[DaydreamRound, ...]) -> TacticCandidate | None: ...


class RelationCanonicalizer(Protocol):
    def canonicalize(self, text: str) -> str: ...


class CuriosityRunner(Protocol):
    def search(self, state: SymbolicState, goal: SymbolicState, tactic_pool: tuple[TacticCandidate, ...], ordinary_attempts: tuple[TacticResult, ...], evaluator: Callable[[TacticCandidate], TacticEvaluation], cancellation: CancellationToken) -> CuriosityOutcome: ...


class WhitespaceRelationCanonicalizer:
    def canonicalize(self, text: str) -> str:
        return " ".join(text.split()).casefold()


class CuriositySearch:
    """Search relation proposals while keeping tactic choice inside a fixed pool."""

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
        attempted = {
            self._tactic_canonicalizer.canonicalize(result.candidate.source)
            for result in ordinary_attempts
        }
        if not self._available_candidates(tactic_pool, attempted):
            return CuriosityOutcome()

        seen_relations: set[str] = set()
        seen_relation_ids: set[str] = set()
        population = self._novel_population(
            self._sampler.sample(state, goal, tactic_pool),
            seen_relations,
            seen_relation_ids,
            expected_generation=0,
        )
        origin = PopulationOrigin.SAMPLED
        rounds: list[DaydreamRound] = []

        while population:
            if cancellation.is_cancelled():
                return CuriosityOutcome(rounds=tuple(rounds), cancelled=True)

            available = self._available_candidates(tactic_pool, attempted)
            if not available:
                return CuriosityOutcome(rounds=tuple(rounds))

            assessments = self._validate_assessments(
                population,
                self._gate.assess(state, goal, tactic_pool, population),
            )
            survivors = tuple(
                item.proposal for item in assessments if item.is_pertinent
            )
            selected: TacticCandidate | None = None
            evaluation: TacticEvaluation | None = None
            if survivors:
                selected = self._resolve_choice(
                    self._chooser.choose(
                        state,
                        goal,
                        available,
                        survivors,
                        tuple(rounds),
                    ),
                    available,
                )
            if selected is not None:
                evaluation = evaluator(selected)
                if evaluation.result.candidate != selected:
                    raise ValueError("evaluator result must reference the selected candidate")

            round_ = DaydreamRound(
                state.id,
                len(rounds),
                origin,
                population,
                assessments,
                survivors,
                available,
                selected,
                None if evaluation is None else evaluation.result,
                None if evaluation is None else evaluation.discarded,
            )
            rounds.append(round_)

            if evaluation is not None:
                attempted.add(self._tactic_canonicalizer.canonicalize(selected.source))
                if evaluation.completed_proof is not None:
                    return CuriosityOutcome(
                        completed_proof=evaluation.completed_proof,
                        rounds=tuple(rounds),
                    )
                if evaluation.successor is not None:
                    return CuriosityOutcome(
                        successor=evaluation.successor,
                        rounds=tuple(rounds),
                    )

            if cancellation.is_cancelled():
                return CuriosityOutcome(rounds=tuple(rounds), cancelled=True)
            if not self._available_candidates(tactic_pool, attempted):
                return CuriosityOutcome(rounds=tuple(rounds))

            population = self._novel_population(
                self._refiner.refine(
                    state,
                    goal,
                    tactic_pool,
                    population,
                    tuple(rounds),
                ),
                seen_relations,
                seen_relation_ids,
                expected_generation=max(item.generation for item in population) + 1,
            )
            origin = PopulationOrigin.REFINED

        return CuriosityOutcome(rounds=tuple(rounds))

    def _novel_population(
        self,
        proposals: Sequence[RelationProposal],
        seen: set[str],
        seen_ids: set[str],
        *,
        expected_generation: int,
    ) -> tuple[RelationProposal, ...]:
        novel: list[RelationProposal] = []
        for proposal in proposals:
            if not isinstance(proposal, RelationProposal):
                raise ValueError("relation population must contain RelationProposal values")
            if proposal.generation != expected_generation:
                raise ValueError("relation generation does not match the search round")
            if proposal.id in seen_ids:
                raise ValueError("relation proposal IDs must be unique across generations")
            if expected_generation > 0 and not set(proposal.parent_ids) <= seen_ids:
                raise ValueError("refined relation parent IDs must reference prior proposals")
            seen_ids.add(proposal.id)
            key = self._relation_canonicalizer.canonicalize(proposal.text)
            if key in seen:
                continue
            seen.add(key)
            novel.append(proposal)
        return tuple(novel)

    def _validate_assessments(
        self,
        population: tuple[RelationProposal, ...],
        assessments: Sequence[PertinenceAssessment],
    ) -> tuple[PertinenceAssessment, ...]:
        normalized = tuple(assessments)
        expected = {item.id: item for item in population}
        if len(expected) != len(population):
            raise ValueError("relation population IDs must be unique")
        if len(normalized) != len(population):
            raise ValueError("gate must return one assessment per proposal")
        observed: set[str] = set()
        for item in normalized:
            if not isinstance(item, PertinenceAssessment):
                raise ValueError("gate assessments must be PertinenceAssessment values")
            identifier = item.proposal.id
            if identifier in observed or expected.get(identifier) != item.proposal:
                raise ValueError("gate assessment mapping does not match population")
            observed.add(identifier)
        if observed != set(expected):
            raise ValueError("gate assessment mapping does not match population")
        by_id = {item.proposal.id: item for item in normalized}
        return tuple(by_id[item.id] for item in population)

    def _available_candidates(
        self,
        pool: tuple[TacticCandidate, ...],
        attempted_keys: set[str],
    ) -> tuple[TacticCandidate, ...]:
        return tuple(
            candidate
            for candidate in pool
            if self._tactic_canonicalizer.canonicalize(candidate.source)
            not in attempted_keys
        )

    def _resolve_choice(
        self,
        choice: TacticCandidate | None,
        available: tuple[TacticCandidate, ...],
    ) -> TacticCandidate | None:
        if choice is None:
            return None
        if not isinstance(choice, TacticCandidate):
            raise ValueError("chooser must return a TacticCandidate or None")
        key = self._tactic_canonicalizer.canonicalize(choice.source)
        matches = [
            candidate
            for candidate in available
            if self._tactic_canonicalizer.canonicalize(candidate.source) == key
        ]
        if len(matches) != 1:
            raise ValueError("chooser selection must belong to the supplied tactic pool")
        return matches[0]
