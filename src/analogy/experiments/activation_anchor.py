"""Freeze one neutral first-stuck state for every matched intervention arm."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from typing import Sequence

from ..config import AppConfig
from ..inference.model_provider import TextGenerator
from ..inference.prompts import configure_prompt_catalog
from ..lean_gym.models import Problem, VerificationStatus, normalize_proof_state_text
from ..lean_gym.verifier import Verifier
from ..records import ExpansionOutcome, SearchStatus, StucknessDecision, SymbolicState, TacticStatus
from ..solver.episode import EpisodeContext, run_episode
from ..solver.lean_adapter import LeanGymAdapter
from ..solver.model_policy import TacticRanker
from ..solver.search import (
    AcceptNovelState, CanonicalStateFingerprinter, ObservableStucknessPolicy,
    WhitespaceTacticCanonicalizer,
)
from .activation_records import AnchorRecord


@dataclass
class _FirstStuck:
    """The episode's sink and cancellation token share the same capture flag."""

    outcome: ExpansionOutcome | None = None

    def is_cancelled(self) -> bool:
        return self.outcome is not None

    def capture(
        self, state: SymbolicState, goal: SymbolicState,
        outcome: ExpansionOutcome, decision: StucknessDecision,
    ) -> None:
        if self.outcome is None:
            self.outcome = outcome


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _anchor_hash(anchor: AnchorRecord) -> str:
    # Bind every persisted field, including the ordinary prompt digest, without
    # making replay depend on a mutable prompt catalog or model configuration.
    payload = asdict(anchor)
    del payload["anchor_hash"]
    return _hash(payload)


def build_anchor(
    problem: Problem, generator: TextGenerator, verifier: Verifier,
    app_config: AppConfig, *, seed: int,
) -> AnchorRecord:
    """Run conventional search once, stopping at its first observable stuckness.

    A solved-before-stuck record retains the initial state as provenance because
    AnchorRecord requires a state. Its flag prevents use as a rescue anchor.
    Initialization, generation, and search-budget failures propagate rather than
    pretending a missing anchor was a conventional proof completion. Typed Lean
    tactic infrastructure failures retain their labels in the diagnostics.
    """
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    configure_prompt_catalog(app_config.prompts.path)
    lean = LeanGymAdapter(problem, verifier)
    initial = lean.initial_state()
    goal = SymbolicState(f"{problem.id}:goal", problem.target, "lean4")
    capture = _FirstStuck()
    calls: list[dict] = []
    result = run_episode(
        EpisodeContext(
            tactic_ranker=TacticRanker(
                generator, top_k=app_config.search.top_k, seed=seed,
                failed_tactics_for=lean.failed_tactics_for, trace_sink=calls.append,
            ),
            verifier=lean,
            stuckness_policy=ObservableStucknessPolicy(),
            state_fingerprinter=CanonicalStateFingerprinter(),
            tactic_canonicalizer=WhitespaceTacticCanonicalizer(),
            progress_policy=AcceptNovelState(),
            cancellation=capture,
            ordinary_width=app_config.search.top_k,
            max_expansions=app_config.search.max_steps,
            stuck_state_sink=capture,
        ),
        initial, goal,
    )

    predecessor = None
    failed: tuple[str, ...] = ()
    diagnostics: list[str] = []
    if capture.outcome is None:
        if result.status is not SearchStatus.COMPLETE:
            raise RuntimeError(f"neutral search ended {result.status.value} before completion or stuckness")
        current = initial
        no_stuck = True
    else:
        ordinary = capture.outcome
        current = ordinary.node.state
        no_stuck = False
        parent_id = ordinary.node.parent_state_id
        if parent_id is not None:
            # Read graph identities only. Expansion order is not branch ancestry.
            states = {expansion.node.state.id: expansion.node.state for expansion in result.ordinary_expansions}
            states.update({node.state.id: node.state for expansion in result.ordinary_expansions for node in expansion.successors})
            if parent_id not in states:
                raise ValueError("anchor predecessor is missing from the expansion graph")
            predecessor = states[parent_id]
            transition = ordinary.node.tactic
            if transition is None or predecessor.context + (transition,) != current.context:
                raise ValueError("accepted graph transition does not match anchor prefix")
        failed = tuple(attempt.candidate.source for attempt in ordinary.attempts if attempt.status is TacticStatus.ERROR)
        for attempt in ordinary.attempts:
            infrastructure = getattr(attempt, "infrastructure_failure", None)
            label = infrastructure or attempt.status.value
            diagnostics.extend(f"{attempt.candidate.source} [{label}]: {message}" for message in attempt.diagnostics)

    diagnostics.extend(f"ordinary_prompt_sha256:{_hash(call['messages'])}" for call in calls)
    anchor = AnchorRecord(
        problem_id=problem.id, seed=seed, theorem=problem.theorem_header(),
        current_state=current, predecessor_state=predecessor,
        accepted_prefix=current.context, failed_tactics=failed,
        diagnostics=tuple(diagnostics),
        state_hash=CanonicalStateFingerprinter().fingerprint(current),
        anchor_hash="pending", no_stuck_anchor=no_stuck,
    )
    return replace(anchor, anchor_hash=_anchor_hash(anchor))


def build_anchor_manifest(
    problems: Sequence[Problem], seeds: Sequence[int],
    generator: TextGenerator, verifier: Verifier, app_config: AppConfig,
) -> tuple[AnchorRecord, ...]:
    """Freeze every problem/seed cell in caller-supplied order, including solves."""
    return tuple(
        build_anchor(problem, generator, verifier, app_config, seed=seed)
        for problem in problems for seed in seeds
    )


def replay_anchor(anchor: AnchorRecord, problem: Problem, verifier: Verifier) -> LeanGymAdapter:
    """Verify the frozen prefix in a fresh adapter and register its exact state."""
    if anchor.no_stuck_anchor:
        raise ValueError("no stuck anchor is available for rescue replay")
    if anchor.problem_id != problem.id or anchor.theorem != problem.theorem_header():
        raise ValueError("anchor problem or theorem does not match")
    current = anchor.current_state
    if current.language != "lean4" or current.context != anchor.accepted_prefix:
        raise ValueError("anchor state does not match its accepted prefix")
    if anchor.state_hash != CanonicalStateFingerprinter().fingerprint(current):
        raise ValueError("anchor state hash does not match")
    if anchor.anchor_hash != _anchor_hash(anchor):
        raise ValueError("anchor hash does not match")

    lean = LeanGymAdapter(problem, verifier)
    lean.initial_state()
    reconstructed = verifier.state_after(problem, anchor.accepted_prefix)
    if (
        reconstructed.status is not VerificationStatus.OPEN
        or not reconstructed.accepted
        or reconstructed.proof_state is None
        or normalize_proof_state_text(reconstructed.proof_state.normalized)
        != normalize_proof_state_text(current.expression)
    ):
        raise ValueError("accepted prefix does not reconstruct the recorded anchor state")
    lean.register_replay_state(current)
    return lean


__all__ = ["build_anchor", "build_anchor_manifest", "replay_anchor"]
