"""Training, checkpointing, and matched evaluation for direct rescue RL."""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import torch

from .analogy_generation import (
    AnalogyGenerator,
    AnalogyTacticGenerator,
    GeneratedHop,
    render_tactic_prompt,
)
from .analogy_loop import AnalogyLoopRunner
from .analogy_transfer_loop import GeneratedDomainAnalogyLoopRunner
from .analogy_transfer import ProofNeed, SourceAnalogy
from .analogy_transfer_generation import (
    SourceAnalogyGenerator, AlignmentGenerator, ProofTransferGenerator, TransferredTacticGenerator,
)
from .analogy_stream import AnalogyHop, AnalogyTacticPlan
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
from .proof_views import build_proof_views, build_policy_representation, compare_transitions
from .records import Message, SearchStatus, SymbolicState, TacticStatus
from .replay import (
    JsonlStuckStateStore,
    StuckStateRecord,
    proof_state_fingerprint,
    reconstruct_stuck_state,
)
from .rescue import (
    DirectRescueRunner,
    GenerationAttempt,
    GenerationStatus,
    ProofRepresentation,
    RescueRollout,
    RescueTermination,
    RolloutCollector,
)
from .rl import (
    DirectRescuePolicy,
    PolicyAction,
    RewardBaseline,
    RewardWeights,
    TrainingMetrics,
    TrainingSample,
    discounted_iteration_samples,
    reinforce_step,
    verifier_reward,
    with_reward,
)
from .surprise import SurpriseConfig, SurpriseController


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
    max_model_calls: int
    max_lean_calls: int
    tactics_per_iteration: int
    wall_clock_seconds: float

    @property
    def budget(self) -> tuple[int, int, int, int, int, int, int, float]:
        """Immutable budget identity used by matched-condition checks."""
        return (
            self.ordinary_width,
            self.max_expansions,
            self.max_rescue_verifier_calls,
            self.max_model_calls,
            self.max_lean_calls,
            self.tactics_per_iteration,
            self.base_seed,
            self.wall_clock_seconds,
        )


@dataclass(frozen=True, slots=True)
class AnalogyAssignment:
    source: StuckStateRecord
    target: StuckStateRecord


@dataclass(frozen=True, slots=True)
class GeneratedSourceRecord:
    problem_id: str
    state: str
    need: ProofNeed
    source: SourceAnalogy


@dataclass(frozen=True, slots=True)
class ConditionSummary:
    name: str
    attempted: int
    solved: int
    verifier_calls: int
    ordinary_expansions: int
    rescue_rollouts: int
    solved_ids: tuple[str, ...] = ()
    newly_solved_ids: tuple[str, ...] = ()
    regressed_ids: tuple[str, ...] = ()
    completion_per_lean_call: float = 0.0
    lean_accepted_tactic_rate: float = 0.0
    successor_rate: float = 0.0
    diagnostic_repair_attempts: int = 0
    diagnostic_repair_successes: int = 0
    diagnostic_repair_success_rate: float = 0.0
    non_null_surprise_rate: float = 0.0
    valid_generation_rate: float = 0.0
    repaired_generation_rate: float = 0.0
    mean_iteration_count: float = 0.0
    stop_reason_histogram: tuple[tuple[str, int], ...] = ()
    mean_surprise_trajectory: tuple[float, ...] = ()
    saturation_rate: float = 0.0
    failures: tuple[tuple[str, str], ...] = ()


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
        self._analogy_iteration_keys: set[tuple[int, str, str, str, int]] = set()

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
            "analogy_iterations.jsonl",
        ):
            (path / name).touch(exist_ok=False)
        return writer

    def append_jsonl(self, name: str, payload: Any) -> None:
        target = self.path / name
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(_jsonable(payload), sort_keys=True) + "\n")
            handle.flush()

    def analogy_iteration_sink(
        self, *, epoch: int, phase: str, problem_id: str, rollout_id: str
    ) -> Callable[[str, Any], None]:
        """Return a sink that flushes each completed loop iteration immediately."""
        def append(actual_rollout_id: str, iteration: Any) -> None:
            del actual_rollout_id
            self.append_analogy_iteration(
                iteration,
                epoch=epoch,
                phase=phase,
                problem_id=problem_id,
                rollout_id=rollout_id,
            )

        return append

    def append_analogy_iteration(
        self,
        iteration: Any,
        *,
        epoch: int,
        phase: str,
        problem_id: str,
        rollout_id: str,
    ) -> None:
        key = (epoch, phase, problem_id, rollout_id, iteration.iteration_index)
        if key in self._analogy_iteration_keys:
            return
        self._analogy_iteration_keys.add(key)
        self.append_jsonl(
            "analogy_iterations.jsonl",
            {
                "epoch": epoch,
                "phase": phase,
                "problem_id": problem_id,
                "rollout_id": rollout_id,
                **asdict(iteration),
            },
        )

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
        raise ValueError(
            f"checkpoint policy schema {payload.get('policy_schema_version')} is incompatible "
            f"with expected schema {expected_schema}"
        )
    policy.load_state_dict(payload["policy"])
    optimizer.load_state_dict(payload["optimizer"])
    saved_baseline = payload.get("reward_baseline")
    if reward_baseline is not None and saved_baseline is not None:
        reward_baseline.load_state_dict(saved_baseline)
    random.setstate(payload["python_rng"])
    torch.random.set_rng_state(payload["torch_rng"])
    return int(payload["epoch"])


def build_evaluation_conditions(config: AppConfig) -> tuple[EvaluationCondition, ...]:
    lean_budget = min(
        config.rl.max_rescue_verifier_calls, config.analogy.max_lean_calls
    )
    common = {
        "ordinary_width": config.search.top_k,
        "max_expansions": config.search.max_steps,
        "max_rescue_verifier_calls": lean_budget,
        "base_seed": config.run.seed,
        "max_model_calls": config.analogy.max_model_calls,
        "max_lean_calls": lean_budget,
        "tactics_per_iteration": config.analogy.tactics_per_iteration,
        "wall_clock_seconds": (
            config.model.timeout_seconds * config.analogy.max_model_calls
        ),
    }
    conditions = (
        EvaluationCondition("conventional", "none", **common),
        EvaluationCondition("learned_direct", "learned_direct", **common),
    )
    if not config.analogy.enabled:
        return conditions
    if config.analogy.strategy == "generated_domain":
        return conditions + (EvaluationCondition("analogy_loop", "analogy_loop", **common),) + tuple(
            EvaluationCondition(name, name, **common) for name in (
                "generated_unsteered", "generated_random", "generated_learned", "generated_shuffled"
            )
        )
    return conditions + (
        EvaluationCondition("analogy_loop", "analogy_loop", **common),
        EvaluationCondition("shuffled_analogy", "shuffled_analogy", **common),
    )


def shuffle_analogy_assignments(
    records: Sequence[StuckStateRecord], *, seed: int
) -> tuple[AnalogyAssignment, ...]:
    """Derange sources within each split/connective compatibility bucket."""
    normalized = tuple(records)
    if not all(isinstance(item, StuckStateRecord) for item in normalized):
        raise ValueError("records must contain only StuckStateRecord values")
    groups: dict[tuple[str, str], list[StuckStateRecord]] = {}
    for item in normalized:
        groups.setdefault((item.split, item.problem.connective), []).append(item)
    assignments: list[AnalogyAssignment] = []
    rng = random.Random(seed)
    for key in sorted(groups):
        targets = sorted(groups[key], key=lambda item: item.problem.id)
        if len(targets) < 2:
            raise ValueError("shuffled analogy requires two compatible records")
        shift = 1 + rng.randrange(len(targets) - 1)
        sources = targets[shift:] + targets[:shift]
        assignments.extend(
            AnalogyAssignment(source, target)
            for source, target in zip(sources, targets)
        )
    return tuple(assignments)


def shuffle_generated_source(source, need, observation, records, *, problem_id, seed):
    """Substitute a different theorem's compatible source before target alignment."""
    def signature(item):
        return (item.operation, item.input_arity, item.output_arity, item.permutation,
                item.information_flow, item.proof_roles)
    eligible = sorted((item for item in records if item.problem_id != problem_id
                       and signature(item.need) == signature(need)),
                      key=lambda item: (item.problem_id, item.state, item.source.analogy_id))
    if not eligible:
        raise ValueError("shuffled source evaluation lacks a structurally compatible other theorem")
    selected = random.Random(seed + observation.iteration_index).choice(eligible)
    return replace(selected.source, analogy_id=f"shuffled-{selected.problem_id}-{selected.source.analogy_id}")


def _evaluation_policy(condition, config, generator, learned):
    if condition == "conventional":
        return None
    if condition == "generated_random":
        with torch.random.fork_rng():
            torch.manual_seed(config.run.seed)
            return _new_policy(config, generator)
    return learned


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
    replay_records: Sequence[StuckStateRecord] | None = None,
) -> TrainingSummary:
    if not config.rl.enabled:
        raise ValueError("rescue training requires rl.enabled=true")
    if replay_records is not None and problem_runner is not None:
        raise ValueError("replay training cannot use a custom problem runner")
    normalized_replay = tuple(replay_records or ())
    if normalized_replay and not all(
        isinstance(item, StuckStateRecord) for item in normalized_replay
    ):
        raise ValueError("replay_records must contain StuckStateRecord values")
    if problems is not None:
        selected = tuple(problems)
    elif normalized_replay:
        selected = tuple(
            {item.problem.id: item.problem for item in normalized_replay}.values()
        )
    else:
        selected = load_problems(config.benchmark.corpus)
    problem_split = split_problems(
        selected,
        train_connectives=config.rl.train_connectives,
        validation_connectives=config.rl.validation_connectives,
        test_connectives=config.rl.test_connectives,
    )
    if normalized_replay:
        expected_split = {
            item.id: split_name
            for split_name, items in (
                ("train", problem_split.train),
                ("validation", problem_split.validation),
                ("test", problem_split.test),
            )
            for item in items
        }
        for record in normalized_replay:
            configured = expected_split[record.problem.id]
            if record.split != configured:
                raise ValueError(
                    f"replay record {record.problem.id!r} is labeled "
                    f"{record.split!r} but belongs to configured {configured} split"
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
    stuck_store = JsonlStuckStateStore(writer.path / "stuck_states.jsonl")
    all_metrics: list[TrainingMetrics] = []
    best_score = -1
    best_checkpoint: Path | None = None

    def execute(item: Problem | StuckStateRecord, phase: str, epoch: int):
        if isinstance(item, StuckStateRecord):
            reconstructed = reconstruct_stuck_state(item, verifier)
            return _execute_problem(
                config,
                generator,
                verifier,
                item.problem,
                phase,
                active_policy,
                initial_state=reconstructed,
                iteration_sink=_live_iteration_sink(
                    writer, epoch=epoch, phase=phase, problem_id=item.problem.id
                ),
            )
        if problem_runner is not None:
            return problem_runner(item, phase, active_policy)
        return _execute_problem(
            config,
            generator,
            verifier,
            item,
            phase,
            active_policy,
            stuck_store,
            iteration_sink=_live_iteration_sink(
                writer, epoch=epoch, phase=phase, problem_id=item.id
            ),
        )

    train_items: Sequence[Problem | StuckStateRecord] = (
        tuple(item for item in normalized_replay if item.split == "train")
        if normalized_replay else problem_split.train
    )
    validation_items: Sequence[Problem | StuckStateRecord] = (
        tuple(item for item in normalized_replay if item.split == "validation")
        if normalized_replay else problem_split.validation
    )
    if normalized_replay and (not train_items or not validation_items):
        raise ValueError("replay training requires train and validation records")

    for epoch in range(start_epoch + 1, config.rl.epochs + 1):
        for item in train_items:
            episode = execute(item, "train", epoch)
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
            execute(item, "validation", epoch) for item in validation_items
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
    if not config.rl.enabled:
        raise ValueError("rescue evaluation requires rl.enabled=true")
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
        raise ValueError(
            f"checkpoint policy schema {payload.get('policy_schema_version')} is incompatible "
            f"with expected evaluation policy schema {learned.schema_version}"
        )
    learned.load_state_dict(payload["policy"])
    writer = RescueRunWriter.create(
        config.run.output_dir / "rescue",
        identifier,
        _run_metadata(config, generator, problem_split),
    )
    summaries: list[ConditionSummary] = []
    conventional_solved: set[str] = set()
    analogy_hops_by_problem: dict[str, tuple[AnalogyHop, ...]] = {}
    analogy_records_by_problem: dict[str, StuckStateRecord] = {}
    generated_sources: list[GeneratedSourceRecord] = []
    for condition in build_evaluation_conditions(config):
        condition_policy = _evaluation_policy(condition.name, config, generator, learned)
        shuffled_sources: dict[str, tuple[AnalogyHop, ...]] = {}
        if condition.name == "shuffled_analogy" and config.analogy.enabled:
            assignments = shuffle_analogy_assignments(
                tuple(analogy_records_by_problem.values()), seed=config.run.seed
            )
            assigned = {
                item.target.problem.id: item.source.problem.id
                for item in assignments
            }
            sources_by_connective: dict[str, list[str]] = {}
            for problem_id, record in analogy_records_by_problem.items():
                sources_by_connective.setdefault(
                    record.problem.connective, []
                ).append(problem_id)
            for item in problem_split.test:
                source_id = assigned.get(item.id)
                if source_id is None:
                    eligible = sorted(
                        candidate_id
                        for candidate_id in sources_by_connective.get(item.connective, ())
                        if candidate_id != item.id
                    )
                    if not eligible:
                        raise ValueError(
                            "shuffled analogy evaluation lacks a compatible test trace"
                        )
                    source_id = eligible[0]
                shuffled_sources[item.id] = analogy_hops_by_problem[source_id]
        episodes = tuple(
            _execute_problem(
                config,
                generator,
                verifier,
                item,
                condition.name,
                condition_policy,
                analogy_hops=shuffled_sources.get(item.id),
                generated_sources=tuple(generated_sources),
                iteration_sink=_live_iteration_sink(
                    writer, epoch=0, phase=condition.name, problem_id=item.id
                ),
            )
            for item in problem_split.test
        )
        if condition.name == "analogy_loop":
            for episode in episodes:
                hops = tuple(
                    iteration.hop
                    for rollout in episode.rollouts
                    if rollout.analogy_trace is not None
                    for iteration in rollout.analogy_trace.iterations
                )
                if hops:
                    analogy_hops_by_problem[episode.problem_id] = hops
                    first = next(
                        iteration
                        for rollout in episode.rollouts
                        if rollout.analogy_trace is not None
                        for iteration in rollout.analogy_trace.iterations
                    )
                    source_problem = next(
                        item for item in problem_split.test
                        if item.id == episode.problem_id
                    )
                    analogy_records_by_problem[episode.problem_id] = StuckStateRecord(
                        source_problem,
                        "test",
                        first.state_before.context,
                        first.state_before.expression,
                        proof_state_fingerprint(first.state_before.expression),
                        (),
                        tuple(
                            attempt.proposal.candidate.source
                            for attempt in first.attempts
                        ),
                    )
        if condition.name == "generated_learned":
            generated_sources.extend(
                GeneratedSourceRecord(episode.problem_id, iteration.state_before.expression,
                                      iteration.need, iteration.source)
                for episode in episodes for rollout in episode.rollouts
                if rollout.analogy_trace is not None
                for iteration in rollout.analogy_trace.iterations if iteration.source is not None
            )
        for episode in episodes:
            _persist_episode(writer, episode, 0, condition.name)
        solved_ids = {item.problem_id for item in episodes if item.completed}
        if condition.name == "conventional":
            conventional_solved = solved_ids
        summaries.append(_summarize_condition(condition.name, episodes, conventional_solved))
    summary = EvaluationSummary(identifier, writer.path, tuple(summaries))
    writer.write_json("summary.json", asdict(summary))
    return summary


def _summarize_condition(
    name: str,
    episodes: Sequence[ProblemEpisode],
    conventional_solved: set[str],
) -> ConditionSummary:
    rollouts = tuple(rollout for episode in episodes for rollout in episode.rollouts)
    traces = tuple(
        rollout.analogy_trace for rollout in rollouts if rollout.analogy_trace is not None
    )
    generations = tuple(
        generation for rollout in rollouts for generation in rollout.generations
    )
    lean_attempts = tuple(
        attempt for rollout in rollouts for attempt in rollout.attempts
    )
    iterations = tuple(
        iteration for trace in traces for iteration in trace.iterations
    )
    solved_ids = tuple(sorted(item.problem_id for item in episodes if item.completed))
    solved_set = set(solved_ids)
    verifier_calls = sum(item.verifier_calls for item in episodes)
    stop_counts = Counter(trace.stop_reason.value for trace in traces)
    max_length = max((len(trace.iterations) for trace in traces), default=0)
    mean_surprise_values: list[float] = []
    for index in range(max_length):
        values = [
            trace.iterations[index].surprise
            for trace in traces
            if len(trace.iterations) > index
            and trace.iterations[index].surprise is not None
        ]
        if values:
            mean_surprise_values.append(sum(values) / len(values))
    valid = sum(item.status.value == "valid" for item in generations)
    repaired = sum(item.status.value == "valid" and item.repair for item in generations)
    generation_count = len(generations)
    saturated = sum(
        trace.stop_reason.value in {"surprise_converged", "improvement_stalled"}
        for trace in traces
    )
    accepted_tactics = sum(
        attempt.result.status in {TacticStatus.ACCEPTED, TacticStatus.COMPLETE}
        for attempt in lean_attempts
    )
    successors = sum(
        any(attempt.result.status is TacticStatus.ACCEPTED and attempt.discarded is None
            for attempt in iteration.attempts)
        for iteration in iterations
    )
    repair_attempts = sum(
        bool(getattr(iteration, "repair_generation_attempts", ())) for iteration in iterations
    )
    repair_successes = sum(getattr(iteration, "repair_succeeded", False) for iteration in iterations)
    surprise_observations = sum(
        iteration.surprise is not None for iteration in iterations
    )
    return ConditionSummary(
        name=name,
        attempted=len(episodes),
        solved=len(solved_ids),
        verifier_calls=verifier_calls,
        ordinary_expansions=sum(item.ordinary_expansions for item in episodes),
        rescue_rollouts=len(rollouts),
        solved_ids=solved_ids,
        newly_solved_ids=tuple(sorted(solved_set - conventional_solved)),
        regressed_ids=tuple(sorted(conventional_solved - solved_set)),
        completion_per_lean_call=(len(solved_ids) / verifier_calls if verifier_calls else 0.0),
        lean_accepted_tactic_rate=(
            accepted_tactics / len(lean_attempts) if lean_attempts else 0.0
        ),
        successor_rate=(successors / len(iterations) if iterations else 0.0),
        diagnostic_repair_attempts=repair_attempts,
        diagnostic_repair_successes=repair_successes,
        diagnostic_repair_success_rate=(
            repair_successes / repair_attempts if repair_attempts else 0.0
        ),
        non_null_surprise_rate=(
            surprise_observations / len(iterations) if iterations else 0.0
        ),
        valid_generation_rate=(valid / generation_count if generation_count else 0.0),
        repaired_generation_rate=(repaired / generation_count if generation_count else 0.0),
        mean_iteration_count=(
            sum(len(trace.iterations) for trace in traces) / len(traces)
            if traces else 0.0
        ),
        stop_reason_histogram=tuple(sorted(stop_counts.items())),
        mean_surprise_trajectory=tuple(mean_surprise_values),
        saturation_rate=(saturated / len(traces) if traces else 0.0),
        failures=tuple(
            (item.problem_id, item.failure)
            for item in episodes
            if item.failure is not None
        ),
    )


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
    analogy_hops: tuple[AnalogyHop, ...] | None = None,
    initial_state: SymbolicState | None = None,
    iteration_sink: Callable[[str, Any], None] | None = None,
    generated_sources: tuple[GeneratedSourceRecord, ...] = (),
) -> ProblemEpisode:
    collector = RolloutCollector()
    cancellation = _DeadlineCancellation(
        config.model.timeout_seconds * config.analogy.max_model_calls
    )
    try:
        adapter = LeanGymAdapter(problem, verifier)
        start_state = (
            adapter.initial_state()
            if initial_state is None
            else adapter.register_replay_state(initial_state)
        )
        ranker = QwenTacticRanker(
            generator,
            top_k=config.search.top_k,
            seed=config.run.seed,
            failed_tactics_for=adapter.failed_tactics_for,
        )
        rescue = None
        if policy is not None:
            use_analogy = config.analogy.enabled and phase not in {
                "learned_direct", "conventional"
            }
            if use_analogy:
                rescue = _new_analogy_runner(
                    config,
                    generator,
                    adapter,
                    policy,
                    analogy_hops=analogy_hops,
                    iteration_sink=iteration_sink,
                    condition=(phase if phase in {"analogy_loop", "shuffled_analogy"} or phase.startswith("generated_") else None),
                    generated_sources=generated_sources,
                    problem_id=problem.id,
                )
            else:
                rescue = DirectRescueRunner(
                    encoder=QwenProofEncoder(generator, layer=config.model.hidden_layer),
                    policy=policy,
                    generator=generator,
                    rollouts_per_state=config.rl.rollouts_per_state,
                    tactics_per_rollout=(
                        config.analogy.tactics_per_iteration
                        if config.analogy.enabled
                        else config.rl.tactics_per_rollout
                    ),
                    max_rescue_verifier_calls=(
                        min(
                            config.rl.max_rescue_verifier_calls,
                            config.analogy.max_lean_calls,
                        )
                        if config.analogy.enabled
                        else config.rl.max_rescue_verifier_calls
                    ),
                    base_seed=config.run.seed,
                    hidden_layer=config.model.hidden_layer,
                    collector=collector,
                    max_model_calls=(
                        config.analogy.max_model_calls
                        if config.analogy.enabled
                        else None
                    ),
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
            cancellation=cancellation,
            ordinary_width=config.search.top_k,
            max_expansions=config.search.max_steps,
            stuck_state_sink=(
                _TrainingStuckStateSink(problem, phase, stuck_store)
                if stuck_store is not None
                else None
            ),
        )
        result = search.search(
            start_state,
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
        if isinstance(rescue, (AnalogyLoopRunner, GeneratedDomainAnalogyLoopRunner)):
            for rollout, actions in rescue.drain_trajectories():
                reward = verifier_reward(rollout, weights).total
                record = with_reward(rollout, reward)
                rewarded.append(record)
                if (actions and rollout_is_trainable(rollout.termination)
                        and (not phase.startswith("generated_") or phase == "generated_learned")):
                    samples.extend(
                        discounted_iteration_samples(
                            rollout.analogy_trace,
                            actions,
                            record,
                            verified_reward=reward,
                            discount=config.analogy.discount,
                            iteration_cost=config.analogy.iteration_cost,
                        )
                    )
        else:
            for rollout, action in collector.drain():
                reward = verifier_reward(rollout, weights).total
                record = with_reward(rollout, reward)
                rewarded.append(record)
                if rollout_is_trainable(rollout.termination):
                    samples.append(TrainingSample(record, action, reward))
        verifier_calls = sum(
            len(expansion.attempts) for expansion in result.ordinary_expansions
        ) + sum(
            (
                rollout.analogy_trace.lean_calls
                if rollout.analogy_trace is not None
                else len(rollout.attempts)
            )
            for rollout in rewarded
        )
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


def _new_analogy_runner(
    config: AppConfig,
    generator: Any,
    adapter: LeanGymAdapter,
    policy: torch.nn.Module,
    *,
    analogy_hops: tuple[AnalogyHop, ...] | None = None,
    iteration_sink: Callable[[str, Any], None] | None = None,
    condition: str | None = None,
    generated_sources: tuple[GeneratedSourceRecord, ...] = (),
    problem_id: str = "",
) -> AnalogyLoopRunner | GeneratedDomainAnalogyLoopRunner:
    """Compose the readable loop from the same frozen model and policy head."""
    if config.analogy.strategy == "generated_domain" and condition not in {"analogy_loop", "shuffled_analogy"}:
        return _new_generated_runner(config, generator, adapter, policy,
            condition=condition or "generated_learned", iteration_sink=iteration_sink,
            generated_sources=generated_sources, problem_id=problem_id)
    representations: dict[int, ProofRepresentation] = {}
    model_calls = 0
    lean_calls = 0
    seen_states: set[str] = set()
    max_lean_calls = min(config.analogy.max_lean_calls, config.rl.max_rescue_verifier_calls)
    proof_view_lean_cost = 1

    def build_views(context):
        views = build_proof_views(
            context,
            adapter.proof_structure_for(context.state),
            generator,
            config.model.hidden_layer,
        )
        representations.clear()
        representations[id(views)] = ProofRepresentation(
            context,
            views.language.provenance.text_hash,
            views.structure.provenance.text_hash,
            views.language.embedding,
            views.structure.embedding,
        )
        return views

    def sample_action(views, iteration_index):
        nonlocal model_calls, lean_calls
        if iteration_index == 0:
            model_calls = lean_calls = 0
            seen_states.clear()
        representation = representations[id(views)]
        fingerprint = proof_state_fingerprint(representation.context.state.expression)
        observation = replace(
            representation,
            scalar_features=(
                iteration_index / max(config.analogy.max_iterations - 1, 1),
                (config.analogy.max_model_calls - model_calls) / config.analogy.max_model_calls,
                (max_lean_calls - lean_calls - proof_view_lean_cost) / max_lean_calls,
                float(fingerprint in seen_states),
            ),
        )
        seen_states.add(fingerprint)
        parameter = next(policy.parameters())
        torch_generator = torch.Generator(device=parameter.device).manual_seed(
            config.run.seed + iteration_index
        )
        return policy.sample(observation, torch_generator)

    def record_iteration(rollout_id, iteration):
        nonlocal model_calls, lean_calls
        # Iteration totals include repairs and finishing calls. The next
        # observation also accounts for its freshly verified proof structure.
        model_calls = iteration.model_calls
        lean_calls = iteration.lean_calls
        if iteration_sink is not None:
            iteration_sink(rollout_id, iteration)

    if analogy_hops is None:
        analogy_generator = AnalogyGenerator(
            generator,
            hidden_layer=config.model.hidden_layer,
            base_seed=config.run.seed,
            max_repair_calls=1,
        )
    else:
        analogy_generator = _ShuffledAnalogyGenerator(
            analogy_hops, base_seed=config.run.seed
        )
    tactic_generator = AnalogyTacticGenerator(
        generator,
        hidden_layer=config.model.hidden_layer,
        base_seed=config.run.seed,
        max_repair_calls=1,
        candidate_count=config.analogy.tactics_per_iteration,
    )

    def rerank_tactics(context, plan):
        scores = generator.score_completions(
            (
                Message(
                    "system",
                    "Rank candidate Lean tactics for the current proof state.",
                ),
                Message("user", render_tactic_prompt(context)),
            ),
            plan.tactics,
        )
        ranked = tuple(
            score.text
            for score in sorted(
                scores, key=lambda item: item.mean_log_likelihood, reverse=True
            )
        )
        return AnalogyTacticPlan(
            plan.predicted_state, ranked, plan.motivating_hop_id
        )
    controller = SurpriseController(
        SurpriseConfig(
            config.analogy.min_iterations,
            config.analogy.max_iterations,
            config.analogy.surprise_threshold,
            config.analogy.surprise_patience,
            config.analogy.min_surprise_improvement,
            config.analogy.improvement_patience,
        )
    )
    return AnalogyLoopRunner(
        proof_view_builder=build_views,
        action_sampler=sample_action,
        analogy_generator=analogy_generator,
        tactic_generator=tactic_generator,
        transition_comparator=lambda before, predicted, actual: compare_transitions(
            before,
            predicted,
            actual,
            generator,
            config.analogy.transition_embedding_layer,
        ),
        controller=controller,
        tactics_per_iteration=config.analogy.tactics_per_iteration,
        max_model_calls=config.analogy.max_model_calls,
        max_lean_calls=max_lean_calls,
        proof_view_lean_cost=proof_view_lean_cost,
        tactic_reranker=(rerank_tactics if config.analogy.rerank_tactics else None),
        tactic_repairer=tactic_generator,
        iteration_sink=record_iteration,
    )


def _new_generated_runner(config, generator, adapter, policy, *, condition,
                          iteration_sink, generated_sources, problem_id):
    if condition not in {"generated_unsteered", "generated_random", "generated_learned", "generated_shuffled"}:
        raise ValueError(f"unknown generated evaluation condition: {condition}")
    lean_budget = min(config.analogy.max_lean_calls, config.rl.max_rescue_verifier_calls)

    def build_observation(observation, structure, context):
        transition = None
        if observation.previous_state is not None:
            transition = json.dumps({name: getattr(observation, name) for name in (
                "previous_state", "current_state", "last_tactic", "predicted_transition",
                "actual_transition", "surprise", "surprise_delta",
            )}, ensure_ascii=False, sort_keys=True)
        diagnostic_context = replace(context, failed_tactics=tuple(
            f"{tactic}: {'; '.join(diagnostics)}" for tactic, diagnostics in observation.rejected_tactics
        ))
        return build_policy_representation(
            diagnostic_context, structure, generator, config.model.hidden_layer,
            previous_state=observation.previous_state, last_tactic=observation.last_tactic,
            verified_transition=transition, latest_source=observation.latest_source,
            latest_alignment=observation.latest_alignment, latest_transfer=observation.latest_transfer,
            scalar_features=(
                observation.iteration_index / max(config.analogy.max_iterations - 1, 1),
                observation.remaining_model_calls / config.analogy.max_model_calls,
                observation.remaining_lean_calls / lean_budget,
                float(observation.repeated_state),
            ),
        )

    def sample_action(observation, index):
        parameter = next(policy.parameters())
        if condition == "generated_unsteered":
            zero = parameter.new_zeros(())
            return PolicyAction(parameter.new_zeros(generator.hidden_size), zero, zero)
        rng = torch.Generator(device=parameter.device).manual_seed(config.run.seed + index)
        return policy.sample(observation, rng)

    options = dict(hidden_layer=config.model.hidden_layer, base_seed=config.run.seed, max_repair_calls=1)
    return GeneratedDomainAnalogyLoopRunner(
        structure_builder=adapter.proof_structure_for,
        observation_builder=build_observation, action_sampler=sample_action,
        source_generator=SourceAnalogyGenerator(generator, **options),
        alignment_generator=AlignmentGenerator(generator, **options),
        transfer_generator=ProofTransferGenerator(generator, **options),
        tactic_generator=TransferredTacticGenerator(generator, **options),
        transition_comparator=lambda before, predicted, actual: compare_transitions(
            before, predicted, actual, generator, config.analogy.transition_embedding_layer),
        controller=SurpriseController(SurpriseConfig(
            config.analogy.min_iterations, config.analogy.max_iterations,
            config.analogy.surprise_threshold, config.analogy.surprise_patience,
            config.analogy.min_surprise_improvement, config.analogy.improvement_patience)),
        tactics_per_iteration=config.analogy.tactics_per_iteration,
        max_model_calls=config.analogy.max_model_calls, max_lean_calls=lean_budget,
        structure_lean_cost=1, iteration_sink=iteration_sink, base_seed=config.run.seed,
        source_transform=(lambda source, need, observation: shuffle_generated_source(
            source, need, observation, generated_sources, problem_id=problem_id,
            seed=config.run.seed)) if condition == "generated_shuffled" else None,
    )


class _ShuffledAnalogyGenerator:
    """Replay cards from another test theorem while preserving target decoding."""

    def __init__(self, hops: tuple[AnalogyHop, ...], *, base_seed: int) -> None:
        if not hops:
            raise ValueError("shuffled analogy source must contain at least one hop")
        self.hops = hops
        self.base_seed = base_seed

    def generate(
        self, context, action, *, iteration_index: int, max_attempts: int | None = None
    ) -> GeneratedHop:
        del action, max_attempts
        source = self.hops[iteration_index % len(self.hops)]
        parent_id = context.stream.hops[-1].hop_id if context.stream.hops else None
        hop = replace(source, hop_id=f"hop-{iteration_index}", parent_id=parent_id)
        raw = json.dumps(asdict(source), sort_keys=True)
        return GeneratedHop(
            hop,
            (
                GenerationAttempt(
                    self.base_seed + iteration_index * 10,
                    raw,
                    GenerationStatus.VALID,
                ),
            ),
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


class _DeadlineCancellation:
    def __init__(self, seconds: float) -> None:
        self.deadline = time.monotonic() + float(seconds)

    def is_cancelled(self) -> bool:
        return time.monotonic() >= self.deadline


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
        trace = rollout.analogy_trace
        if trace is not None:
            for iteration in trace.iterations:
                writer.append_analogy_iteration(
                    iteration,
                    epoch=epoch,
                    phase=phase,
                    problem_id=episode.problem_id,
                    rollout_id=rollout.rollout_id,
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


def _live_iteration_sink(
    writer: RescueRunWriter, *, epoch: int, phase: str, problem_id: str
) -> Callable[[str, Any], None]:
    def append(rollout_id: str, iteration: Any) -> None:
        writer.append_analogy_iteration(
            iteration,
            epoch=epoch,
            phase=phase,
            problem_id=problem_id,
            rollout_id=rollout_id,
        )

    return append


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
    "AnalogyAssignment",
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
    "shuffle_analogy_assignments",
    "split_problems",
    "train_direct_rescue",
]
