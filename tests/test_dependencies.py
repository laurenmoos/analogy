from importlib.util import find_spec
from pathlib import Path


ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "src" / "analogy"


def test_retired_execution_packages_are_absent():
    assert find_spec("analogy.experiments.activation_cli") is None
    assert find_spec("analogy.transfer.generated_loop") is None
    assert find_spec("analogy.training.trainer") is None


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
