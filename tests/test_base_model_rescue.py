from analogy.records import CandidateOrigin, SymbolicState, TacticResult, TacticStatus
from analogy.solver.rescue import (
    AnalogyFallback,
    GenerationStatus,
    RescueOutputError,
    RescueTermination,
    TacticEvaluation,
    parse_analogy_tactic,
)
from analogy.solver.search import NeverCancelled


STATE = SymbolicState("state", "P", "lean4")
GOAL = SymbolicState("goal", "P", "lean4")


def _search(generator, evaluate, *, calls=3, cancellation=None, traces=None):
    return AnalogyFallback(
        generator,
        max_model_calls=calls,
        seed=7,
        trace_sink=None if traces is None else traces.append,
    ).search(
        STATE, GOAL, (), (), evaluate, cancellation or NeverCancelled()
    )


def test_analogy_schema_requires_one_singular_tactic():
    valid = '{"analogy":"a lock and key","predicted_state":"closed","tactic":"exact h"}'

    analogy, proposal = parse_analogy_tactic(valid)

    assert analogy == "a lock and key"
    assert proposal.candidate.source == "exact h"
    assert proposal.candidate.origin is CandidateOrigin.ANALOGICAL
    for invalid in (
        '{"analogy":"x","predicted_state":"y","tactics":["a"]}',
        '{"analogy":"x","predicted_state":"y","tactic":"a","extra":1}',
        '{"analogy":"x","predicted_state":"y","tactic":"a\\nb"}',
    ):
        try:
            parse_analogy_tactic(invalid)
        except RescueOutputError:
            pass
        else:
            raise AssertionError(f"accepted invalid schema: {invalid}")


def test_invalid_generations_retry_but_first_valid_response_gets_one_lean_call():
    model_calls = []
    lean_calls = []
    traces = []

    class Generator:
        def generate(self, messages, *, seed):
            model_calls.append((messages, seed))
            if len(model_calls) == 1:
                return "not json"
            assert "not json" in messages[-1].content
            assert "must be one JSON object" in messages[-1].content
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
    assert outcome.rollouts[1].analogy_trace == "a lock and key"
    assert outcome.rollouts[1].generations[0].repair is True
    assert [trace["kind"] for trace in traces] == [
        "analogy_model_call",
        "analogy_model_call",
    ]


def test_valid_tactic_rejection_ends_event_without_another_model_call():
    calls = []

    class Generator:
        def generate(self, _messages, *, seed):
            calls.append(seed)
            return '{"analogy":"a lock and key","predicted_state":"open","tactic":"bad"}'

    def evaluate(candidate):
        return TacticEvaluation(
            TacticResult(candidate, TacticStatus.ERROR, diagnostics=("rejected",))
        )

    outcome = _search(Generator(), evaluate, calls=5)

    assert len(calls) == 1
    assert len(outcome.rollouts) == 1
    assert outcome.rollouts[0].termination is RescueTermination.REJECTED
    assert len(outcome.rollouts[0].attempts) == 1


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

    fallback = AnalogyFallback(Generator(), max_model_calls=1, seed=7)

    def run(state):
        return fallback.search(
            state, GOAL, (), (), lambda _candidate: None, NeverCancelled()
        )

    run(SymbolicState("first", "P", "lean4"))
    run(SymbolicState("second", "Q", "lean4"))
    run(SymbolicState("first", "P", "lean4"))

    assert seeds[0] != seeds[1]
    assert seeds[0] == seeds[2]
