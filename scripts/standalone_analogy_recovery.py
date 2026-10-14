#!/usr/bin/env python3
"""Standalone poetry/physics retrieval experiment for Lean tactic recovery.

This file intentionally does not import the ``analogy`` package.  It owns
its data, model, retrieval, verification, and reporting boundaries so the
experiment can be inspected and run independently of the numbered plans.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import random
import re
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol


class ExperimentError(RuntimeError):
    """Raised when experiment inputs or runtime behavior are invalid."""


class ModelOutputError(ExperimentError):
    """Raised when generated or embedded model output violates its schema."""


class ModelConfigurationError(ExperimentError):
    """Raised when the configured model cannot satisfy the experiment."""


def _nonblank(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        qualifier = "string" if allow_empty else "nonblank string"
        raise ExperimentError(f"{field} must be a {qualifier}")
    return value


@dataclass(frozen=True, slots=True)
class Problem:
    id: str
    binders: str
    target: str
    title: str = ""
    difficulty: int = 1

    def __post_init__(self) -> None:
        _nonblank(self.id, "problem.id")
        _nonblank(self.binders, "problem.binders", allow_empty=True)
        _nonblank(self.target, "problem.target")
        if isinstance(self.difficulty, bool) or not isinstance(self.difficulty, int) or self.difficulty <= 0:
            raise ExperimentError("problem.difficulty must be a positive integer")

    def theorem_header(self, name: str = "current_problem") -> str:
        binder_segment = f" {self.binders}" if self.binders else ""
        return f"theorem {name}{binder_segment} : {self.target} := by"


@dataclass(frozen=True, slots=True)
class ReferenceProof:
    problem_id: str
    tactics: tuple[str, ...]

    def __post_init__(self) -> None:
        _nonblank(self.problem_id, "reference_proof.problem_id")
        normalized = tuple(self.tactics)
        if not normalized or any(not isinstance(item, str) or not item.strip() for item in normalized):
            raise ExperimentError("reference_proof.tactics must be nonempty strings")
        object.__setattr__(self, "tactics", normalized)


@dataclass(frozen=True, slots=True)
class Transition:
    id: str
    domain: str
    before: str
    after: str
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        _nonblank(self.id, "transition.id")
        if self.domain not in {"poetry", "physics"}:
            raise ExperimentError("transition.domain must be poetry or physics")
        _nonblank(self.before, "transition.before")
        _nonblank(self.after, "transition.after")
        if not isinstance(self.provenance, Mapping):
            raise ExperimentError("transition.provenance must be a mapping")
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


class Condition(str, Enum):
    NONE = "none"
    RANDOM = "random"
    ANALOGY = "analogy"


@dataclass(frozen=True, slots=True)
class Message:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise ExperimentError("message.role is invalid")
        _nonblank(self.content, "message.content")


@dataclass(frozen=True, slots=True)
class Embedding:
    values: tuple[float, ...]
    layer: int
    token_index: int
    model_revision: str
    text_hash: str

    def __post_init__(self) -> None:
        values = tuple(float(item) for item in self.values)
        if not values or not all(math.isfinite(item) for item in values):
            raise ModelOutputError("embedding values must be nonempty and finite")
        norm = math.sqrt(sum(item * item for item in values))
        if norm == 0.0:
            raise ModelOutputError("embedding vector must not be zero")
        object.__setattr__(self, "values", values)
        if self.token_index < 0:
            raise ModelOutputError("embedding token index must be nonnegative")
        _nonblank(self.model_revision, "embedding.model_revision")
        _nonblank(self.text_hash, "embedding.text_hash")

    @property
    def dimension(self) -> int:
        return len(self.values)


class TextModel(Protocol):
    model_revision: str

    def generate(self, messages: tuple[Message, ...], *, seed: int) -> str: ...

    def embed(self, text: str, layer: int) -> Embedding: ...


CANDIDATE_PROMPT_VERSION = "candidate-v1"
PROOF_NEED_PROMPT_VERSION = "proof-need-v1"
TRANSFORMATION_PROMPT_VERSION = "transformation-v1"
RANKING_PROMPT_VERSION = "ranking-v1"
PROMPT_VERSIONS = MappingProxyType(
    {
        "candidate": CANDIDATE_PROMPT_VERSION,
        "proof_need": PROOF_NEED_PROMPT_VERSION,
        "transformation": TRANSFORMATION_PROMPT_VERSION,
        "ranking": RANKING_PROMPT_VERSION,
    }
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _context_block(values: tuple[str, ...]) -> str:
    return "\n".join(values) if values else "(none)"


def candidate_prompt(
    problem: Problem,
    state: str,
    accepted: tuple[str, ...],
    failed: tuple[str, ...],
    top_k: int,
) -> tuple[Message, ...]:
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ExperimentError("top_k must be a positive integer")
    payload = masked_problem_payload(problem)
    return (
        Message(
            "system",
            f"Prompt {CANDIDATE_PROMPT_VERSION}. Propose ranked next Lean 4 tactics. "
            "Return only a nonempty JSON array of unique, complete, single-line "
            "tactic strings. Use only visible identifiers and do not repeat failures.",
        ),
        Message(
            "user",
            f"Binders: {payload['binders'] or '(none)'}\n"
            f"Overall target: {payload['target']}\n"
            f"Current goals:\n{state}\n"
            f"Accepted prefix:\n{_context_block(accepted)}\n"
            f"Failed tactics:\n{_context_block(failed)}\n"
            f"Return at most {top_k} tactics.",
        ),
    )


def proof_need_prompt(
    problem: Problem,
    state: str,
    accepted: tuple[str, ...],
    failed: tuple[str, ...],
) -> tuple[Message, ...]:
    payload = masked_problem_payload(problem)
    return (
        Message(
            "system",
            f"Prompt {PROOF_NEED_PROMPT_VERSION}. Describe the abstract structural "
            "change needed next. Do not name Lean tactics or copy identifiers. "
            "Return one concise sentence only.",
        ),
        Message(
            "user",
            f"Overall target: {payload['target']}\nCurrent goals:\n{state}\n"
            f"Accepted progress:\n{_context_block(accepted)}\n"
            f"Failed attempts:\n{_context_block(failed)}",
        ),
    )


def transformation_prompt(transition: Transition) -> tuple[Message, ...]:
    return (
        Message(
            "system",
            f"Prompt {TRANSFORMATION_PROMPT_VERSION}. State only the domain-neutral "
            "structural change from before to after. Do not use Lean syntax or tactic "
            "names. Return exactly one JSON object with key transformation.",
        ),
        Message(
            "user",
            f"Domain: {transition.domain}\nBefore: {transition.before}\nAfter: {transition.after}",
        ),
    )


def ranking_prompt(
    problem: Problem,
    state: str,
    candidates: tuple[str, ...],
    transformations: tuple[str, ...],
) -> tuple[Message, ...]:
    payload = masked_problem_payload(problem)
    numbered = "\n".join(f"{index}: {item}" for index, item in enumerate(candidates))
    analogies = _context_block(transformations)
    return (
        Message(
            "system",
            f"Prompt {RANKING_PROMPT_VERSION}. Rank only the supplied Lean tactic "
            "indices. Return a JSON array of unique integer indices. Do not write tactics.",
        ),
        Message(
            "user",
            f"Overall target: {payload['target']}\nCurrent goals:\n{state}\n"
            f"Structural transformations:\n{analogies}\nCandidates:\n{numbered}",
        ),
    )


def parse_candidates(text: str, top_k: int) -> tuple[str, ...]:
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ExperimentError("top_k must be a positive integer")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ModelOutputError("candidate output must be valid JSON") from error
    if not isinstance(value, list) or not value:
        raise ModelOutputError("candidate output must be a nonempty array")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip() or any(character in item for character in "\r\n\x00"):
            raise ModelOutputError("candidate tactics must be nonblank single lines")
        canonical = " ".join(item.split())
        if canonical in seen:
            raise ModelOutputError("candidate tactics must be unique")
        seen.add(canonical)
        result.append(canonical)
    return tuple(result[:top_k])


_TACTIC_WORDS = re.compile(r"\b(?:intro|exact|apply|cases|constructor|simp|aesop|assumption|rfl)\b", re.IGNORECASE)


def parse_transformation(text: str) -> str:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ModelOutputError("transformation output must be valid JSON") from error
    if not isinstance(value, dict) or set(value) != {"transformation"}:
        raise ModelOutputError("transformation output must contain exactly transformation")
    transformation = value["transformation"]
    if not isinstance(transformation, str) or not transformation.strip():
        raise ModelOutputError("transformation must be a nonblank string")
    normalized = " ".join(transformation.split())
    if _TACTIC_WORDS.search(normalized):
        raise ModelOutputError("transformation must not contain Lean tactic names")
    return normalized


def parse_ranking(text: str, candidate_count: int) -> tuple[int, ...]:
    if isinstance(candidate_count, bool) or not isinstance(candidate_count, int) or candidate_count <= 0:
        raise ExperimentError("candidate_count must be a positive integer")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ModelOutputError("ranking output must be valid JSON") from error
    if not isinstance(value, list):
        raise ModelOutputError("ranking output must be an array")
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item < candidate_count:
            raise ModelOutputError("ranking indices are out of range")
        if item in seen:
            raise ModelOutputError("ranking indices must be unique")
        seen.add(item)
        result.append(item)
    result.extend(index for index in range(candidate_count) if index not in seen)
    return tuple(result)


class QwenModel:
    """Frozen local Qwen boundary for deterministic generation and embeddings."""

    def __init__(
        self,
        *,
        tokenizer: Any,
        model: Any,
        torch_module: Any,
        device: str,
        max_new_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self._tokenizer = tokenizer
        self._model = model
        self._torch = torch_module
        self._device = device
        self._max_new_tokens = max_new_tokens
        self._timeout_seconds = timeout_seconds
        self.model_revision = str(
            getattr(getattr(model, "config", None), "_commit_hash", None)
            or getattr(getattr(model, "config", None), "name_or_path", None)
            or "unknown"
        )
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        model.eval()

    @classmethod
    def load(
        cls,
        name: str,
        device: str,
        max_new_tokens: int,
        timeout_seconds: float,
    ) -> QwenModel:
        try:
            import torch
            import transformers
        except ImportError as error:
            raise ModelConfigurationError("torch and transformers are required") from error
        normalized = device.casefold()
        if normalized == "auto":
            if torch.cuda.is_available():
                normalized = "cuda"
            elif torch.backends.mps.is_available():
                normalized = "mps"
            else:
                normalized = "cpu"
        if normalized not in {"cpu", "cuda", "mps"}:
            raise ModelConfigurationError(f"unknown model device {device!r}")
        if normalized == "cuda" and not torch.cuda.is_available():
            raise ModelConfigurationError("configured cuda device is unavailable")
        if normalized == "mps" and not torch.backends.mps.is_available():
            raise ModelConfigurationError("configured mps device is unavailable")
        tokenizer = transformers.AutoTokenizer.from_pretrained(name)
        model = transformers.AutoModelForCausalLM.from_pretrained(name, torch_dtype="auto")
        model.to(normalized)
        return cls(
            tokenizer=tokenizer,
            model=model,
            torch_module=torch,
            device=normalized,
            max_new_tokens=max_new_tokens,
            timeout_seconds=timeout_seconds,
        )

    def _render(self, messages: tuple[Message, ...]) -> str:
        return self._tokenizer.apply_chat_template(
            [{"role": item.role, "content": item.content} for item in messages],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )

    def generate(self, messages: tuple[Message, ...], *, seed: int) -> str:
        prompt = self._render(messages)
        inputs = self._tokenizer([prompt], return_tensors="pt").to(self._device)
        self._torch.manual_seed(seed)
        started = time.monotonic()
        with self._torch.inference_mode():
            generated = self._model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=self._max_new_tokens,
                max_time=self._timeout_seconds,
            )
        if time.monotonic() - started >= self._timeout_seconds:
            raise TimeoutError(f"generation exceeded {self._timeout_seconds:g} seconds")
        input_length = int(inputs["input_ids"].shape[-1])
        return self._tokenizer.decode(generated[0][input_length:], skip_special_tokens=True)

    def embed(self, text: str, layer: int) -> Embedding:
        _nonblank(text, "embedding text")
        inputs = self._tokenizer([text], return_tensors="pt").to(self._device)
        with self._torch.inference_mode():
            output = self._model(**inputs, output_hidden_states=True, use_cache=False)
        hidden_states = tuple(output.hidden_states)
        resolved = layer if layer >= 0 else len(hidden_states) + layer
        if not 0 <= resolved < len(hidden_states):
            raise ModelConfigurationError(
                f"embedding layer {layer} is outside [-{len(hidden_states)}, {len(hidden_states) - 1}]"
            )
        attention = inputs["attention_mask"]
        token_index = int(attention[0].sum().item()) - 1
        vector = hidden_states[resolved][0, token_index].to(dtype=self._torch.float32)
        if not bool(self._torch.isfinite(vector).all().item()):
            raise ModelOutputError("embedding vector must be finite")
        norm = self._torch.linalg.vector_norm(vector)
        if not math.isfinite(float(norm.item())) or float(norm.item()) == 0.0:
            raise ModelOutputError("embedding vector must be finite and nonzero")
        normalized = vector / norm
        values = tuple(float(item) for item in normalized.detach().cpu().tolist())
        return Embedding(
            values=values,
            layer=resolved,
            token_index=token_index,
            model_revision=self.model_revision,
            text_hash=sha256_text(text),
        )


def masked_problem_payload(problem: Problem) -> dict[str, str]:
    """Return the only benchmark fields allowed to enter inference prompts."""

    if not isinstance(problem, Problem):
        raise ExperimentError("problem must be a Problem")
    return {
        "problem_id": problem.id,
        "binders": problem.binders,
        "target": problem.target,
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExperimentError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise ExperimentError(f"{path} must contain a JSON object")
    return value


def load_benchmark(
    problem_path: Path,
    proof_path: Path,
) -> tuple[tuple[Problem, ...], dict[str, ReferenceProof]]:
    """Load the raw benchmark while keeping reference proofs separate."""

    problem_root = _read_json_object(Path(problem_path))
    proof_root = _read_json_object(Path(proof_path))
    if problem_root.get("schema_version") != 1 or proof_root.get("schema_version") != 1:
        raise ExperimentError("benchmark schema_version must be 1")
    raw_problems = problem_root.get("problems")
    raw_proofs = proof_root.get("proofs")
    if not isinstance(raw_problems, list) or not isinstance(raw_proofs, list):
        raise ExperimentError("benchmark problems and proofs must be arrays")

    problems: list[Problem] = []
    seen_problem_ids: set[str] = set()
    for index, raw in enumerate(raw_problems):
        if not isinstance(raw, dict):
            raise ExperimentError(f"problems[{index}] must be an object")
        try:
            problem = Problem(
                id=raw["id"],
                binders=raw["binders"],
                target=raw["target"],
                title=raw.get("title", ""),
                difficulty=raw["difficulty"],
            )
        except KeyError as error:
            raise ExperimentError(f"problems[{index}] is missing {error.args[0]}") from error
        if problem.id in seen_problem_ids:
            raise ExperimentError(f"duplicate problem id {problem.id!r}")
        seen_problem_ids.add(problem.id)
        problems.append(problem)

    proofs: dict[str, ReferenceProof] = {}
    for index, raw in enumerate(raw_proofs):
        if not isinstance(raw, dict):
            raise ExperimentError(f"proofs[{index}] must be an object")
        try:
            proof = ReferenceProof(raw["problem_id"], tuple(raw["tactics"]))
        except (KeyError, TypeError) as error:
            raise ExperimentError(f"proofs[{index}] is malformed") from error
        if proof.problem_id in proofs:
            raise ExperimentError(f"duplicate proof id {proof.problem_id!r}")
        proofs[proof.problem_id] = proof

    proof_ids = set(proofs)
    if seen_problem_ids != proof_ids:
        missing = sorted(seen_problem_ids - proof_ids)
        extra = sorted(proof_ids - seen_problem_ids)
        raise ExperimentError(f"reference coverage mismatch: missing={missing}, extra={extra}")
    return tuple(sorted(problems, key=lambda item: (item.difficulty, item.id))), proofs


def _transition_id(domain: str, source_id: str, before: str, after: str) -> str:
    content = json.dumps(
        [domain, source_id, before, after],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]


def iter_poetry_transitions(path: Path) -> Iterator[Transition]:
    """Yield adjacent nonblank lines without crossing Gutenberg works."""

    previous_gid: str | None = None
    previous_line: str | None = None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    previous_gid = None
                    previous_line = None
                    continue
                if not isinstance(raw, dict):
                    previous_gid = None
                    previous_line = None
                    continue
                gid = raw.get("gid")
                text = raw.get("s")
                if not isinstance(gid, str) or not gid.strip() or not isinstance(text, str) or not text.strip():
                    previous_gid = None
                    previous_line = None
                    continue
                text = " ".join(text.split())
                if gid == previous_gid and previous_line is not None and previous_line != text:
                    source_id = f"gutenberg:{gid}:{line_number - 1}-{line_number}"
                    yield Transition(
                        _transition_id("poetry", source_id, previous_line, text),
                        "poetry",
                        previous_line,
                        text,
                        {"gid": gid, "line_end": line_number, "source_id": source_id},
                    )
                previous_gid = gid
                previous_line = text
    except OSError as error:
        raise ExperimentError(f"cannot read poetry corpus {path}: {error}") from error


def iter_physics_transitions(path: Path) -> Iterator[Transition]:
    """Yield adjacent sentence transitions from every valid TRIP story."""

    try:
        handle = Path(path).open(encoding="utf-8")
    except OSError as error:
        raise ExperimentError(f"cannot read physics corpus {path}: {error}") from error
    with handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or not isinstance(record.get("stories"), list):
                continue
            example_id = record.get("example_id")
            for story_position, story in enumerate(record["stories"]):
                if not isinstance(story, dict) or not isinstance(story.get("sentences"), list):
                    continue
                sentences = [
                    " ".join(item.split())
                    for item in story["sentences"]
                    if isinstance(item, str) and item.strip()
                ]
                story_id = story.get("story_id", story_position)
                for sentence_index, (before, after) in enumerate(zip(sentences, sentences[1:])):
                    if before == after:
                        continue
                    source_id = f"trip:{example_id}:{story_id}:{sentence_index}-{sentence_index + 1}"
                    provenance = {
                        "source_id": source_id,
                        "line": line_number,
                        "example_id": example_id,
                        "story_id": story_id,
                        "plausible": story.get("plausible"),
                        "breakpoint": story.get("breakpoint", record.get("breakpoint")),
                        "confl_sents": story.get("confl_sents", record.get("confl_sents", [])),
                        "confl_pairs": story.get("confl_pairs", record.get("confl_pairs", [])),
                    }
                    yield Transition(
                        _transition_id("physics", source_id, before, after),
                        "physics",
                        before,
                        after,
                        provenance,
                    )


def _reservoir_sample(
    values: Iterable[Transition],
    count: int,
    rng: random.Random,
) -> tuple[Transition, ...]:
    reservoir: list[Transition] = []
    seen_ids: set[str] = set()
    eligible_count = 0
    for item in values:
        if not isinstance(item, Transition):
            raise ExperimentError("transition streams must contain Transition values")
        if item.id in seen_ids:
            continue
        seen_ids.add(item.id)
        eligible_count += 1
        if len(reservoir) < count:
            reservoir.append(item)
            continue
        replacement = rng.randrange(eligible_count)
        if replacement < count:
            reservoir[replacement] = item
    return tuple(reservoir)


def sample_transitions(
    poetry: Iterable[Transition],
    physics: Iterable[Transition],
    per_domain: int,
    seed: int,
) -> tuple[Transition, ...]:
    """Reservoir-sample a deterministic balanced index without full materialization."""

    if isinstance(per_domain, bool) or not isinstance(per_domain, int) or per_domain <= 0:
        raise ExperimentError("per_domain must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ExperimentError("seed must be an integer")
    poetry_sample = _reservoir_sample(poetry, per_domain, random.Random(f"{seed}:poetry"))
    physics_sample = _reservoir_sample(physics, per_domain, random.Random(f"{seed}:physics"))
    return poetry_sample + physics_sample

