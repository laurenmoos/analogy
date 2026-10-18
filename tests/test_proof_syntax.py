from analogy.solver.proof.state import proof_state_fingerprint
from analogy.solver.proof.syntax import proof_goals, proposition_root


def test_proof_syntax_parses_live_goal_context() -> None:
    goal = proof_goals("P Q : Prop\nh : P ∨ Q\n⊢ Q")[0]

    assert goal.declarations == (("P", "Prop"), ("Q", "Prop"), ("h", "P ∨ Q"))
    assert goal.target == "Q"
    assert proposition_root(goal.declarations[-1][1]).operator == "∨"


def test_proof_state_fingerprint_is_normalized_and_state_sensitive() -> None:
    assert proof_state_fingerprint("h : P\n⊢ P") == proof_state_fingerprint(
        "h : P  \r\n⊢ P\r\n"
    )
    assert proof_state_fingerprint("h : P\n⊢ P") != proof_state_fingerprint(
        "h : P\n⊢ Q"
    )
