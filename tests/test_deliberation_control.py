import json

from analogy.solver.analogy.rescue import (
    RescueTermination,
    TacticEvaluation,
)
from analogy.solver.deliberation import (
    DeliberationFallback,
    _parse_deliberation_tactic,
    parse_deliberation_plan,
)
from analogy.solver.search import NeverCancelled
from analogy.utils.records import (
    CandidateOrigin,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)


STATE = SymbolicState("stuck", "p q : Prop\nhp : p\n⊢ p ∨ q", "lean4")
GOAL = SymbolicState("goal", "p q : Prop\n⊢ p ∨ q", "lean4")


def _plan(label):
    return json.dumps({
        "analysis": label,
        "branches": [
            {
                "tactic": "left",
                "resulting_obligation": "prove p using hp",
            },
            {
                "tactic": "right",
                "resulting_obligation": "prove q without a visible fact",
            },
        ],
        "recommended_approach": "choose the supported left branch",
    })


def _tactic(tactic):
    return json.dumps({
        "decision": {
            "current_proof_situation": "the goal is p or q and hp proves p",
            "options": [
                {
                    "tactic": "left",
                    "resulting_obligation": "prove p using hp",
                },
                {
                    "tactic": "right",
                    "resulting_obligation": "prove q without a visible fact",
                },
            ],
            "selection_reason": "the left obligation has direct evidence",
        },
        "tactic": tactic,
        "predicted_state": "p q : Prop\nhp : p\n⊢ p",
    })


def test_deliberation_plan_requires_both_disjunction_branches():
    payload = json.loads(_plan("compare both branches"))
    payload["branches"] = payload["branches"][:1]

    try:
        parse_deliberation_plan(json.dumps(payload), STATE.expression)
    except ValueError as error:
        assert "left and right" in str(error)
    else:
        raise AssertionError("one-sided disjunction plan must be rejected")


def test_deliberation_fallback_matches_two_plans_then_one_tactic_call():
    calls = []
    traces = []

    class Generator:
        def generate(self, messages, *, seed):
            calls.append(messages)
            if len(calls) == 1:
                return _plan("first independent direct plan")
            if len(calls) == 2:
                return _plan("second independent direct plan")
            return _tactic("left")

    def evaluate(candidate):
        assert candidate.origin is CandidateOrigin.DELIBERATIVE
        result = TacticResult(
            candidate,
            TacticStatus.ACCEPTED,
            state=SymbolicState("next", "p q : Prop\nhp : p\n⊢ p", "lean4"),
        )
        from analogy.utils.records import SearchNode

        return TacticEvaluation(result, successor=SearchNode(result.state, 1.0))

    outcome = DeliberationFallback(
        Generator(), max_model_calls=2, seed=0, trace_sink=traces.append
    ).search(
        STATE,
        GOAL,
        (),
        evaluate,
        NeverCancelled(),
        "SAT: continue",
        "dependency evidence",
    )

    assert outcome.successor is not None
    assert len(calls) == 3
    assert [event["kind"] for event in traces] == [
        "deliberation_plan_call",
        "deliberation_plan_call",
        "deliberation_tactic_call",
    ]
    prompt_text = "\n".join(message.content for call in calls for message in call)
    assert "dependency evidence" in prompt_text
    assert "traveler holding a blue ticket" not in prompt_text.lower()
    assert "selected everyday story" not in prompt_text.lower()
    assert outcome.rollouts[-1].termination is RescueTermination.PROGRESS


def test_deliberation_duplicate_tactic_never_reaches_lean():
    calls = []

    class Generator:
        def generate(self, messages, *, seed):
            calls.append(messages)
            if len(calls) <= 2:
                return _plan(f"plan {len(calls)}")
            return _tactic("left")

    ordinary = TacticResult(
        # The ordinary arm has already tried this exact tactic at this state.
        TacticCandidate("left", 1.0, CandidateOrigin.ORDINARY),
        TacticStatus.ERROR,
        diagnostics=("failed",),
    )

    outcome = DeliberationFallback(
        Generator(), max_model_calls=2, seed=0
    ).search(
        STATE,
        GOAL,
        (ordinary,),
        lambda _candidate: (_ for _ in ()).throw(
            AssertionError("duplicate tactic must not reach Lean")
        ),
        NeverCancelled(),
        "SAT: continue",
        "dependency evidence",
    )

    assert outcome.successor is None
    assert all(not rollout.attempts for rollout in outcome.rollouts)
    assert any(
        rollout.termination is RescueTermination.DUPLICATE
        for rollout in outcome.rollouts
    )


def test_direct_decision_accepts_semantically_equivalent_nested_fields():
    raw = json.dumps({
        "decision": {
            "current_proof_situation": {
                "options": [
                    {
                        "tactic": "left",
                        "resulting_obligation": "prove p",
                    },
                    {
                        "tactic": "right",
                        "resulting_obligation": "prove q",
                    },
                ],
                "selection_reason": "the right branch is preferred",
            },
        },
        "tactic": "right",
        "predicted_state": "p q : Prop\nhp : p\n⊢ q",
    })

    proposal = _parse_deliberation_tactic(
        raw, STATE.expression, attempted_tactics=set()
    )

    assert proposal.candidate.source == "right"
    assert proposal.candidate.origin is CandidateOrigin.DELIBERATIVE

