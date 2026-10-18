from analogy.model import prompts as prompts_module
from analogy.model.prompts import PromptCatalog
from analogy.records import (
    CandidateOrigin,
    DiscardReason,
    DiscardedSuccessor,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)
from analogy.lean_gym.models import VerificationStatus
from analogy.solver.lean_adapter import LeanInfrastructureResult
from analogy.solver.rescue import (
    AnalogyFallback,
    GenerationStatus,
    PredictionLabel,
    RescueOutputError,
    RescueTermination,
    TacticEvaluation,
    _analogy_correction_feedback,
    parse_analogy_tactic,
)
from analogy.solver.analogy_state import AnalogyScratchpad, AnalogyState
from analogy.solver.search import NeverCancelled
from analogy.solver.semantic import (
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
    return AnalogyFallback(
        generator,
        max_model_calls=calls,
        seed=7,
        trace_sink=None if traces is None else traces.append,
        analogy_scratchpad=scratchpad,
    ).search(
        STATE,
        GOAL,
        (),
        ordinary_attempts,
        evaluate,
        cancellation or NeverCancelled(),
        semantic_guidance,
        dependency_table,
        force_domain_change=force_domain_change,
    )


def _block(domain, mapping, steps, abandoned=()):
    return AnalogyState(domain, tuple(mapping), tuple(steps), tuple(abandoned)).render()


def test_stuck_search_can_revise_only_steps_and_commits_on_lean_progress():
    initial = AnalogyState("locksmithing", ("P -> lock",), ("Inspect it.",))
    scratchpad = AnalogyScratchpad(initial)
    successor = SymbolicState("next", "h : P\n⊢ P", "lean4")

    class Generator:
        def generate(self, messages, *, seed):
            assert messages[1].content == initial.render()
            return _block(
                "locksmithing", ("P -> lock",), ("Turn the matching key.",)
            ) + '\n{"action":"revise_steps","predicted_state":"h : P\\n⊢ P","tactic":"intro h"}'

    def evaluate(candidate):
        from analogy.records import SearchNode
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
            assert all(initial.render() not in message.content for message in messages)
            assert any("locksmithing" in message.content for message in messages)
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
    assert scratchpad.current is None
    assert scratchpad.abandoned_domains == ("locksmithing",)
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
    assert scratchpad.abandoned_domains == ("shipping",)
    assert outcome.rollouts[0].termination is RescueTermination.INVALID_OUTPUT


def test_update_repair_prompt_strips_previous_block_and_uses_action_schema():
    current = AnalogyState("locksmithing", ("P -> lock",), ("Inspect it.",))
    scratchpad = AnalogyScratchpad(current)
    calls = []

    class Generator:
        def generate(self, messages, *, seed):
            calls.append(messages)
            if len(calls) == 1:
                return current.render() + "\nnot json"
            feedback = messages[-1].content
            assert current.render() not in feedback
            assert feedback.count("<analogy>") == 1
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
    assert "account for it explicitly" in prompts[0]


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


def test_analogy_tactic_is_normalized_with_the_current_proof_state():
    raw = (
        '{"analogy":"unpack a pair","predicted_state":"H and U available",'
        '"tactic":"rcases hB with ⟨hA,hB⟩; exact hA"}'
    )
    state = "G H U : Prop\nhA : G\nhB : H ∧ U\n⊢ H"

    _, proposal = parse_analogy_tactic(raw, proof_state=state)

    assert proposal.candidate.source == "rcases hB with ⟨hH,hU⟩; exact hH"


def test_invalid_generations_retry_but_first_valid_response_gets_one_lean_call():
    model_calls = []
    lean_calls = []
    traces = []

    class Generator:
        def generate(self, messages, *, seed):
            model_calls.append((messages, seed))
            if len(model_calls) == 1:
                assert "Lean card:" in messages[1].content
                assert "SAT: no countermodel; continue from this state." in messages[1].content
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
    proposal = parse_analogy_tactic(
        '{"analogy":"lock","predicted_state":"SOLVED","tactic":"exact h"}'
    )[1]
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
        from analogy.records import SearchNode
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
        from analogy.records import SearchNode
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

    fallback = AnalogyFallback(Generator(), max_model_calls=1, seed=7)

    def run(state):
        return fallback.search(
            state,
            GOAL,
            (),
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
