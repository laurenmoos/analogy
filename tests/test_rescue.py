from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from analogy.records import (
    CandidateOrigin,
    DiscardReason,
    DiscardedSuccessor,
    SearchNode,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)
from analogy.rescue import (
    GenericRescueOutcome,
    PolicyActionRecord,
    ProofRescueContext,
    RescueAttempt,
    RescueOutputError,
    RescueProposal,
    RescueRollout,
    RescueTermination,
    parse_rescue_proposals,
    render_direct_rescue_prompt,
)


def state(identifier: str, expression: str | None = None) -> SymbolicState:
    return SymbolicState(identifier, expression or f"goal {identifier}", "lean4")


def candidate(source: str = "simp") -> TacticCandidate:
    return TacticCandidate(source, 1.0, CandidateOrigin.ANALOGICAL)


def action() -> PolicyActionRecord:
    return PolicyActionRecord(seed=7, delta_norm=0.25, log_probability=-0.5, entropy=1.0)


def attempt(status: TacticStatus, *, discarded: bool = False) -> RescueAttempt:
    selected = candidate()
    proposal = RescueProposal(selected, "smaller goal")
    next_state = state("next") if status is TacticStatus.ACCEPTED else None
    result = TacticResult(
        selected,
        status,
        state=next_state,
        proof="by simp" if status is TacticStatus.COMPLETE else None,
    )
    dropped = (
        DiscardedSuccessor(result, DiscardReason.REPEATED_STATE)
        if discarded
        else None
    )
    return RescueAttempt(proposal, result, dropped)


def test_parse_rescue_proposals_returns_new_analogical_candidates() -> None:
    parsed = parse_rescue_proposals(
        '{"predicted_state":"one goal remains","tactics":["cases h","simp_all"]}',
        limit=2,
    )

    assert [item.candidate.source for item in parsed] == ["cases h", "simp_all"]
    assert all(item.candidate.origin is CandidateOrigin.ANALOGICAL for item in parsed)
    assert all(item.predicted_state == "one goal remains" for item in parsed)
    assert sum(item.candidate.score for item in parsed) == pytest.approx(1.0)


def test_parser_validates_every_tactic_before_truncating() -> None:
    with pytest.raises(RescueOutputError, match="multiline"):
        parse_rescue_proposals(
            '{"predicted_state":"goal","tactics":["simp","exact h\\nassumption"]}',
            limit=1,
        )


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "{}",
        '{"predicted_state":"","tactics":["simp"]}',
        '{"predicted_state":"goal","tactics":[]}',
        '{"predicted_state":"goal","tactics":["simp\\nexact h"]}',
        '{"predicted_state":"goal","tactics":["simp"," simp "]}',
        '{"predicted_state":"goal","tactics":["simp"],"extra":true}',
        '[{"predicted_state":"goal","tactics":["simp"]}]',
        '{"predicted_state":"goal","tactics":["sim\\u0000p"]}',
    ],
)
def test_parse_rescue_proposals_rejects_malformed_output(text: str) -> None:
    with pytest.raises(RescueOutputError):
        parse_rescue_proposals(text, limit=2)


@pytest.mark.parametrize("limit", [True, False, 0, -1, 1.5])
def test_parse_rescue_proposals_requires_positive_integer_limit(limit: object) -> None:
    with pytest.raises(ValueError, match="limit"):
        parse_rescue_proposals(
            '{"predicted_state":"goal","tactics":["simp"]}',
            limit=limit,  # type: ignore[arg-type]
        )


def test_direct_rescue_prompt_has_canonical_sections_and_json_contract() -> None:
    context = ProofRescueContext(
        state=SymbolicState("s", "P : Prop\nh : P\n⊢ P", "lean4", ("intro h",)),
        goal=state("g", "P"),
        accepted_tactics=("intro h",),
        failed_tactics=("exact missing",),
    )

    prompt = render_direct_rescue_prompt(context)

    headings = [
        "Overall target:",
        "Current goals:",
        "Accepted prefix:",
        "Failed tactics:",
    ]
    assert [prompt.index(heading) for heading in headings] == sorted(
        prompt.index(heading) for heading in headings
    )
    assert 'exactly the keys "predicted_state" and "tactics"' in prompt
    assert "intro h" in prompt
    assert "exact missing" in prompt


def test_rescue_records_are_immutable_and_normalize_iterables() -> None:
    context = ProofRescueContext(
        state("s"),
        state("g"),
        (item for item in ["intro h"]),  # type: ignore[arg-type]
        (item for item in ["simp"]),  # type: ignore[arg-type]
    )
    assert context.accepted_tactics == ("intro h",)
    with pytest.raises(FrozenInstanceError):
        context.state = state("other")  # type: ignore[misc]


@pytest.mark.parametrize("field", ["delta_norm", "log_probability", "entropy"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True])
def test_action_metadata_requires_finite_real_values(field: str, value: object) -> None:
    values: dict[str, object] = {
        "seed": 7,
        "delta_norm": 0.0,
        "log_probability": 0.0,
        "entropy": 0.0,
    }
    values[field] = value
    with pytest.raises(ValueError, match="finite"):
        PolicyActionRecord(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("seed", [True, -1, 1.5])
def test_action_metadata_requires_nonnegative_integer_seed(seed: object) -> None:
    with pytest.raises(ValueError, match="seed"):
        PolicyActionRecord(seed, 0.0, 0.0, 0.0)  # type: ignore[arg-type]


def test_rescue_attempt_requires_result_for_its_proposal_candidate() -> None:
    proposal = RescueProposal(candidate("simp"), "goal")
    result = TacticResult(candidate("aesop"), TacticStatus.ERROR)
    with pytest.raises(ValueError, match="candidate"):
        RescueAttempt(proposal, result)


def test_rescue_attempt_requires_discard_to_reference_result() -> None:
    first = candidate("simp")
    second = candidate("aesop")
    result = TacticResult(first, TacticStatus.ACCEPTED, state("next"))
    other = TacticResult(second, TacticStatus.ACCEPTED, state("other"))
    discarded = DiscardedSuccessor(other, DiscardReason.REPEATED_STATE)
    with pytest.raises(ValueError, match="discarded"):
        RescueAttempt(RescueProposal(first, "goal"), result, discarded)


@pytest.mark.parametrize("field", ["rollout_id", "state_id"])
def test_rollout_rejects_blank_ids(field: str) -> None:
    values = {
        "rollout_id": "rollout-1",
        "state_id": "state-1",
        "action": action(),
        "proposals": (),
        "attempts": (),
        "termination": RescueTermination.EXHAUSTED,
    }
    values[field] = " "
    with pytest.raises(ValueError, match=field):
        RescueRollout(**values)  # type: ignore[arg-type]


def test_rollout_rejects_invalid_termination_value() -> None:
    with pytest.raises(ValueError, match="termination"):
        RescueRollout("r", "s", action(), (), (), "complete")  # type: ignore[arg-type]


def test_complete_rollout_requires_a_completing_attempt() -> None:
    rejected = attempt(TacticStatus.ERROR)
    with pytest.raises(ValueError, match="complete"):
        RescueRollout(
            "r", "s", action(), (rejected.proposal,), (rejected,), RescueTermination.COMPLETE
        )


@pytest.mark.parametrize(
    "progress_attempt",
    [attempt(TacticStatus.ERROR), attempt(TacticStatus.ACCEPTED, discarded=True)],
)
def test_progress_rollout_requires_frontier_successor(progress_attempt: RescueAttempt) -> None:
    with pytest.raises(ValueError, match="progress"):
        RescueRollout(
            "r",
            "s",
            action(),
            (progress_attempt.proposal,),
            (progress_attempt,),
            RescueTermination.PROGRESS,
        )


def test_rollout_reward_must_be_finite() -> None:
    with pytest.raises(ValueError, match="reward"):
        RescueRollout(
            "r", "s", action(), (), (), RescueTermination.EXHAUSTED, math.nan
        )


def test_generic_outcome_forbids_conflicting_or_cancelled_results() -> None:
    node = SearchNode(state("next"), 0.5)
    with pytest.raises(ValueError, match="terminal"):
        GenericRescueOutcome("by simp", node)
    with pytest.raises(ValueError, match="cancelled"):
        GenericRescueOutcome(successor=node, cancelled=True)
