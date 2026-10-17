from analogy.solver.inference import render_problem_trace


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
            "rescue_rollouts": [
                {
                    "termination": "complete",
                    "analogy_trace": "a key fitting a lock",
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
    assert "Analogy fallbacks: **1**" in report
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
