"""Interpretable, corpus-free analogy streams for proof rescue."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .records import Message
from .records import CandidateOrigin, TacticCandidate, TacticStatus
from .rescue import (
    GenericRescueOutcome, PolicyActionRecord, ProofRescueContext, RescueAttempt,
    RescueProposal, RescueRollout, RescueTermination, TacticEvaluation,
)


class AnalogyOutputError(ValueError):
    pass


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalogyOutputError(f"{name} must be nonblank text")
    return value.strip()


@dataclass(frozen=True, slots=True)
class AnalogyHop:
    hop_id: str
    parent_id: str | None
    domain: str
    situation: str
    relation_chain: tuple[str, ...]
    mapping: tuple[tuple[str, str], ...]
    continuation: str
    predicted_consequence: str
    rationale: str
    stop: bool

    def __post_init__(self) -> None:
        for name in (
            "hop_id", "domain", "situation", "continuation",
            "predicted_consequence", "rationale",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.parent_id is not None:
            object.__setattr__(self, "parent_id", _text(self.parent_id, "parent_id"))
        if not self.relation_chain or any(not item.strip() for item in self.relation_chain):
            raise AnalogyOutputError("relation_chain must contain nonblank relations")
        if not self.mapping or any(not left.strip() or not right.strip() for left, right in self.mapping):
            raise AnalogyOutputError("mapping must contain nonblank pairs")
        if not isinstance(self.stop, bool):
            raise AnalogyOutputError("stop must be a boolean")


@dataclass(frozen=True, slots=True)
class AnalogyStream:
    hops: tuple[AnalogyHop, ...]

    def __post_init__(self) -> None:
        for index, hop in enumerate(self.hops):
            expected = None if index == 0 else self.hops[index - 1].hop_id
            if hop.parent_id != expected:
                raise ValueError("analogy hops must form one parent-linked chain")


@dataclass(frozen=True, slots=True)
class AnalogyTacticPlan:
    predicted_state: str
    tactics: tuple[str, ...]
    motivating_hop_id: str


_HOP_KEYS = {
    "domain", "situation", "relation_chain", "mapping", "continuation",
    "predicted_consequence", "rationale", "stop",
}


def parse_analogy_hop(
    raw: str, *, hop_id: str = "hop-0", parent_id: str | None = None
) -> AnalogyHop:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise AnalogyOutputError("analogy hop must be one JSON object") from error
    if not isinstance(payload, dict) or set(payload) != _HOP_KEYS:
        raise AnalogyOutputError("analogy hop has missing or unknown keys")
    relations = payload["relation_chain"]
    mapping = payload["mapping"]
    if not isinstance(relations, list) or not all(isinstance(item, str) for item in relations):
        raise AnalogyOutputError("relation_chain must be a string list")
    if not isinstance(mapping, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in mapping.items()
    ):
        raise AnalogyOutputError("mapping must be a text mapping")
    return AnalogyHop(
        hop_id,
        parent_id,
        payload["domain"],
        payload["situation"],
        tuple(relations),
        tuple(mapping.items()),
        payload["continuation"],
        payload["predicted_consequence"],
        payload["rationale"],
        payload["stop"],
    )


def render_analogy_hop_prompt(
    context: ProofRescueContext, stream: AnalogyStream
) -> tuple[Message, ...]:
    history = json.dumps([asdict(hop) for hop in stream.hops], sort_keys=True)
    return (
        Message("system", "Generate one explicit analogy step, not a tactic."),
        Message(
            "user",
            f"Target: {context.goal.expression}\nCurrent proof state: {context.state.expression}\n"
            f"Accepted prefix: {list(context.accepted_tactics)}\nFailed tactics: {list(context.failed_tactics)}\n"
            f"Prior analogy hops: {history}\nReturn exactly domain, situation, relation_chain, mapping, "
            "continuation, predicted_consequence, rationale, and boolean stop as JSON.",
        ),
    )


def render_analogy_tactic_prompt(
    context: ProofRescueContext, stream: AnalogyStream
) -> tuple[Message, ...]:
    return (
        Message("system", "Decode the cited analogy continuation into Lean tactics."),
        Message(
            "user",
            f"Current proof state: {context.state.expression}\nAnalogy stream: "
            f"{json.dumps([asdict(hop) for hop in stream.hops], sort_keys=True)}\n"
            "Return exactly predicted_state, tactics, and motivating_hop_id as JSON.",
        ),
    )


def parse_analogy_tactic_plan(raw: str, stream: AnalogyStream) -> AnalogyTacticPlan:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise AnalogyOutputError("tactic plan must be one JSON object") from error
    if not isinstance(payload, dict) or set(payload) != {
        "predicted_state", "tactics", "motivating_hop_id"
    }:
        raise AnalogyOutputError("tactic plan has missing or unknown keys")
    tactics = payload["tactics"]
    if not isinstance(tactics, list) or not tactics or not all(
        isinstance(item, str) and item.strip() and "\n" not in item for item in tactics
    ):
        raise AnalogyOutputError("tactics must be nonblank single-line strings")
    citation = _text(payload["motivating_hop_id"], "motivating_hop_id")
    if citation not in {hop.hop_id for hop in stream.hops}:
        raise AnalogyOutputError("motivating_hop_id must cite the supplied stream")
    return AnalogyTacticPlan(
        _text(payload["predicted_state"], "predicted_state"), tuple(tactics), citation
    )


def discounted_stream_samples(
    actions: tuple[object, ...],
    rollout: RescueRollout,
    final_reward: float,
    hop_cost: float,
    discount: float,
) -> tuple[object, ...]:
    """Attach a discounted verified return to every action in a stream."""
    from .rl import TrainingSample

    if not 0.0 < discount <= 1.0 or hop_cost < 0.0:
        raise ValueError("discount and hop cost are out of range")
    count = len(actions)
    return tuple(
        TrainingSample(
            rollout,
            action,
            discount ** (count - index - 1) * final_reward
            - hop_cost * (count - index - 1),
        )
        for index, action in enumerate(actions)
    )


class AnalogyStreamRunner:
    """Bounded corpus-free analogy generation followed by Lean-only decoding."""

    def __init__(
        self, *, encoder: object, policy: object, generator: object,
        max_hops: int, tactics_per_rollout: int, max_model_calls: int,
        max_rescue_verifier_calls: int, base_seed: int, hidden_layer: int,
    ) -> None:
        if min(max_hops, tactics_per_rollout, max_model_calls, max_rescue_verifier_calls) <= 0:
            raise ValueError("stream budgets must be positive")
        self.encoder, self.policy, self.generator = encoder, policy, generator
        self.max_hops, self.tactics_per_rollout = max_hops, tactics_per_rollout
        self.max_model_calls = max_model_calls
        self.max_rescue_verifier_calls = max_rescue_verifier_calls
        self.base_seed, self.hidden_layer = base_seed, hidden_layer
        self.live_actions: tuple[object, ...] = ()

    def search(self, state, goal, tactic_pool, ordinary_attempts, evaluator, cancellation):
        del tactic_pool
        context = ProofRescueContext(
            state, goal, state.context,
            tuple(item.candidate.source for item in ordinary_attempts),
        )
        representation = self.encoder.encode(context)
        hops: list[AnalogyHop] = []
        actions: list[object] = []
        calls = 0
        for index in range(self.max_hops):
            if cancellation.is_cancelled():
                return GenericRescueOutcome(cancelled=True)
            action = self._sample(representation, index)
            actions.append(action)
            raw = self.generator.generate_with_delta(
                render_analogy_hop_prompt(context, AnalogyStream(tuple(hops))),
                delta=action.delta.detach(), layer=self.hidden_layer,
                seed=self.base_seed + index,
            )
            calls += 1
            try:
                hop = parse_analogy_hop(
                    raw, hop_id=f"hop-{index}",
                    parent_id=hops[-1].hop_id if hops else None,
                )
            except AnalogyOutputError:
                return self._failed(state, hops, actions, RescueTermination.INVALID_OUTPUT)
            hops.append(hop)
            if hop.stop and index >= 0:
                break
        stream = AnalogyStream(tuple(hops))
        if calls >= self.max_model_calls:
            return self._failed(state, hops, actions, RescueTermination.BUDGET_EXHAUSTED)
        raw_plan = self.generator.generate_with_delta(
            render_analogy_tactic_prompt(context, stream),
            delta=actions[-1].delta.detach(), layer=self.hidden_layer,
            seed=self.base_seed + self.max_hops,
        )
        try:
            plan = parse_analogy_tactic_plan(raw_plan, stream)
        except AnalogyOutputError:
            return self._failed(state, hops, actions, RescueTermination.INVALID_OUTPUT)
        proposals = tuple(
            RescueProposal(TacticCandidate(t, 1.0 / min(len(plan.tactics), self.tactics_per_rollout), CandidateOrigin.ANALOGICAL), plan.predicted_state)
            for t in plan.tactics[:self.tactics_per_rollout]
        )
        attempts = []
        termination = RescueTermination.EXHAUSTED
        completed = None
        successor = None
        for proposal in proposals[:self.max_rescue_verifier_calls]:
            evaluation = evaluator(proposal.candidate)
            attempts.append(RescueAttempt(proposal, evaluation.result, evaluation.discarded))
            if evaluation.completed_proof is not None:
                termination, completed = RescueTermination.COMPLETE, evaluation.completed_proof
                break
            if evaluation.successor is not None:
                termination, successor = RescueTermination.PROGRESS, evaluation.successor
                break
        if attempts and all(item.result.status is TacticStatus.ERROR for item in attempts):
            termination = RescueTermination.REJECTED
        rollout = self._rollout(state, stream, actions, proposals, tuple(attempts), termination)
        self.live_actions = tuple(actions)
        return GenericRescueOutcome(completed, successor, (rollout,))

    def _sample(self, representation, index):
        import torch
        parameter = next(self.policy.parameters())
        generator = torch.Generator(device=parameter.device).manual_seed(self.base_seed + index)
        return self.policy.sample(representation, generator)

    def _rollout(self, state, stream, actions, proposals, attempts, termination):
        action = actions[0]
        record = PolicyActionRecord(
            self.base_seed, float(action.delta.detach().norm()),
            float(action.log_probability.detach()), float(action.entropy.detach()),
        )
        return RescueRollout(
            f"{state.id}:analogy", state.id, record, tuple(proposals), tuple(attempts),
            termination, analogy_stream=stream,
        )

    def _failed(self, state, hops, actions, termination):
        stream = AnalogyStream(tuple(hops))
        self.live_actions = tuple(actions)
        rollout = self._rollout(state, stream, actions, (), (), termination)
        return GenericRescueOutcome(rollouts=(rollout,))


class StreamRescuePolicy:
    """Compatibility wrapper around the stable direction/radius policy head.

    The runner resamples it at every hop; the readable hop history remains in the
    prompt and artifacts, while the proof representation anchors every action.
    """

    def __new__(cls, *args, **kwargs):
        from .rl import DirectRescuePolicy
        return DirectRescuePolicy(*args, **kwargs)


__all__ = [
    "AnalogyHop", "AnalogyOutputError", "AnalogyStream", "AnalogyTacticPlan",
    "parse_analogy_hop", "parse_analogy_tactic_plan", "render_analogy_hop_prompt",
    "render_analogy_tactic_prompt",
    "AnalogyStreamRunner", "StreamRescuePolicy", "discounted_stream_samples",
]
