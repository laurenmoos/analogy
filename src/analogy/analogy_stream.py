"""Interpretable, corpus-free analogy streams for proof rescue."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .records import Message
from .rescue import ProofRescueContext


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


__all__ = [
    "AnalogyHop", "AnalogyOutputError", "AnalogyStream", "AnalogyTacticPlan",
    "parse_analogy_hop", "parse_analogy_tactic_plan", "render_analogy_hop_prompt",
    "render_analogy_tactic_prompt",
]
