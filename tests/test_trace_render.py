from dataclasses import asdict

from analogy.trace_render import render_theorem_trace


def test_story_operator_trace_renders_decision_applications_and_all_lean_results(tmp_path):
    from test_story_operator_loop import _Backend, _Cancellation, _evaluation, _runner
    from analogy.records import SymbolicState

    outcome = _runner(tmp_path, _Backend()).search(
        SymbolicState("start", "h0 h1 h2 : P\n⊢ P", "lean4"),
        SymbolicState("goal", "P", "lean4"), (), (), _evaluation, _Cancellation(),
    )
    record = {
        "problem": {"id": "story", "target": "P"},
        "initial_state": asdict(SymbolicState("start", "h0 h1 h2 : P\n⊢ P", "lean4")),
        "rollouts": [asdict(outcome.rollouts[0])],
    }
    rendered = render_theorem_trace(record)
    assert "STORY: Three matching keys" in rendered
    assert "OPERATOR: exact_local" in rendered
    assert "application:0" in rendered
    assert rendered.count("LEAN RESULT:") == 3
