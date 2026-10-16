"""Neutral anchors stop the shared search and replay the same accepted branch."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from analogy.config import SearchConfig, load_config
from analogy.experiments.activation_anchor import (
    build_anchor, build_anchor_manifest, replay_anchor,
)
from analogy.lean_gym.models import (
    Diagnostic, Problem, ProofState, VerificationResult, VerificationStatus,
)
from analogy.records import CandidateOrigin, TacticCandidate, TacticStatus


ROOT = Path(__file__).resolve().parents[1]
PROBLEM = Problem("anchor", "test", "(P : Prop)", "P → P", "test", "test", "imp", 1)
INITIAL = "P : Prop\n⊢ P → P"
AFTER_INTRO = "P : Prop\nhp : P\n⊢ P"


def config(*, width=1, steps=10):
    return replace(load_config(ROOT / "config.yaml"), search=SearchConfig(width, steps, 1))


def result(status, text=None, *, diagnostic=None):
    return VerificationResult(
        status, status in (VerificationStatus.OPEN, VerificationStatus.COMPLETE),
        None if text is None else ProofState.from_text(text),
        () if diagnostic is None else (Diagnostic("error", None, diagnostic, None, None),),
        0, 0,
    )


class Generator:
    def __init__(self, slates):
        self.slates = iter(slates)
        self.calls = []

    def generate(self, messages, *, seed):
        self.calls.append((messages, seed))
        return json.dumps([{"tactic": tactic, "score": 1.0} for tactic in next(self.slates)])


class Verifier:
    def __init__(self, transitions=None):
        self.transitions = transitions or {}
        self.initializations = []
        self.attempts = []
        self.reconstructions = []
        self.replay_text = AFTER_INTRO

    def initial_state(self, problem):
        self.initializations.append(problem.id)
        return result(VerificationStatus.OPEN, INITIAL)

    def try_tactic(self, problem, prefix, tactic):
        self.attempts.append((problem.id, prefix, tactic))
        return self.transitions.get((prefix, tactic), result(VerificationStatus.REJECTED, diagnostic="unknown tactic"))

    def state_after(self, problem, prefix):
        self.reconstructions.append((problem.id, prefix))
        return result(VerificationStatus.OPEN, self.replay_text)


def later_anchor():
    verifier = Verifier({((), "intro hp"): result(VerificationStatus.OPEN, AFTER_INTRO)})
    return build_anchor(PROBLEM, Generator([("intro hp",), ("bad",)]), verifier, config(), seed=7)


def test_first_stuck_cancels_even_with_an_unvisited_frontier(monkeypatch):
    # Without sink cancellation the pending deeper branch would generate again.
    import analogy.experiments.activation_anchor as module
    real_episode = module.run_episode
    observed = []

    def run(context, initial, goal):
        output = real_episode(context, initial, goal)
        observed.append((context.cancellation.is_cancelled(), len(output.stuckness_decisions)))
        return output

    monkeypatch.setattr(module, "run_episode", run)
    verifier = Verifier({
        ((), "intro hp"): result(VerificationStatus.OPEN, AFTER_INTRO),
        (("intro hp",), "left"): result(VerificationStatus.OPEN, "⊢ left"),
        (("intro hp",), "right"): result(VerificationStatus.OPEN, "⊢ right"),
        (("intro hp", "left"), "deeper"): result(VerificationStatus.OPEN, "⊢ deeper"),
    })
    generator = Generator([("intro hp",), ("left", "right"), ("deeper",), ("bad",)])

    anchor = build_anchor(PROBLEM, generator, verifier, config(width=2), seed=7)

    assert observed == [(True, 4)]
    assert len(generator.calls) == 4
    assert anchor.current_state.expression == "⊢ right"
    # The last expanded state was 'left'; the graph parent was the intro state.
    assert anchor.predecessor_state.expression == AFTER_INTRO
    assert anchor.predecessor_state.context == ("intro hp",)
    assert anchor.accepted_prefix == ("intro hp", "right")
    assert anchor.current_state.context == anchor.accepted_prefix
    assert anchor.failed_tactics == ("bad",)
    assert any("unknown tactic" in item for item in anchor.diagnostics)
    assert [seed for _, seed in generator.calls] == [7, 7, 7, 7]


def test_root_stuckness_never_manufactures_a_predecessor():
    anchor = build_anchor(PROBLEM, Generator([("bad",)]), Verifier(), config(), seed=0)
    assert anchor.predecessor_state is None
    assert anchor.accepted_prefix == ()
    assert anchor.current_state.expression == INITIAL
    assert anchor.no_stuck_anchor is False


def test_every_condition_replays_exact_state_and_prefix_in_a_fresh_adapter():
    anchor = later_anchor()
    verifier = Verifier({(("intro hp",), "exact hp"): result(VerificationStatus.COMPLETE)})
    adapters = [replay_anchor(anchor, PROBLEM, verifier) for _ in range(4)]
    assert len({id(adapter) for adapter in adapters}) == 4
    for adapter in adapters:
        output = adapter.run_tactic(anchor.current_state, TacticCandidate("exact hp", 1.0, CandidateOrigin.ORDINARY))
        assert output.status is TacticStatus.COMPLETE
    assert verifier.initializations == ["anchor"] * 4
    assert verifier.reconstructions == [("anchor", ("intro hp",))] * 4
    assert verifier.attempts == [("anchor", ("intro hp",), "exact hp")] * 4


def test_completed_conventional_search_remains_visible_but_cannot_be_rescued():
    verifier = Verifier({((), "assumption"): result(VerificationStatus.COMPLETE)})
    anchor = build_anchor(PROBLEM, Generator([("assumption",)]), verifier, config(), seed=5)
    assert anchor.no_stuck_anchor is True
    assert anchor.predecessor_state is None
    assert anchor.accepted_prefix == anchor.failed_tactics == ()
    with pytest.raises(ValueError, match="no stuck anchor"):
        replay_anchor(anchor, PROBLEM, verifier)


def test_manifest_keeps_solved_cells_and_deterministic_problem_seed_order():
    verifier = Verifier({((), "assumption"): result(VerificationStatus.COMPLETE)})
    other = replace(PROBLEM, id="other")
    manifest = build_anchor_manifest(
        (PROBLEM, other), (3, 8), Generator([("assumption",)] * 4), verifier, config(),
    )
    assert isinstance(manifest, tuple)
    assert [(item.problem_id, item.seed, item.no_stuck_anchor) for item in manifest] == [
        ("anchor", 3, True), ("anchor", 8, True), ("other", 3, True), ("other", 8, True),
    ]


def test_anchor_hashes_are_stable_and_bind_the_recorded_context():
    anchor = later_anchor()
    assert anchor == later_anchor()
    assert len(anchor.state_hash) == len(anchor.anchor_hash) == 64
    changed = build_anchor(PROBLEM, Generator([("other_bad",)]), Verifier(), config(), seed=7)
    assert changed.anchor_hash != anchor.anchor_hash
    assert any(item.startswith("ordinary_prompt_sha256:") for item in anchor.diagnostics)


@pytest.mark.parametrize("mutation", [
    {"problem_id": "other"}, {"theorem": "different theorem"},
    {"accepted_prefix": ("different",)}, {"state_hash": "corrupted"},
    {"anchor_hash": "corrupted"},
])
def test_replay_rejects_mismatched_or_tampered_anchor(mutation):
    with pytest.raises(ValueError):
        replay_anchor(replace(later_anchor(), **mutation), PROBLEM, Verifier())


def test_replay_rejects_drifted_lean_state():
    verifier = Verifier()
    verifier.replay_text = "⊢ something else"
    with pytest.raises(ValueError, match="reconstruct"):
        replay_anchor(later_anchor(), PROBLEM, verifier)


def test_budget_exhaustion_is_not_misreported_as_solved_without_stuckness():
    verifier = Verifier({((), "intro hp"): result(VerificationStatus.OPEN, AFTER_INTRO)})
    with pytest.raises(RuntimeError, match="before.*stuck"):
        build_anchor(PROBLEM, Generator([("intro hp",)]), verifier, config(steps=1), seed=0)
