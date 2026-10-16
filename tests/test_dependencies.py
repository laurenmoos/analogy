from __future__ import annotations

import ast
import tomllib
from collections import deque
from pathlib import Path


ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "src" / "analogy"


def _local_imports(module: str) -> set[str]:
    path = PACKAGE / (module.removeprefix("analogy.").replace(".", "/") + ".py")
    if not path.is_file():
        package = PACKAGE / module.removeprefix("analogy.").replace(".", "/") / "__init__.py"
        if not package.is_file():
            return set()
        path = package
    result = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names if alias.name.startswith("analogy"))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parent = module.split(".")[:-node.level]
                imported = ".".join((*parent, node.module or ""))
            else:
                imported = node.module or ""
            if imported.startswith("analogy"):
                result.add(imported.rstrip("."))
    return result


def _reachable_from(*entrypoints: str) -> set[str]:
    pending = deque(entrypoints)
    reached = set()
    while pending:
        module = pending.popleft()
        if module in reached:
            continue
        reached.add(module)
        pending.extend(_local_imports(module) - reached)
    return reached


def test_mlx_lm_stays_on_transformers_4_compatible_release_line() -> None:
    pyproject = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert "mlx-lm>=0.29.1,<0.30" in pyproject["project"]["optional-dependencies"]["mlx"]


def test_current_execution_graph_has_no_staged_grounding_modules() -> None:
    reached = _reachable_from(
        "analogy.training", "analogy.analogy_transfer_loop", "analogy.grounded_curriculum",
    )

    assert not reached.intersection({
        "analogy.analogy_transfer_generation",
        "analogy.grounded_plan",
    })
    assert not (PACKAGE / "analogy_transfer_generation.py").exists()
    assert not (PACKAGE / "grounded_plan.py").exists()
