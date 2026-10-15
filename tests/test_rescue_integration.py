from __future__ import annotations

import shutil

import pytest
import torch

from analogy.lean_gym.models import Problem
from analogy.lean_gym.resources import LEAN_PROJECT_ROOT
from analogy.lean_gym.verifier import LeanGymAdapter, LeanSubprocessVerifier
from analogy.loop import (
    AcceptNovelState,
    LocalRescueSearch,
    NeverCancelled,
    ObservableStucknessPolicy,
    StateIdFingerprinter,
    WhitespaceTacticCanonicalizer,
)
from analogy.records import (
    CandidateOrigin,
    Embedding,
    SearchStatus,
    SymbolicState,
    TacticCandidate,
    TacticResult,
    TacticStatus,
)
from analogy.rescue import DirectRescueRunner, ProofRepresentation, ProofRescueContext
from analogy.rl import PolicyAction


class StaticEncoder:
    def encode(self, context):
        return ProofRepresentation(
            context,
            "language",
            "structure",
            Embedding("model", (1.0, 0.0)),
            Embedding("model", (0.0, 1.0)),
        )


class ZeroPolicy(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.anchor = torch.nn.Parameter(torch.tensor(0.0))

    def sample(self, representation, generator):
        scalar = self.anchor * 0.0
        return PolicyAction(torch.zeros(2), scalar, scalar)


class StaticGenerator:
    def __init__(self, tactic):
        self.tactic = tactic

    def generate_with_delta(self, messages, *, delta, layer, seed):
        return (
            '{"predicted_state":"disjunction cases exposed","tactics":["'
            + self.tactic
            + '"]}'
        )


class StateAwareRanker:
    def __init__(self):
        self.initial_id = None

    def rank(self, state, goal):
        if self.initial_id is None:
            self.initial_id = state.id
        tactic = "exact missing" if state.id == self.initial_id else "all_goals simp_all"
        return (TacticCandidate(tactic, 1.0, CandidateOrigin.ORDINARY),)


class MappingRanker:
    def __init__(self, mapping):
        self.mapping = mapping

    def rank(self, state, goal):
        return self.mapping.get(state.id, ())


class MappingVerifier:
    def __init__(self, mapping):
        self.mapping = mapping

    def run_tactic(self, state, candidate):
        return self.mapping[(state.id, candidate.source)]


def symbolic_state(identifier):
    return SymbolicState(identifier, f"goal {identifier}", "lean4")


def ordinary_candidate(source):
    return TacticCandidate(source, 1.0, CandidateOrigin.ORDINARY)


def runner(tactic="cases h"):
    return DirectRescueRunner(
        encoder=StaticEncoder(),
        policy=ZeroPolicy(),
        generator=StaticGenerator(tactic),
        rollouts_per_state=1,
        tactics_per_rollout=1,
        max_rescue_verifier_calls=1,
        base_seed=7,
        hidden_layer=-1,
    )


def test_direct_runner_returns_fake_lean_progress_to_ordinary_search() -> None:
    bad = ordinary_candidate("bad")
    generated = TacticCandidate("cases h", 1.0, CandidateOrigin.ANALOGICAL)
    finish = ordinary_candidate("finish")
    verifier = MappingVerifier(
        {
            ("start", "bad"): TacticResult(bad, TacticStatus.ERROR),
            ("start", "cases h"): TacticResult(
                generated, TacticStatus.ACCEPTED, symbolic_state("split")
            ),
            ("split", "finish"): TacticResult(
                finish, TacticStatus.COMPLETE, proof="proof"
            ),
        }
    )
    search = LocalRescueSearch(
        tactic_ranker=MappingRanker({"start": (bad,), "split": (finish,)}),
        verifier=verifier,
        stuckness_policy=ObservableStucknessPolicy(),
        curiosity_search=None,
        rollout_rescue=runner(),
        state_fingerprinter=StateIdFingerprinter(),
        tactic_canonicalizer=WhitespaceTacticCanonicalizer(),
        progress_policy=AcceptNovelState(),
        cancellation=NeverCancelled(),
    )

    result = search.search(symbolic_state("start"), symbolic_state("goal"))

    assert result.status is SearchStatus.COMPLETE
    assert len(result.ordinary_expansions) == 2
    assert len(result.rescue_rollouts) == 1


@pytest.mark.skipif(shutil.which("lake") is None, reason="lake is unavailable")
def test_direct_runner_and_real_lean_resume_ordinary_search() -> None:
    problem = Problem(
        "rescue_or_swap",
        "Or swap",
        "(P Q : Prop) (h : P ∨ Q)",
        "Q ∨ P",
        "fixture.lean",
        "fixture",
        "or",
        1,
    )
    adapter = LeanGymAdapter(problem, LeanSubprocessVerifier(LEAN_PROJECT_ROOT))
    search = LocalRescueSearch(
        tactic_ranker=StateAwareRanker(),
        verifier=adapter,
        stuckness_policy=ObservableStucknessPolicy(),
        curiosity_search=None,
        rollout_rescue=runner(),
        state_fingerprinter=StateIdFingerprinter(),
        tactic_canonicalizer=WhitespaceTacticCanonicalizer(),
        progress_policy=AcceptNovelState(),
        cancellation=NeverCancelled(),
        ordinary_width=1,
        max_expansions=4,
    )

    result = search.search(
        adapter.initial_state(),
        SymbolicState("goal", problem.target, "lean4"),
    )

    assert result.status is SearchStatus.COMPLETE
    assert result.proof is not None
    assert "cases h" in result.proof
    assert "all_goals simp_all" in result.proof
