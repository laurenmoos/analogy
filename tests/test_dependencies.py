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


def _resolved_import_targets(path: Path, node: ast.ImportFrom) -> set[str]:
    module_parts = node.module.split(".") if node.module else []
    if node.level:
        package_parts = list(
            path.relative_to(ROOT / "src").with_suffix("").parts[:-1]
        )
        package_parts = package_parts[: len(package_parts) - node.level + 1]
        module_parts = package_parts + module_parts
    base = ".".join(module_parts)
    targets = {base} if base else set()
    targets.update(
        ".".join((*module_parts, alias.name)) for alias in node.names
    )
    return targets


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
    assert _find_spec_or_none("analogy.solver.analogy.protocol") is not None
    report_spec = _find_spec_or_none("analogy.solver.report")
    assert report_spec is not None
    assert report_spec.submodule_search_locations is not None
    assert _find_spec_or_none("analogy.solver.run_records") is None
    search_spec = _find_spec_or_none("analogy.solver.search")
    assert search_spec is not None
    assert search_spec.submodule_search_locations is not None
    for module in ("policies", "result", "session", "setup"):
        assert _find_spec_or_none(f"analogy.solver.search.{module}") is not None
    assert _find_spec_or_none("analogy.solver.episode") is None
    assert _find_spec_or_none("analogy.solver.search_setup") is None


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
    from analogy.solver.analogy.state import AnalogyScratchpad, AnalogyState
    from analogy.solver.search import EpisodeContext
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
    assert not hasattr(AnalogyScratchpad, "context_block")
    assert not hasattr(AnalogyState, "render")
    assert not hasattr(rescue, "parse_analogy_tactic")
    assert not hasattr(rescue, "PolicyActionRecord")


def test_computational_state_is_separate_from_report_presentation() -> None:
    from analogy.solver.analogy.state import AnalogyScratchpad, AnalogyState
    from analogy.solver.report.records import (
        ProblemRunRecord,
        RunState,
        SolveRunRecord,
    )

    tree = ast.parse(
        (PACKAGE / "solver" / "analogy" / "state.py").read_text(
            encoding="utf-8"
        )
    )
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "model.prompts" not in imported_modules
    assert not any(module.endswith("model.prompts") for module in imported_modules)
    assert not hasattr(AnalogyState, "render")
    assert not hasattr(AnalogyScratchpad, "context_block")
    for record_type in (ProblemRunRecord, RunState, SolveRunRecord):
        assert not any(
            name.startswith(("render", "write"))
            for name in vars(record_type)
        )


def test_run_uses_search_facade_and_proof_does_not_depend_on_search() -> None:
    run_tree = ast.parse(
        (PACKAGE / "solver" / "run.py").read_text(encoding="utf-8")
    )
    search_imports = [
        node
        for node in ast.walk(run_tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "search"
    ]

    assert {
        alias.name for node in search_imports for alias in node.names
    } >= {
        "initialize_search",
        "submit_tactics_to_lean",
        "update_search_with_scored_proof_states",
    }
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module in {"episode", "search_setup", "search.session"}
        for node in ast.walk(run_tree)
    )

    reverse_dependencies = []
    for path in (PACKAGE / "solver" / "proof").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if any(
                    target == "analogy.solver.search"
                    or target.startswith("analogy.solver.search.")
                    for target in _resolved_import_targets(path, node)
                ):
                    reverse_dependencies.append((path.name, node.lineno))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "analogy.solver.search" or alias.name.startswith(
                        "analogy.solver.search."
                    ):
                        reverse_dependencies.append((path.name, node.lineno))

    assert reverse_dependencies == []


def test_both_run_recipes_name_general_search_scoring_explicitly() -> None:
    tree = ast.parse((PACKAGE / "solver" / "run.py").read_text(encoding="utf-8"))
    recipes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }

    for name in ("run_vanilla", "run_analogy_assisted"):
        calls = [
            node.func.id
            for node in ast.walk(recipes[name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        assert "generate_and_score_tactics_with_model_policy" in calls
        assert "submit_tactics_to_lean" in calls
        assert "update_search_with_scored_proof_states" in calls
        assert "generate_tactics_with_model_policy" not in calls
        assert "update_search" not in calls


def test_scoring_package_does_not_import_search_or_model_policy() -> None:
    forbidden = ("analogy.solver.search", "analogy.solver.model_policy")
    dependencies = []
    for path in (PACKAGE / "solver" / "scoring").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                targets = _resolved_import_targets(path, node)
            elif isinstance(node, ast.Import):
                targets = {alias.name for alias in node.names}
            else:
                continue
            for target in targets:
                if any(
                    target == blocked or target.startswith(f"{blocked}.")
                    for blocked in forbidden
                ):
                    dependencies.append((path.name, node.lineno, target))

    assert dependencies == []
