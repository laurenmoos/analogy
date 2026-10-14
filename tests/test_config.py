from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _config_module():
    return importlib.import_module("analogy.config")


def _valid_yaml() -> str:
    return """\
experiment:
  stage: capability
  minimum_solved: 1
model:
  name: Qwen/Qwen3-8B
  device: auto
  hidden_layer: -1
  max_new_tokens: 128
  timeout_seconds: 120.0
  do_sample: true
  temperature: 0.3
  top_p: 0.9
benchmark:
  corpus: corpus/problems.json
search:
  top_k: 8
  max_steps: 32
  stuck_after: 3
analogy:
  enabled: false
  samples: 4
rl:
  enabled: false
  algorithm: reinforce
run:
  output_dir: runs
  seed: 0
data:
  root: /tmp/analogy-data
"""


def test_load_config_resolves_paths_from_config_file(tmp_path: Path) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml(), encoding="utf-8")

    config = module.load_config(path)

    assert config.benchmark.corpus == (tmp_path / "corpus/problems.json").resolve()
    assert config.run.output_dir == (tmp_path / "runs").resolve()
    assert config.data.root == Path("/tmp/analogy-data").resolve()
    assert config.model.name == "Qwen/Qwen3-8B"
    assert config.experiment.minimum_solved == 1
    assert config.model.max_new_tokens == 128
    assert config.model.timeout_seconds == 120.0
    assert config.model.do_sample is True
    assert config.model.temperature == 0.3
    assert config.model.top_p == 0.9


def test_load_config_rejects_unknown_root_key(tmp_path: Path) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml() + "unknown: true\n", encoding="utf-8")

    with pytest.raises(module.ConfigError, match=r"config\.unknown"):
        module.load_config(path)


def test_load_config_rejects_unknown_nested_key(tmp_path: Path) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml().replace("  top_k: 8", "  top_k: 8\n  typo: 1"), encoding="utf-8")

    with pytest.raises(module.ConfigError, match=r"config\.search\.typo"):
        module.load_config(path)


def test_load_config_rejects_missing_key(tmp_path: Path) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml().replace("  max_steps: 32\n", ""), encoding="utf-8")

    with pytest.raises(module.ConfigError, match=r"config\.search\.max_steps"):
        module.load_config(path)


@pytest.mark.parametrize(
    ("old", "new", "error_path"),
    [
        ("  top_k: 8", "  top_k: 0", r"config\.search\.top_k"),
        ("  samples: 4", "  samples: false", r"config\.analogy\.samples"),
        ("  enabled: false", "  enabled: 1", r"config\.analogy\.enabled"),
        ("  do_sample: true", "  do_sample: 1", r"config\.model\.do_sample"),
        ("  temperature: 0.3", "  temperature: 0", r"config\.model\.temperature"),
        ("  top_p: 0.9", "  top_p: 1.1", r"config\.model\.top_p"),
    ],
)
def test_load_config_rejects_invalid_types_and_ranges(
    tmp_path: Path, old: str, new: str, error_path: str
) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml().replace(old, new, 1), encoding="utf-8")

    with pytest.raises(module.ConfigError, match=error_path):
        module.load_config(path)


def test_config_records_are_immutable(tmp_path: Path) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml(), encoding="utf-8")
    config = module.load_config(path)

    with pytest.raises(AttributeError):
        config.search.top_k = 10
