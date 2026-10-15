"""Local text generation boundary for tactic-sequence proposals."""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from .config import AppConfig, ModelConfig
from .daydream import CuriosityOutcome
from .rescue import GenericRescueOutcome
from .rescue import (
    ProofRepresentation,
    ProofRescueContext,
    proof_prompt_hash,
    render_proof_embeddings,
)
from .lean_gym.corpus import load_problems
from .lean_gym.models import Problem, VerificationStatus
from .lean_gym.session import ProofSession, SessionError
from .lean_gym.traces import JsonlTraceWriter
from .lean_gym.verifier import LeanGymAdapter, Verifier
from .loop import (
    AcceptNovelState,
    LocalRescueSearch,
    NeverCancelled,
    ObservableStucknessPolicy,
    StateIdFingerprinter,
    WhitespaceTacticCanonicalizer,
)
from .records import (
    BaselineProblemResult,
    BaselineStatus,
    BenchmarkRun,
    CandidateOrigin,
    CapabilityResult,
    DiscardReason,
    Embedding,
    Message,
    SearchStatus,
    SymbolicState,
    TacticCandidate,
    TacticStatus,
)


class ModelOutputError(ValueError):
    """Raised when generated text is not a valid tactic sequence."""


class ModelConfigurationError(ValueError):
    """Raised when the requested local model configuration cannot run."""


class TacticRankingError(RuntimeError):
    """Raised when the conventional model cannot produce a candidate pool."""


class TextGenerator(Protocol):
    def generate(self, messages: tuple[Message, ...], *, seed: int) -> str: ...


class QwenGenerator:
    """Frozen Qwen chat generator with lazily imported ML dependencies."""

    def __init__(
        self,
        *,
        tokenizer: Any,
        model: Any,
        torch_module: Any,
        device: str,
        max_new_tokens: int,
        timeout_seconds: float,
        do_sample: bool = False,
        temperature: float = 1.0,
        top_p: float = 1.0,
    ) -> None:
        self._tokenizer = tokenizer
        self._model = model
        self._torch = torch_module
        self._device = device
        self._max_new_tokens = max_new_tokens
        self.timeout_seconds = timeout_seconds
        self._do_sample = do_sample
        self._temperature = temperature
        self._top_p = top_p
        self.model_revision = str(
            getattr(getattr(model, "config", None), "_commit_hash", None)
            or getattr(getattr(model, "config", None), "name_or_path", None)
            or "unknown"
        )
        raw_hidden_size = getattr(getattr(model, "config", None), "hidden_size", None)
        self.hidden_size = int(raw_hidden_size) if raw_hidden_size is not None else 0
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        model.eval()

    @classmethod
    def from_config(
        cls,
        config: ModelConfig,
        *,
        torch_module: Any | None = None,
        transformers_module: Any | None = None,
    ) -> "QwenGenerator":
        if torch_module is None:
            try:
                import torch as torch_module
            except ImportError as error:
                raise ModelConfigurationError(
                    "the model extra is not installed; install analogy[model]"
                ) from error

        device = cls._resolve_device(config.device, torch_module)
        if transformers_module is None:
            try:
                import transformers as transformers_module
            except ImportError as error:
                raise ModelConfigurationError(
                    "the model extra is not installed; install analogy[model]"
                ) from error

        tokenizer = transformers_module.AutoTokenizer.from_pretrained(config.name)
        if device == "mps":
            model = transformers_module.AutoModelForCausalLM.from_pretrained(
                config.name,
                torch_dtype="auto",
                low_cpu_mem_usage=True,
            )
            model.to("mps")
        else:
            device_map: Any = "auto" if config.device == "auto" else {"": device}
            model = transformers_module.AutoModelForCausalLM.from_pretrained(
                config.name,
                device_map=device_map,
                torch_dtype="auto",
            )
        actual_device = str(getattr(model, "device", device))
        return cls(
            tokenizer=tokenizer,
            model=model,
            torch_module=torch_module,
            device=actual_device,
            max_new_tokens=config.max_new_tokens,
            timeout_seconds=config.timeout_seconds,
            do_sample=config.do_sample,
            temperature=config.temperature,
            top_p=config.top_p,
        )

    @staticmethod
    def _resolve_device(requested: str, torch_module: Any) -> str:
        normalized = requested.casefold()
        if normalized == "cuda":
            if not torch_module.cuda.is_available():
                raise ModelConfigurationError("configured cuda device is unavailable")
            return "cuda"
        if normalized == "mps":
            if not torch_module.backends.mps.is_available():
                raise ModelConfigurationError("configured mps device is unavailable")
            return "mps"
        if normalized == "cpu":
            return "cpu"
        if normalized != "auto":
            raise ModelConfigurationError(f"unknown model device: {requested}")
        if torch_module.cuda.is_available():
            return "cuda"
        if torch_module.backends.mps.is_available():
            return "mps"
        return "cpu"

    def generate(self, messages: tuple[Message, ...], *, seed: int) -> str:
        model_inputs = self._prepare_generation(messages)
        return self._run_generation(model_inputs, seed=seed)

    def generate_with_delta(
        self,
        messages: tuple[Message, ...],
        *,
        delta: Any,
        layer: int,
        seed: int,
    ) -> str:
        if not isinstance(delta, self._torch.Tensor) or delta.ndim != 1:
            raise ModelConfigurationError(
                "latent delta must be a one-dimensional Torch tensor"
            )
        if not bool(self._torch.isfinite(delta).all().item()):
            raise ModelConfigurationError("latent delta must be finite")
        blocks = getattr(getattr(self._model, "model", None), "layers", None)
        if blocks is None:
            raise ModelConfigurationError("model does not expose decoder layers")
        resolved = _resolve_hidden_layer(layer, len(blocks))
        hidden_size = getattr(getattr(self._model, "config", None), "hidden_size", None)
        if hidden_size is None or int(hidden_size) != delta.numel():
            raise ModelConfigurationError(
                "latent delta dimension must match model hidden size"
            )
        model_inputs = self._prepare_generation(messages)
        applied = False

        def inject(_module: Any, _inputs: Any, output: Any) -> Any:
            nonlocal applied
            if applied:
                return output
            hidden = output[0] if isinstance(output, tuple) else output
            if not isinstance(hidden, self._torch.Tensor) or hidden.ndim != 3:
                raise ModelConfigurationError(
                    "decoder layer hidden output must have rank 3"
                )
            if hidden.shape[0] != 1 or hidden.shape[-1] != delta.numel():
                raise ModelConfigurationError(
                    "latent delta batch or dimension does not match hidden output"
                )
            changed = hidden.clone()
            changed[:, -1, :] = changed[:, -1, :] + delta.detach().to(
                device=hidden.device, dtype=hidden.dtype
            )
            applied = True
            if isinstance(output, tuple):
                return (changed, *output[1:])
            return changed

        handle = blocks[resolved].register_forward_hook(inject)
        try:
            return self._run_generation(model_inputs, seed=seed)
        finally:
            handle.remove()

    def _prepare_generation(self, messages: tuple[Message, ...]) -> Any:
        rendered = [{"role": item.role, "content": item.content} for item in messages]
        prompt = self._tokenizer.apply_chat_template(
            rendered,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        return self._tokenizer([prompt], return_tensors="pt").to(self._device)

    def _run_generation(self, model_inputs: Any, *, seed: int) -> str:
        self._torch.manual_seed(seed)
        started = time.monotonic()
        decoding: dict[str, Any] = {"do_sample": self._do_sample}
        if self._do_sample:
            decoding.update(temperature=self._temperature, top_p=self._top_p)
        generated = self._model.generate(
            **model_inputs,
            max_new_tokens=self._max_new_tokens,
            max_time=self.timeout_seconds,
            **decoding,
        )
        elapsed = time.monotonic() - started
        if elapsed >= self.timeout_seconds:
            raise TimeoutError(
                f"generation exceeded {self.timeout_seconds:g} seconds"
            )
        input_ids = model_inputs["input_ids"]
        input_length = (
            int(input_ids.shape[-1])
            if hasattr(input_ids, "shape")
            else len(input_ids[0])
        )
        new_tokens = generated[0][input_length:]
        return self._tokenizer.decode(new_tokens, skip_special_tokens=True)

    def embed_text(self, text: str, *, layer: int) -> Embedding:
        if not isinstance(text, str) or not text.strip():
            raise ModelConfigurationError("embedding text must be nonblank")
        if isinstance(layer, bool) or not isinstance(layer, int):
            raise ModelConfigurationError("hidden layer must be an integer")
        encoded = self._tokenizer(
            [text], return_tensors="pt", padding=True
        ).to(self._device)
        with self._torch.no_grad():
            output = self._model(
                **encoded, output_hidden_states=True, use_cache=False
            )
        hidden_states = getattr(output, "hidden_states", None)
        if not isinstance(hidden_states, (tuple, list)) or not hidden_states:
            raise ModelConfigurationError("model did not return hidden states")
        resolved = _resolve_hidden_layer(layer, len(hidden_states))
        hidden = hidden_states[resolved]
        if not hasattr(hidden, "ndim") or hidden.ndim != 3:
            raise ModelConfigurationError("hidden state must have rank 3")
        attention_mask = encoded.get("attention_mask")
        if attention_mask is None or attention_mask.ndim != 2:
            raise ModelConfigurationError("tokenizer must return a rank-2 attention mask")
        if hidden.shape[0] != 1 or attention_mask.shape[0] != 1:
            raise ModelConfigurationError("embedding extraction requires batch size one")
        token_count = int(attention_mask[0].sum().item())
        if token_count <= 0 or token_count > hidden.shape[1]:
            raise ModelConfigurationError("attention mask selects no valid final token")
        vector = hidden[0, token_count - 1].detach().float()
        norm = self._torch.linalg.vector_norm(vector)
        finite_vector = bool(self._torch.isfinite(vector).all().item())
        finite_norm = bool(self._torch.isfinite(norm).item())
        if not finite_vector or not finite_norm or norm.item() <= 0.0:
            raise ModelConfigurationError("embedding must be finite and nonzero")
        values = tuple(float(item) for item in (vector / norm).cpu().tolist())
        return Embedding(
            f"{self.model_revision}:layer={resolved}:final-nonpadding", values
        )


def _resolve_hidden_layer(layer: int, count: int) -> int:
    if isinstance(layer, bool) or not isinstance(layer, int):
        raise ModelConfigurationError("hidden layer must be an integer")
    resolved = layer if layer >= 0 else count + layer
    if resolved < 0 or resolved >= count:
        raise ModelConfigurationError(
            f"hidden layer {layer} is out of range for {count} returned layers"
        )
    return resolved


class QwenProofEncoder:
    def __init__(self, generator: Any, *, layer: int) -> None:
        if isinstance(layer, bool) or not isinstance(layer, int):
            raise ValueError("layer must be an integer")
        self._generator = generator
        self._layer = layer

    def encode(self, context: ProofRescueContext) -> ProofRepresentation:
        language, structure = render_proof_embeddings(context)
        return ProofRepresentation(
            context=context,
            language_prompt_hash=proof_prompt_hash(language),
            structure_prompt_hash=proof_prompt_hash(structure),
            language_embedding=self._generator.embed_text(
                language, layer=self._layer
            ),
            structure_embedding=self._generator.embed_text(
                structure, layer=self._layer
            ),
        )


class QwenTacticRanker:
    """Generate a normalized conventional tactic pool for one proof state."""

    def __init__(
        self,
        generator: TextGenerator,
        *,
        top_k: int,
        seed: int,
        failed_tactics_for: Any | None = None,
    ) -> None:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        self._generator = generator
        self._top_k = top_k
        self._seed = seed
        self._failed_tactics_for = failed_tactics_for or (lambda _state_id: ())

    def rank(
        self,
        state: Any,
        goal: Any,
    ) -> tuple[TacticCandidate, ...]:
        accepted = "\n".join(state.context) if state.context else "(none)"
        failed_values = tuple(self._failed_tactics_for(state.id))
        failed = "\n".join(failed_values) if failed_values else "(none)"
        messages = (
            Message(
                "system",
                "Propose ranked next tactics for Lean 4. Return only a nonempty "
                "JSON array of objects with exactly `tactic` and `score`. Each tactic "
                "must be one complete line. Scores are nonnegative relative weights. "
                "Use only visible identifiers and do not repeat failed tactics.",
            ),
            Message(
                "user",
                f"Overall target:\n{goal.expression}\n"
                f"Current goals:\n{state.expression}\n"
                f"Accepted prefix:\n{accepted}\n"
                f"Failed tactics:\n{failed}\n"
                f"Return at most {self._top_k} candidates.",
            ),
        )
        try:
            generated = self._generator.generate(messages, seed=self._seed)
            return parse_tactic_candidates(generated, self._top_k)
        except Exception as error:
            raise TacticRankingError(
                f"{type(error).__name__}: {error}"
            ) from error


def parse_tactic_sequence(text: str) -> tuple[str, ...]:
    """Parse a generated nonempty JSON array of nonblank tactic strings."""

    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ModelOutputError("model output must be valid JSON") from error
    if not isinstance(value, list) or not value:
        raise ModelOutputError("model output must be a nonempty JSON array")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ModelOutputError("every tactic must be a nonblank string")
    return tuple(value)


def parse_tactic_candidates(
    text: str,
    top_k: int,
) -> tuple[TacticCandidate, ...]:
    """Parse, deduplicate, truncate, and normalize ranked tactic weights."""

    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ModelOutputError("candidate output must be valid JSON") from error
    if not isinstance(value, list) or not value:
        raise ModelOutputError("candidate output must be a nonempty JSON array")

    order: list[str] = []
    weights: dict[str, float] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"tactic", "score"}:
            raise ModelOutputError(
                "every candidate must contain exactly tactic and score"
            )
        tactic = item["tactic"]
        if (
            not isinstance(tactic, str)
            or not tactic.strip()
            or "\n" in tactic
            or "\r" in tactic
            or "\x00" in tactic
        ):
            raise ModelOutputError("candidate tactics must be nonblank single lines")
        score = item["score"]
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
            or float(score) < 0.0
        ):
            raise ModelOutputError(
                "candidate scores must be finite nonnegative numbers"
            )
        canonical = " ".join(tactic.split())
        if canonical not in weights:
            order.append(canonical)
            weights[canonical] = float(score)
        else:
            weights[canonical] = max(weights[canonical], float(score))

    selected = order[:top_k]
    total = sum(weights[tactic] for tactic in selected)
    if total <= 0.0:
        raise ModelOutputError("selected candidate scores must not all be zero")
    return tuple(
        TacticCandidate(
            tactic,
            weights[tactic] / total,
            CandidateOrigin.ORDINARY,
        )
        for tactic in selected
    )


def capability_prompt(problem: Problem) -> tuple[Message, ...]:
    """Build a proof prompt without source metadata or reference proofs."""

    return (
        Message(
            "system",
            "You produce correct, short Lean 4 tactic proofs. Return only a "
            "nonempty JSON array of tactic strings, with no Markdown or explanation. "
            "Use only identifiers visible in the binders or introduced by you. "
            "Do not invent identifiers, theorem names, or tactic names. "
            "Binders are already in the local context; introduce only a target "
            "implication or negation. Do not invent identifiers.\n"
            "Adapt these Lean patterns to the visible names and types:\n"
            '(A : Prop) (ha : A) ⊢ A => ["exact ha"]\n'
            '(A : Prop) ⊢ A → A => ["intro ha", "exact ha"]\n'
            '(A B : Prop) (ha : A) (hb : B) ⊢ A ∧ B => '
            '["constructor", "exact ha", "exact hb"]\n'
            '(A B : Prop) (h : A ∧ B) ⊢ B ∧ A => '
            '["constructor", "exact h.2", "exact h.1"]\n'
            '(A B : Prop) (f : A → B) (ha : A) ⊢ B => '
            '["apply f", "exact ha"]\n'
            '(A B : Prop) (h : A ∨ B) ⊢ B ∨ A => '
            '["cases h with | inl ha => right; exact ha | inr hb => left; exact hb"]\n'
            '(A B : Prop) (h : A ↔ B) ⊢ A → B => '
            '["intro ha", "exact h.mp ha"]\n'
            '(A B : Prop) (f : A → B) (nb : ¬B) ⊢ ¬A => '
            '["intro ha", "apply nb", "exact f ha"]\n'
            '(none) ⊢ ¬False => ["intro h", "exact h"]\n'
            "Return tactics in execution order. No holes.",
        ),
        Message(
            "user",
            f"Binders: {problem.binders or '(none)'}\n"
            f"Target: {problem.target}\n"
            "Return only the JSON tactic array.",
        ),
    )


def _write_result(path: Path, result: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _failure_result(
    problem_id: str,
    tactics: tuple[str, ...],
    failure: str,
    started: float,
) -> CapabilityResult:
    return CapabilityResult(
        problem_id,
        tactics,
        False,
        failure,
        time.monotonic() - started,
    )


def run_capability(
    config: AppConfig,
    generator: TextGenerator,
    verifier: Verifier,
) -> tuple[CapabilityResult, ...]:
    """Generate and Lean-verify one tactic sequence for every benchmark problem."""

    run_started = time.monotonic()
    run_id = uuid.uuid4().hex
    run_directory = config.run.output_dir / "capability" / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    results: list[CapabilityResult] = []

    for problem in load_problems(config.benchmark.corpus):
        started = time.monotonic()
        tactics: tuple[str, ...] = ()
        try:
            generated = generator.generate(
                capability_prompt(problem),
                seed=config.run.seed,
            )
            tactics = parse_tactic_sequence(generated)
        except TimeoutError as error:
            result = _failure_result(
                problem.id,
                tactics,
                f"generation_timeout: {error}",
                started,
            )
        except ModelOutputError as error:
            result = _failure_result(
                problem.id,
                tactics,
                f"model_output: {error}",
                started,
            )
        except Exception as error:
            result = _failure_result(
                problem.id,
                tactics,
                f"generation_error: {type(error).__name__}: {error}",
                started,
            )
        else:
            trace_path = run_directory / f"{problem.id}.trace.jsonl"
            session: ProofSession | None = None
            solved = False
            failure: str | None = None
            try:
                session = ProofSession.start(
                    problem,
                    verifier,
                    JsonlTraceWriter(trace_path),
                    session_id=f"{run_id}-{problem.id}",
                )
                for tactic in tactics:
                    verification = session.submit_tactic(tactic)
                    if verification.status is VerificationStatus.COMPLETE:
                        solved = True
                        break
                    if not verification.accepted:
                        detail = "; ".join(
                            item.message for item in verification.diagnostics
                        )
                        failure = (
                            f"lean_rejected: {verification.status.value}"
                            + (f": {detail}" if detail else "")
                        )
                        break
                if not solved and failure is None:
                    failure = "proof_incomplete: generated tactics exhausted"
            except SessionError as error:
                failure = f"lean_session: {error}"
            finally:
                if session is not None:
                    session.close("capability")
            result = CapabilityResult(
                problem.id,
                tactics,
                solved,
                failure,
                time.monotonic() - started,
            )

        _write_result(run_directory / f"{problem.id}.jsonl", result)
        results.append(result)

    summary = {
        "run_id": run_id,
        "stage": "capability",
        "model": config.model.name,
        "model_revision": getattr(generator, "model_revision", config.model.name),
        "seed": config.run.seed,
        "decoding": {
            "do_sample": config.model.do_sample,
            "temperature": config.model.temperature,
            "top_p": config.model.top_p,
        },
        "attempted_count": len(results),
        "solved_count": sum(item.solved for item in results),
        "minimum_solved": config.experiment.minimum_solved,
        "elapsed_seconds": time.monotonic() - run_started,
        "results": [asdict(item) for item in results],
    }
    with (run_directory / "run.json").open(
        "x", encoding="utf-8", newline="\n"
    ) as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return tuple(results)


class _DisabledCuriosity:
    def search(self, *args: Any) -> CuriosityOutcome:
        return CuriosityOutcome()


class _DisabledRolloutRescue:
    def search(self, *args: Any) -> GenericRescueOutcome:
        return GenericRescueOutcome()


def _baseline_problem_result(
    problem_id: str,
    search_result: Any,
) -> BaselineProblemResult:
    attempts = tuple(
        attempt
        for expansion in search_result.ordinary_expansions
        for attempt in expansion.attempts
    )
    discarded = tuple(
        item
        for expansion in search_result.ordinary_expansions
        for item in expansion.discarded
    )
    if search_result.status is SearchStatus.COMPLETE:
        status = BaselineStatus.COMPLETE
    elif search_result.status is SearchStatus.BUDGET_EXHAUSTED:
        status = BaselineStatus.BUDGET_EXHAUSTED
    else:
        status = BaselineStatus.EXHAUSTED
    return BaselineProblemResult(
        problem_id=problem_id,
        status=status,
        proof=search_result.proof,
        failure=None,
        expansion_count=len(search_result.ordinary_expansions),
        verifier_call_count=len(attempts),
        invalid_tactic_count=sum(
            attempt.status is TacticStatus.ERROR for attempt in attempts
        ),
        repeated_state_count=sum(
            item.reason is DiscardReason.REPEATED_STATE for item in discarded
        ),
        no_progress_count=sum(
            item.reason is DiscardReason.NO_PROGRESS for item in discarded
        ),
        stuck_state_count=sum(
            decision.is_stuck for decision in search_result.stuckness_decisions
        ),
    )


def run_baseline(
    config: AppConfig,
    generator: TextGenerator,
    verifier: Verifier,
) -> BenchmarkRun:
    """Run the conventional top-k proof-state search without analogy or RL."""

    if config.analogy.enabled or config.rl.enabled:
        raise ModelConfigurationError(
            "baseline requires analogy.enabled=false and rl.enabled=false"
        )
    started = time.monotonic()
    run_id = uuid.uuid4().hex
    run_directory = config.run.output_dir / "baseline" / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    results: list[BaselineProblemResult] = []

    for problem in load_problems(config.benchmark.corpus):
        adapter = LeanGymAdapter(problem, verifier)
        ranker = QwenTacticRanker(
            generator,
            top_k=config.search.top_k,
            seed=config.run.seed,
            failed_tactics_for=adapter.failed_tactics_for,
        )
        search = LocalRescueSearch(
            tactic_ranker=ranker,
            verifier=adapter,
            stuckness_policy=ObservableStucknessPolicy(),
            curiosity_search=_DisabledCuriosity(),
            state_fingerprinter=StateIdFingerprinter(),
            tactic_canonicalizer=WhitespaceTacticCanonicalizer(),
            progress_policy=AcceptNovelState(),
            cancellation=NeverCancelled(),
            ordinary_width=config.search.top_k,
            max_expansions=config.search.max_steps,
        )
        try:
            result = search.search(
                adapter.initial_state(),
                SymbolicState(f"{problem.id}:goal", problem.target, "lean4"),
            )
            outcome = _baseline_problem_result(problem.id, result)
        except TacticRankingError as error:
            outcome = BaselineProblemResult(
                problem.id,
                BaselineStatus.MODEL_FAILURE,
                None,
                str(error),
                0,
                0,
                0,
                0,
                0,
                0,
            )
        except Exception as error:
            outcome = BaselineProblemResult(
                problem.id,
                BaselineStatus.VERIFIER_FAILURE,
                None,
                f"{type(error).__name__}: {error}",
                0,
                0,
                0,
                0,
                0,
                0,
            )
        _write_result(run_directory / f"{problem.id}.jsonl", outcome)
        results.append(outcome)

    benchmark_run = BenchmarkRun(
        run_id,
        tuple(results),
        time.monotonic() - started,
    )
    summary = {
        "run_id": run_id,
        "stage": "baseline",
        "model": config.model.name,
        "model_revision": getattr(generator, "model_revision", config.model.name),
        "seed": config.run.seed,
        "top_k": config.search.top_k,
        "max_steps": config.search.max_steps,
        "attempted_count": benchmark_run.attempted_count,
        "solved_count": benchmark_run.solved_count,
        "model_failure_count": benchmark_run.model_failure_count,
        "invalid_tactic_count": benchmark_run.invalid_tactic_count,
        "repeated_state_count": benchmark_run.repeated_state_count,
        "no_progress_count": benchmark_run.no_progress_count,
        "stuck_state_count": benchmark_run.stuck_state_count,
        "verifier_call_count": benchmark_run.verifier_call_count,
        "elapsed_seconds": benchmark_run.elapsed_seconds,
        "results": [asdict(item) for item in benchmark_run.results],
    }
    with (run_directory / "run.json").open(
        "x", encoding="utf-8", newline="\n"
    ) as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return benchmark_run


__all__ = [
    "ModelConfigurationError",
    "ModelOutputError",
    "QwenGenerator",
    "QwenProofEncoder",
    "QwenTacticRanker",
    "TacticRankingError",
    "TextGenerator",
    "capability_prompt",
    "parse_tactic_sequence",
    "parse_tactic_candidates",
    "run_capability",
    "run_baseline",
]
