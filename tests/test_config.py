from pathlib import Path

import pytest
import yaml

from analogy.config import ConfigError, load_config


ROOT = Path(__file__).parents[1]


def _payload():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_runtime_config_has_one_analogy_boolean_and_schema_call_budget():
    config = load_config(ROOT / "config.yaml")

    assert config.analogy.enabled is True
    assert config.analogy.max_model_calls == 3
    assert set(config.analogy.__dataclass_fields__) == {"enabled", "max_model_calls"}


@pytest.mark.parametrize(
    "key,value",
    [
        ("strategy", "generated_domain"),
        ("tactics_per_iteration", 4),
        ("max_lean_calls", 64),
        ("decoder", "grounded"),
    ],
)
def test_config_rejects_retired_analogy_selectors(tmp_path, key, value):
    payload = _payload()
    payload["analogy"][key] = value

    with pytest.raises(ConfigError, match=rf"analogy\.{key} is unknown"):
        load_config(_write(tmp_path, payload))


def test_config_rejects_non_boolean_runtime_gate(tmp_path):
    payload = _payload()
    payload["analogy"]["enabled"] = "yes"

    with pytest.raises(ConfigError, match="analogy.enabled must be a boolean"):
        load_config(_write(tmp_path, payload))
