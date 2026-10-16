from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from analogy.config import ConfigError, load_config
from analogy.grounding import GroundingOperator


ROOT = Path(__file__).parents[1]


def config_payload():
    payload = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    payload["prompts"] = {"path": "prompts.yaml"}
    payload["analogy"]["grounding_operators"] = [
        "exact_local", "introduce", "apply_implication", "select_left",
    ]
    return payload


def write_config(tmp_path: Path, payload: dict) -> Path:
    (tmp_path / "prompts.yaml").write_text("prompts:\n  x:\n    version: v1\n    system: s\n    user: u\n")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_config_resolves_prompt_path_and_grounding_operators(tmp_path):
    config = load_config(write_config(tmp_path, config_payload()))
    assert config.prompts.path == (tmp_path / "prompts.yaml").resolve()
    assert config.analogy.grounding_operators == (
        GroundingOperator.EXACT_LOCAL,
        GroundingOperator.INTRODUCE,
        GroundingOperator.APPLY_IMPLICATION,
        GroundingOperator.SELECT_LEFT,
    )


@pytest.mark.parametrize("operators, diagnostic", [
    ([], "non-empty"),
    (["exact_local", "exact_local"], "duplicates"),
    (["invented"], "GroundingOperator"),
])
def test_config_rejects_invalid_grounding_operators(tmp_path, operators, diagnostic):
    payload = config_payload()
    payload["analogy"]["grounding_operators"] = operators
    with pytest.raises(ConfigError, match=diagnostic):
        load_config(write_config(tmp_path, payload))


def test_generated_analogy_rl_requires_sibling_rollouts(tmp_path):
    payload = yaml.safe_load((ROOT / "config.mlx-8b-generated-analogy.yaml").read_text())
    payload["rl"]["rollouts_per_state"] = 1
    with pytest.raises(ConfigError, match="rollouts_per_state >= 2"):
        load_config(write_config(tmp_path, payload))
