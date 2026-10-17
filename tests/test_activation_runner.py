"""Matched experiments must freeze blind selections and obey both budgets."""

from dataclasses import FrozenInstanceError, asdict, replace
import json
from pathlib import Path

import pytest

from analogy.experiments import activation_runner as runner
from analogy.experiments.activation_anchor import build_anchor
from analogy.experiments.activation_records import ActivationCondition as Arm, load_activation_eval_config
from analogy.inference.model_provider import CompletionActivationTrace, CompletionScore, LayerActivation
from analogy.lean_gym.models import Problem, ProofState, VerificationResult, VerificationStatus
from analogy.records import Embedding, SymbolicState
from analogy.solver.proof_views import ProofStructure
from analogy.solver.rescue import ANALOGY_FAMILIES

from test_activation_generation import source, alignment, transfer, tactics


ROOT = Path(__file__).parents[1]
STATE = "P : Prop\n⊢ P → P"
STRUCTURE = ProofStructure(1, '{"root":"implication","children":[{"root":"atom"},{"root":"target"}]}', ())


def verified(status, state=None):
    return VerificationResult(status, status in (VerificationStatus.OPEN, VerificationStatus.COMPLETE),
                              ProofState.from_text(state) if state else None, (), 0, 0)


class Lean:
    def __init__(self):
        self.allowed = True
        self.calls = []
        self.replays = []

    def initial_state(self, problem):
        assert self.allowed, "Lean leaked across phase boundary"
        return verified(VerificationStatus.OPEN, STATE)

    def state_after(self, problem, prefix):
        assert self.allowed, "Lean leaked across phase boundary"
        self.replays.append((problem.id, prefix))
        return verified(VerificationStatus.OPEN, STATE)

    def try_tactic(self, problem, prefix, tactic):
        assert self.allowed, "Lean leaked across phase boundary"
        self.calls.append((problem.id, prefix, tactic))
        if tactic == "selected":
            return verified(VerificationStatus.COMPLETE)
        if tactic == "prompted":
            return verified(VerificationStatus.OPEN, "P : Prop\nhp : P\n⊢ P")
        return verified(VerificationStatus.REJECTED)


class Provider:
    """Deterministic external model double, with an independently summed ledger."""
    model_revision = "fake-frozen"

    def __init__(self, *, failure=None, capture_passes=2):
        self.failure = failure
        self.capture_passes = capture_passes
        self.calls = []
        self.positions = 0

    def resolve_decoder_layers(self, count, *, through):
        return (0,)

    def count_token_positions(self, messages, completion):
        return 10 + (2 if completion else 0)

    def generate_with_usage(self, messages, *, seed):
        from analogy.inference.model_provider import GenerationResult
        text = self.generate(messages, seed=seed)
        try:
            return GenerationResult(text, self.count_token_positions(messages, text))
        except Exception as error:
            return GenerationResult(text, None, str(error))

    def generate(self, messages, *, seed):
        text = "\n".join(m.content for m in messages)
        self.calls.append(("generate", text, seed))
        if self.failure == "runtime":
            raise TimeoutError("unknown usage")
        self.positions += 12
        if messages[0].content.startswith("Generate one concrete non-proof"):
            return "{}" if self.failure == "malformed" else source()
        if messages[0].content.startswith("Align the declared"):
            return alignment()
        if messages[0].content.startswith("Transfer the complete"):
            need = json.loads(text.split("Current ProofNeed: ")[1].split("\n")[0])
            return transfer(need["active_subgoal"])
        if "Complete explicit candidate:" in text:
            payload = json.loads(text.split("Complete explicit candidate:\n")[1].split("\nReturn ")[0])
            return tactics("selected" if payload["alignment"]["mapping"][0][1] == "introduced_assumption" else "shuffled")
        if "analogy" in text.lower():
            family = next(f for f in ANALOGY_FAMILIES if f"[{f}]" in text)
            return json.dumps({"analogy": f"[{family}] example", "predicted_state": "next", "tactics": ["prompted"]})
        if "Propose ranked next tactics" in text:
            return '[{"tactic":"ordinary_bad","score":1.0}]'
        return tactics("neutral")

    def capture_completion_trace(self, messages, completion, *, layers):
        self.calls.append(("capture", completion, layers))
        self.positions += self.capture_passes * 12
        value = 2.0 if "Complete explicit candidate" in messages[-1].content else 1.0
        return CompletionActivationTrace(CompletionScore(completion, -2.0, -1.0, 2),
                                         (LayerActivation(0, (value, 0.0)),), 2, self.capture_passes * 12)

    def score_completion_with_delta(self, messages, completion, *, delta, layer):
        self.calls.append(("patch", completion, delta))
        self.positions += 12
        return CompletionScore(completion, -1.0, -0.5, 2)

    def score_completions(self, messages, completions):
        self.calls.append(("score", completions, messages))
        self.positions += 12 * len(completions)
        return tuple(CompletionScore(item, -3.0, -1.5, 2) for item in completions)

    def embed_text(self, text, *, layer):
        return Embedding("fake-frozen", (1.0, 0.5))


class Ordinary:
    def generate(self, messages, *, seed):
        return '[{"tactic":"bad","score":1.0}]'


def setup(*, failure=None, capture_passes=2, no_stuck=False):
    cfg = replace(load_activation_eval_config(ROOT / "config.activation-analogy.yaml"),
                  problem_ids=("p", "q"), seed_start=0, seed_count=1,
                  domain_cues=("mechanical",), analogy_candidates=1, activation_layer_count=1)
    app = replace(cfg.base_config, model=replace(cfg.base_config.model, max_new_tokens=20))
    problems = tuple(Problem(pid, pid, "(P : Prop)", "P → P", "test", "test", "imp", 1) for pid in cfg.problem_ids)
    lean = Lean()
    anchors = tuple(build_anchor(p, Ordinary(), lean, app, seed=0) for p in problems)
    if no_stuck:
        anchors = tuple(replace(a, no_stuck_anchor=True) for a in anchors)
    lean.calls.clear()
    lean.allowed = False
    provider = Provider(failure=failure, capture_passes=capture_passes)
    structures = {(a.problem_id, a.seed): STRUCTURE for a in anchors if not a.no_stuck_anchor}
    return cfg, app, problems, anchors, lean, provider, structures


def collect(data):
    cfg, app, _, anchors, _, provider, structures = data
    return runner.collect_prelean(cfg, app, anchors, provider, proof_structures=structures)


def test_all_cells_exist_before_calibration_and_frozen_winner_cannot_follow_lean(monkeypatch):
    data = setup()
    original = runner.fit_leave_one_problem_out
    fits = []

    def fit(vectors, components, **kwargs):
        assert {v.problem_id for v in vectors} == {"p", "q"}
        fits.append(len(data[5].calls))
        return original(vectors, components, **kwargs)

    monkeypatch.setattr(runner, "fit_leave_one_problem_out", fit)
    collection = collect(data)
    assert fits == []
    before = len(data[5].calls)
    frozen = runner.freeze_selections(collection)
    assert fits and len(data[5].calls) == before
    winner = frozen.cells[0].winner
    assert winner is not None
    with pytest.raises(FrozenInstanceError):
        frozen.cells[0].winner = None
    data[4].allowed = True
    records = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], lean_call_cap=2, max_topups=0)
    assert frozen.cells[0].winner == winner
    assert next(o for o in records[0].outcomes if o.condition is Arm.SELECTED).completed


def test_matched_outcomes_keep_immediate_utility_continuation_and_diversity_separate():
    data = setup()
    frozen = runner.freeze_selections(collect(data))
    data[4].allowed = True
    records = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], lean_call_cap=2, max_topups=0)
    record = records[0]
    outcomes = {o.condition: o for o in record.outcomes}
    assert outcomes[Arm.NEUTRAL].immediate_utility == "rejected"
    assert outcomes[Arm.PROMPTED].immediate_utility == "accepted_progressing"
    assert outcomes[Arm.PROMPTED].continuation_lean_calls == 1
    assert outcomes[Arm.PROMPTED].diversity[0].novelty == 1.0
    assert outcomes[Arm.SELECTED].immediate_utility == "completed"
    assert outcomes[Arm.SELECTED].continuation_lean_calls == 0
    assert outcomes[Arm.SHUFFLED].immediate_utility == "rejected"
    assert [o.condition for o in record.outcomes if o.completed] == [Arm.SELECTED]
    assert all(o.lean_calls <= 2 and o.final_tactic_cap == 4 for o in record.outcomes)
    assert all(prefix == () for _, prefix in data[4].replays)
    rescue_calls = [prefix for _, prefix, tactic in data[4].calls if tactic != "ordinary_bad"]
    assert set(rescue_calls) == {()}
    assert len(record.equal_compute_outcomes) == 4


def test_actual_capture_passes_charged_once_and_compute_topups_admitted_before_start():
    data = setup(capture_passes=2)
    collection = collect(data)
    # Per cell: neutral 12 + 4 candidate stages * 12 + two tactics *
    # (neutral capture 24 + analogy capture 24 + patch 12) = 180.
    assert [cell.processed_token_positions for cell in collection.cells] == [180, 180]
    assert data[5].positions == 360
    frozen = runner.freeze_selections(collection)
    data[4].allowed = True
    records = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], lean_call_cap=1, max_topups=100)
    for record in records:
        assert record.compute_ceiling == 192
        baseline = {o.condition: o for o in record.outcomes}
        compute = {o.condition: o for o in record.equal_compute_outcomes}
        for arm in (Arm.NEUTRAL, Arm.PROMPTED):
            assert baseline[arm].processed_token_positions == 12
            assert compute[arm].processed_token_positions == 168
            assert compute[arm].topup_estimates[-1] == 30
            assert compute[arm].topup_admitted[-1] is False
            assert len(compute[arm].generation_seeds) == 14
            assert set(compute[arm].generation_seeds[1:]).isdisjoint(baseline[arm].generation_seeds)
        assert compute[Arm.SELECTED].generation_seeds == baseline[Arm.SELECTED].generation_seeds
        assert compute[Arm.SHUFFLED].generation_seeds == baseline[Arm.SHUFFLED].generation_seeds
        assert all(o.lean_calls <= 1 for o in (*record.outcomes, *record.equal_compute_outcomes))


@pytest.mark.parametrize("failure,status", [("malformed", "failed_selection"), ("runtime", "invalid_usage")])
def test_failed_selection_never_falls_back_and_unknown_usage_excludes_entire_cell(failure, status):
    data = setup(failure=failure)
    frozen = runner.freeze_selections(collect(data))
    assert all(cell.status == status for cell in frozen.cells)
    data[4].allowed = True
    records = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], lean_call_cap=1, max_topups=0)
    for record in records:
        selected = next(o for o in record.outcomes if o.condition is Arm.SELECTED)
        assert selected.generated_tactics == () and selected.lean_calls == 0
        assert selected.immediate_utility == status
    if failure == "runtime":
        assert data[4].calls == []
        assert all(not r.rescue_eligible for r in records)


def test_no_anchor_cells_visible_serializable_and_never_touch_model_or_lean():
    data = setup(no_stuck=True)
    frozen = runner.freeze_selections(collect(data))
    records = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], lean_call_cap=2)
    assert data[5].calls == [] and data[4].calls == []
    assert all(not r.rescue_eligible and r.status == "no_stuck_anchor" for r in records)
    assert len(json.loads(json.dumps([asdict(r) for r in records]))) == 2


def test_missing_structure_and_incomplete_manifest_rejected_before_model_work():
    data = setup()
    with pytest.raises(ValueError, match="structure"):
        runner.collect_prelean(data[0], data[1], data[3], data[5], proof_structures={})
    with pytest.raises(ValueError, match="manifest"):
        runner.collect_prelean(data[0], data[1], data[3][:1], data[5], proof_structures=data[6])
    assert data[5].calls == []


def test_predecessor_likelihood_reuses_capture_score_and_charges_only_real_extra_pass():
    data = list(setup())
    data[3] = tuple(replace(a, predecessor_state=SymbolicState("previous", "⊢ previous", "lean4"),
                            accepted_prefix=("intro hp",), current_state=replace(a.current_state, context=("intro hp",)))
                    for a in data[3])
    collection = collect(data)
    assert [c.processed_token_positions for c in collection.cells] == [204, 204]
    assert data[5].positions == 408
    frozen = runner.freeze_selections(collection)
    assert all(c.winner.previous_state_value == 1.0 for c in frozen.cells)
    assert len([c for c in data[5].calls if c[0] == "score"]) == 4
    assert all("(omitted)" in c[2][-1].content for c in data[5].calls if c[0] == "score")
    assert runner.freeze_selections(collect(setup())).cells[0].winner.previous_state_value is None


@pytest.mark.parametrize("operation", ["capture", "patch", "final", "topup"])
def test_unknown_model_usage_excludes_cells_in_every_execution_stage(operation):
    data = list(setup())

    class Fails(Provider):
        def capture_completion_trace(self, *args, **kwargs):
            if operation == "capture":
                raise RuntimeError("capture failed after work")
            return super().capture_completion_trace(*args, **kwargs)

        def score_completion_with_delta(self, *args, **kwargs):
            if operation == "patch":
                raise RuntimeError("patch failed after work")
            return super().score_completion_with_delta(*args, **kwargs)

        def generate(self, messages, *, seed):
            if (operation == "topup" and seed % 2 == 1 and seed < 2**40) or (
                operation == "final" and "Use one concrete structural analogy" in messages[0].content
            ):
                raise RuntimeError("generation failed after work")
            return super().generate(messages, seed=seed)

    data[5] = Fails()
    frozen = runner.freeze_selections(collect(data))
    # Lean remains forbidden: invalid cells must never even replay the anchor.
    records = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], lean_call_cap=2)
    assert all(r.status == "invalid_usage" and not r.rescue_eligible for r in records)
    assert all(o.lean_calls == 0 for r in records for o in (*r.outcomes, *r.equal_compute_outcomes))


def test_unscorable_likelihoods_do_not_fall_back_to_other_generation():
    data = list(setup())

    class BadScores(Provider):
        def score_completion_with_delta(self, messages, completion, **kwargs):
            super().score_completion_with_delta(messages, completion, **kwargs)
            return CompletionScore("different tactic", -1.0, -0.5, 2)

    data[5] = BadScores()
    frozen = runner.freeze_selections(collect(data))
    assert all(c.status == "failed_selection" and c.winner is None for c in frozen.cells)
    assert all(c.measurements[0].diagnostics for c in frozen.cells)


def test_execution_rotation_leaves_condition_seeds_unchanged_and_views_preserve_raw_generations():
    data = setup()
    frozen = runner.freeze_selections(collect(data))
    data[4].allowed = True
    records = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], lean_call_cap=1, max_topups=1)
    assert runner.condition_order(0) == (Arm.NEUTRAL, Arm.PROMPTED, Arm.SELECTED, Arm.SHUFFLED)
    assert runner.condition_order(1) == (Arm.PROMPTED, Arm.SELECTED, Arm.SHUFFLED, Arm.NEUTRAL)
    for record in records:
        for base, compute in zip(record.outcomes, record.equal_compute_outcomes):
            assert compute.generation_seeds[0] == base.generation_seeds[0]
            assert base.generations[0].raw_outputs
            assert len(compute.generations) == (2 if base.condition in (Arm.NEUTRAL, Arm.PROMPTED) else 1)


def test_continuation_can_complete_but_is_distinct_from_immediate_rescue():
    data = setup()
    frozen = runner.freeze_selections(collect(data))

    class Completes(Lean):
        def try_tactic(self, problem, prefix, tactic):
            if tactic == "ordinary_bad":
                self.calls.append((problem.id, prefix, tactic))
                return verified(VerificationStatus.COMPLETE)
            return super().try_tactic(problem, prefix, tactic)

    records = runner.run_lean_conditions(frozen, data[2], Completes(), generator=data[5], lean_call_cap=2, max_topups=0)
    prompted = next(o for o in records[0].outcomes if o.condition is Arm.PROMPTED)
    assert prompted.completed and prompted.immediate_utility == "accepted_progressing"
    assert prompted.continuation_status == "complete"
    assert prompted.continuation_processed_token_positions == 12
    assert prompted.continuation_lean_calls == 1 and prompted.rescue_lean_calls == 1


def test_failed_diversity_measurement_does_not_block_valid_rescue_or_continuation():
    data = setup()
    frozen = runner.freeze_selections(collect(data))
    data[4].allowed = True

    class NoEmbedding(Provider):
        def embed_text(self, text, *, layer):
            raise RuntimeError("embedding unavailable")

    records = runner.run_lean_conditions(frozen, data[2], data[4], generator=NoEmbedding(), lean_call_cap=2, max_topups=0)
    prompted = next(o for o in records[0].outcomes if o.condition is Arm.PROMPTED)
    assert prompted.immediate_utility == "accepted_progressing"
    assert prompted.continuation_lean_calls == 1
    assert prompted.diversity == ()
    assert any("embedding unavailable" in d for d in prompted.diagnostics)


def test_extra_candidates_and_multiple_slate_entries_never_expand_lean_cap():
    data = list(setup())

    class Many(Provider):
        def generate(self, messages, *, seed):
            raw = super().generate(messages, seed=seed)
            payload = json.loads(raw)
            if isinstance(payload, dict) and "tactics" in payload:
                name = payload["tactics"][0]
                payload["tactics"] = [f"{name}_{seed}_{i}" for i in range(4)]
                return json.dumps(payload)
            return raw

    data[5] = Many()
    frozen = runner.freeze_selections(collect(data))
    data[4].allowed = True
    records = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], lean_call_cap=2, max_topups=2)
    for record in records:
        assert all(o.rescue_lean_calls == 2 for o in (*record.outcomes, *record.equal_compute_outcomes))
        for outcome in record.equal_compute_outcomes:
            assert len(outcome.generated_tactics) == (12 if outcome.condition in (Arm.NEUTRAL, Arm.PROMPTED) else 4)
    assert len(data[4].calls) == 32


def test_full_orchestration_collects_and_freezes_every_seed_before_first_lean_call():
    data = list(setup())
    data[0] = replace(data[0], seed_count=2)
    anchor_lean = Lean()
    data[3] = tuple(build_anchor(p, Ordinary(), anchor_lean, data[1], seed=seed) for p in data[2] for seed in (0, 1))
    data[6] = {(a.problem_id, a.seed): STRUCTURE for a in data[3]}

    class AuditedLean(Lean):
        def initial_state(self, problem):
            # Four cells, each containing two neutral and two analogy captures.
            assert len([call for call in data[5].calls if call[0] == "capture"]) == 16
            return super().initial_state(problem)

    output = runner.run_activation_evaluation(data[0], data[1], data[3], data[5], data[2], AuditedLean(),
                                              proof_structures=data[6], lean_call_cap=1, max_topups=0)
    assert len(output.collection.cells) == len(output.selections.cells) == len(output.trials) == 4
    assert output.trials[0].execution_order[0] is Arm.NEUTRAL
    assert output.trials[1].execution_order[0] is Arm.PROMPTED


def test_runtime_call_cap_mismatch_is_rejected_before_optional_generation():
    data = setup()
    frozen = runner.freeze_selections(collect(data))
    data[5].max_new_tokens = 1000
    before = len(data[5].calls)
    with pytest.raises(ValueError, match="completion allowance"):
        runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], max_topups=1)
    assert len(data[5].calls) == before


def test_held_out_vectors_cannot_indirectly_change_their_own_component_scales():
    data = list(setup())
    data[0] = replace(data[0], problem_ids=("p", "q", "r"))
    data[2] = (*data[2], replace(data[2][0], id="r"))
    data[3] = tuple(build_anchor(p, Ordinary(), Lean(), data[1], seed=0) for p in data[2])
    data[6] = {(a.problem_id, a.seed): STRUCTURE for a in data[3]}
    collection = collect(data)
    original = runner.freeze_selections(collection)
    held_out = collection.cells[0]
    changed = replace(held_out, neutral_traces=tuple(replace(t, activations=(LayerActivation(0, (100.0, 0.0)),))
                                                   for t in held_out.neutral_traces))
    mutated = runner.freeze_selections(replace(collection, cells=(changed, *collection.cells[1:])))
    assert mutated.cells[0].calibration == original.cells[0].calibration


def test_causal_ranking_change_and_malformed_candidate_evidence_are_serialized():
    data = list(setup())

    class RankingChange(Provider):
        def score_completion_with_delta(self, messages, completion, **kwargs):
            super().score_completion_with_delta(messages, completion, **kwargs)
            return CompletionScore(completion, -0.1 if completion == "selected" else -1.0, -0.05, 2)

    data[5] = RankingChange()
    frozen = runner.freeze_selections(collect(data))
    data[4].allowed = True
    records = runner.run_lean_conditions(frozen, data[2], data[4], generator=data[5], lean_call_cap=1, max_topups=0)
    assert records[0].causal_ranking_changes == ((frozen.cells[0].winner.candidate_id, True),)
    bad = setup(failure="malformed")
    frozen_bad = runner.freeze_selections(collect(bad))
    bad[4].allowed = True
    bad_records = runner.run_lean_conditions(frozen_bad, bad[2], bad[4], generator=bad[5], lean_call_cap=1, max_topups=0)
    assert bad_records[0].generation_failures[0].stage == "source"
    assert "raw_outputs" in asdict(bad_records[0])["generation_failures"][0]


def test_final_generation_accounting_error_cannot_become_zero_cost_selection():
    data = setup()
    frozen = runner.freeze_selections(collect(data))

    class BrokenCounter(Provider):
        def count_token_positions(self, messages, completion):
            if "Complete explicit candidate" in messages[-1].content:
                raise ValueError("token accounting failed after generation")
            return super().count_token_positions(messages, completion)

    records = runner.run_lean_conditions(frozen, data[2], data[4], generator=BrokenCounter(), max_topups=0)
    assert all(r.status == "invalid_usage" for r in records)
    assert all(next(o for o in r.outcomes if o.condition is Arm.SELECTED).processed_token_positions is None for r in records)


@pytest.mark.parametrize("stage", ["neutral", "candidate"])
def test_collection_accounting_failure_keeps_raw_evidence_and_continues_other_cells(stage):
    data = list(setup())

    class BrokenCollectionCounter(Provider):
        failed = False

        def count_token_positions(self, messages, completion):
            target = "Use Lean feedback" if stage == "neutral" else "Generate one concrete non-proof"
            if not self.failed and completion and messages[0].content.startswith(target):
                self.failed = True
                raise ValueError("collection accounting failed after model work")
            return super().count_token_positions(messages, completion)

    data[5] = BrokenCollectionCounter()
    collection = collect(data)
    broken, healthy = collection.cells
    assert broken.status == "invalid_usage" and broken.processed_token_positions is None
    assert healthy.status == "ready" and healthy.processed_token_positions == 180
    evidence = broken.neutral if stage == "neutral" else broken.candidates[0]
    assert evidence.raw_outputs[0]
    assert evidence.attempts[0].raw_text == evidence.raw_outputs[0]
    assert any("collection accounting" in (attempt.diagnostic or "") for attempt in evidence.attempts)
    assert any(c.processed_token_positions is None for c in broken.charges)


def test_admitted_topup_accounting_failure_excludes_only_affected_cell_and_preserves_evidence():
    data = setup()
    frozen = runner.freeze_selections(collect(data))

    class BrokenTopupCounter(Provider):
        fail_next_count = False
        failed = False

        def generate(self, messages, *, seed):
            self.fail_next_count = seed % 2 == 1 and seed < 2**40 and not self.failed
            return super().generate(messages, seed=seed)

        def count_token_positions(self, messages, completion):
            if self.fail_next_count and completion:
                self.failed = True
                self.fail_next_count = False
                raise ValueError("admitted topup accounting failed after model work")
            return super().count_token_positions(messages, completion)

    data[4].allowed = True
    records = runner.run_lean_conditions(frozen, data[2], data[4], generator=BrokenTopupCounter(), lean_call_cap=1, max_topups=1)
    broken, healthy = records
    assert broken.status == "invalid_usage" and not broken.rescue_eligible
    assert healthy.status == "ready" and healthy.rescue_eligible
    assert broken.compute_ceiling == healthy.compute_ceiling == 192
    failed_arm = next(o for o in broken.equal_compute_outcomes if o.processed_token_positions is None)
    assert failed_arm.topup_admitted == (True,)
    assert failed_arm.generations[-1].raw_outputs
    assert failed_arm.generations[-1].attempts[0].raw_text == failed_arm.generations[-1].raw_outputs[0]
    assert any("admitted topup accounting" in d for d in failed_arm.diagnostics)
    assert failed_arm.charges[-1].processed_token_positions is None
    assert all(o.lean_calls == 0 for o in (*broken.outcomes, *broken.equal_compute_outcomes))


@pytest.mark.parametrize("failure", ["timeout", "accounting"])
def test_unknown_continuation_usage_invalidates_trial_without_rewriting_selection_or_outcomes(failure):
    data = setup()
    frozen = runner.freeze_selections(collect(data))
    winners = tuple(c.winner for c in frozen.cells)

    class BrokenContinuation(Provider):
        failed = False

        def generate(self, messages, *, seed):
            if failure == "timeout" and not self.failed and "Propose ranked next tactics" in messages[0].content:
                self.failed = True
                raise TimeoutError("continuation usage unknown")
            return super().generate(messages, seed=seed)

        def count_token_positions(self, messages, completion):
            if failure == "accounting" and not self.failed and completion and "Propose ranked next tactics" in messages[0].content:
                self.failed = True
                raise ValueError("continuation usage unknown")
            return super().count_token_positions(messages, completion)

    data[4].allowed = True
    records = runner.run_lean_conditions(frozen, data[2], data[4], generator=BrokenContinuation(), lean_call_cap=2, max_topups=0)
    broken, healthy = records
    assert broken.status == "invalid_usage" and not broken.rescue_eligible
    assert healthy.status == "ready" and healthy.rescue_eligible
    assert broken.compute_ceiling == healthy.compute_ceiling == 192
    assert tuple(c.winner for c in frozen.cells) == winners
    assert broken.selected_candidate_id == winners[0].candidate_id
    prompted = next(o for o in broken.outcomes if o.condition is Arm.PROMPTED)
    assert prompted.immediate_utility == "accepted_progressing"
    assert prompted.continuation_processed_token_positions is None
    assert prompted.rescue_lean_calls == 1 and prompted.continuation_lean_calls == 0
    assert any("continuation" in d and "unknown" in d for d in broken.diagnostics)
    assert len(broken.outcomes) == len(broken.equal_compute_outcomes) == 4
