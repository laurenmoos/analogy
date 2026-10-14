from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from analogy.records import Embedding, SymbolicState
from analogy.records import (
    CandidateOrigin,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)


def state(identifier: str) -> SymbolicState:
    return SymbolicState(identifier, f"goal {identifier}", "lean4")


def tactic(source: str = "simp", score: float = 0.7) -> TacticCandidate:
    return TacticCandidate(source, score, CandidateOrigin.ORDINARY)


def proposal(identifier: str = "r1", text: str = "uncover what is hidden"):
    from analogy.daydream import RelationProposal

    return RelationProposal(identifier, text, "poetry", f"poem:{identifier}", 0)


def test_relation_proposal_carries_readable_content_and_optional_embedding() -> None:
    from analogy.daydream import RelationProposal

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
    from analogy.daydream import RelationProposal

    with pytest.raises(ValueError, match="parent"):
        RelationProposal("r2", "invert it", "physics", "trip:2", 1)
    with pytest.raises(ValueError, match="parent"):
        RelationProposal("r1", "open", "poetry", "poem:1", 0, ("old",))


def test_pertinence_score_is_a_probability() -> None:
    from analogy.daydream import PertinenceAssessment

    with pytest.raises(ValueError, match="score"):
        PertinenceAssessment(proposal(), 1.1, True)


def test_round_requires_selected_result_to_refer_to_same_candidate() -> None:
    from analogy.daydream import DaydreamRound, PertinenceAssessment, PopulationOrigin

    selected = tactic("simp")
    wrong = tactic("aesop")
    item = proposal()
    with pytest.raises(ValueError, match="selected"):
        DaydreamRound(
            "s",
            0,
            PopulationOrigin.SAMPLED,
            (item,),
            (PertinenceAssessment(item, 0.8, True),),
            (item,),
            (selected,),
            selected,
            TacticResult(wrong, TacticStatus.ERROR),
        )


def test_default_relation_canonicalizer_ignores_case_and_whitespace() -> None:
    from analogy.daydream import WhitespaceRelationCanonicalizer

    normalizer = WhitespaceRelationCanonicalizer()
    assert normalizer.canonicalize("  Break   OPEN ") == "break open"


class SequenceSampler:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def sample(self, current, goal, tactic_pool):
        self.calls.append((current, goal, tactic_pool))
        return self.items


class SequenceRefiner:
    def __init__(self, rounds):
        self.rounds = rounds
        self.calls = []

    def refine(self, current, goal, tactic_pool, population, prior_rounds):
        self.calls.append((current, goal, tactic_pool, population, prior_rounds))
        index = min(len(self.calls) - 1, len(self.rounds) - 1)
        return self.rounds[index]


class SequenceGate:
    def __init__(self, rounds):
        self.rounds = rounds
        self.calls = []

    def assess(self, current, goal, tactic_pool, population):
        self.calls.append((current, goal, tactic_pool, population))
        index = min(len(self.calls) - 1, len(self.rounds) - 1)
        return self.rounds[index]


class SequenceChooser:
    def __init__(self, choices):
        self.choices = choices
        self.calls = []

    def choose(self, current, goal, candidates, analogies, prior_rounds):
        self.calls.append((current, goal, candidates, analogies, prior_rounds))
        index = min(len(self.calls) - 1, len(self.choices) - 1)
        return self.choices[index]


class NeverCancelled:
    def is_cancelled(self):
        return False


class ToggleCancellation:
    def __init__(self, values):
        self.values = values
        self.calls = 0

    def is_cancelled(self):
        index = min(self.calls, len(self.values) - 1)
        self.calls += 1
        return self.values[index]


def refined(identifier: str, text: str, parent: str):
    from analogy.daydream import RelationProposal

    return RelationProposal(identifier, text, "physics", f"trip:{identifier}", 1, (parent,))


def assessment(item, pertinent: bool):
    from analogy.daydream import PertinenceAssessment

    return PertinenceAssessment(item, 0.8 if pertinent else 0.2, pertinent)


def engine(sampler, refiner, gate, chooser):
    from analogy.daydream import CuriositySearch, WhitespaceRelationCanonicalizer
    from analogy.loop import WhitespaceTacticCanonicalizer

    return CuriositySearch(
        sampler=sampler,
        refiner=refiner,
        gate=gate,
        chooser=chooser,
        relation_canonicalizer=WhitespaceRelationCanonicalizer(),
        tactic_canonicalizer=WhitespaceTacticCanonicalizer(),
    )


def error_evaluator(calls):
    from analogy.daydream import TacticEvaluation

    def evaluate(selected):
        calls.append(selected)
        return TacticEvaluation(TacticResult(selected, TacticStatus.ERROR))

    return evaluate


def test_rejected_population_is_refined_before_chooser_runs() -> None:
    from analogy.daydream import PopulationOrigin

    top = tactic("simp", 0.9)
    chosen = tactic("aesop", 0.7)
    first = proposal("r0", "a veil conceals")
    second = refined("r1", "remove an obstruction", "r0")
    sampler = SequenceSampler((first,))
    refiner = SequenceRefiner([(second,)])
    gate = SequenceGate([(assessment(first, False),), (assessment(second, True),)])
    chooser = SequenceChooser([chosen])
    evaluated = []

    outcome = engine(sampler, refiner, gate, chooser).search(
        state("s"), state("g"), (top, chosen),
        (TacticResult(top, TacticStatus.ERROR),), error_evaluator(evaluated), NeverCancelled()
    )

    assert evaluated == [chosen]
    assert [round_.origin for round_ in outcome.rounds] == [PopulationOrigin.SAMPLED, PopulationOrigin.REFINED]
    assert outcome.rounds[0].selected is None
    assert outcome.rounds[0].survivors == ()
    assert outcome.rounds[1].population == (second,)
    assert outcome.rounds[1].survivors == (second,)
    assert outcome.rounds[1].selected is chosen
    assert chooser.calls[0][3] == (second,)
    assert refiner.calls[0][4] == (outcome.rounds[0],)


def test_chooser_selection_is_resolved_to_original_pool_candidate() -> None:
    top = tactic("simp", 0.9)
    original = tactic("aesop", 0.72)
    replacement = tactic("  aesop ", 0.01)
    item = proposal()
    evaluated = []
    outcome = engine(
        SequenceSampler((item,)), SequenceRefiner([()]),
        SequenceGate([(assessment(item, True),)]), SequenceChooser([replacement]),
    ).search(
        state("s"), state("g"), (top, original),
        (TacticResult(top, TacticStatus.ERROR),), error_evaluator(evaluated), NeverCancelled()
    )

    assert evaluated == [original]
    assert evaluated[0] is original
    assert outcome.rounds[0].selected is original


def test_out_of_pool_choice_raises() -> None:
    top = tactic("simp", 0.9)
    available = tactic("aesop", 0.7)
    item = proposal()
    evaluated = []
    search = engine(
        SequenceSampler((item,)), SequenceRefiner([()]),
        SequenceGate([(assessment(item, True),)]), SequenceChooser([tactic("omega")]),
    )

    with pytest.raises(ValueError, match="pool"):
        search.search(
            state("s"), state("g"), (top, available),
            (TacticResult(top, TacticStatus.ERROR),), error_evaluator(evaluated), NeverCancelled()
        )
    assert evaluated == []


def test_gate_must_assess_each_population_member_exactly_once() -> None:
    top = tactic("simp", 0.9)
    available = tactic("aesop", 0.7)
    first = proposal("r1", "open")
    second = proposal("r2", "divide")
    chooser = SequenceChooser([available])
    evaluated = []

    with pytest.raises(ValueError, match="assessment"):
        engine(
            SequenceSampler((first, second)), SequenceRefiner([()]),
            SequenceGate([(assessment(first, True), assessment(first, False))]), chooser,
        ).search(
            state("s"), state("g"), (top, available),
            (TacticResult(top, TacticStatus.ERROR),), error_evaluator(evaluated), NeverCancelled()
        )
    assert chooser.calls == []
    assert evaluated == []


def test_empty_sample_abstains_without_gate_or_refinement() -> None:
    top = tactic("simp", 0.9)
    available = tactic("aesop", 0.7)
    refiner = SequenceRefiner([()])
    gate = SequenceGate([()])
    chooser = SequenceChooser([available])
    evaluated = []
    outcome = engine(SequenceSampler(()), refiner, gate, chooser).search(
        state("s"), state("g"), (top, available),
        (TacticResult(top, TacticStatus.ERROR),), error_evaluator(evaluated), NeverCancelled()
    )

    assert outcome.rounds == ()
    assert gate.calls == refiner.calls == chooser.calls == evaluated == []


def test_chooser_abstention_can_be_refined() -> None:
    top = tactic("simp", 0.9)
    available = tactic("aesop", 0.7)
    first = proposal("r0", "open")
    second = refined("r1", "expose the interior", "r0")
    outcome = engine(
        SequenceSampler((first,)), SequenceRefiner([(second,)]),
        SequenceGate([(assessment(first, True),), (assessment(second, True),)]),
        SequenceChooser([None, available]),
    ).search(
        state("s"), state("g"), (top, available),
        (TacticResult(top, TacticStatus.ERROR),), error_evaluator([]), NeverCancelled()
    )

    assert outcome.rounds[0].selected is None
    assert outcome.rounds[0].result is None
    assert outcome.rounds[1].selected is available
    assert outcome.rounds[1].result.status is TacticStatus.ERROR
    assert [round_.survivors for round_ in outcome.rounds] == [(first,), (second,)]


def test_duplicate_only_refinement_reaches_relation_fixed_point() -> None:
    top = tactic("simp", 0.9)
    available = tactic("aesop", 0.7)
    first = proposal("r0", "Break open")
    duplicate = refined("r1", " break  OPEN ", "r0")
    refiner = SequenceRefiner([(duplicate,)])
    gate = SequenceGate([(assessment(first, True),)])
    chooser = SequenceChooser([None])

    outcome = engine(SequenceSampler((first,)), refiner, gate, chooser).search(
        state("s"), state("g"), (top, available),
        (TacticResult(top, TacticStatus.ERROR),), error_evaluator([]), NeverCancelled()
    )

    assert len(outcome.rounds) == 1
    assert outcome.rounds[0].population == (first,)
    assert len(gate.calls) == len(refiner.calls) == 1


def test_failed_choices_exhaust_only_the_fixed_pool() -> None:
    top = tactic("simp", 0.9)
    second = tactic("aesop", 0.7)
    third = tactic("omega", 0.6)
    first_relation = proposal("r0", "open")
    next_relation = refined("r1", "calculate the pressure", "r0")
    refiner = SequenceRefiner([(next_relation,)])
    evaluated = []

    outcome = engine(
        SequenceSampler((first_relation,)), refiner,
        SequenceGate([(assessment(first_relation, True),), (assessment(next_relation, True),)]),
        SequenceChooser([second, third]),
    ).search(
        state("s"), state("g"), (top, second, third),
        (TacticResult(top, TacticStatus.ERROR),), error_evaluator(evaluated), NeverCancelled()
    )

    assert evaluated == [second, third]
    assert len(outcome.rounds) == 2
    assert len(refiner.calls) == 1
    assert outcome.rounds[1].available_candidates == (third,)


def test_cancellation_between_relation_rounds_preserves_trace() -> None:
    top = tactic("simp", 0.9)
    second = tactic("aesop", 0.7)
    third = tactic("omega", 0.6)
    item = proposal()
    refiner = SequenceRefiner([(refined("r2", "invert", "r1"),)])
    outcome = engine(
        SequenceSampler((item,)), refiner,
        SequenceGate([(assessment(item, True),)]), SequenceChooser([second]),
    ).search(
        state("s"), state("g"), (top, second, third),
        (TacticResult(top, TacticStatus.ERROR),), error_evaluator([]), ToggleCancellation([False, True])
    )

    assert outcome.cancelled
    assert len(outcome.rounds) == 1
    assert outcome.rounds[0].selected is second
    assert refiner.calls == []


def test_package_root_exports_curiosity_selection_api() -> None:
    import analogy

    expected = {
        "ConstrainedTacticChooser", "CuriosityOutcome", "CuriosityRunner",
        "CuriositySearch", "DaydreamRound", "PertinenceAssessment",
        "PertinenceGate", "PopulationOrigin", "RelationCanonicalizer",
        "RelationProposal", "RelationRefiner", "RelationSampler",
        "TacticEvaluation", "WhitespaceRelationCanonicalizer",
    }
    assert expected <= set(analogy.__all__)
    for name in expected:
        assert getattr(analogy, name) is not None


def test_tactic_evaluation_and_outcome_reject_wrong_runtime_types() -> None:
    from analogy.daydream import CuriosityOutcome, TacticEvaluation

    accepted = TacticResult(tactic(), TacticStatus.ACCEPTED, state("next"))
    with pytest.raises(ValueError, match="successor"):
        TacticEvaluation(accepted, successor="not a node")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="successor"):
        CuriosityOutcome(successor="not a node")  # type: ignore[arg-type]


def test_sampler_and_refiner_must_preserve_generation_contract() -> None:
    top = tactic("simp", 0.9)
    available = tactic("aesop", 0.7)
    malformed_sample = refined("bad-sample", "open", "parent")
    with pytest.raises(ValueError, match="generation"):
        engine(
            SequenceSampler((malformed_sample,)), SequenceRefiner([()]),
            SequenceGate([()]), SequenceChooser([None]),
        ).search(
            state("s"), state("g"), (top, available),
            (TacticResult(top, TacticStatus.ERROR),), error_evaluator([]), NeverCancelled()
        )

    first = proposal("r0", "open")
    malformed_refinement = proposal("r1", "divide")
    with pytest.raises(ValueError, match="generation"):
        engine(
            SequenceSampler((first,)), SequenceRefiner([(malformed_refinement,)]),
            SequenceGate([(assessment(first, True),)]), SequenceChooser([None]),
        ).search(
            state("s"), state("g"), (top, available),
            (TacticResult(top, TacticStatus.ERROR),), error_evaluator([]), NeverCancelled()
        )


def test_relation_ids_cannot_be_reused_across_generations() -> None:
    top = tactic("simp", 0.9)
    available = tactic("aesop", 0.7)
    first = proposal("same-id", "open")
    reused = refined("same-id", "divide", "same-id")
    with pytest.raises(ValueError, match="IDs"):
        engine(
            SequenceSampler((first,)), SequenceRefiner([(reused,)]),
            SequenceGate([(assessment(first, True),)]), SequenceChooser([None]),
        ).search(
            state("s"), state("g"), (top, available),
            (TacticResult(top, TacticStatus.ERROR),), error_evaluator([]), NeverCancelled()
        )
