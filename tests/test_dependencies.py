import ast
import inspect
from importlib.util import find_spec
from pathlib import Path


ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "src" / "analogy"


def _find_spec_or_none(name):
    try:
        return find_spec(name)
    except ModuleNotFoundError:
        return None


def test_retired_execution_packages_are_absent():
    assert _find_spec_or_none("analogy.experiments.activation_cli") is None
    assert _find_spec_or_none("analogy.transfer.generated_loop") is None
    assert _find_spec_or_none("analogy.training.trainer") is None
    assert _find_spec_or_none("analogy.inference") is None
    assert _find_spec_or_none("analogy.model") is not None
    assert _find_spec_or_none("analogy.solver.inference") is None
    assert _find_spec_or_none("analogy.solver.run") is not None
    assert _find_spec_or_none("analogy.records") is None
    assert _find_spec_or_none("analogy.utils.records") is not None
    assert _find_spec_or_none("analogy.problem_index") is None
    assert _find_spec_or_none("analogy.utils.checkpoint") is not None
    assert _find_spec_or_none("analogy.solver.proof.replay") is None
    assert _find_spec_or_none("analogy.solver.proof.views") is None
    assert _find_spec_or_none("analogy.solver.proof.inventory") is None
    assert _find_spec_or_none("analogy.solver.proof.syntax") is not None
    assert _find_spec_or_none("analogy.solver.proof.state") is not None


def test_no_secondary_runtime_entry_points_exist():
    retired = (
        "experiments/activation_runner.py",
        "experiments/activation_generation.py",
        "transfer/generated_loop.py",
        "training/trainer.py",
        "lean_gym/__main__.py",
        "lean_gym/cli.py",
    )

    assert [name for name in retired if (PACKAGE / name).exists()] == []


def test_run_module_contains_only_two_visible_recipes():
    tree = ast.parse((PACKAGE / "solver" / "run.py").read_text(encoding="utf-8"))
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]

    assert {node.name for node in functions} == {
        "run_vanilla",
        "run_analogy_assisted",
    }
    assert len(functions) == 2
    assert not any(isinstance(node, ast.ClassDef) for node in ast.walk(tree))

    for function in functions:
        assert any(
            isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and node.target.id == "problem"
            for node in ast.walk(function)
        )
        assert any(
            isinstance(node, ast.While)
            and isinstance(node.test, ast.Call)
            and isinstance(node.test.func, ast.Attribute)
            and node.test.func.attr == "has_work"
            for node in ast.walk(function)
        )


def test_live_solver_types_do_not_expose_retired_hooks() -> None:
    from analogy.solver.analogy import rescue
    from analogy.solver.analogy.state import AnalogyScratchpad
    from analogy.solver.episode import EpisodeContext
    from analogy.solver.model_policy import TacticRanker
    from analogy.solver.proof.adapter import LeanGymAdapter

    assert not hasattr(LeanGymAdapter, "proof_structure_for")
    assert not hasattr(LeanGymAdapter, "local_inventory_for")
    assert not hasattr(LeanGymAdapter, "register_replay_state")
    assert "stuck_state_sink" not in EpisodeContext.__dataclass_fields__
    assert "verifier" not in EpisodeContext.__dataclass_fields__
    ranker_parameters = inspect.signature(TacticRanker).parameters
    assert "tolerate_failure" not in ranker_parameters
    assert ranker_parameters["failed_tactics_for"].default is inspect.Parameter.empty
    assert ranker_parameters["trace_sink"].default is inspect.Parameter.empty
    assert not hasattr(AnalogyScratchpad, "abandoned_domains")
    assert not hasattr(rescue, "parse_analogy_tactic")
    assert not hasattr(rescue, "PolicyActionRecord")
