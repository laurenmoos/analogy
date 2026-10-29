import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from analogy.main import load_config
from analogy.lean_gym.models import Problem
from analogy.model.prompts import PromptError
from analogy.utils.checkpoint import load_checkpoint
from analogy.utils.records import SearchStatus, SolveStatus, SolveSummary, SymbolicState
from analogy.solver import run as run_module
from analogy.solver.report.build import (
    _problem_result,
    _problem_route,
    build_problem_record,
)
from analogy.solver.report.render import render_problem_trace
from analogy.solver.run import (
    run_analogy_assisted,
    run_deliberation_assisted,
    run_vanilla,
)
from analogy.solver.search import SearchResult
from analogy.solver.proof.semantic import (
    SemanticRecoveryEvent,
    SemanticResult,
)


ROOT = Path(__file__).parents[1]


def test_run_module_exports_the_three_explicit_recipes() -> None:
    assert "run_vanilla" in run_module.__all__
    assert "run_analogy_assisted" in run_module.__all__
    assert "run_deliberation_assisted" in run_module.__all__


@pytest.mark.parametrize(
    "recipe", (run_vanilla, run_analogy_assisted, run_deliberation_assisted)
)
def test_run_recipe_rejects_invalid_prompts_before_creating_artifacts(
    tmp_path, recipe
) -> None:
    config = load_config(ROOT / "config.yaml")
    output_directory = tmp_path / "output"
    config = replace(
        config,
        prompts=replace(config.prompts, path=tmp_path / "missing-prompts.yaml"),
        run=replace(config.run, output_dir=output_directory),
    )
    problem = Problem("plain", "demo", "", "P", "test", "test", "atom", 1)

    with pytest.raises(PromptError, match="could not be read"):
        recipe(config, (problem,), object(), object())

    assert not output_directory.exists()


def test_vanilla_run_has_no_analogy_state_or_fallback(
    tmp_path, monkeypatch
) -> None:
    policies = []
    fallbacks = []

    class FakeLean:
        def __init__(self, problem, verifier):
            self.problem = problem

        def failed_tactics_for(self, _state_id):
            return ()

        def initial_state(self):
            return SymbolicState(f"{self.problem.id}:initial", "P : Prop\n⊢ P", "lean4")

        def goal_state(self):
            return SymbolicState(f"{self.problem.id}:goal", "⊢ P", "lean4")

    class FakeSearch:
        result = SearchResult(SearchStatus.EXHAUSTED, None)

        def has_work(self):
            return False

    monkeypatch.setattr(run_module, "initialize_proof", FakeLean)
    monkeypatch.setattr(run_module, "build_proof_belief", lambda _search: object())
    monkeypatch.setattr(
        run_module,
        "initialize_tactic_generator",
        lambda *args, **kwargs: policies.append(kwargs.get("analogy_scratchpad")) or object(),
    )
    monkeypatch.setattr(
        run_module,
        "initialize_search",
        lambda *args, **kwargs: fallbacks.append(kwargs.get("analogy_fallback")) or FakeSearch(),
    )
    config = load_config(ROOT / "config.yaml")
    config = replace(
        config,
        analogy=replace(config.analogy, enabled=False),
        run=replace(config.run, output_dir=tmp_path),
    )
    problem = Problem("plain", "demo", "", "P", "test", "test", "atom", 1)

    result = run_vanilla(
        config,
        (problem,),
        object(),
        object(),
    )

    assert result.summary.run_id == "analogy-off"
    assert policies == [None]
    assert fallbacks == [None]


def test_analogy_assisted_initializes_ordinary_search_without_analogy_model(
    tmp_path, monkeypatch
) -> None:
    pairs = []
    rankers = []

    class FakeLean:
        def __init__(self, problem, verifier):
            self.problem = problem

        def failed_tactics_for(self, _state_id):
            return ()

        def initial_state(self):
            return SymbolicState(f"{self.problem.id}:initial", "P : Prop\n⊢ P", "lean4")

        def goal_state(self):
            return SymbolicState(f"{self.problem.id}:goal", "⊢ P", "lean4")

    class FakeSearch:
        result = SearchResult(SearchStatus.EXHAUSTED, None)

        def has_work(self):
            return False

    monkeypatch.setattr(run_module, "initialize_proof", FakeLean)
    monkeypatch.setattr(run_module, "build_proof_belief", lambda _search: object())

    monkeypatch.setattr(
        run_module,
        "initialize_analogy_harness",
        lambda *_args, **_kwargs: pytest.fail(
            "analogy must not initialize before search is stuck"
        ),
    )
    monkeypatch.setattr(
        run_module,
        "initialize_tactic_generator",
        lambda *_args, **_kwargs: rankers.append(object()) or rankers[-1],
    )
    monkeypatch.setattr(
        run_module,
        "initialize_search",
        lambda *args, **kwargs: pairs.append(
            (
                args[-1],
                kwargs["analogy_fallback"],
                kwargs["analogy_recovery_enabled"],
                kwargs["initial_state"],
                kwargs["goal_state"],
            )
        )
        or FakeSearch(),
    )
    config = load_config(ROOT / "config.yaml")
    config = replace(
        config,
        analogy=replace(config.analogy, enabled=True),
        run=replace(config.run, output_dir=tmp_path),
    )
    problems = tuple(
        Problem(f"p{index}", "demo", "", "P", "test", "test", "atom", 1)
        for index in range(2)
    )

    result = run_analogy_assisted(
        config,
        problems,
        object(),
        object(),
    )

    assert result.summary.run_id == "analogy-on"
    assert len(pairs) == 2
    first_ranker, first_fallback, recovery_enabled, first_initial, first_goal = pairs[0]
    second_ranker, _, _, _, _ = pairs[1]
    assert first_ranker is rankers[0]
    assert first_fallback is None
    assert recovery_enabled is True
    assert first_ranker is not second_ranker
    assert first_initial.id == "p0:initial"
    assert first_goal.id == "p0:goal"


def test_assisted_stuck_loop_initializes_for_exact_rescue_state(
    tmp_path, monkeypatch
) -> None:
    calls = []
    first = SimpleNamespace(
        id="first", scratchpad=object(), tactic_generator=object(), fallback=object()
    )
    harness = SimpleNamespace(tracks=(first,))

    class FakeLean:
        def __init__(self, problem, verifier):
            pass

        def initial_state(self):
            return SymbolicState("initial", "P : Prop\n⊢ P", "lean4")

        def goal_state(self):
            return SymbolicState("goal", "⊢ P", "lean4")

    class FakeSearch:
        result = SearchResult(SearchStatus.EXHAUSTED, None)
        goal = SymbolicState("goal", "⊢ P", "lean4")
        rescue_node = SimpleNamespace(
            state=SymbolicState("rescue", "Q : Prop\nh : Q\n⊢ Q", "lean4")
        )

        def __init__(self):
            self.cycles = 0

        def has_work(self):
            self.cycles += 1
            return self.cycles <= 2

        def is_stuck(self):
            return True

    monkeypatch.setattr(run_module, "initialize_proof", FakeLean)
    monkeypatch.setattr(run_module, "build_proof_belief", lambda _search: object())
    def initialize_harness(*args, **_kwargs):
        calls.append(("initialize_analogy", args[4]))
        return harness

    monkeypatch.setattr(run_module, "initialize_analogy_harness", initialize_harness)
    monkeypatch.setattr(
        run_module,
        "update_analogies",
        lambda search, value: calls.append(("update_analogies", value)) or value,
    )
    monkeypatch.setattr(
        run_module, "initialize_tactic_generator", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        run_module,
        "initialize_search",
        lambda *_args, **_kwargs: FakeSearch(),
    )
    monkeypatch.setattr(
        run_module,
        "generate_and_score_tactics",
        lambda search: object(),
    )
    monkeypatch.setattr(
        run_module,
        "submit_tactics_to_lean",
        lambda proof, tactics: object(),
    )
    monkeypatch.setattr(
        run_module,
        "update_search_with_scored_proof_states",
        lambda search, result: calls.append(("update_search", result)),
    )
    monkeypatch.setattr(
        run_module,
        "activate_analogy",
        lambda search, track: calls.append(("activate", track)),
    )
    monkeypatch.setattr(
        run_module,
        "generate_analogy_rescue",
        lambda search, scratchpad, **kwargs: calls.append(
            ("rescue", scratchpad, kwargs)
        )
        or object(),
    )
    monkeypatch.setattr(
        run_module,
        "submit_analogy_rescue_to_lean",
        lambda proof, rescue: calls.append(("submit_rescue", rescue))
        or "verified",
    )
    config = load_config(ROOT / "config.yaml")
    config = replace(config, run=replace(config.run, output_dir=tmp_path))
    problem = Problem("p", "demo", "", "P", "test", "test", "atom", 1)

    run_analogy_assisted(config, (problem,), object(), object())

    lifecycle = {
        "activate",
        "rescue",
        "submit_rescue",
    }
    stuck_calls = [name for name, *_ in calls if name in lifecycle]
    assert stuck_calls[-3:] == [
        "activate",
        "rescue",
        "submit_rescue",
    ]
    assert ("initialize_analogy", FakeSearch.rescue_node.state) in calls
    assert ("activate", first) in calls
    assert ("rescue", first.scratchpad, {}) in calls
    assert len([item for item in calls if item[0] == "initialize_analogy"]) == 1
    assert calls.count(("update_analogies", harness)) == 2
    assert len([item for item in calls if item[0] == "submit_rescue"]) == 2


def test_assisted_run_fails_open_when_lazy_analogy_initialization_fails(
    tmp_path, monkeypatch
) -> None:
    abandoned = []

    class FakeLean:
        def __init__(self, problem, verifier):
            pass

        def initial_state(self):
            return SymbolicState("initial", "P : Prop\n⊢ P", "lean4")

        def goal_state(self):
            return SymbolicState("goal", "⊢ P", "lean4")

    class FakeSearch:
        result = SearchResult(SearchStatus.EXHAUSTED, None)
        goal = SymbolicState("goal", "⊢ P", "lean4")
        rescue_node = SimpleNamespace(
            state=SymbolicState("rescue", "P : Prop\n⊢ P", "lean4")
        )

        def __init__(self):
            self.cycles = 0

        def has_work(self):
            self.cycles += 1
            return self.cycles == 1

        def is_stuck(self):
            return True

        def abandon_pending_rescue(self):
            abandoned.append(self.rescue_node.state.id)

    monkeypatch.setattr(run_module, "initialize_proof", FakeLean)
    monkeypatch.setattr(run_module, "build_proof_belief", lambda _search: object())
    monkeypatch.setattr(
        run_module, "initialize_tactic_generator", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        run_module, "initialize_search", lambda *_args, **_kwargs: FakeSearch()
    )
    monkeypatch.setattr(
        run_module, "generate_and_score_tactics", lambda _search: object()
    )
    monkeypatch.setattr(
        run_module, "submit_tactics_to_lean", lambda _proof, _tactics: object()
    )
    monkeypatch.setattr(
        run_module, "update_search_with_scored_proof_states", lambda *_args: None
    )
    monkeypatch.setattr(
        run_module,
        "initialize_analogy_harness",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            run_module.AnalogyHarnessInitializationError("invalid analogy")
        ),
    )
    config = load_config(ROOT / "config.yaml")
    config = replace(config, run=replace(config.run, output_dir=tmp_path))
    problem = Problem("p", "demo", "", "P", "test", "test", "atom", 1)

    result = run_analogy_assisted(config, (problem,), object(), object())

    assert abandoned == ["rescue"]
    assert result.summary.results[0].status is SolveStatus.EXHAUSTED


def test_assisted_run_caches_failed_initialization_by_normalized_stuck_state(
    tmp_path, monkeypatch
) -> None:
    calls = []
    events = []

    class FakeLean:
        def __init__(self, problem, verifier): pass
        def initial_state(self): return SymbolicState("initial", "⊢ P", "lean4")
        def goal_state(self): return SymbolicState("goal", "⊢ P", "lean4")

    class FakeSearch:
        result = SearchResult(SearchStatus.EXHAUSTED, None)
        goal = SymbolicState("goal", "⊢ P", "lean4")
        rescue_node = SimpleNamespace(
            state=SymbolicState("rescue", "case h.h\nP : Prop\n⊢ P", "lean4")
        )
        def __init__(self): self.cycles = 0
        def has_work(self):
            self.cycles += 1
            return self.cycles <= 2
        def is_stuck(self): return True
        def abandon_pending_rescue(self): calls.append("abandon")

    monkeypatch.setattr(run_module, "initialize_proof", FakeLean)
    monkeypatch.setattr(run_module, "initialize_tactic_generator", lambda *_a, **_k: object())
    monkeypatch.setattr(run_module, "initialize_search", lambda *_a, **_k: FakeSearch())
    monkeypatch.setattr(run_module, "generate_and_score_tactics", lambda _s: object())
    monkeypatch.setattr(run_module, "submit_tactics_to_lean", lambda _p, _t: object())
    monkeypatch.setattr(run_module, "update_search_with_scored_proof_states", lambda *_a: None)
    monkeypatch.setattr(run_module, "build_proof_belief", lambda _s: object())
    def fail(*_args, **_kwargs):
        calls.append("initialize")
        raise run_module.AnalogyStoryInitializationError("invalid")
    monkeypatch.setattr(run_module, "initialize_analogy_harness", fail)
    original_build = run_module.build_problem_record
    def capture(problem, search, model_events, failure):
        events.extend(model_events)
        return original_build(problem, search, model_events, failure)
    monkeypatch.setattr(run_module, "build_problem_record", capture)

    config = load_config(ROOT / "config.yaml")
    config = replace(config, run=replace(config.run, output_dir=tmp_path))
    problem = Problem("p", "demo", "", "P", "test", "test", "atom", 1)
    run_analogy_assisted(config, (problem,), object(), object())

    assert calls == ["initialize", "abandon", "abandon"]
    assert [event["kind"] for event in events].count(
        "analogy_initialization_cache_hit"
    ) == 1

    calls.clear()
    events.clear()
    def tactic_fail(*_args, **_kwargs):
        calls.append("initialize")
        raise run_module.AnalogyHarnessInitializationError("invalid tactic JSON")
    monkeypatch.setattr(run_module, "initialize_analogy_harness", tactic_fail)
    run_analogy_assisted(config, (problem,), object(), object())
    assert calls == ["initialize", "abandon", "initialize", "abandon"]
    assert not any(
        event["kind"] == "analogy_initialization_cache_hit" for event in events
    )


def test_solve_artifact_uses_summary_key(tmp_path, monkeypatch) -> None:
    config = load_config(ROOT / "config.yaml")
    config = replace(config, run=replace(config.run, output_dir=tmp_path))
    run = run_module.run_analogy_assisted(config, (), object(), object())
    payload = json.loads((run.output_directory / "run.json").read_text())

    assert payload["summary"]["results"] == []
    assert "inference" not in payload


def test_problem_record_preserves_generation_seeds():
    problem = Problem("p", "demo", "", "P", "test", "test", "atom", 1)

    record = build_problem_record(
        problem,
        None,
        [{"kind": "model_call", "seed": 17}],
        RuntimeError("failed"),
    )

    assert record.payload["model_events"] == [
        {"kind": "model_call", "seed": 17}
    ]


def test_checkpoint_identity_distinguishes_run_seed(tmp_path):
    base = load_config(ROOT / "config.yaml")
    problem = Problem("p", "demo", "", "P", "test", "test", "atom", 1)
    zero = replace(
        base,
        run=replace(base.run, output_dir=tmp_path / "runs", seed=0),
    )
    one = replace(zero, run=replace(zero.run, seed=1))

    seed_zero = load_checkpoint(zero, (problem,))
    seed_one = load_checkpoint(one, (problem,))

    assert seed_zero.signature != seed_one.signature


def test_checkpoint_identity_distinguishes_solver_condition(tmp_path):
    base = load_config(ROOT / "config.yaml")
    config = replace(
        base,
        run=replace(base.run, output_dir=tmp_path / "runs"),
    )
    problem = Problem("p", "demo", "", "P", "test", "test", "atom", 1)

    ordinary = load_checkpoint(config, (problem,), condition="ordinary")
    deliberation = load_checkpoint(
        config, (problem,), condition="deliberation"
    )

    assert ordinary.signature != deliberation.signature


def test_checkpoint_identity_hashes_calibration_contents(tmp_path):
    base = load_config(ROOT / "config.yaml")
    calibration = tmp_path / "calibration.npz"
    calibration.write_bytes(b"first")
    config = replace(
        base,
        analogy=replace(base.analogy, calibration_path=calibration),
        run=replace(base.run, output_dir=tmp_path / "runs"),
    )
    problem = Problem("p", "demo", "", "P", "test", "test", "atom", 1)

    first = load_checkpoint(config, (problem,), condition="analogy")
    calibration.write_bytes(b"second")
    second = load_checkpoint(config, (problem,), condition="analogy")

    assert first.signature != second.signature


def test_run_artifact_records_effective_config_hashes_and_model_revisions(
    tmp_path,
) -> None:
    config = load_config(ROOT / "config.yaml")
    config = replace(config, run=replace(config.run, output_dir=tmp_path))
    ordinary = SimpleNamespace(model_revision="ordinary@abc")
    analogy = SimpleNamespace(model_revision="analogy@def")

    run = run_analogy_assisted(
        config,
        (),
        ordinary,
        object(),
        analogy_generator=analogy,
    )
    payload = json.loads((run.output_directory / "run.json").read_text())

    assert payload["run_seed"] == config.run.seed
    assert payload["effective_config"]["model"]["name"] == config.model.name
    assert payload["model_revision"] == "ordinary@abc"
    assert payload["analogy_model_revision"] == "analogy@def"
    assert len(payload["prompts_sha256"]) == 64
    assert len(payload["corpus_sha256"]) == 64
    assert len(payload["calibration_sha256"]) == 64
    assert payload["story_candidate_cache"] == {
        "path": str(tmp_path / "story-candidates-v1.json"),
        "sha256": None,
        "schema_version": 1,
    }


def test_problem_report_shows_analogy_as_one_fallback_inside_shared_search():
    detail = {
        "problem": {"id": "demo", "binders": "", "target": "P"},
        "outcome": {
            "status": "complete",
            "proof": "by exact h",
            "failure": None,
            "verifier_call_count": 2,
            "invalid_tactic_count": 1,
            "stuck_state_count": 1,
            "analogy_fallback_count": 1,
        },
        "search": {
            "ordinary_expansions": [],
            "semantic_recoveries": [
                {
                    "event_id": "semantic:0",
                    "trigger_state_id": "bad-child",
                    "result": "countermodel",
                    "countermodel": (("G", False), ("H", True)),
                    "rescue_state_id": "parent",
                    "abandoned_tactics": ("left",),
                    "error": None,
                }
            ],
            "rescue_rollouts": [
                {
                    "semantic_event_id": "semantic:0",
                    "termination": "complete",
                    "analogy_trace": "a key fitting a lock",
                    "generations": [
                        {
                            "seed": 5,
                            "status": "valid",
                            "repair": False,
                            "error_kind": None,
                            "diagnostic": None,
                            "structural_distance": 0.125,
                            "structural_coupling": ((0.5, 0.5),),
                        }
                    ],
                    "attempts": [
                        {
                            "proposal": {"candidate": {"source": "exact h"}},
                            "result": {
                                "status": "complete",
                                "diagnostics": [],
                                "proof": "by exact h",
                            },
                        }
                    ],
                }
            ],
            "diversity_observations": [],
        },
    }

    report = render_problem_trace(detail, [], route="analogy-assisted")

    assert "Analogy fallback 1 — complete" in report
    assert "a key fitting a lock" in report
    assert "Analogy generations: **1**" in report
    assert "Analogy tactics submitted to Lean: **1**" in report
    assert "Structural distance: **0.125**" in report
    assert "Fixed-order alignment" in report
    assert "Structural coupling" not in report
    assert "Semantic recovery 1 — countermodel" in report
    assert "Trigger state: `bad-child`" in report
    assert "Countermodel: `G=false, H=true`" in report
    assert "Rescue state: `parent`" in report
    assert "Abandoned tactics: `left`" in report
    assert report.index("Semantic recovery 1") < report.index("Analogy fallback 1")
    assert "Direct feedback" not in report


def test_problem_report_handles_failure_before_search_starts():
    detail = {
        "problem": {"id": "broken", "binders": "", "target": "P"},
        "outcome": {
            "status": "model_failure",
            "proof": None,
            "failure": "invalid JSON",
            "verifier_call_count": 0,
            "invalid_tactic_count": 0,
            "stuck_state_count": 0,
            "analogy_fallback_count": 0,
        },
        "search": None,
    }

    report = render_problem_trace(detail, [], route="unsolved")

    assert "**Failure:** invalid JSON" in report
    assert "Search did not start." in report


def test_problem_route_handles_failure_before_search_starts():
    assert _problem_route(SimpleNamespace(status="model_failure"), None) == "unsolved"


def test_problem_report_labels_invalid_generation_retries_and_diagnostics():
    detail = {
        "problem": {"id": "retry", "binders": "", "target": "P"},
        "outcome": {
            "status": "exhausted",
            "proof": None,
            "failure": None,
            "verifier_call_count": 0,
            "invalid_tactic_count": 0,
            "stuck_state_count": 1,
            "analogy_fallback_count": 0,
        },
        "search": {
            "ordinary_expansions": [],
            "rescue_rollouts": [
                {
                    "termination": "invalid_output",
                    "attempts": [],
                    "generations": [
                        {
                            "seed": 7,
                            "status": "invalid",
                            "repair": True,
                            "error_kind": "schema",
                            "diagnostic": "missing tactic",
                        }
                    ],
                }
            ],
        },
    }
    events = [
        {
            "kind": "ordinary_model_call",
            "responses": [
                {
                    "seed": 3,
                    "raw_text": "not json",
                    "status": "invalid",
                    "repair": True,
                    "error_kind": "schema",
                    "diagnostic": "expected object",
                }
            ],
        }
    ]

    report = render_problem_trace(detail, events, route="unsolved")

    assert "Response — invalid — retry" in report
    assert "schema: expected object" in report
    assert "Generation — invalid — retry" in report
    assert "schema: missing tactic" in report
    assert "Analogy generations: **1**" in report
    assert "Analogy tactics submitted to Lean: **0**" in report


def test_problem_report_renders_post_selection_analogy_tactic_score():
    detail = {
        "problem": {"id": "score", "binders": "", "target": "P"},
        "outcome": {
            "status": "exhausted",
            "proof": None,
            "failure": None,
            "verifier_call_count": 0,
            "invalid_tactic_count": 0,
            "stuck_state_count": 1,
        },
        "search": {"ordinary_expansions": [], "rescue_rollouts": []},
    }
    events = [
        {
            "kind": "analogy_tactic_score",
            "state_id": "stuck",
            "generation_index": 2,
            "structural_rank": 1,
            "tactic": "exact h",
            "total_log_likelihood": -1.5,
            "mean_log_likelihood": -0.5,
            "token_count": 3,
            "error": None,
        }
    ]

    report = render_problem_trace(detail, events, route="unsolved")

    assert "Post-selection tactic score" in report
    assert "Structural rank: **1**" in report
    assert "`exact h`" in report
    assert "mean log-probability: **-0.5**" in report


def test_one_semantic_event_groups_multiple_analogy_generations() -> None:
    detail = {
        "problem": {"id": "retry", "binders": "", "target": "P"},
        "outcome": {
            "status": "exhausted",
            "proof": None,
            "failure": None,
            "verifier_call_count": 0,
            "invalid_tactic_count": 0,
            "stuck_state_count": 1,
            "analogy_fallback_count": 0,
        },
        "search": {
            "ordinary_expansions": [],
            "semantic_recoveries": [
                {
                    "event_id": "semantic:0",
                    "trigger_state_id": "state",
                    "result": "valid",
                    "countermodel": (),
                    "rescue_state_id": "state",
                    "abandoned_tactics": (),
                    "error": None,
                }
            ],
            "rescue_rollouts": [
                {
                    "semantic_event_id": "semantic:0",
                    "termination": "invalid_output",
                    "attempts": [],
                    "generations": [],
                },
                {
                    "semantic_event_id": "semantic:0",
                    "termination": "invalid_output",
                    "attempts": [],
                    "generations": [],
                },
            ],
        },
    }

    report = render_problem_trace(detail, [], route="analogy-assisted")

    assert report.count("Semantic recovery 1") == 1
    assert report.count("Analogy fallback") == 2
    assert report.index("Semantic recovery 1") < report.index("Analogy fallback 1")


def test_semantic_failure_status_and_events_survive_problem_serialization() -> None:
    event = SemanticRecoveryEvent(
        "semantic:0",
        "root",
        SemanticResult.COUNTERMODEL,
        (("P", False),),
    )
    search = SearchResult(
        SearchStatus.SEMANTIC_FAILURE,
        None,
        semantic_recoveries=(event,),
    )

    outcome = _problem_result("demo", search)

    assert outcome.status is SolveStatus.SEMANTIC_FAILURE
    assert outcome.failure == "no propositionally valid rescue ancestor"
    assert asdict(search)["semantic_recoveries"][0]["countermodel"] == (
        ("P", False),
    )


def test_report_explains_semantic_checker_error_without_analogy_rollout() -> None:
    detail = {
        "problem": {"id": "broken", "binders": "", "target": "P"},
        "outcome": {
            "status": "semantic_failure",
            "proof": None,
            "failure": "semantic check failed: cannot parse goal",
            "verifier_call_count": 1,
            "invalid_tactic_count": 1,
            "stuck_state_count": 1,
            "analogy_fallback_count": 0,
        },
        "search": {
            "ordinary_expansions": [],
            "semantic_recoveries": [
                {
                    "event_id": "semantic:0",
                    "trigger_state_id": "broken-state",
                    "result": None,
                    "countermodel": (),
                    "rescue_state_id": None,
                    "abandoned_tactics": (),
                    "error": "cannot parse goal",
                }
            ],
            "rescue_rollouts": [],
        },
    }

    report = render_problem_trace(detail, [], route="unsolved")

    assert "Semantic recovery 1 — error" in report
    assert "Semantic check error: cannot parse goal" in report
    assert "Analogy fallback" not in report


def test_problem_trace_explains_semantically_pruned_successor() -> None:
    detail = {
        "problem": {"id": "p", "binders": "(P Q : Prop)", "target": "P"},
        "outcome": {"status": "exhausted", "verifier_call_count": 1},
        "search": {
            "ordinary_expansions": (),
            "semantic_recoveries": (),
            "rescue_rollouts": (),
            "semantic_prunes": (
                {
                    "parent_state_id": "s0",
                    "successor_state_id": "s1",
                    "tactic": "left",
                    "countermodel": (("P", False), ("Q", True)),
                    "invalid_goal_index": 0,
                },
            ),
        },
    }

    report = render_problem_trace(detail, (), route="unsolved")

    assert "SAT prune 1" in report
    assert "`left` from `s0` produced `s1`" in report
    assert "P=false, Q=true" in report


def test_problem_trace_labels_pruned_ordinary_attempt_and_model_filters() -> None:
    detail = {
        "problem": {"id": "p", "binders": "", "target": "P"},
        "outcome": {"status": "exhausted"},
        "search": {
            "semantic_prunes": (),
            "semantic_recoveries": (),
            "rescue_rollouts": (),
            "ordinary_expansions": (
                {
                    "attempts": ({"candidate": {"source": "left"}, "status": "accepted", "state": {"expression": "⊢ P"}},),
                    "discarded": ({"result": {"candidate": {"source": "left"}}, "reason": "semantic_refuted"},),
                },
            ),
        },
    }
    events = ({
        "kind": "ordinary_model_call",
        "messages": (),
        "discarded_candidates": ("tauto: banned or unknown tactic",),
        "responses": ({"raw_text": "[]", "messages": ({"role": "user", "content": "replacement"},)},),
    },)

    report = render_problem_trace(detail, events, route="unsolved")

    assert "SAT-refuted successor" in report
    assert "tauto: banned or unknown tactic" in report
    assert "replacement" in report


def test_forced_moves_count_as_verifier_calls_and_invalid_tactics() -> None:
    from analogy.utils.records import CandidateOrigin, SymbolicState, TacticCandidate, TacticResult, TacticStatus
    from analogy.solver.proof.semantic import ForcedMoveEvent

    candidate = TacticCandidate("rcases h with hP | hQ", 1.0, CandidateOrigin.ORDINARY)
    forced = ForcedMoveEvent(
        "s0", "h", candidate.source,
        TacticResult(candidate, TacticStatus.ERROR, diagnostics=("bad",)),
        "rejected",
    )
    result = _problem_result("p", SearchResult(SearchStatus.EXHAUSTED, None, forced_moves=(forced,)))

    assert result.verifier_call_count == 1
    assert result.invalid_tactic_count == 1


def test_problem_metrics_separate_submissions_cache_hits_and_winning_analogy() -> None:
    from analogy.utils.records import CandidateOrigin, TacticCandidate, TacticResult, TacticStatus, WinningTacticStep
    from analogy.solver.analogy.rescue import RescueAttempt, RescueProposal, RescueRollout, RescueTermination

    candidate = TacticCandidate("exact h", 1.0, CandidateOrigin.ANALOGICAL, 1)
    attempt = TacticResult(candidate, TacticStatus.COMPLETE, proof="exact h", cache_hit=True)
    rollout = RescueRollout(
        "r", "s", (RescueProposal(candidate, "solved"),),
        (RescueAttempt(RescueProposal(candidate, "solved"), attempt),),
        RescueTermination.COMPLETE,
    )
    search = SearchResult(
        SearchStatus.COMPLETE,
        "exact h",
        rescue_rollouts=(rollout,),
        winning_path=(WinningTacticStep("exact h", CandidateOrigin.ANALOGICAL, 1, True),),
    )

    result = _problem_result("p", search)

    assert result.tactic_submission_count == 1
    assert result.verifier_call_count == 0
    assert result.analogy_fallback_count == 1
    assert result.analogy_winning_path_count == 1
    assert result.winning_tactic_ranks == (1,)


def test_problem_metrics_distinguish_direct_deliberation_from_analogy() -> None:
    from analogy.utils.records import CandidateOrigin, TacticCandidate, TacticResult, TacticStatus, WinningTacticStep
    from analogy.solver.analogy.rescue import RescueAttempt, RescueProposal, RescueRollout, RescueTermination

    candidate = TacticCandidate(
        "left", 1.0, CandidateOrigin.DELIBERATIVE, 1
    )
    proposal = RescueProposal(candidate, "p : Prop\n⊢ p")
    attempt = TacticResult(
        candidate, TacticStatus.COMPLETE, proof="by left"
    )
    rollout = RescueRollout(
        "r", "s", (proposal,), (RescueAttempt(proposal, attempt),),
        RescueTermination.COMPLETE,
    )
    search = SearchResult(
        SearchStatus.COMPLETE,
        "by left",
        rescue_rollouts=(rollout,),
        winning_path=(
            WinningTacticStep(
                "left", CandidateOrigin.DELIBERATIVE, 1, False
            ),
        ),
    )

    result = _problem_result("p", search)

    assert result.analogy_fallback_count == 0
    assert result.analogy_winning_path_count == 0
    assert result.deliberation_fallback_count == 1
    assert result.deliberation_winning_path_count == 1
    assert _problem_route(result, search) == "deliberation-assisted"


def test_problem_trace_renders_stuck_decisions() -> None:
    detail = {
        "problem": {"id": "p", "binders": "", "target": "P"},
        "outcome": {"status": "exhausted"},
        "search": {
            "ordinary_expansions": (),
            "semantic_prunes": (),
            "semantic_recoveries": (),
            "forced_moves": (),
            "rescue_rollouts": (),
            "stuckness_decisions": (
                {"state_id": "s0", "is_stuck": True, "reasons": ("all_tactics_errored",)},
            ),
        },
    }

    report = render_problem_trace(detail, (), route="unsolved")

    assert "Stuck decision 1 — stuck" in report
    assert "all tactics errored" in report


def test_problem_trace_shows_dependency_table_and_forced_move() -> None:
    detail = {
        "problem": {"id": "p", "binders": "", "target": "R"},
        "outcome": {"status": "exhausted"},
        "search": {
            "ordinary_expansions": (),
            "semantic_prunes": (),
            "rescue_rollouts": (),
            "semantic_recoveries": (
                {
                    "event_id": "semantic:0",
                    "trigger_state_id": "s0",
                    "result": "countermodel",
                    "rescue_state_id": "s0",
                    "countermodel": (("P", True),),
                    "abandoned_tactics": ("left",),
                    "error": None,
                    "countermodel_table": {
                        "rescue_state_id": "s0",
                        "rows": (
                            {
                                "tactic": "left",
                                "countermodel": (("P", True),),
                                "hypothesis_sides": (("hPQ", "left"),),
                            },
                        ),
                    },
                },
            ),
            "forced_moves": (
                {
                    "rescue_state_id": "s0",
                    "hypothesis": "hPQ",
                    "tactic": "rcases hPQ with hP | hQ",
                    "outcome": "progress",
                    "result": {"diagnostics": ()},
                },
            ),
        },
    }

    report = render_problem_trace(detail, (), route="unsolved")

    assert "Countermodel dependency table" in report
    assert "hPQ=left" in report
    assert "Forced move 1 — progress" in report
