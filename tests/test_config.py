from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _config_module():
    return importlib.import_module("analogy.config")


def test_curriculum_profile_loads_strict_pinned_paths_and_rejects_unknown_keys(tmp_path):
    import yaml
    module = _config_module()
    profile = Path(__file__).parents[1] / "config.grounded-curriculum.yaml"
    config = module.load_curriculum_config(profile)
    assert config.problems.is_file() and config.reference_proofs.is_file()
    assert config.split_manifest.is_file()
    assert (config.lean_project / "lean-toolchain").is_file()
    assert config.analogies_per_transition == 0
    assert config.model is None
    raw = yaml.safe_load(profile.read_text())
    raw["curriculum"]["guess_splits"] = True
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(yaml.safe_dump(raw))
    with pytest.raises(module.ConfigError, match="unknown"):
        module.load_curriculum_config(invalid)


@pytest.mark.parametrize("field,value", [("seed", True), ("analogies_per_transition", -1),
    ("schema_version", "future"), ("model_revision", "")])
def test_curriculum_rejects_invalid_identity_fields(tmp_path, field, value):
    from dataclasses import replace
    from test_grounded_curriculum import config
    with pytest.raises(_config_module().ConfigError):
        replace(config(tmp_path), **{field: value})


def _valid_yaml() -> str:
    return """\
experiment:
  stage: capability
  minimum_solved: 1
model:
  backend: transformers
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
  min_iterations: 1
  max_iterations: 6
  surprise_threshold: 0.2
  surprise_patience: 2
  min_surprise_improvement: 0.05
  improvement_patience: 2
  tactics_per_iteration: 2
  max_model_calls: 18
  max_lean_calls: 12
  transition_embedding_layer: -1
  discount: 0.95
  iteration_cost: 0.01
  rerank_tactics: false
rl:
  enabled: false
  algorithm: reinforce
  rollouts_per_state: 4
  tactics_per_rollout: 2
  hidden_width: 256
  max_delta_norm: 2.0
  initial_log_scale: -2.0
  learning_rate: 0.0001
  entropy_weight: 0.001
  max_gradient_norm: 1.0
  completion_reward: 1.0
  progress_reward: 0.1
  rejection_reward: -0.1
  invalid_output_reward: -0.05
  empty_output_reward: -0.02
  hop_cost: 0.01
  max_rescue_verifier_calls: 8
  epochs: 2
  checkpoint_interval: 1
  checkpoint: null
  train_connectives: [implication, and, or]
  validation_connectives: [not]
  test_connectives: [iff]
run:
  output_dir: runs
  seed: 0
data:
  root: /tmp/analogy-data
"""


@pytest.mark.parametrize("decoder", [None, "raw", "grounded"])
def test_grounded_decoder_is_explicit_and_old_profiles_default_raw(tmp_path, decoder):
    path = tmp_path / "config.yaml"
    text = _valid_yaml()
    if decoder is not None:
        text = text.replace("analogy:\n", f"analogy:\n  decoder: {decoder}\n")
    path.write_text(text)
    config = _config_module().load_config(path)
    assert config.analogy.decoder == (decoder or "raw")
    assert config.analogy.grounding_repair_calls == 1


@pytest.mark.parametrize("setting", ["decoder: other", "decoder: true",
    "grounding_repair_calls: 2", "grounding_repair_calls: -1", "grounding_repair_calls: true",
    "grounding_repair_calls: 1.0"])
def test_grounded_decoder_rejects_invalid_settings(tmp_path, setting):
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml().replace("analogy:\n", f"analogy:\n  {setting}\n"))
    with pytest.raises(_config_module().ConfigError, match="decoder|grounding_repair_calls"):
        _config_module().load_config(path)


@pytest.mark.parametrize("count", [0, 1])
def test_grounded_decoder_accepts_explicit_repair_limit(tmp_path, count):
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml().replace("analogy:\n", f"analogy:\n  grounding_repair_calls: {count}\n"))
    assert _config_module().load_config(path).analogy.grounding_repair_calls == count


@pytest.mark.parametrize("strategy", [None, "legacy", "generated_domain"])
def test_analogy_strategy_defaults_to_legacy_and_accepts_generated_domain(tmp_path, strategy):
    path = tmp_path / "config.yaml"
    text = _valid_yaml()
    if strategy is not None:
        text = text.replace("analogy:\n", f"analogy:\n  strategy: {strategy}\n")
    path.write_text(text)
    assert _config_module().load_config(path).analogy.strategy == (strategy or "legacy")


@pytest.mark.parametrize("strategy", ["unknown", "true", "7", "null"])
def test_analogy_strategy_rejects_unknown_or_non_string_values(tmp_path, strategy):
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml().replace("analogy:\n", f"analogy:\n  strategy: {strategy}\n"))
    with pytest.raises(_config_module().ConfigError, match="analogy.strategy"):
        _config_module().load_config(path)


def test_load_config_resolves_paths_from_config_file(tmp_path: Path) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml(), encoding="utf-8")

    config = module.load_config(path)

    assert config.benchmark.corpus == (tmp_path / "corpus/problems.json").resolve()
    assert config.run.output_dir == (tmp_path / "runs").resolve()
    assert config.data.root == Path("/tmp/analogy-data").resolve()
    assert config.model.name == "Qwen/Qwen3-8B"
    assert config.model.backend == "transformers"
    assert config.experiment.minimum_solved == 1
    assert config.model.max_new_tokens == 128
    assert config.model.timeout_seconds == 120.0
    assert config.model.do_sample is True
    assert config.model.temperature == 0.3
    assert config.model.top_p == 0.9
    assert config.rl.rollouts_per_state == 4
    assert config.rl.completion_reward == 1.0
    assert config.rl.invalid_output_reward == -0.05
    assert config.rl.empty_output_reward == -0.02
    assert config.rl.hop_cost == 0.01
    assert config.rl.checkpoint is None
    assert config.rl.train_connectives == ("implication", "and", "or")
    assert config.analogy.min_iterations == 1
    assert config.analogy.max_iterations == 6
    assert config.analogy.surprise_threshold == 0.2


def test_model_backend_rejects_unknown_value(tmp_path: Path) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(
        _valid_yaml().replace("backend: transformers", "backend: metal"),
        encoding="utf-8",
    )

    with pytest.raises(module.ConfigError, match=r"config\.model\.backend"):
        module.load_config(path)


def test_mlx_8b_fast_profile() -> None:
    module = _config_module()
    config = module.load_config(
        Path(__file__).parents[1] / "config.mlx-8b-fast.yaml"
    )

    assert config.model.backend == "mlx"
    assert config.model.name == "mlx-community/Qwen3-8B-4bit"
    assert config.model.max_new_tokens == 256
    assert config.model.timeout_seconds == 45.0
    assert config.search.top_k == 3
    assert config.search.max_steps == 8
    assert config.analogy.max_iterations == 3
    assert config.analogy.max_model_calls == 8
    assert config.analogy.max_lean_calls == 6
    assert config.analogy.tactics_per_iteration == 4
    assert config.rl.rollouts_per_state == 1
    assert config.rl.tactics_per_rollout == 4
    assert config.rl.epochs == 1


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


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("  min_iterations: 1", "  min_iterations: 7"),
        ("  surprise_threshold: 0.2", "  surprise_threshold: 2.1"),
        ("  discount: 0.95", "  discount: 0"),
        ("  iteration_cost: 0.01", "  iteration_cost: -0.01"),
    ],
)
def test_analogy_loop_config_rejects_invalid_ranges(
    tmp_path: Path, old: str, new: str
) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml().replace(old, new), encoding="utf-8")

    with pytest.raises(module.ConfigError):
        module.load_config(path)


def test_config_records_are_immutable(tmp_path: Path) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml(), encoding="utf-8")
    config = module.load_config(path)

    with pytest.raises(AttributeError):
        config.search.top_k = 10


@pytest.mark.parametrize(
    ("old", "new", "error_path"),
    [
        ("  rollouts_per_state: 4", "  rollouts_per_state: false", "rollouts_per_state"),
        ("  tactics_per_rollout: 2", "  tactics_per_rollout: 0", "tactics_per_rollout"),
        ("  hidden_width: 256", "  hidden_width: -1", "hidden_width"),
        ("  max_delta_norm: 2.0", "  max_delta_norm: .inf", "max_delta_norm"),
        ("  initial_log_scale: -2.0", "  initial_log_scale: .nan", "initial_log_scale"),
        ("  learning_rate: 0.0001", "  learning_rate: 0", "learning_rate"),
        ("  entropy_weight: 0.001", "  entropy_weight: -0.1", "entropy_weight"),
        ("  max_gradient_norm: 1.0", "  max_gradient_norm: true", "max_gradient_norm"),
        ("  max_rescue_verifier_calls: 8", "  max_rescue_verifier_calls: 0", "max_rescue_verifier_calls"),
    ],
)
def test_load_config_rejects_invalid_rl_values(
    tmp_path: Path, old: str, new: str, error_path: str
) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml().replace(old, new, 1), encoding="utf-8")

    with pytest.raises(module.ConfigError, match=error_path):
        module.load_config(path)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("  completion_reward: 1.0", "  completion_reward: 0.05"),
        ("  progress_reward: 0.1", "  progress_reward: -0.01"),
        ("  rejection_reward: -0.1", "  rejection_reward: 0"),
        ("  invalid_output_reward: -0.05", "  invalid_output_reward: 0"),
        ("  empty_output_reward: -0.02", "  empty_output_reward: 0.01"),
        ("  hop_cost: 0.01", "  hop_cost: -0.01"),
    ],
)
def test_load_config_enforces_rl_reward_ordering(
    tmp_path: Path, old: str, new: str
) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml().replace(old, new, 1), encoding="utf-8")

    with pytest.raises(module.ConfigError, match="reward|hop_cost"):
        module.load_config(path)


def test_load_config_rejects_unknown_rl_key(tmp_path: Path) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(
        _valid_yaml().replace("  algorithm: reinforce", "  algorithm: reinforce\n  typo: 1"),
        encoding="utf-8",
    )

    with pytest.raises(module.ConfigError, match=r"config\.rl\.typo"):
        module.load_config(path)


@pytest.mark.parametrize(
    ("old", "new", "error_path"),
    [
        ("  epochs: 2", "  epochs: 0", "epochs"),
        ("  checkpoint_interval: 1", "  checkpoint_interval: false", "checkpoint_interval"),
        ("  train_connectives: [implication, and, or]", "  train_connectives: []", "train_connectives"),
        ("  validation_connectives: [not]", "  validation_connectives: [not, not]", "validation_connectives"),
        ("  test_connectives: [iff]", "  test_connectives: [or]", "disjoint"),
    ],
)
def test_load_config_rejects_invalid_training_split_fields(
    tmp_path: Path, old: str, new: str, error_path: str
) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml().replace(old, new, 1), encoding="utf-8")

    with pytest.raises(module.ConfigError, match=error_path):
        module.load_config(path)


def test_load_config_resolves_optional_checkpoint_path(tmp_path: Path) -> None:
    module = _config_module()
    path = tmp_path / "config.yaml"
    path.write_text(
        _valid_yaml().replace("  checkpoint: null", "  checkpoint: saved/policy.pt"),
        encoding="utf-8",
    )

    assert module.load_config(path).rl.checkpoint == (tmp_path / "saved/policy.pt").resolve()
