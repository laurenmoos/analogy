from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "standalone_analogy_recovery.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("standalone_analogy_recovery", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_masked_problem_payload_has_no_reference_channel() -> None:
    module = load_script()
    problem = module.Problem("secret", "(P : Prop)", "P → P")

    payload = module.masked_problem_payload(problem)

    rendered = json.dumps(payload, sort_keys=True)
    assert rendered == '{"binders": "(P : Prop)", "problem_id": "secret", "target": "P \\u2192 P"}'
    assert "intro hidden_name" not in rendered


def test_poetry_pairs_only_adjacent_lines_from_same_work(tmp_path: Path) -> None:
    module = load_script()
    path = tmp_path / "poetry.ndjson.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in (
            {"gid": "a", "s": "first"},
            {"gid": "a", "s": "second"},
            {"gid": "b", "s": "third"},
        ):
            handle.write(json.dumps(row) + "\n")

    transitions = tuple(module.iter_poetry_transitions(path))

    assert [(item.before, item.after) for item in transitions] == [("first", "second")]


def test_balanced_sampling_is_deterministic_and_deduplicated() -> None:
    module = load_script()
    poetry = tuple(
        module.Transition(f"p{i}", "poetry", "a", f"b{i}", {})
        for i in range(4)
    )
    physics = tuple(
        module.Transition(f"f{i}", "physics", "a", f"b{i}", {})
        for i in range(4)
    )

    first = module.sample_transitions(poetry, physics, per_domain=2, seed=7)
    second = module.sample_transitions(poetry, physics, per_domain=2, seed=7)

    assert first == second
    assert [item.domain for item in first].count("poetry") == 2
    assert [item.domain for item in first].count("physics") == 2
    assert len({item.id for item in first}) == 4
