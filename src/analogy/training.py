"""Training, checkpointing, and matched evaluation for direct rescue RL."""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import torch

from .config import AppConfig
from .lean_gym.corpus import load_problems
from .lean_gym.models import Problem
from .lean_gym.verifier import LeanGymAdapter
from .loop import (
    AcceptNovelState,
    LocalRescueSearch,
    NeverCancelled,
    ObservableStucknessPolicy,
    StateIdFingerprinter,
    WhitespaceTacticCanonicalizer,
)
from .model import QwenProofEncoder, QwenTacticRanker
from .records import SearchStatus, SymbolicState
from .replay import JsonlStuckStateStore, StuckStateRecord, proof_state_fingerprint
from .rescue import DirectRescueRunner, RescueRollout, RescueTermination, RolloutCollector
from .rl import (
    DirectRescuePolicy,
    PolicyAction,
    RewardBaseline,
    RewardWeights,
    TrainingMetrics,
    TrainingSample,
    reinforce_step,
    verifier_reward,
    with_reward,
)


@dataclass(frozen=True, slots=True)
class ProblemSplit:
    train: tuple[Problem, ...]
    validation: tuple[Problem, ...]
    test: tuple[Problem, ...]


@dataclass(frozen=True, slots=True)
class ProblemEpisode:
    problem_id: str
    completed: bool
    rollouts: tuple[RescueRollout, ...]
    training_samples: tuple[TrainingSample, ...]
    ordinary_expansions: int
    verifier_calls: int
    failure: str | None = None


@dataclass(frozen=True, slots=True)
class TrainingSummary:
    run_id: str
    run_directory: Path
    epochs_completed: int
    best_checkpoint: Path
    metrics: tuple[TrainingMetrics, ...]


@dataclass(frozen=True, slots=True)
class EvaluationCondition:
    name: str
    rescue_kind: str
    ordinary_width: int
    max_expansions: int
    max_rescue_verifier_calls: int
    base_seed: int


@dataclass(frozen=True, slots=True)
class ConditionSummary:
    name: str
    attempted: int
    solved: int
    verifier_calls: int
    ordinary_expansions: int
    rescue_rollouts: int


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    run_id: str
    run_directory: Path
    condition_summaries: tuple[ConditionSummary, ...]


def split_problems(
    problems: Iterable[Problem],
    *,
    train_connectives: Sequence[str] = ("implication", "and", "or"),
    validation_connectives: Sequence[str] = ("not",),
    test_connectives: Sequence[str] = ("iff",),
) -> ProblemSplit:
    groups = tuple(tuple(values) for values in (
        train_connectives, validation_connectives, test_connectives
    ))
    if any(not group or len(set(group)) != len(group) for group in groups):
        raise ValueError("connective splits must be nonempty and contain no duplicates")
    sets = tuple(map(set, groups))
    if any(sets[left] & sets[right] for left, right in ((0, 1), (0, 2), (1, 2))):
        raise ValueError("connective splits must be pairwise disjoint")
    normalized = tuple(sorted(problems, key=lambda item: (item.id, item.difficulty)))
    assigned = sets[0] | sets[1] | sets[2]
    unknown = sorted({item.connective for item in normalized} - assigned)
    if unknown:
        raise ValueError(f"unassigned problem connectives: {unknown}")
    return ProblemSplit(
        tuple(item for item in normalized if item.connective in sets[0]),
        tuple(item for item in normalized if item.connective in sets[1]),
        tuple(item for item in normalized if item.connective in sets[2]),
    )


def group_sibling_samples(
    samples: Sequence[TrainingSample],
) -> tuple[tuple[TrainingSample, ...], ...]:
    order: list[str] = []
    grouped: dict[str, list[TrainingSample]] = {}
    for sample in samples:
        state_id = sample.rollout.state_id
        if state_id not in grouped:
            order.append(state_id)
            grouped[state_id] = []
        grouped[state_id].append(sample)
    return tuple(tuple(grouped[state_id]) for state_id in order)


def rollout_is_trainable(termination: RescueTermination) -> bool:
    """Return whether a rollout represents a valid sampled policy action."""
    if not isinstance(termination, RescueTermination):
        raise ValueError("termination must be a RescueTermination")
    return termination not in {
        RescueTermination.CANCELLED,
        RescueTermination.GENERATION_ERROR,
    }


class RescueRunWriter:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def create(
        cls, root: Path, run_id: str, resolved_config: dict[str, Any]
    ) -> "RescueRunWriter":
        path = Path(root) / run_id
        path.mkdir(parents=True, exist_ok=False)
        (path / "checkpoints").mkdir()
        writer = cls(path)
        writer.write_json("config.json", resolved_config)
        for name in (
            "rollouts.jsonl",
            "generations.jsonl",
            "problems.jsonl",
            "metrics.jsonl",
            "stuck_states.jsonl",
        ):
            (path / name).touch(exist_ok=False)
        return writer

    def append_jsonl(self, name: str, payload: Any) -> None:
        target = self.path / name
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(_jsonable(payload), sort_keys=True) + "\n")
            handle.flush()

    def write_json(self, name: str, payload: Any) -> None:
        target = self.path / name
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(_jsonable(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()


def save_training_checkpoint(
    path: Path,
    policy: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    epoch: int,
    problem_split: ProblemSplit,
    config_hash: str,
    *,
    reward_baseline: RewardBaseline | None = None,
) -> None:
    payload = {
        "policy": policy.state_dict(),
        "optimizer": optimizer.state_dict(),
        "epoch": epoch,
        "python_rng": random.getstate(),
        "torch_rng": torch.random.get_rng_state(),
        "split": asdict(problem_split),
        "config_hash": config_hash,
        "policy_schema_version": int(getattr(policy, "schema_version", 1)),
        "reward_baseline": (
            reward_baseline.state_dict() if reward_baseline is not None else None
        ),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    torch.save(payload, temporary)
    temporary.replace(target)


def load_training_checkpoint(
    path: Path,
    policy: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    problem_split: ProblemSplit,
    config_hash: str,
    *,
    reward_baseline: RewardBaseline | None = None,
) -> int:
    payload = _torch_load(Path(path))
    if payload.get("config_hash") != config_hash:
        raise ValueError("checkpoint config hash does not match")
    if payload.get("split") != asdict(problem_split):
        raise ValueError("checkpoint problem split does not match")
    expected_schema = int(getattr(policy, "schema_version", 1))
    if payload.get("policy_schema_version") != expected_schema:
        raise ValueError("checkpoint policy schema does not match")
    policy.load_state_dict(payload["policy"])
    optimizer.load_state_dict(payload["optimizer"])
    saved_baseline = payload.get("reward_baseline")
    if reward_baseline is not None and saved_baseline is not None:
        reward_baseline.load_state_dict(saved_baseline)
    random.setstate(payload["python_rng"])
    torch.random.set_rng_state(payload["torch_rng"])
    return int(payload["epoch"])


def build_evaluation_conditions(config: AppConfig) -> tuple[EvaluationCondition, ...]:
    common = {
        "ordinary_width": config.search.top_k,
        "max_expansions": config.search.max_steps,
        "max_rescue_verifier_calls": config.rl.max_rescue_verifier_calls,
        "base_seed": config.run.seed,
    }
    return (
        EvaluationCondition("conventional", "none", **common),
        EvaluationCondition("unperturbed", "zero", **common),
        EvaluationCondition("random", "random", **common),
        EvaluationCondition("learned", "learned", **common),
    )


ProblemRunner = Callable[[Problem, str, torch.nn.Module], ProblemEpisode]


def train_direct_rescue(
    config: AppConfig,
    generator: Any,
    verifier: Any,
    *,
    problems: Sequence[Problem] | None = None,
    policy: torch.nn.Module | None = None,
    optimizer: torch.optim.Optimizer | None = None,
    problem_runner: ProblemRunner | None = None,
    run_id: str | None = None,
) -> TrainingSummary:
    if not config.rl.enabled or config.analogy.enabled:
        raise ValueError("rescue training requires rl.enabled=true and analogy.enabled=false")
    selected = tuple(problems) if problems is not None else load_problems(config.benchmark.corpus)
    problem_split = split_problems(
        selected,
        train_connectives=config.rl.train_connectives,
        validation_connectives=config.rl.validation_connectives,
        test_connectives=config.rl.test_connectives,
    )
    random.seed(config.run.seed)
    torch.manual_seed(config.run.seed)
    active_policy = policy or _new_policy(config, generator)
    active_optimizer = optimizer or torch.optim.Adam(
        active_policy.parameters(), lr=config.rl.learning_rate
    )
    reward_baseline = RewardBaseline()
    resolved_hash = _config_hash(config)
    start_epoch = 0
    if config.rl.checkpoint is not None:
        start_epoch = load_training_checkpoint(
            config.rl.checkpoint,
            active_policy,
            active_optimizer,
            problem_split,
            resolved_hash,
            reward_baseline=reward_baseline,
        )
    identifier = run_id or uuid.uuid4().hex
    writer = RescueRunWriter.create(
        config.run.output_dir / "rescue", identifier, _run_metadata(config, generator, problem_split)
    )
    run_problem = problem_runner or _default_problem_runner(
        config,
        generator,
        verifier,
        JsonlStuckStateStore(writer.path / "stuck_states.jsonl"),
    )
    all_metrics: list[TrainingMetrics] = []
    best_score = -1
    best_checkpoint: Path | None = None

    for epoch in range(start_epoch + 1, config.rl.epochs + 1):
        for item in problem_split.train:
            episode = run_problem(item, "train", active_policy)
            _persist_episode(writer, episode, epoch, "train")
            for siblings in group_sibling_samples(episode.training_samples):
                metrics = reinforce_step(
                    active_policy,
                    active_optimizer,
                    siblings,
                    config.rl.entropy_weight,
                    config.rl.max_gradient_norm,
                    reward_baseline,
                    "train",
                )
                all_metrics.append(metrics)
                writer.append_jsonl(
                    "metrics.jsonl", {"epoch": epoch, "phase": "train", **asdict(metrics)}
                )

        validation_episodes = tuple(
            run_problem(item, "validation", active_policy)
            for item in problem_split.validation
        )
        for episode in validation_episodes:
            _persist_episode(writer, episode, epoch, "validation")
        validation_score = sum(episode.completed for episode in validation_episodes)
        checkpoint = writer.path / "checkpoints" / f"epoch-{epoch:04d}.pt"
        if epoch % config.rl.checkpoint_interval == 0 or epoch == config.rl.epochs:
            save_training_checkpoint(
                checkpoint,
                active_policy,
                active_optimizer,
                epoch,
                problem_split,
                resolved_hash,
                reward_baseline=reward_baseline,
            )
        if validation_score > best_score:
            best_score = validation_score
            if not checkpoint.exists():
                save_training_checkpoint(
                    checkpoint,
                    active_policy,
                    active_optimizer,
                    epoch,
                    problem_split,
                    resolved_hash,
                    reward_baseline=reward_baseline,
                )
            best_checkpoint = checkpoint

    if best_checkpoint is None:
        raise RuntimeError("training produced no checkpoint")
    summary = TrainingSummary(
        identifier,
        writer.path,
        config.rl.epochs,
        best_checkpoint,
        tuple(all_metrics),
    )
    writer.write_json("summary.json", asdict(summary))
    return summary


def evaluate_direct_rescue(
    config: AppConfig,
    generator: Any,
    verifier: Any,
    checkpoint: Path,
) -> EvaluationSummary:
    if not config.rl.enabled or config.analogy.enabled:
        raise ValueError("rescue evaluation requires rl.enabled=true and analogy.enabled=false")
    checkpoint = Path(checkpoint)
    identifier = f"evaluate-{checkpoint.stem}"
    problems = load_problems(config.benchmark.corpus)
    problem_split = split_problems(
        problems,
        train_connectives=config.rl.train_connectives,
        validation_connectives=config.rl.validation_connectives,
        test_connectives=config.rl.test_connectives,
    )
    learned = _new_policy(config, generator)
    payload = _torch_load(checkpoint)
    if payload.get("config_hash") != _config_hash(config):
        raise ValueError("checkpoint config hash does not match evaluation config")
    if payload.get("split") != asdict(problem_split):
        raise ValueError("checkpoint split does not match evaluation split")
    if payload.get("policy_schema_version") != learned.schema_version:
        raise ValueError("checkpoint policy schema does not match evaluation policy")
    learned.load_state_dict(payload["policy"])
    writer = RescueRunWriter.create(
        config.run.output_dir / "rescue",
        identifier,
        _run_metadata(config, generator, problem_split),
    )
    summaries: list[ConditionSummary] = []
    for condition in build_evaluation_conditions(config):
        if condition.name == "conventional":
            condition_policy = None
        elif condition.name == "unperturbed":
            condition_policy = _ZeroPolicy(generator.hidden_size)
        elif condition.name == "random":
            torch.manual_seed(config.run.seed)
            condition_policy = _new_policy(config, generator)
        else:
            condition_policy = learned
        episodes = tuple(
            _execute_problem(
                config, generator, verifier, item, condition.name, condition_policy
            )
            for item in problem_split.test
        )
        for episode in episodes:
            _persist_episode(writer, episode, 0, condition.name)
        summaries.append(
            ConditionSummary(
                condition.name,
                len(episodes),
                sum(item.completed for item in episodes),
                sum(item.verifier_calls for item in episodes),
                sum(item.ordinary_expansions for item in episodes),
                sum(len(item.rollouts) for item in episodes),
            )
        )
    summary = EvaluationSummary(identifier, writer.path, tuple(summaries))
    writer.write_json("summary.json", asdict(summary))
    return summary


def _default_problem_runner(
    config: AppConfig,
    generator: Any,
    verifier: Any,
    stuck_store: JsonlStuckStateStore | None = None,
) -> ProblemRunner:
    return lambda problem, phase, policy: _execute_problem(
        config, generator, verifier, problem, phase, policy, stuck_store
    )


def _execute_problem(
    config: AppConfig,
    generator: Any,
    verifier: Any,
    problem: Problem,
    phase: str,
    policy: torch.nn.Module | None,
    stuck_store: JsonlStuckStateStore | None = None,
) -> ProblemEpisode:
    collector = RolloutCollector()
    try:
        adapter = LeanGymAdapter(problem, verifier)
        ranker = QwenTacticRanker(
            generator,
            top_k=config.search.top_k,
            seed=config.run.seed,
            failed_tactics_for=adapter.failed_tactics_for,
        )
        rescue = None
        if policy is not None:
            rescue = DirectRescueRunner(
                encoder=QwenProofEncoder(generator, layer=config.model.hidden_layer),
                policy=policy,
                generator=generator,
                rollouts_per_state=config.rl.rollouts_per_state,
                tactics_per_rollout=config.rl.tactics_per_rollout,
                max_rescue_verifier_calls=config.rl.max_rescue_verifier_calls,
                base_seed=config.run.seed,
                hidden_layer=config.model.hidden_layer,
                collector=collector,
            )
        search = LocalRescueSearch(
            tactic_ranker=ranker,
            verifier=adapter,
            stuckness_policy=ObservableStucknessPolicy(),
            curiosity_search=None,
            rollout_rescue=rescue,
            state_fingerprinter=StateIdFingerprinter(),
            tactic_canonicalizer=WhitespaceTacticCanonicalizer(),
            progress_policy=AcceptNovelState(),
            cancellation=NeverCancelled(),
            ordinary_width=config.search.top_k,
            max_expansions=config.search.max_steps,
            stuck_state_sink=(
                _TrainingStuckStateSink(problem, phase, stuck_store)
                if stuck_store is not None
                else None
            ),
        )
        result = search.search(
            adapter.initial_state(),
            SymbolicState(f"{problem.id}:goal", problem.target, "lean4"),
        )
        weights = RewardWeights(
            config.rl.completion_reward,
            config.rl.progress_reward,
            config.rl.rejection_reward,
            config.rl.invalid_output_reward,
            config.rl.empty_output_reward,
            config.rl.hop_cost,
        )
        rewarded: list[RescueRollout] = []
        samples: list[TrainingSample] = []
        for rollout, action in collector.drain():
            reward = verifier_reward(rollout, weights).total
            record = with_reward(rollout, reward)
            rewarded.append(record)
            if rollout_is_trainable(rollout.termination):
                samples.append(TrainingSample(record, action, reward))
        verifier_calls = sum(
            len(expansion.attempts) for expansion in result.ordinary_expansions
        ) + sum(len(rollout.attempts) for rollout in rewarded)
        return ProblemEpisode(
            problem.id,
            result.status is SearchStatus.COMPLETE,
            tuple(rewarded),
            tuple(samples),
            len(result.ordinary_expansions),
            verifier_calls,
        )
    except Exception as error:
        preserved = collector.rollouts
        return ProblemEpisode(
            problem.id,
            False,
            preserved,
            (),
            0,
            sum(len(rollout.attempts) for rollout in preserved),
            f"{type(error).__name__}: {error}",
        )


class _TrainingStuckStateSink:
    def __init__(
        self, problem: Problem, split: str, store: JsonlStuckStateStore
    ) -> None:
        self.problem = problem
        self.split = split
        self.store = store

    def capture(self, state: Any, goal: Any, outcome: Any, decision: Any) -> None:
        del goal, decision
        self.store.add(
            StuckStateRecord(
                self.problem,
                self.split,
                tuple(state.context),
                state.expression,
                proof_state_fingerprint(state.expression),
                tuple(item.source for item in outcome.candidate_pool),
                tuple(item.candidate.source for item in outcome.attempts),
            )
        )


class _ZeroPolicy(torch.nn.Module):
    def __init__(self, dimension: int) -> None:
        super().__init__()
        self.dimension = dimension
        self.anchor = torch.nn.Parameter(torch.tensor(0.0), requires_grad=False)

    def sample(self, representation: Any, generator: torch.Generator) -> PolicyAction:
        del representation, generator
        scalar = self.anchor * 0.0
        return PolicyAction(torch.zeros(self.dimension), scalar, scalar)


def _new_policy(config: AppConfig, generator: Any) -> DirectRescuePolicy:
    return DirectRescuePolicy(
        generator.hidden_size,
        config.rl.hidden_width,
        config.rl.max_delta_norm,
        config.rl.initial_log_scale,
    )


def _persist_episode(
    writer: RescueRunWriter, episode: ProblemEpisode, epoch: int, phase: str
) -> None:
    for rollout in episode.rollouts:
        writer.append_jsonl(
            "rollouts.jsonl", {"epoch": epoch, "phase": phase, **asdict(rollout)}
        )
        for generation_index, generation in enumerate(rollout.generations):
            writer.append_jsonl(
                "generations.jsonl",
                {
                    "epoch": epoch,
                    "phase": phase,
                    "problem_id": episode.problem_id,
                    "rollout_id": rollout.rollout_id,
                    "generation_index": generation_index,
                    **asdict(generation),
                },
            )
    writer.append_jsonl(
        "problems.jsonl",
        {
            "epoch": epoch,
            "phase": phase,
            "problem_id": episode.problem_id,
            "completed": episode.completed,
            "ordinary_expansions": episode.ordinary_expansions,
            "verifier_calls": episode.verifier_calls,
            "failure": episode.failure,
        },
    )


def _run_metadata(
    config: AppConfig, generator: Any, problem_split: ProblemSplit
) -> dict[str, Any]:
    return {
        "git_revision": _git_revision(),
        "model": config.model.name,
        "model_revision": getattr(generator, "model_revision", config.model.name),
        "lean_toolchain": "leanprover/lean4:v4.29.1",
        "prompt_versions": {"direct_rescue": 1, "proof_views": 1},
        "split": asdict(problem_split),
        "budgets": {
            "ordinary_width": config.search.top_k,
            "max_expansions": config.search.max_steps,
            "rollouts_per_state": config.rl.rollouts_per_state,
            "tactics_per_rollout": config.rl.tactics_per_rollout,
            "max_rescue_verifier_calls": config.rl.max_rescue_verifier_calls,
        },
        "seed": config.run.seed,
        "config_hash": _config_hash(config),
    }


def _config_hash(config: AppConfig) -> str:
    payload = asdict(config)
    payload["experiment"]["stage"] = "rescue"
    payload["rl"]["checkpoint"] = None
    encoded = json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _git_revision() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _torch_load(path: Path) -> dict[str, Any]:
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "ConditionSummary",
    "EvaluationCondition",
    "EvaluationSummary",
    "ProblemEpisode",
    "ProblemSplit",
    "RescueRunWriter",
    "TrainingSummary",
    "build_evaluation_conditions",
    "evaluate_direct_rescue",
    "group_sibling_samples",
    "load_training_checkpoint",
    "rollout_is_trainable",
    "save_training_checkpoint",
    "split_problems",
    "train_direct_rescue",
]
