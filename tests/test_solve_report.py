import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

from analogy.config import load_config
from analogy.lean_gym.models import Problem
from analogy.utils.records import SearchStatus, SolveStatus, SolveSummary, SymbolicState
from analogy.solver import run as run_module
from analogy.solver.run import (
    _problem_result,
    _problem_route,
    render_problem_trace,
    run_analogy_assisted,
    run_vanilla,
)
from analogy.solver.search import SearchResult
from analogy.solver.proof.semantic import (
    SemanticRecoveryEvent,
    SemanticResult,
)


ROOT = Path(__file__).parents[1]


def test_vanilla_run_has_no_analogy_state_or_fallback(
    tmp_path, monkeypatch
) -> None:
    contexts = []

    class FakeLean:
        def __init__(self, problem, verifier):
            self.problem = problem

        def failed_tactics_for(self, _state_id):
            return ()

        def initial_state(self):
            return SymbolicState(f"{self.problem.id}:initial", "P : Prop\n⊢ P", "lean4")

    def fake_run_episode(context, initial, goal):
        contexts.append(context)
        return SearchResult(SearchStatus.EXHAUSTED, None)

    monkeypatch.setattr(run_module, "LeanGymAdapter", FakeLean)
    monkeypatch.setattr(run_module, "run_episode", fake_run_episode)
    config = load_config(ROOT / "config.yaml")
    config = replace(config, analogy=replace(config.analogy, enabled=False))
    problem = Problem("plain", "demo", "", "P", "test", "test", "atom", 1)

    result = run_vanilla(
        config,
        (problem,),
        object(),
        object(),
        run_directory=tmp_path,
    )

    assert result.summary.run_id == "analogy-off"
    assert len(contexts) == 1
    assert contexts[0].tactic_ranker._analogy_scratchpad is None
    assert contexts[0].analogy_fallback is None


def test_analogy_assisted_run_shares_one_scratchpad_and_isolates_problems(
    tmp_path, monkeypatch
) -> None:
    contexts = []

    class FakeLean:
        def __init__(self, problem, verifier):
            self.problem = problem

        def failed_tactics_for(self, _state_id):
            return ()

        def initial_state(self):
            return SymbolicState(f"{self.problem.id}:initial", "P : Prop\n⊢ P", "lean4")

    def fake_run_episode(context, initial, goal):
        contexts.append(context)
        return SearchResult(SearchStatus.EXHAUSTED, None)

    monkeypatch.setattr(run_module, "LeanGymAdapter", FakeLean)
    monkeypatch.setattr(run_module, "run_episode", fake_run_episode)
    config = load_config(ROOT / "config.yaml")
    config = replace(config, analogy=replace(config.analogy, enabled=True))
    problems = tuple(
        Problem(f"p{index}", "demo", "", "P", "test", "test", "atom", 1)
        for index in range(2)
    )

    result = run_analogy_assisted(
        config,
        problems,
        object(),
        object(),
        run_directory=tmp_path,
    )

    assert result.summary.run_id == "analogy-on"
    assert len(contexts) == 2
    first_ranker = contexts[0].tactic_ranker._analogy_scratchpad
    first_fallback = contexts[0].analogy_fallback._analogy_scratchpad
    second_ranker = contexts[1].tactic_ranker._analogy_scratchpad
    assert first_ranker is first_fallback
    assert first_ranker is not second_ranker


def test_solve_artifact_uses_summary_key(tmp_path, monkeypatch) -> None:
    config = load_config(ROOT / "config.yaml")
    config = replace(config, run=replace(config.run, output_dir=tmp_path))
    summary = SolveSummary("analogy-on", (), 0.0)
    monkeypatch.setattr(
        run_module,
        "_run_runtime",
        lambda *args, **kwargs: (summary, [], {}),
    )

    run = run_module.solve_problems(config, (), object(), object())
    payload = json.loads((run.output_directory / "run.json").read_text())

    assert payload["summary"]["results"] == []
    assert "inference" not in payload


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
