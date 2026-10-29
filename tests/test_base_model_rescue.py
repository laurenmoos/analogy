import json
from types import SimpleNamespace

import pytest

from analogy.model.model_provider import CompletionScore
from analogy.model import prompts as prompts_module
from analogy.model.prompt_runner import GenerationStatus
from analogy.model.prompts import PromptCatalog
from analogy.utils.records import (
    CandidateOrigin,
    DiscardReason,
    DiscardedSuccessor,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)
from analogy.lean_gym.models import VerificationStatus
from analogy.solver.proof.adapter import LeanInfrastructureResult
from analogy.solver.analogy.rescue import (
    AnalogyFallback,
    PredictionLabel,
    RescueOutputError,
    RescueProposal,
    RescueTermination,
    TacticEvaluation,
    _fallback_seed,
    _parse_tactic_only,
    initialize_analogy_fallback,
    _analogy_correction_feedback,
    order_analogy_revisions,
    parse_analogy_update,
)
from analogy.solver.analogy.protocol import (
    render_analogy_state,
    render_mental_model,
    render_scratchpad_context,
)
from analogy.solver.analogy.state import (
    AnalogyScratchpad,
    AnalogyState,
    MentalModel,
    MentalModelCorrespondence,
)
from analogy.solver.analogy.structural_scoring import (
    ProofBelief,
    StructuralElement,
    StructuralGraph,
    StructuralScoringError,
    UnscorableMentalModelError,
)
from analogy.solver.search import NeverCancelled
from analogy.solver.proof.semantic import (
    SemanticRecoveryEvent,
    SemanticResult,
    render_semantic_guidance,
)


STATE = SymbolicState("state", "P", "lean4")
GOAL = SymbolicState("goal", "P", "lean4")


def _search(
    generator,
    evaluate,
    *,
    calls=3,
    cancellation=None,
    traces=None,
    ordinary_attempts=(),
    semantic_guidance="SAT: no countermodel; continue from this state.",
    dependency_table="(none)",
    scratchpad=None,
    force_domain_change=False,
):
    scratchpad = scratchpad or AnalogyScratchpad()

    class CurrentSchemaGenerator:
        def generate(self, messages, *, seed):
            raw = generator.generate(messages, seed=seed)
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return raw
            if set(payload) != {"analogy", "predicted_state", "tactic"}:
                return raw
            analogy = render_analogy_state(
                AnalogyState(
                    payload["analogy"],
                    ("proof state -> analogous situation",),
                    ("Apply the analogous step.",),
                )
            )
            return analogy + "\n" + json.dumps(
                {
                    "action": "change_domain",
                    "predicted_state": payload["predicted_state"],
                    "tactic": payload["tactic"],
                }
            )

    return AnalogyFallback(
        CurrentSchemaGenerator(),
        max_model_calls=calls,
        seed=7,
        trace_sink=None if traces is None else traces.append,
        analogy_scratchpad=scratchpad,
    ).search(
        STATE,
        GOAL,
        ordinary_attempts,
        evaluate,
        cancellation or NeverCancelled(),
        semantic_guidance,
        dependency_table,
        force_domain_change=force_domain_change,
    )


def _block(domain, mapping, steps, abandoned=()):
    return render_analogy_state(
        AnalogyState(domain, tuple(mapping), tuple(steps), tuple(abandoned))
    )


def _mental_response(domain, transition, tactic, *, action="revise_model"):
    return json.dumps({
        "domain": domain,
        "correspondences": [{
            "domain_before": "start",
            "domain_transition": transition,
            "domain_after": "finish",
        }],
        "abandoned_domains": [],
    })


def _tactic_response(tactic, predicted_state="SOLVED"):
    return json.dumps({"tactic": tactic, "predicted_state": predicted_state})


def _decision_tactic_response(tactic, predicted_state="SOLVED"):
    return json.dumps({
        "decision": {
            "current_everyday_state": "the cook is choosing which order to prepare",
            "options": [
                {
                    "tactic": "left",
                    "everyday_consequence": "prepare the order whose ingredients are available",
                    "proof_consequence": "the left disjunct becomes the goal",
                },
                {
                    "tactic": "right",
                    "everyday_consequence": "prepare the order requiring a missing ingredient",
                    "proof_consequence": "the right disjunct becomes the goal",
                },
            ],
            "selection_reason": "the available route can be completed",
        },
        "tactic": tactic,
        "predicted_state": predicted_state,
    })


def test_analogy_revision_ordering_is_structural_or_seeded_without_mutation():
    candidates = (
        SimpleNamespace(distance=0.2, generation_index=0, content="first"),
        SimpleNamespace(distance=0.1, generation_index=1, content="second"),
        SimpleNamespace(distance=0.1, generation_index=2, content="third"),
    )

    structural = order_analogy_revisions(
        candidates, selection="structural", seed=7
    )
    random_once = order_analogy_revisions(candidates, selection="random", seed=7)
    random_twice = order_analogy_revisions(candidates, selection="random", seed=7)

    assert tuple(item.generation_index for item in structural) == (1, 2, 0)
    assert tuple(item.generation_index for item in random_once) == (2, 0, 1)
    assert random_once == random_twice
    assert {id(item) for item in random_once} == {id(item) for item in candidates}
    assert tuple(item.content for item in candidates) == ("first", "second", "third")


def test_analogy_revision_ordering_rejects_unknown_selection():
    with pytest.raises(ValueError, match="selection must be 'structural' or 'random'"):
        order_analogy_revisions((), selection="closest", seed=0)


def test_disjunction_tactic_requires_a_prospective_analogy_of_both_branches():
    state = "p q : Prop\nhp : p\n⊢ p ∨ q"

    with pytest.raises(RescueOutputError, match="decision"):
        _parse_tactic_only(_tactic_response("left"), state)

    proposal = _parse_tactic_only(_decision_tactic_response("left"), state)

    assert proposal.candidate.source == "left"


def test_recovery_decision_cannot_select_a_tactic_already_tried_at_that_state():
    state = "p q : Prop\nhp : p\n⊢ p ∨ q"

    with pytest.raises(RescueOutputError, match="already attempted"):
        _parse_tactic_only(
            _decision_tactic_response("right"),
            state,
            attempted_tactics={"right"},
        )


def _proof_belief():
    return ProofBelief(
        StructuralGraph(
            (
                StructuralElement("proof-state:root", "proof-state", "P", 0.0),
                StructuralElement("proof-tactic:step", "proof-tactic", "exact h", 1.0),
                StructuralElement("proof-state:state", "proof-state", "P", 2.0),
            ),
            (
                ("proof-state:root", "proof-tactic:step"),
                ("proof-tactic:step", "proof-state:state"),
            ),
        )
    )


def _p4_recovery_belief():
    return ProofBelief(
        StructuralGraph(
            (
                StructuralElement(
                    "proof-state:root",
                    "proof-state",
                    "p1 p4 p3 : Prop\n⊢ p1 ∧ p4 ∧ p3 → p4",
                    0.0,
                ),
                StructuralElement(
                    "proof-tactic:intro",
                    "proof-tactic",
                    "intro h",
                    1.0,
                ),
                StructuralElement(
                    "proof-state:current",
                    "proof-state",
                    "p1 p4 p3 : Prop\nh : p1 ∧ p4 ∧ p3\n⊢ p4",
                    2.0,
                ),
            ),
            (
                ("proof-state:root", "proof-tactic:intro"),
                ("proof-tactic:intro", "proof-state:current"),
            ),
        )
    )


def test_ranked_recovery_rebases_story_to_current_path_then_requests_tactic(
    monkeypatch,
):
    state = SymbolicState(
        "p4-state", "p1 p4 p3 : Prop\nh : p1 ∧ p4 ∧ p3\n⊢ p4", "lean4"
    )
    scratchpad = AnalogyScratchpad(
        MentalModel(
            "travel or navigation",
            (
                MentalModelCorrespondence(
                    "a traveler waits",
                    "the traveler chooses an old branch",
                    "the traveler reaches the wrong stop",
                    "constructor",
                ),
            ),
        )
    )
    calls = []
    traces = []
    lean_calls = []

    class Generator:
        def generate(self, messages, *, seed):
            calls.append(messages)
            if len(calls) == 1:
                return json.dumps({
                    "domain": "travel or navigation",
                    "correspondences": [{
                        "domain_before": "travel or navigation",
                        "domain_transition": "take on an assumption",
                        "domain_after": "travel or navigation",
                    }],
                    "abandoned_domains": [],
                })
            if len(calls) == 2:
                assert "state-change vectors must be finite and nonzero" in messages[-1].content
                return json.dumps({
                    "domain": "travel or navigation",
                    "correspondences": [{
                        "domain_before": "a traveler enters a station carrying a nested ticket wallet",
                        "domain_transition": "the traveler accepts responsibility for presenting the wallet",
                        "domain_after": "the traveler stands at the gate holding the nested wallet",
                    }],
                    "abandoned_domains": [],
                })
            return json.dumps({
                "tactic": "obtain ⟨hp1, hp4p3⟩ := h",
                "predicted_state": "p1 p4 p3 : Prop\nhp1 : p1\nhp4p3 : p4 ∧ p3\n⊢ p4",
            })

        def score_completions(self, _messages, completions):
            return (CompletionScore(completions[0], -0.2, -0.2, 1),)

    monkeypatch.setattr(
        "analogy.solver.analogy.rescue.score_mental_model_revision",
        lambda model, belief, embedder: (
            (_ for _ in ()).throw(
                UnscorableMentalModelError(
                    "state-change vectors must be finite and nonzero"
                )
            )
            if model.correspondences[0].domain_before
            == model.correspondences[0].domain_after
            else SimpleNamespace(distance=0.1, coupling=((1.0,),))
        ),
    )

    def evaluate(candidate):
        lean_calls.append(candidate.source)
        successor_state = SymbolicState(
            "successor",
            "p1 p4 p3 : Prop\nhp1 : p1\nhp4p3 : p4 ∧ p3\n⊢ p4",
            "lean4",
            (candidate.source,),
        )
        result = TacticResult(candidate, TacticStatus.ACCEPTED, state=successor_state)
        return TacticEvaluation(
            result,
            successor=SimpleNamespace(state=successor_state),
        )

    outcome = AnalogyFallback(
        Generator(),
        max_model_calls=2,
        seed=7,
        trace_sink=traces.append,
        analogy_scratchpad=scratchpad,
    ).search(
        state,
        GOAL,
        (),
        evaluate,
        NeverCancelled(),
        "SAT: no countermodel; continue from this state.",
        proof_belief=_p4_recovery_belief(),
    )

    story_prompt = "\n".join(message.content for message in calls[0])
    tactic_prompt = "\n".join(message.content for message in calls[2])
    assert "take on an assumption" in story_prompt
    assert "distinct" in story_prompt
    assert "p1" not in story_prompt and "intro h" not in story_prompt and "⊢" not in story_prompt
    assert tactic_prompt.index("Current focused Lean proof state") < tactic_prompt.index(
        "Selected everyday story"
    )
    assert "proof_transition" not in tactic_prompt
    assert "old branch" not in tactic_prompt
    assert lean_calls == ["obtain ⟨hp1, hp4p3⟩ := h"]
    assert outcome.successor is not None
    assert tuple(
        item.proof_transition for item in scratchpad.current.correspondences
    ) == ("intro h",)
    assert [event["kind"] for event in traces] == [
        "analogy_recovery_story_call",
        "analogy_recovery_story_call",
        "mental_model_structural_score",
        "analogy_recovery_tactic_call",
        "analogy_tactic_score",
    ]


def test_initialize_fallback_loads_model_matched_configured_calibration(
    monkeypatch, tmp_path
):
    calibration = object()
    calls = []

    def load(path, *, expected_model_id):
        calls.append((path, expected_model_id))
        return calibration

    monkeypatch.setattr(
        "analogy.solver.analogy.rescue.load_embedding_calibration", load
    )
    config = SimpleNamespace(
        analogy=SimpleNamespace(
            max_model_calls=3,
            calibration_path=tmp_path / "frozen.npz",
            model_name="example/14b",
        ),
        model=SimpleNamespace(name="example/8b"),
        run=SimpleNamespace(seed=11),
    )

    fallback = initialize_analogy_fallback(
        config, object(), [], AnalogyScratchpad()
    )

    assert calls == [(tmp_path / "frozen.npz", "example/14b")]
    assert fallback._calibration is calibration


def test_queued_initial_tactic_reaches_lean_before_any_revision_generation():
    class Generator:
        def generate(self, _messages, *, seed):
            raise AssertionError("revision generation must not precede queued tactic")

    proposal = RescueProposal(
        TacticCandidate("exact h", 1.0, CandidateOrigin.ANALOGICAL), "SOLVED"
    )
    seen = []
    def evaluate(candidate):
        seen.append(candidate.source)
        return TacticEvaluation(
            TacticResult(candidate, TacticStatus.COMPLETE, proof="done"),
            completed_proof="done",
        )

    outcome = AnalogyFallback(
        Generator(),
        max_model_calls=3,
        seed=7,
        analogy_scratchpad=AnalogyScratchpad(
            MentalModel(
                "cooking",
                (MentalModelCorrespondence("food waits", "cook it", "food is ready"),),
            )
        ),
        initial_proposal=proposal,
        initial_state_id=STATE.id,
    ).search(
        STATE, GOAL, (), evaluate, NeverCancelled(), "guidance"
    )

    assert seen == ["exact h"]
    assert outcome.completed_proof == "done"
    assert outcome.rollouts[0].rollout_id.endswith(":initial")


def test_countermodel_domain_restart_discards_queued_initial_tactic():
    class Generator:
        def generate(self, _messages, *, seed): return "invalid"

    proposal = RescueProposal(
        TacticCandidate("exact h", 1.0, CandidateOrigin.ANALOGICAL), "SOLVED"
    )
    seen = []
    fallback = AnalogyFallback(
        Generator(),
        max_model_calls=1,
        seed=7,
        analogy_scratchpad=AnalogyScratchpad(
            MentalModel(
                "cooking",
                (MentalModelCorrespondence("food waits", "cook", "food ready"),),
            )
        ),
        initial_proposal=proposal,
        initial_state_id=STATE.id,
    )
    fallback.search(
        STATE,
        GOAL,
        (),
        lambda candidate: seen.append(candidate.source),
        NeverCancelled(),
        "countermodel",
        force_domain_change=True,
    )
    assert seen == []


def test_runtime_mental_model_recovery_rejects_legacy_analogy_blocks():
    current = MentalModel(
        "cooking or restaurant preparation",
        (MentalModelCorrespondence("start", "cook", "finish"),),
    )
    raw = _block("travel", ("P -> ticket",), ("Travel.",)) + "\n" + json.dumps({
        "action": "change_domain",
        "predicted_state": "SOLVED",
        "tactic": "exact h",
    })

    with pytest.raises(RescueOutputError, match="validated mental-model"):
        parse_analogy_update(
            raw,
            proof_state="P : Prop\nh : P\n⊢ P",
            current=current,
            required_domain="travel or navigation",
        )


def test_mental_model_candidates_are_all_scored_before_lean_and_tried_by_fit(
    monkeypatch,
):
    scratchpad = AnalogyScratchpad(
        MentalModel(
            "travel or navigation",
            (MentalModelCorrespondence("start", "old", "finish"),),
        )
    )
    events = []
    generation_seeds = []
    generated = iter(
        (
            _mental_response("travel or navigation", "middle", "exact middle"),
            _mental_response("travel or navigation", "best", "exact best"),
            _mental_response("travel or navigation", "worst", "exact worst"),
            _tactic_response("exact best"),
            _tactic_response("exact middle"),
        )
    )

    class Generator:
        def generate(self, _messages, *, seed):
            generation_seeds.append(seed)
            return next(generated)

        def score_completions(self, _messages, completions):
            tactic = next(
                tactic
                for tactic in ("exact best", "exact middle", "exact worst")
                if tactic in completions[0]
            )
            events.append(("tactic_score", tactic))
            mean = {"exact best": -9.0, "exact middle": -0.1, "exact worst": -0.01}[tactic]
            return (CompletionScore(completions[0], mean * 2, mean, 2),)

    distances = {"middle": 0.2, "best": 0.1, "worst": 0.3}

    def score(model, belief, embedder):
        transition = model.correspondences[0].domain_transition
        events.append(("score", transition))
        return SimpleNamespace(
            distance=distances[transition],
            coupling=((1.0,),),
        )

    monkeypatch.setattr(
        "analogy.solver.analogy.rescue.score_mental_model_revision", score
    )

    def evaluate(candidate):
        events.append(("lean", candidate.source))
        if candidate.source == "exact middle":
            result = TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
            return TacticEvaluation(result, completed_proof="done")
        return TacticEvaluation(
            TacticResult(candidate, TacticStatus.ERROR, diagnostics=("rejected",))
        )

    outcome = AnalogyFallback(
        Generator(),
        max_model_calls=3,
        seed=7,
        analogy_scratchpad=scratchpad,
    ).search(
        STATE,
        GOAL,
        (),
        evaluate,
        NeverCancelled(),
        "guidance",
        proof_belief=_proof_belief(),
    )

    assert events == [
        ("score", "middle"),
        ("score", "best"),
        ("score", "worst"),
        ("tactic_score", "exact best"),
        ("lean", "exact best"),
        ("tactic_score", "exact middle"),
        ("lean", "exact middle"),
    ]
    assert generation_seeds[3:] == [
        _fallback_seed(10_007, STATE, 1),
        _fallback_seed(10_007, STATE, 0),
    ]
    assert outcome.completed_proof == "done"
    assert scratchpad.current.correspondences[0].domain_transition == "middle"


def test_post_selection_tactic_score_cannot_reorder_structural_winners(monkeypatch):
    scratchpad = AnalogyScratchpad(
        MentalModel(
            "travel or navigation",
            (MentalModelCorrespondence("start", "old", "finish"),),
        )
    )
    generated = iter(
        (
            _mental_response("travel or navigation", "structural winner", "exact low"),
            _mental_response("travel or navigation", "structural runner-up", "exact high"),
            _tactic_response("exact low"),
        )
    )
    events = []
    scoring_inputs = []

    class Generator:
        def generate(self, _messages, *, seed):
            return next(generated)

        def score_completions(self, _messages, completions):
            scoring_inputs.append((_messages, completions))
            tactic = completions[0]
            events.append(("tactic_score", tactic))
            mean = -100.0 if tactic == "exact low" else 0.0
            return (CompletionScore(completions[0], mean, mean, 1),)

    monkeypatch.setattr(
        "analogy.solver.analogy.rescue.score_mental_model_revision",
        lambda model, belief, embedder: SimpleNamespace(
            distance=(
                0.1
                if model.correspondences[0].domain_transition == "structural winner"
                else 0.2
            ),
            coupling=((1.0,),),
        ),
    )

    def evaluate(candidate):
        events.append(("lean", candidate.source, candidate.model_rank))
        return TacticEvaluation(
            TacticResult(candidate, TacticStatus.COMPLETE, proof="done"),
            completed_proof="done",
        )

    outcome = AnalogyFallback(
        Generator(),
        max_model_calls=2,
        seed=7,
        analogy_scratchpad=scratchpad,
    ).search(
        STATE,
        GOAL,
        (),
        evaluate,
        NeverCancelled(),
        "guidance",
        proof_belief=_proof_belief(),
    )

    assert outcome.completed_proof == "done"
    assert events == [
        ("tactic_score", "exact low"),
        ("lean", "exact low", 1),
    ]
    assert scoring_inputs[0][1] == ("exact low",)
    assert "structural winner" in scoring_inputs[0][0][-1].content
    assert "selected everyday story" in scoring_inputs[0][0][-1].content.lower()


def test_post_selection_tactic_score_failure_is_logged_and_fails_open(monkeypatch):
    scratchpad = AnalogyScratchpad(
        MentalModel(
            "travel or navigation",
            (MentalModelCorrespondence("start", "old", "finish"),),
        )
    )
    traces = []
    lean_calls = []

    class Generator:
        calls = 0

        def generate(self, _messages, *, seed):
            self.calls += 1
            return (
                _mental_response("travel or navigation", "candidate", "exact h")
                if self.calls == 1
                else _tactic_response("exact h")
            )

        def score_completions(self, _messages, _completions):
            raise RuntimeError("scorer offline")

    monkeypatch.setattr(
        "analogy.solver.analogy.rescue.score_mental_model_revision",
        lambda model, belief, embedder: SimpleNamespace(
            distance=0.1, coupling=((1.0,),)
        ),
    )

    def evaluate(candidate):
        lean_calls.append(candidate.source)
        return TacticEvaluation(
            TacticResult(candidate, TacticStatus.COMPLETE, proof="done"),
            completed_proof="done",
        )

    outcome = AnalogyFallback(
        Generator(),
        max_model_calls=1,
        seed=7,
        trace_sink=traces.append,
        analogy_scratchpad=scratchpad,
    ).search(
        STATE,
        GOAL,
        (),
        evaluate,
        NeverCancelled(),
        "guidance",
        proof_belief=_proof_belief(),
    )

    assert outcome.completed_proof == "done"
    assert lean_calls == ["exact h"]
    tactic_event = next(event for event in traces if event["kind"] == "analogy_tactic_score")
    assert tactic_event["structural_rank"] == 1
    assert "RuntimeError: scorer offline" in tactic_event["error"]


def test_cancellation_during_post_selection_score_prevents_lean_submission(monkeypatch):
    scratchpad = AnalogyScratchpad(
        MentalModel(
            "travel or navigation",
            (MentalModelCorrespondence("start", "old", "finish"),),
        )
    )

    class Token:
        cancelled = False

        def is_cancelled(self):
            return self.cancelled

    token = Token()

    class Generator:
        calls = 0

        def generate(self, _messages, *, seed):
            self.calls += 1
            return (
                _mental_response("travel or navigation", "candidate", "exact h")
                if self.calls == 1
                else _tactic_response("exact h")
            )

        def score_completions(self, _messages, completions):
            token.cancelled = True
            return (CompletionScore(completions[0], -0.5, -0.5, 1),)

    monkeypatch.setattr(
        "analogy.solver.analogy.rescue.score_mental_model_revision",
        lambda model, belief, embedder: SimpleNamespace(
            distance=0.1, coupling=((1.0,),)
        ),
    )

    outcome = AnalogyFallback(
        Generator(),
        max_model_calls=1,
        seed=7,
        analogy_scratchpad=scratchpad,
    ).search(
        STATE,
        GOAL,
        (),
        lambda _candidate: pytest.fail("cancelled rescue must not call Lean"),
        token,
        "guidance",
        proof_belief=_proof_belief(),
    )

    assert outcome.cancelled is True
    assert len(outcome.rollouts) == 1
    assert outcome.rollouts[0].termination is RescueTermination.SCORED


def test_global_structural_scorer_failure_aborts_revision_ranking(monkeypatch):
    scratchpad = AnalogyScratchpad(
        MentalModel(
            "travel or navigation",
            (MentalModelCorrespondence("start", "old", "finish"),),
        )
    )

    class Generator:
        def generate(self, _messages, *, seed):
            return _mental_response("travel or navigation", "new", "exact new")

    monkeypatch.setattr(
        "analogy.solver.analogy.rescue.score_mental_model_revision",
        lambda *_args: (_ for _ in ()).throw(
            StructuralScoringError("calibration metadata mismatch")
        ),
    )

    with pytest.raises(StructuralScoringError, match="calibration metadata"):
        AnalogyFallback(
            Generator(),
            max_model_calls=1,
            seed=7,
            analogy_scratchpad=scratchpad,
        ).search(
            STATE,
            GOAL,
            (),
            lambda _candidate: pytest.fail("Lean must not run"),
            NeverCancelled(),
            "guidance",
            proof_belief=_proof_belief(),
        )


def test_countermodel_batches_only_new_domain_mental_models(monkeypatch):
    scratchpad = AnalogyScratchpad(
        MentalModel(
            "travel or navigation",
            (MentalModelCorrespondence("start", "old", "finish"),),
        )
    )
    responses = iter(
        (
            _mental_response("travel or navigation", "invalid", "exact invalid"),
            _mental_response(
                "gardening", "plant", "exact h", action="change_domain"
            ),
            _tactic_response("exact h"),
        )
    )
    scratchpad.abandon_current("gardening")
    lean_calls = []

    class Generator:
        def generate(self, _messages, *, seed):
            return next(responses)

    monkeypatch.setattr(
        "analogy.solver.analogy.rescue.score_mental_model_revision",
        lambda model, belief, embedder: SimpleNamespace(
            distance=0.1, coupling=((1.0,),)
        ),
    )

    def evaluate(candidate):
        lean_calls.append(candidate.source)
        result = TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
        return TacticEvaluation(result, completed_proof="done")

    outcome = AnalogyFallback(
        Generator(),
        max_model_calls=2,
        seed=7,
        analogy_scratchpad=scratchpad,
    ).search(
        STATE,
        GOAL,
        (),
        evaluate,
        NeverCancelled(),
        "guidance",
        force_domain_change=True,
        proof_belief=_proof_belief(),
    )

    assert outcome.completed_proof == "done"
    assert lean_calls == ["exact h"]
    assert scratchpad.current.domain == "gardening"
    assert [rollout.termination for rollout in outcome.rollouts] == [
        RescueTermination.INVALID_OUTPUT,
        RescueTermination.COMPLETE,
    ]


def test_rejected_scored_revisions_leave_model_and_history_unchanged(monkeypatch):
    initial = MentalModel(
        "travel or navigation",
        (MentalModelCorrespondence("start", "old", "finish"),),
    )
    scratchpad = AnalogyScratchpad(initial)
    responses = iter(
        (
            _mental_response(
                "travel or navigation", "first", "exact first"
            ),
            _mental_response(
                "travel or navigation", "second", "exact second"
            ),
            _tactic_response("exact first"),
            _tactic_response("exact second"),
        )
    )

    class Generator:
        def generate(self, _messages, *, seed):
            return next(responses)

    monkeypatch.setattr(
        "analogy.solver.analogy.rescue.score_mental_model_revision",
        lambda model, belief, embedder: SimpleNamespace(
            distance=0.1, coupling=((1.0,),)
        ),
    )
    outcome = AnalogyFallback(
        Generator(),
        max_model_calls=2,
        seed=7,
        analogy_scratchpad=scratchpad,
    ).search(
        STATE,
        GOAL,
        (),
        lambda candidate: TacticEvaluation(
            TacticResult(candidate, TacticStatus.ERROR, diagnostics=("rejected",))
        ),
        NeverCancelled(),
        "guidance",
        proof_belief=_proof_belief(),
    )

    assert scratchpad.current == initial
    assert scratchpad.history == ()
    assert [rollout.termination for rollout in outcome.rollouts] == [
        RescueTermination.REJECTED,
        RescueTermination.REJECTED,
    ]


def test_scored_but_unsubmitted_revisions_remain_in_generation_records(monkeypatch):
    scratchpad = AnalogyScratchpad(
        MentalModel(
            "travel or navigation",
            (MentalModelCorrespondence("start", "old", "finish"),),
        )
    )
    responses = iter(
        (
            _mental_response("travel or navigation", "best", "exact best"),
            _mental_response("travel or navigation", "other", "exact other"),
            _tactic_response("exact best"),
        )
    )

    class Generator:
        def generate(self, _messages, *, seed):
            return next(responses)

    monkeypatch.setattr(
        "analogy.solver.analogy.rescue.score_mental_model_revision",
        lambda model, belief, embedder: SimpleNamespace(
            distance=(
                0.1
                if model.correspondences[0].domain_transition == "best"
                else 0.2
            ),
            coupling=((1.0,),),
        ),
    )

    def evaluate(candidate):
        result = TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
        return TacticEvaluation(result, completed_proof="done")

    outcome = AnalogyFallback(
        Generator(),
        max_model_calls=2,
        seed=7,
        analogy_scratchpad=scratchpad,
    ).search(
        STATE,
        GOAL,
        (),
        evaluate,
        NeverCancelled(),
        "guidance",
        proof_belief=_proof_belief(),
    )

    assert [rollout.termination for rollout in outcome.rollouts] == [
        RescueTermination.COMPLETE,
        RescueTermination.SCORED,
    ]
    assert all(
        rollout.generations[0].structural_distance is not None
        for rollout in outcome.rollouts
    )


def test_cancellation_during_batch_keeps_already_scored_revision(monkeypatch):
    scratchpad = AnalogyScratchpad(
        MentalModel(
            "travel or navigation",
            (MentalModelCorrespondence("start", "old", "finish"),),
        )
    )

    class Token:
        checks = 0

        def is_cancelled(self):
            self.checks += 1
            return self.checks >= 3

    class Generator:
        def generate(self, _messages, *, seed):
            return _mental_response("travel or navigation", "candidate", "exact h")

    monkeypatch.setattr(
        "analogy.solver.analogy.rescue.score_mental_model_revision",
        lambda model, belief, embedder: SimpleNamespace(
            distance=0.1, coupling=((1.0,),)
        ),
    )
    outcome = AnalogyFallback(
        Generator(),
        max_model_calls=2,
        seed=7,
        analogy_scratchpad=scratchpad,
    ).search(
        STATE,
        GOAL,
        (),
        lambda _candidate: (_ for _ in ()).throw(
            AssertionError("cancelled batch must not reach Lean")
        ),
        Token(),
        "guidance",
        proof_belief=_proof_belief(),
    )

    assert outcome.cancelled is True
    assert len(outcome.rollouts) == 1
    assert outcome.rollouts[0].termination is RescueTermination.SCORED


def test_stuck_search_can_revise_only_steps_and_commits_on_lean_progress():
    initial = AnalogyState("locksmithing", ("P -> lock",), ("Inspect it.",))
    scratchpad = AnalogyScratchpad(initial)
    successor = SymbolicState("next", "h : P\n⊢ P", "lean4")

    class Generator:
        def generate(self, messages, *, seed):
            assert messages[1].content == render_analogy_state(initial)
            return _block(
                "locksmithing", ("P -> lock",), ("Turn the matching key.",)
            ) + '\n{"action":"revise_steps","predicted_state":"h : P\\n⊢ P","tactic":"intro h"}'

    def evaluate(candidate):
        from analogy.utils.records import SearchNode
        result = TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)
        return TacticEvaluation(result, successor=SearchNode(successor, 1.0))

    outcome = _search(Generator(), evaluate, calls=1, scratchpad=scratchpad)

    assert outcome.successor is not None
    assert scratchpad.current is not None
    assert scratchpad.current.domain == initial.domain
    assert scratchpad.current.mapping == initial.mapping
    assert scratchpad.current.steps == ("Turn the matching key.",)


def test_change_domain_trials_continue_until_lean_accepts_and_abandon_old_domain():
    initial = AnalogyState("locksmithing", ("P -> lock",), ("Inspect it.",))
    scratchpad = AnalogyScratchpad(initial)
    calls = []

    class Generator:
        def generate(self, _messages, *, seed):
            calls.append(seed)
            if len(calls) == 1:
                state = _block("shipping", ("P -> parcel",), ("Send it.",))
                tactic = "exact missing"
            else:
                state = _block("gardening", ("P -> seed",), ("Plant it.",))
                tactic = "exact h"
            return state + f'\n{{"action":"change_domain","predicted_state":"SOLVED","tactic":"{tactic}"}}'

    def evaluate(candidate):
        if candidate.source == "exact h":
            result = TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
            return TacticEvaluation(result, completed_proof="done")
        return TacticEvaluation(
            TacticResult(candidate, TacticStatus.ERROR, diagnostics=("unknown",))
        )

    outcome = _search(Generator(), evaluate, calls=3, scratchpad=scratchpad)

    assert outcome.completed_proof == "done"
    assert len(calls) == 2
    assert scratchpad.current is not None
    assert scratchpad.current.domain == "gardening"
    assert scratchpad.current.abandoned_domains == ("locksmithing",)


def test_countermodel_forces_destruction_and_rejects_step_revision():
    initial = AnalogyState("locksmithing", ("P -> lock",), ("Inspect it.",))
    scratchpad = AnalogyScratchpad(initial)

    class Generator:
        def generate(self, messages, *, seed):
            assert any(
                render_analogy_state(initial) in message.content
                for message in messages
            )
            return """<analogy>
domain: locksmithing
mapping:
- P -> lock
steps:
1. Try a different key.
abandoned domains:
- locksmithing
</analogy>
{"action":"revise_steps","predicted_state":"SOLVED","tactic":"exact h"}"""

    outcome = _search(
        Generator(),
        lambda _candidate: (_ for _ in ()).throw(AssertionError("must not call Lean")),
        calls=1,
        scratchpad=scratchpad,
        force_domain_change=True,
    )

    assert outcome.completed_proof is None
    assert scratchpad.current == initial
    assert outcome.rollouts[0].termination is RescueTermination.INVALID_OUTPUT


def test_prediction_match_is_provisional_while_later_domain_can_make_progress():
    scratchpad = AnalogyScratchpad(
        AnalogyState("locksmithing", ("P -> lock",), ("Inspect it.",))
    )
    repeated = TacticResult(
        TacticCandidate("assumption", 1.0, CandidateOrigin.ANALOGICAL),
        TacticStatus.ACCEPTED,
        state=SymbolicState("same", "Q : Prop\n⊢ Q", "lean4"),
    )

    calls = []

    class Generator:
        def generate(self, _messages, *, seed):
            calls.append(seed)
            if len(calls) == 1:
                return _block("shipping", ("P -> parcel",), ("Send it.",)) + (
                    '\n{"action":"change_domain","predicted_state":"Q : Prop\\n⊢ Q",'
                    '"tactic":"assumption"}'
                )
            return _block("gardening", ("P -> seed",), ("Plant it.",)) + (
                '\n{"action":"change_domain","predicted_state":"SOLVED",'
                '"tactic":"exact h"}'
            )

    def evaluate(candidate):
        if candidate.source == "exact h":
            result = TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
            return TacticEvaluation(result, completed_proof="done")
        return TacticEvaluation(
            repeated,
            discarded=DiscardedSuccessor(repeated, DiscardReason.REPEATED_STATE),
        )

    outcome = _search(
        Generator(),
        evaluate,
        calls=2,
        scratchpad=scratchpad,
    )

    assert outcome.completed_proof == "done"
    assert len(calls) == 2
    assert scratchpad.current is not None
    assert scratchpad.current.domain == "gardening"
    assert outcome.rollouts[0].generations[0].prediction_label is PredictionLabel.PREDICTION_MATCHED


def test_abandoned_domain_update_is_rejected_before_lean_without_mutation():
    current = AnalogyState(
        "locksmithing", ("P -> lock",), ("Inspect it.",), ("shipping",)
    )
    scratchpad = AnalogyScratchpad(current)
    lean_calls = []

    class Generator:
        def generate(self, _messages, *, seed):
            return _block("shipping", ("P -> parcel",), ("Send it.",)) + (
                '\n{"action":"change_domain","predicted_state":"SOLVED",'
                '"tactic":"exact h"}'
            )

    outcome = _search(
        Generator(),
        lambda candidate: lean_calls.append(candidate),
        calls=1,
        scratchpad=scratchpad,
    )

    assert lean_calls == []
    assert scratchpad.current == current
    assert scratchpad.current.abandoned_domains == ("shipping",)
    assert outcome.rollouts[0].termination is RescueTermination.INVALID_OUTPUT


def test_update_repair_prompt_strips_previous_block_and_uses_action_schema():
    current = AnalogyState("locksmithing", ("P -> lock",), ("Inspect it.",))
    scratchpad = AnalogyScratchpad(current)
    calls = []

    class Generator:
        def generate(self, messages, *, seed):
            calls.append(messages)
            if len(calls) == 1:
                return render_analogy_state(current) + "\nnot json"
            feedback = messages[-1].content
            assert render_analogy_state(current) not in feedback
            assert feedback.count("<mental_model>") == 1
            assert '"action", "predicted_state", and "tactic"' in feedback
            assert '"analogy", "predicted_state"' not in feedback
            return _block(
                "locksmithing", ("P -> lock",), ("Turn the key.",)
            ) + '\n{"action":"revise_steps","predicted_state":"SOLVED","tactic":"exact h"}'

    def evaluate(candidate):
        result = TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
        return TacticEvaluation(result, completed_proof="done")

    assert _search(
        Generator(), evaluate, calls=2, scratchpad=scratchpad
    ).completed_proof == "done"


def test_semantic_guidance_distinguishes_continue_from_restart() -> None:
    valid = SemanticRecoveryEvent(
        "semantic:0", "state", SemanticResult.VALID, rescue_state_id="state"
    )
    invalid = SemanticRecoveryEvent(
        "semantic:1",
        "false",
        SemanticResult.COUNTERMODEL,
        (("G", False), ("H", True)),
        "parent",
        ("left",),
    )

    assert (
        render_semantic_guidance(valid)
        == "SAT: no countermodel; continue from this state."
    )
    assert render_semantic_guidance(invalid) == (
        "SAT restart: left led to a countermodel (G=false, H=true); "
        "choose a different plan from this last valid state."
    )


def test_semantic_guidance_has_a_hard_size_ceiling() -> None:
    event = SemanticRecoveryEvent(
        "semantic:1",
        "false",
        SemanticResult.COUNTERMODEL,
        tuple((f"proposition_{index}_with_a_long_name", bool(index % 2)) for index in range(30)),
        "parent",
        ("apply an_extremely_long_tactic_name " + "x" * 300,),
    )

    guidance = render_semantic_guidance(event)

    assert len(guidance) <= 240
    assert guidance.startswith("SAT restart:")
    assert guidance.endswith("choose a different plan from this last valid state.")


def test_analogy_prompt_separates_open_from_rejected_feedback():
    prompts = []
    opened = TacticResult(
        TacticCandidate("constructor", 1.0, CandidateOrigin.ORDINARY),
        TacticStatus.ACCEPTED,
        state=SymbolicState("open", "P : Prop\n⊢ P", "lean4"),
    )
    rejected = TacticResult(
        TacticCandidate("exact nope", 1.0, CandidateOrigin.ORDINARY),
        TacticStatus.ERROR,
        diagnostics=("unknown identifier",),
    )

    class Generator:
        def generate(self, messages, *, seed):
            prompts.append(messages[-1].content)
            return '{"analogy":"x","predicted_state":"done","tactic":"exact h"}'

    def evaluate(candidate):
        result = TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
        return TacticEvaluation(result, completed_proof="done")

    _search(Generator(), evaluate, ordinary_attempts=(opened, rejected))

    prompt = prompts[0]
    open_section, rejected_section = prompt.split("REJECTED tactics", 1)
    assert "constructor: accepted" in open_section
    assert "exact nope: unknown identifier" not in open_section
    assert "exact nope: unknown identifier" in rejected_section


def test_analogy_prompt_requires_using_available_dependency_evidence():
    prompts = []

    class Generator:
        def generate(self, messages, *, seed):
            prompts.append(messages[-1].content)
            return '{"analogy":"map hPQ sides","predicted_state":"done","tactic":"exact h"}'

    def evaluate(candidate):
        result = TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
        return TacticEvaluation(result, completed_proof="done")

    _search(
        Generator(),
        evaluate,
        dependency_table="left: P=true; hPQ=left\nright: Q=true; hPQ=right",
    )

    assert "Dependency evidence (may be absent)" in prompts[0]
    assert "hPQ=left" in prompts[0] and "hPQ=right" in prompts[0]


def test_invalid_generations_retry_but_first_valid_response_gets_one_lean_call():
    model_calls = []
    lean_calls = []
    traces = []

    class Generator:
        def generate(self, messages, *, seed):
            model_calls.append((messages, seed))
            if len(model_calls) == 1:
                assert "Lean card:" in messages[-1].content
                assert "Everyday-domain card:" in messages[-1].content
                assert "SAT: no countermodel; continue from this state." in messages[-1].content
                return "not json"
            assert "not json" in messages[-1].content
            assert '"action", "predicted_state", and "tactic"' in messages[-1].content
            return '{"analogy":"a lock and key","predicted_state":"solved","tactic":"exact h"}'

    def evaluate(candidate):
        lean_calls.append(candidate.source)
        result = TacticResult(candidate, TacticStatus.COMPLETE, proof="by exact h")
        return TacticEvaluation(result, completed_proof="by exact h")

    outcome = _search(Generator(), evaluate, traces=traces)

    assert outcome.completed_proof == "by exact h"
    assert len({seed for _, seed in model_calls}) == 2
    assert model_calls[0][0] != model_calls[1][0]
    assert lean_calls == ["exact h"]
    assert [rollout.termination for rollout in outcome.rollouts] == [
        RescueTermination.INVALID_OUTPUT,
        RescueTermination.COMPLETE,
    ]
    assert outcome.rollouts[0].generations[0].status is GenerationStatus.INVALID
    assert "domain: a lock and key" in outcome.rollouts[1].analogy_trace
    assert outcome.rollouts[1].generations[0].repair is True
    assert [trace["kind"] for trace in traces] == [
        "analogy_model_call",
        "analogy_model_call",
    ]


def test_valid_tactic_rejection_feeds_lean_feedback_into_a_second_attempt():
    calls = []

    class Generator:
        def generate(self, messages, *, seed):
            calls.append((messages, seed))
            if len(calls) == 1:
                return '{"analogy":"a lock and key","predicted_state":"open","tactic":"exact h2"}'
            assert "exact h2" in messages[-1].content
            assert "h2 has type U but the goal is H" in messages[-1].content
            return '{"analogy":"use the matching key","predicted_state":"solved","tactic":"exact hH"}'

    def evaluate(candidate):
        if candidate.source == "exact hH":
            result = TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
            return TacticEvaluation(result, completed_proof="done")
        return TacticEvaluation(
            TacticResult(
                candidate,
                TacticStatus.ERROR,
                diagnostics=("h2 has type U but the goal is H",),
            )
        )

    outcome = _search(Generator(), evaluate, calls=3)

    assert outcome.completed_proof == "done"
    assert len(calls) == 2
    assert [item.termination for item in outcome.rollouts] == [
        RescueTermination.REJECTED,
        RescueTermination.COMPLETE,
    ]


def test_discarded_analogy_progress_is_explained_and_retried():
    calls = []

    class Generator:
        def generate(self, messages, *, seed):
            calls.append((messages, seed))
            tactic = "assumption" if len(calls) == 1 else "exact h"
            if len(calls) == 2:
                assert "repeated_state" in messages[-1].content
            return (
                '{"analogy":"try another route","predicted_state":"open",'
                f'"tactic":"{tactic}"}}'
            )

    def evaluate(candidate):
        if candidate.source == "exact h":
            result = TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
            return TacticEvaluation(result, completed_proof="done")
        result = TacticResult(candidate, TacticStatus.ACCEPTED, state=STATE)
        discarded = DiscardedSuccessor(result, DiscardReason.REPEATED_STATE)
        return TacticEvaluation(result, discarded=discarded)

    outcome = _search(Generator(), evaluate, calls=3)

    assert outcome.completed_proof == "done"
    assert [item.termination for item in outcome.rollouts] == [
        RescueTermination.EXHAUSTED,
        RescueTermination.COMPLETE,
    ]


def test_rejected_analogy_tactics_exhaust_the_existing_three_call_budget():
    calls = []

    class Generator:
        def generate(self, _messages, *, seed):
            calls.append(seed)
            tactic = ("assumption", "rfl", "contradiction")[len(calls) - 1]
            return (
                '{"analogy":"route","predicted_state":"open",'
                f'"tactic":"{tactic}"}}'
            )

    def evaluate(candidate):
        return TacticEvaluation(
            TacticResult(candidate, TacticStatus.ERROR, diagnostics=("rejected",))
        )

    outcome = _search(Generator(), evaluate, calls=3)

    assert len(calls) == 3
    assert len(outcome.rollouts) == 3
    assert all(
        item.termination is RescueTermination.REJECTED
        for item in outcome.rollouts
    )


def test_analogy_retry_feedback_is_bounded():
    calls = []
    long_diagnostic = "detail " + ("x" * 900)

    class Generator:
        def generate(self, messages, *, seed):
            calls.append((messages, seed))
            if len(calls) == 2:
                feedback = messages[-1].content
                assert "[truncated]" in feedback
                assert len(feedback) < 800
            tactic = "bad" if len(calls) == 1 else "exact h"
            return (
                '{"analogy":"route","predicted_state":"open",'
                f'"tactic":"{tactic}"}}'
            )

    def evaluate(candidate):
        if candidate.source == "exact h":
            result = TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
            return TacticEvaluation(result, completed_proof="done")
        return TacticEvaluation(
            TacticResult(candidate, TacticStatus.ERROR, diagnostics=(long_diagnostic,))
        )

    assert _search(Generator(), evaluate).completed_proof == "done"


def test_infrastructure_failure_does_not_consume_an_analogy_correction_call():
    calls = []

    class Generator:
        def generate(self, _messages, *, seed):
            calls.append(seed)
            return '{"analogy":"route","predicted_state":"open","tactic":"exact h"}'

    def evaluate(candidate):
        result = LeanInfrastructureResult(
            candidate=candidate,
            status=TacticStatus.ERROR,
            diagnostics=("timed out",),
            verification_status=VerificationStatus.TIMEOUT,
        )
        return TacticEvaluation(result)

    outcome = _search(Generator(), evaluate, calls=3)

    assert len(calls) == 1
    assert len(outcome.rollouts) == 1


def test_duplicate_ordinary_tactic_is_logged_as_duplicate_and_resampled_without_lean():
    model_calls = []
    lean_calls = []
    ordinary = TacticResult(
        TacticCandidate("apply h2", 1.0, CandidateOrigin.ORDINARY),
        TacticStatus.ERROR,
        diagnostics=("rejected",),
    )

    class Generator:
        def generate(self, messages, *, seed):
            model_calls.append((messages, seed))
            if len(model_calls) == 1:
                return (
                    '{"analogy":"same key","predicted_state":"open",'
                    '"tactic":"  apply   h2  "}'
                )
            assert "already attempted at this proof state" in messages[-1].content
            assert "Tactics already tried here: apply h2" in messages[-1].content
            return (
                '{"analogy":"contrapositive","predicted_state":"solved",'
                '"tactic":"intro h; exact h2 (h1 h)"}'
            )

    def evaluate(candidate):
        lean_calls.append(candidate.source)
        result = TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
        return TacticEvaluation(result, completed_proof="done")

    outcome = _search(
        Generator(),
        evaluate,
        calls=3,
        ordinary_attempts=(ordinary,),
    )

    assert outcome.completed_proof == "done"
    assert lean_calls == ["intro h; exact h2 (h1 h)"]
    assert len(model_calls) == 2
    assert outcome.rollouts[0].termination is RescueTermination.DUPLICATE
    assert outcome.rollouts[0].generations[0].status is GenerationStatus.DUPLICATE
    assert outcome.rollouts[0].generations[0].error_kind == "duplicate_tactic"
    assert "return a different tactic" not in (
        outcome.rollouts[0].generations[0].diagnostic or ""
    )
    assert outcome.rollouts[1].generations[0].repair is True


def test_correction_wording_comes_from_prompt_catalog(tmp_path, monkeypatch):
    path = tmp_path / "prompts.yaml"
    path.write_text(
        """prompts:
  analogy_lean_correction_repair:
    version: analogy-lean-correction-repair-test-v1
    text: 'CUSTOM {{tactic}} {{diagnostic}} {{contract}}'
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        prompts_module,
        "_ACTIVE_CATALOG",
        PromptCatalog.load(path),
    )
    from analogy.solver.analogy.rescue import RescueProposal

    proposal = RescueProposal(
        TacticCandidate("exact h", 1.0, CandidateOrigin.ANALOGICAL),
        "SOLVED",
    )
    evaluation = TacticEvaluation(
        TacticResult(
            proposal.candidate,
            TacticStatus.ERROR,
            diagnostics=("unknown h",),
        )
    )

    assert _analogy_correction_feedback(
        proposal,
        evaluation,
        contract="CONTRACT",
    ) == "CUSTOM exact h unknown h CONTRACT"


def test_predicted_no_change_is_labeled_and_never_sent_to_lean():
    lean_calls = []

    class Generator:
        calls = 0

        def generate(self, messages, *, seed):
            self.calls += 1
            if self.calls == 1:
                return '{"analogy":"stall","predicted_state":"P","tactic":"assumption"}'
            assert "predicted no state change" in messages[-1].content
            assert "Tactics already tried here: assumption" in messages[-1].content
            return '{"analogy":"finish","predicted_state":"SOLVED","tactic":"exact h"}'

    def evaluate(candidate):
        lean_calls.append(candidate.source)
        result = TacticResult(candidate, TacticStatus.COMPLETE, proof="done")
        return TacticEvaluation(result, completed_proof="done")

    outcome = _search(Generator(), evaluate)

    assert lean_calls == ["exact h"]
    assert outcome.rollouts[0].generations[0].prediction_label is PredictionLabel.PREDICTED_NO_CHANGE
    assert outcome.rollouts[1].generations[0].prediction_label is PredictionLabel.PREDICTION_MATCHED


def test_changed_prediction_is_labeled_mismatched_when_lean_disagrees():
    successor = SymbolicState("next", "Q : Prop\n⊢ Q", "lean4")

    class Generator:
        def generate(self, _messages, *, seed):
            return '{"analogy":"move","predicted_state":"R : Prop\\n⊢ R","tactic":"intro h"}'

    def evaluate(candidate):
        result = TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)
        from analogy.utils.records import SearchNode
        return TacticEvaluation(result, successor=SearchNode(successor, 1.0))

    outcome = _search(Generator(), evaluate, calls=1)

    assert outcome.rollouts[0].generations[0].prediction_label is PredictionLabel.PREDICTION_MISMATCHED


def test_prediction_matching_preserves_lean_identifier_case():
    successor = SymbolicState("next", "P : Prop\nhA : P\n⊢ P", "lean4")

    class Generator:
        def generate(self, _messages, *, seed):
            return '{"analogy":"move","predicted_state":"p : Prop\\nha : p\\n⊢ p","tactic":"intro h"}'

    def evaluate(candidate):
        result = TacticResult(candidate, TacticStatus.ACCEPTED, state=successor)
        from analogy.utils.records import SearchNode
        return TacticEvaluation(result, successor=SearchNode(successor, 1.0))

    outcome = _search(Generator(), evaluate, calls=1)

    assert outcome.rollouts[0].generations[0].prediction_label is PredictionLabel.PREDICTION_MISMATCHED


def test_cancellation_before_generation_makes_no_model_or_lean_call():
    class Cancelled:
        def is_cancelled(self):
            return True

    class Generator:
        def generate(self, _messages, *, seed):
            raise AssertionError(f"unexpected model call at seed {seed}")

    def fail_evaluate(_candidate):
        raise AssertionError("unexpected Lean call")

    outcome = _search(
        Generator(), fail_evaluate, cancellation=Cancelled()
    )

    assert outcome.cancelled is True
    assert outcome.rollouts == ()


def test_cancellation_during_generation_preserves_trace_without_calling_lean():
    class Token:
        cancelled = False

        def is_cancelled(self):
            return self.cancelled

    token = Token()

    class Generator:
        def generate(self, _messages, *, seed):
            token.cancelled = True
            return '{"analogy":"a lock","predicted_state":"open","tactic":"exact h"}'

    def fail_evaluate(_candidate):
        raise AssertionError("cancelled generation must not reach Lean")

    outcome = _search(Generator(), fail_evaluate, cancellation=token)

    assert outcome.cancelled is True
    assert len(outcome.rollouts) == 1
    assert outcome.rollouts[0].termination is RescueTermination.CANCELLED
    assert outcome.rollouts[0].generations[0].status is GenerationStatus.VALID
    assert outcome.rollouts[0].attempts == ()


def test_fallback_seed_namespace_is_stable_and_distinct_per_state():
    seeds = []

    class Generator:
        def generate(self, _messages, *, seed):
            seeds.append(seed)
            return "invalid"

    fallback = AnalogyFallback(
        Generator(),
        max_model_calls=1,
        seed=7,
        analogy_scratchpad=AnalogyScratchpad(),
    )

    def run(state):
        return fallback.search(
            state,
            GOAL,
            (),
            lambda _candidate: None,
            NeverCancelled(),
            "SAT: no countermodel; continue from this state.",
        )

    run(SymbolicState("first", "P", "lean4"))
    run(SymbolicState("second", "Q", "lean4"))
    run(SymbolicState("first", "P", "lean4"))

    assert seeds[0] != seeds[1]
    assert seeds[0] == seeds[2]
