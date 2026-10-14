#!/usr/bin/env python3
"""Standalone poetry/physics retrieval experiment for Lean tactic recovery.

This file intentionally does not import the ``analogy`` package.  It owns
its data, model, retrieval, verification, and reporting boundaries so the
experiment can be inspected and run independently of the numbered plans.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import heapq
import inspect
import json
import math
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
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


class ExperimentRunIncomplete(ExperimentError):
    """Raised after preserving artifacts for a run with operational failures."""


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
    proof_need: str | None = None,
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
            f"Structural proof need:\n{proof_need or '(none)'}\n"
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

    def generate(
        self,
        messages: tuple[Message, ...],
        *,
        seed: int,
        timeout_seconds: float | None = None,
    ) -> str:
        prompt = self._render(messages)
        inputs = self._tokenizer([prompt], return_tensors="pt").to(self._device)
        self._torch.manual_seed(seed)
        effective_timeout = self._timeout_seconds
        if timeout_seconds is not None:
            effective_timeout = min(effective_timeout, max(float(timeout_seconds), 0.001))
        started = time.monotonic()
        with self._torch.inference_mode():
            generated = self._model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=self._max_new_tokens,
                max_time=effective_timeout,
            )
        if time.monotonic() - started >= effective_timeout:
            raise TimeoutError(f"generation exceeded {effective_timeout:g} seconds")
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


@dataclass(frozen=True, slots=True)
class IndexedTransition:
    transition: Transition
    transformation: str
    embedding: Embedding
    cache_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.transition, Transition):
            raise ExperimentError("indexed transition requires a Transition")
        _nonblank(self.transformation, "indexed_transition.transformation")
        if not isinstance(self.embedding, Embedding):
            raise ExperimentError("indexed transition requires an Embedding")
        _nonblank(self.cache_key, "indexed_transition.cache_key")


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    item: IndexedTransition
    similarity: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.item, IndexedTransition):
            raise ExperimentError("retrieval hit requires an IndexedTransition")
        if self.similarity is not None and not math.isfinite(float(self.similarity)):
            raise ExperimentError("retrieval similarity must be finite or None")


@dataclass(frozen=True, slots=True)
class ProofNeed:
    description: str
    embedding: Embedding

    def __post_init__(self) -> None:
        _nonblank(self.description, "proof_need.description")
        if not isinstance(self.embedding, Embedding):
            raise ExperimentError("proof need requires an Embedding")


@dataclass(frozen=True, slots=True)
class RankingDecision:
    candidates: tuple[str, ...]
    indices: tuple[int, ...]
    hits: tuple[RetrievalHit, ...] = ()
    proof_need: ProofNeed | None = None

    def __post_init__(self) -> None:
        candidates = tuple(self.candidates)
        indices = tuple(self.indices)
        hits = tuple(self.hits)
        if not candidates or len(indices) != len(candidates) or set(indices) != set(range(len(candidates))):
            raise ExperimentError("ranking decision must be a permutation of candidates")
        if not all(isinstance(item, RetrievalHit) for item in hits):
            raise ExperimentError("ranking hits must contain RetrievalHit values")
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "indices", indices)
        object.__setattr__(self, "hits", hits)

    @property
    def ordered_candidates(self) -> tuple[str, ...]:
        return tuple(self.candidates[index] for index in self.indices)


def derived_seed(seed: int, *parts: str) -> int:
    material = "\x1f".join((str(seed), *parts))
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")


def transformation_cache_key(
    transition: Transition,
    model_revision: str,
    layer: int,
    prompt_version: str,
) -> str:
    payload = {
        "transition": {
            "id": transition.id,
            "domain": transition.domain,
            "before": transition.before,
            "after": transition.after,
            "provenance": dict(transition.provenance),
        },
        "model_revision": model_revision,
        "layer": layer,
        "prompt_version": prompt_version,
        "pooling": "final-nonpadding-l2-float32-v1",
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(encoded)


def _cached_index_item(path: Path, transition: Transition, expected_key: str) -> IndexedTransition:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExperimentError(f"invalid transition cache {path}: {error}") from error
    if not isinstance(raw, dict) or raw.get("cache_key") != expected_key or raw.get("transition_id") != transition.id:
        raise ExperimentError(f"transition cache identity mismatch: {path}")
    embedding_raw = raw.get("embedding")
    if not isinstance(embedding_raw, dict):
        raise ExperimentError(f"transition cache embedding is invalid: {path}")
    try:
        embedding = Embedding(
            tuple(embedding_raw["values"]),
            embedding_raw["layer"],
            embedding_raw["token_index"],
            embedding_raw["model_revision"],
            embedding_raw["text_hash"],
        )
        transformation = _nonblank(raw["transformation"], "cached transformation")
    except (KeyError, TypeError, ValueError) as error:
        raise ExperimentError(f"transition cache content is invalid: {path}") from error
    norm = math.sqrt(sum(value * value for value in embedding.values))
    if not math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-6):
        raise ExperimentError(f"transition cache vector is not normalized: {path}")
    return IndexedTransition(transition, transformation, embedding, expected_key)


def build_index(
    transitions: Iterable[Transition],
    model: TextModel,
    cache_dir: Path | None,
    layer: int,
) -> tuple[IndexedTransition, ...]:
    if cache_dir is not None:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
    indexed: list[IndexedTransition] = []
    for transition in transitions:
        key = transformation_cache_key(
            transition,
            model.model_revision,
            layer,
            TRANSFORMATION_PROMPT_VERSION,
        )
        cache_path = None if cache_dir is None else Path(cache_dir) / f"{key}.json"
        if cache_path is not None and cache_path.exists():
            indexed.append(_cached_index_item(cache_path, transition, key))
            continue
        generated = model.generate(
            transformation_prompt(transition),
            seed=derived_seed(0, transition.id, TRANSFORMATION_PROMPT_VERSION),
        )
        transformation = parse_transformation(generated)
        embedding = model.embed(transformation, layer)
        item = IndexedTransition(transition, transformation, embedding, key)
        if cache_path is not None:
            payload = {
                "cache_key": key,
                "transition_id": transition.id,
                "transformation": transformation,
                "embedding": {
                    "values": list(embedding.values),
                    "layer": embedding.layer,
                    "token_index": embedding.token_index,
                    "model_revision": embedding.model_revision,
                    "text_hash": embedding.text_hash,
                },
            }
            temporary = cache_path.with_name(f".{cache_path.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(cache_path)
        indexed.append(item)
    return tuple(indexed)


def _validated_retrieval(index: tuple[IndexedTransition, ...], count: int) -> None:
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ExperimentError("retrieval count must be a positive integer")
    if not index:
        raise ExperimentError("retrieval index is empty")
    if count > len(index):
        raise ExperimentError("retrieval count exceeds the eligible index")


def cosine_retrieve(
    query: Embedding | tuple[float, ...],
    index: tuple[IndexedTransition, ...],
    count: int,
) -> tuple[RetrievalHit, ...]:
    _validated_retrieval(index, count)
    values = query.values if isinstance(query, Embedding) else tuple(float(item) for item in query)
    if not values or not all(math.isfinite(item) for item in values):
        raise ExperimentError("query vector must be nonempty and finite")
    norm = math.sqrt(sum(item * item for item in values))
    if norm == 0.0:
        raise ExperimentError("query vector must not be zero")
    scored: list[RetrievalHit] = []
    for item in index:
        other = item.embedding.values
        if len(other) != len(values):
            raise ExperimentError("retrieval embedding dimensions do not match")
        other_norm = math.sqrt(sum(value * value for value in other))
        similarity = sum(left * right for left, right in zip(values, other)) / (norm * other_norm)
        scored.append(RetrievalHit(item, similarity))
    scored.sort(key=lambda hit: (-float(hit.similarity), hit.item.transition.id))
    return tuple(scored[:count])


def random_retrieve(
    index: tuple[IndexedTransition, ...],
    count: int,
    seed: int,
) -> tuple[RetrievalHit, ...]:
    _validated_retrieval(index, count)
    selected = random.Random(seed).sample(list(index), count)
    return tuple(RetrievalHit(item, None) for item in selected)


def _generate_before_deadline(
    model: TextModel,
    messages: tuple[Message, ...],
    *,
    seed: int,
    deadline: float | None,
) -> str:
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        raise TimeoutError("theorem timeout")
    parameters = inspect.signature(model.generate).parameters
    if "timeout_seconds" in parameters:
        result = model.generate(messages, seed=seed, timeout_seconds=remaining)
    else:
        result = model.generate(messages, seed=seed)
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("theorem timeout")
    return result


def rank_for_condition(
    condition: Condition,
    problem: Problem,
    state: str,
    candidates: tuple[str, ...],
    proof_need: ProofNeed | None,
    index: tuple[IndexedTransition, ...],
    model: TextModel,
    retrieval_count: int,
    seed: int,
    *,
    layer: int = -1,
    accepted: tuple[str, ...] = (),
    failed: tuple[str, ...] = (),
    deadline: float | None = None,
) -> RankingDecision:
    if not isinstance(condition, Condition):
        raise ExperimentError("condition must be a Condition")
    candidates = tuple(candidates)
    if not candidates:
        raise ExperimentError("candidate pool must not be empty")
    hits: tuple[RetrievalHit, ...] = ()
    current_need = proof_need
    if condition is not Condition.NONE:
        if current_need is None:
            description = _generate_before_deadline(
                model,
                proof_need_prompt(problem, state, accepted, failed),
                seed=derived_seed(seed, problem.id, sha256_text(state), "proof-need"),
                deadline=deadline,
            ).strip()
            if not description:
                raise ModelOutputError("proof need description must not be blank")
            current_need = ProofNeed(description, model.embed(description, layer))
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("theorem timeout")
        if condition is Condition.ANALOGY:
            hits = cosine_retrieve(current_need.embedding, index, retrieval_count)
        else:
            hits = random_retrieve(
                index,
                retrieval_count,
                derived_seed(seed, problem.id, sha256_text(state), "random-retrieval"),
            )
    transformations = tuple(hit.item.transformation for hit in hits)
    generated = _generate_before_deadline(
        model,
        ranking_prompt(
            problem,
            state,
            candidates,
            transformations,
            current_need.description if current_need is not None else None,
        ),
        seed=derived_seed(seed, problem.id, sha256_text(state), condition.value, "ranking"),
        deadline=deadline,
    )
    indices = parse_ranking(generated, len(candidates))
    return RankingDecision(candidates, indices, hits, current_need)


class CandidatePoolStore:
    """Generate each conventional tactic pool once per normalized proof state."""

    def __init__(self, model: TextModel) -> None:
        self._model = model
        self._pools: dict[tuple[str, str, int, str, int], tuple[str, ...]] = {}

    def get(
        self,
        problem: Problem,
        state: str,
        accepted: tuple[str, ...],
        failed: tuple[str, ...],
        *,
        seed: int,
        top_k: int,
    ) -> tuple[str, ...]:
        state_hash = sha256_text(state)
        key = (problem.id, state_hash, seed, CANDIDATE_PROMPT_VERSION, top_k)
        if key not in self._pools:
            generated = self._model.generate(
                candidate_prompt(problem, state, accepted, failed, top_k),
                seed=derived_seed(seed, problem.id, state_hash, "candidates"),
            )
            self._pools[key] = parse_candidates(generated, top_k)
        return self._pools[key]


class LeanStatus(str, Enum):
    OPEN = "open"
    COMPLETE = "complete"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class LeanResult:
    status: LeanStatus
    accepted: bool
    state: str | None
    diagnostics: tuple[str, ...]
    exit_code: int | None
    duration_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.status, LeanStatus) or not isinstance(self.accepted, bool):
            raise ExperimentError("Lean result status or acceptance is invalid")
        diagnostics = tuple(str(item) for item in self.diagnostics)
        object.__setattr__(self, "diagnostics", diagnostics)
        if self.status is LeanStatus.OPEN and (not self.accepted or not self.state):
            raise ExperimentError("open Lean results require an accepted state")
        if self.status is LeanStatus.COMPLETE and (not self.accepted or self.state is not None):
            raise ExperimentError("complete Lean results require acceptance and no state")
        if self.status not in {LeanStatus.OPEN, LeanStatus.COMPLETE} and self.accepted:
            raise ExperimentError("failed Lean results cannot be accepted")


@dataclass(frozen=True, slots=True)
class _LeanDiagnostic:
    severity: str
    kind: str | None
    message: str
    line: int | None


@dataclass(frozen=True, slots=True)
class _LeanRun:
    exit_code: int | None
    diagnostics: tuple[_LeanDiagnostic, ...]
    duration_ms: int
    timed_out: bool = False
    internal_error: bool = False


_TEMP_LEAN_PATH = re.compile(r"(?:/[A-Za-z0-9_.-]+)+/(?:Current|current_problem)\.lean(?::\d+:\d+)?")


def normalize_lean_text(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized = "\n".join(line.rstrip() for line in lines).strip("\n")
    return _TEMP_LEAN_PATH.sub("<temp>", normalized)


class LeanVerifier:
    """Independent subprocess verifier for the pinned Lean project."""

    def __init__(
        self,
        project_root: Path,
        *,
        timeout_seconds: float = 10.0,
        max_output_bytes: int = 1_048_576,
        lake_executable: str = "lake",
    ) -> None:
        self.project_root = Path(project_root)
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_bytes = max_output_bytes
        self.lake_executable = lake_executable

    def initial_state(
        self, problem: Problem, *, timeout_seconds: float | None = None
    ) -> LeanResult:
        return self._state_after(problem, (), timeout_seconds=timeout_seconds)

    def try_tactic(
        self,
        problem: Problem,
        prefix: tuple[str, ...],
        candidate: str,
        *,
        timeout_seconds: float | None = None,
    ) -> LeanResult:
        invalid = self._validate_tactics(prefix + (candidate,))
        if invalid is not None:
            return invalid
        return self._state_after(
            problem, prefix + (candidate,), timeout_seconds=timeout_seconds
        )

    def _state_after(
        self,
        problem: Problem,
        tactics: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> LeanResult:
        invalid = self._validate_tactics(tactics)
        if invalid is not None:
            return invalid
        source, trace_line = self._render_probe(problem, tactics)
        local_deadline = (
            None if timeout_seconds is None else time.monotonic() + timeout_seconds
        )
        run = self._run_lean(source, timeout_seconds=timeout_seconds)
        if run.timed_out:
            return self._result(LeanStatus.TIMEOUT, False, None, run)
        if run.internal_error:
            return self._result(LeanStatus.INTERNAL_ERROR, False, None, run)
        errors = [item for item in run.diagnostics if item.severity == "error"]
        traces = [
            item
            for item in run.diagnostics
            if item.kind == "trace" and item.line == trace_line
        ]
        if not errors and run.exit_code == 0 and len(traces) == 1 and not traces[0].message.strip():
            remaining = (
                None if local_deadline is None else local_deadline - time.monotonic()
            )
            if remaining is not None and remaining <= 0:
                return LeanResult(
                    LeanStatus.TIMEOUT, False, None, ("theorem timeout",), None, run.duration_ms
                )
            return self._verify_complete(problem, tactics, timeout_seconds=remaining)
        if errors or run.exit_code != 0 or len(traces) != 1 or not traces[0].message.strip():
            return self._result(LeanStatus.REJECTED, False, None, run)
        if not any(item.kind == "hasSorry" for item in run.diagnostics):
            altered = _LeanRun(
                run.exit_code,
                run.diagnostics
                + (_LeanDiagnostic("error", "protocol", "probe did not report generated sorry", None),),
                run.duration_ms,
                internal_error=True,
            )
            return self._result(LeanStatus.INTERNAL_ERROR, False, None, altered)
        return self._result(
            LeanStatus.OPEN,
            True,
            normalize_lean_text(traces[0].message),
            run,
        )

    def _verify_complete(
        self,
        problem: Problem,
        tactics: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> LeanResult:
        run = self._run_lean(
            self._render_final(problem, tactics), timeout_seconds=timeout_seconds
        )
        if run.timed_out:
            return self._result(LeanStatus.TIMEOUT, False, None, run)
        if run.internal_error:
            return self._result(LeanStatus.INTERNAL_ERROR, False, None, run)
        has_error = any(item.severity == "error" for item in run.diagnostics)
        has_sorry = any(item.kind == "hasSorry" for item in run.diagnostics)
        if run.exit_code == 0 and not has_error and not has_sorry:
            return self._result(LeanStatus.COMPLETE, True, None, run)
        return self._result(LeanStatus.REJECTED, False, None, run)

    @staticmethod
    def _validate_tactics(tactics: tuple[str, ...]) -> LeanResult | None:
        for tactic in tactics:
            if (
                not isinstance(tactic, str)
                or not tactic.strip()
                or any(character in tactic for character in "\r\n\x00")
                or len(tactic) > 4096
            ):
                return LeanResult(
                    LeanStatus.REJECTED,
                    False,
                    None,
                    ("tactic must be one nonblank line of at most 4096 characters",),
                    None,
                    0,
                )
        return None

    @staticmethod
    def _render_probe(problem: Problem, tactics: tuple[str, ...]) -> tuple[str, int]:
        lines = [problem.theorem_header()]
        lines.extend(f"  {tactic}" for tactic in tactics)
        trace_line = len(lines) + 1
        lines.append("  trace_state")
        lines.append("  all_goals sorry")
        return "\n".join(lines) + "\n", trace_line

    @staticmethod
    def _render_final(problem: Problem, tactics: tuple[str, ...]) -> str:
        lines = [problem.theorem_header()]
        lines.extend(f"  {tactic}" for tactic in tactics)
        return "\n".join(lines) + "\n"

    def _run_lean(
        self, source: str, *, timeout_seconds: float | None = None
    ) -> _LeanRun:
        started = time.monotonic_ns()
        effective_timeout = self.timeout_seconds
        if timeout_seconds is not None:
            effective_timeout = min(effective_timeout, max(float(timeout_seconds), 0.001))
        source_path: Path | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="standalone-lean-") as directory:
                source_path = Path(directory) / "Current.lean"
                source_path.write_text(source, encoding="utf-8")
                environment = {
                    **os.environ,
                    "ELAN_TOOLCHAIN": "leanprover/lean4:v4.29.1",
                }
                completed = subprocess.run(
                    [self.lake_executable, "env", "lean", "--json", str(source_path)],
                    cwd=self.project_root,
                    capture_output=True,
                    text=False,
                    timeout=effective_timeout,
                    check=False,
                    env=environment,
                )
        except subprocess.TimeoutExpired:
            duration = (time.monotonic_ns() - started) // 1_000_000
            return _LeanRun(
                None,
                (_LeanDiagnostic("error", "timeout", f"Lean exceeded {effective_timeout:g} seconds", None),),
                duration,
                timed_out=True,
            )
        except (OSError, ValueError) as error:
            duration = (time.monotonic_ns() - started) // 1_000_000
            return _LeanRun(
                None,
                (_LeanDiagnostic("error", "internal", normalize_lean_text(str(error)), None),),
                duration,
                internal_error=True,
            )
        duration = (time.monotonic_ns() - started) // 1_000_000
        raw = completed.stdout + completed.stderr
        if len(raw) > self.max_output_bytes:
            return _LeanRun(
                completed.returncode,
                (_LeanDiagnostic("error", "output_limit", "Lean output exceeded configured bytes", None),),
                duration,
                internal_error=True,
            )
        diagnostics = self._parse_diagnostics(raw, source_path)
        return _LeanRun(completed.returncode, diagnostics, duration)

    @staticmethod
    def _parse_diagnostics(raw: bytes, source_path: Path | None) -> tuple[_LeanDiagnostic, ...]:
        diagnostics: list[_LeanDiagnostic] = []
        source_text = str(source_path) if source_path is not None else None
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise TypeError("diagnostic is not an object")
                position = item.get("pos") if isinstance(item.get("pos"), dict) else {}
                data = item.get("data", "")
                message = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, sort_keys=True)
                if source_text:
                    message = message.replace(source_text, "<temp>")
                diagnostics.append(
                    _LeanDiagnostic(
                        str(item.get("severity", "error")),
                        str(item["kind"]) if item.get("kind") is not None else None,
                        normalize_lean_text(message),
                        position.get("line") if isinstance(position.get("line"), int) else None,
                    )
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                message = line.replace(source_text, "<temp>") if source_text else line
                diagnostics.append(_LeanDiagnostic("error", "protocol", normalize_lean_text(message), None))
        return tuple(diagnostics)

    @staticmethod
    def _result(
        status: LeanStatus,
        accepted: bool,
        state: str | None,
        run: _LeanRun,
    ) -> LeanResult:
        messages = tuple(item.message for item in run.diagnostics if item.message.strip())
        return LeanResult(status, accepted, state, messages, run.exit_code, run.duration_ms)


@dataclass(frozen=True, slots=True)
class SearchLimits:
    max_expansions: int
    max_verifier_calls: int
    theorem_timeout_seconds: float
    top_k: int = 8

    def __post_init__(self) -> None:
        for value, field in (
            (self.max_expansions, "max_expansions"),
            (self.max_verifier_calls, "max_verifier_calls"),
            (self.top_k, "top_k"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ExperimentError(f"{field} must be a positive integer")
        if self.theorem_timeout_seconds <= 0:
            raise ExperimentError("theorem_timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    problem_id: str
    condition: str
    seed: int
    state_fingerprint: str
    prefix: tuple[str, ...]
    candidates: tuple[str, ...]
    ordered_indices: tuple[int, ...]
    candidate: str
    disposition: str
    successor_fingerprint: str | None
    diagnostics: tuple[str, ...]
    retrieval_hits: tuple[str, ...] = ()
    retrieval_domains: tuple[str, ...] = ()
    retrieval_similarities: tuple[float | None, ...] = ()
    proof_need: Mapping[str, Any] | None = None
    operation_seeds: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TrialResult:
    problem_id: str
    condition: str
    seed: int
    status: str
    solved: bool
    tactics: tuple[str, ...]
    expansion_count: int
    verifier_call_count: int
    rejected_count: int
    repeated_state_count: int
    novel_state_count: int
    failure: str | None
    elapsed_seconds: float
    reference_coverage: bool = False
    exact_reference_overlap: int = 0


def state_fingerprint(problem: Problem, state: str) -> str:
    return sha256_text(f"{problem.id}\x1f{normalize_lean_text(state)}")[:24]


class SearchRanker(Protocol):
    def rank(
        self,
        condition: Condition,
        problem: Problem,
        state: str,
        accepted: tuple[str, ...],
        failed: tuple[str, ...],
        candidates: tuple[str, ...],
        seed: int,
        deadline: float | None = None,
    ) -> RankingDecision: ...


def _call_with_remaining(call: Callable[..., Any], *args: Any, deadline: float) -> Any:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("theorem timeout")
    parameters = inspect.signature(call).parameters
    if "timeout_seconds" in parameters:
        result = call(*args, timeout_seconds=remaining)
    else:
        result = call(*args)
    if time.monotonic() >= deadline:
        raise TimeoutError("theorem timeout")
    return result


def _attempt_metadata(
    decision: RankingDecision,
    problem: Problem,
    state: str,
    seed: int,
    condition: Condition,
) -> tuple[Mapping[str, Any] | None, Mapping[str, int]]:
    need = decision.proof_need
    proof_need = None
    if need is not None:
        proof_need = {
            "description": need.description,
            "embedding": {
                "text_hash": need.embedding.text_hash,
                "model_revision": need.embedding.model_revision,
                "layer": need.embedding.layer,
                "token_index": need.embedding.token_index,
                "dimension": need.embedding.dimension,
            },
        }
    state_hash = sha256_text(state)
    return proof_need, {
        "candidate": derived_seed(seed, problem.id, state_hash, "candidates"),
        "proof_need": derived_seed(seed, problem.id, state_hash, "proof-need"),
        "random_retrieval": derived_seed(seed, problem.id, state_hash, "random-retrieval"),
        "ranking": derived_seed(seed, problem.id, state_hash, condition.value, "ranking"),
    }


def search_problem(
    problem: Problem,
    condition: Condition,
    seed: int,
    pool_store: Any,
    ranker: SearchRanker,
    verifier: Any,
    limits: SearchLimits,
    attempt_sink: Callable[[AttemptRecord], Any],
) -> TrialResult:
    started = time.monotonic()
    deadline = started + limits.theorem_timeout_seconds
    try:
        initial = _call_with_remaining(verifier.initial_state, problem, deadline=deadline)
    except TimeoutError:
        return TrialResult(
            problem.id, condition.value, seed, "timeout", False, (), 0, 0, 0, 0, 0,
            "theorem timeout", time.monotonic() - started,
        )
    if initial.status is not LeanStatus.OPEN or not initial.state:
        return TrialResult(
            problem.id,
            condition.value,
            seed,
            "initialization_failed",
            False,
            (),
            0,
            0,
            0,
            0,
            0,
            "; ".join(initial.diagnostics),
            time.monotonic() - started,
        )

    insertion_order = 0
    initial_state = normalize_lean_text(initial.state)
    initial_fingerprint = state_fingerprint(problem, initial_state)
    frontier: list[tuple[int, int, str, tuple[str, ...]]] = [
        (0, insertion_order, initial_state, ())
    ]
    seen = {initial_fingerprint}
    failed_by_state: dict[str, list[str]] = {}
    expansions = 0
    verifier_calls = 0
    rejected = 0
    repeated = 0
    novel = 0
    status = ""

    while frontier:
        if time.monotonic() >= deadline:
            status = "timeout"
            failure = "theorem timeout"
            break
        if expansions >= limits.max_expansions or verifier_calls >= limits.max_verifier_calls:
            status = "budget_exhausted"
            failure = "search budget exhausted"
            break
        _, _, state, prefix = heapq.heappop(frontier)
        fingerprint = state_fingerprint(problem, state)
        expansions += 1
        failed = tuple(failed_by_state.get(fingerprint, ()))
        try:
            pool_started = time.monotonic()
            candidates = pool_store.get(
                problem,
                state,
                prefix,
                failed,
                seed=seed,
                top_k=limits.top_k,
            )
            deadline += time.monotonic() - pool_started
            decision = ranker.rank(
                condition,
                problem,
                state,
                prefix,
                failed,
                candidates,
                seed,
                deadline=deadline,
            )
        except TimeoutError as error:
            if time.monotonic() >= deadline or str(error) == "theorem timeout":
                status = "timeout"
                failure = "theorem timeout"
            else:
                status = "model_failure"
                failure = f"TimeoutError: {error}"
            break
        except Exception as error:
            status = "model_failure"
            failure = f"{type(error).__name__}: {error}"
            break
        for candidate in decision.ordered_candidates:
            if time.monotonic() >= deadline:
                status = "timeout"
                failure = "theorem timeout"
                break
            if verifier_calls >= limits.max_verifier_calls:
                status = "budget_exhausted"
                failure = "search budget exhausted"
                break
            verifier_calls += 1
            try:
                result = _call_with_remaining(
                    verifier.try_tactic,
                    problem,
                    prefix,
                    candidate,
                    deadline=deadline,
                )
            except TimeoutError:
                status = "timeout"
                failure = "theorem timeout"
                break
            successor_fingerprint: str | None = None
            proof_need, operation_seeds = _attempt_metadata(
                decision, problem, state, seed, condition
            )
            if result.status is LeanStatus.COMPLETE and result.accepted:
                disposition = "complete"
                attempt_sink(
                    AttemptRecord(
                        problem.id,
                        condition.value,
                        seed,
                        fingerprint,
                        prefix,
                        tuple(candidates),
                        tuple(decision.indices),
                        candidate,
                        disposition,
                        None,
                        result.diagnostics,
                        tuple(hit.item.transition.id for hit in decision.hits),
                        tuple(hit.item.transition.domain for hit in decision.hits),
                        tuple(hit.similarity for hit in decision.hits),
                        proof_need,
                        operation_seeds,
                    )
                )
                tactics = prefix + (candidate,)
                return TrialResult(
                    problem.id,
                    condition.value,
                    seed,
                    "complete",
                    True,
                    tactics,
                    expansions,
                    verifier_calls,
                    rejected,
                    repeated,
                    novel,
                    None,
                    time.monotonic() - started,
                )
            if result.status is LeanStatus.OPEN and result.accepted and result.state:
                successor = normalize_lean_text(result.state)
                successor_fingerprint = state_fingerprint(problem, successor)
                if successor_fingerprint in seen:
                    disposition = "repeated"
                    repeated += 1
                else:
                    disposition = "novel"
                    novel += 1
                    seen.add(successor_fingerprint)
                    insertion_order += 1
                    successor_prefix = prefix + (candidate,)
                    heapq.heappush(
                        frontier,
                        (-len(successor_prefix), insertion_order, successor, successor_prefix),
                    )
            else:
                disposition = result.status.value
                rejected += 1
                failed_by_state.setdefault(fingerprint, []).append(candidate)
            attempt_sink(
                AttemptRecord(
                    problem.id,
                    condition.value,
                    seed,
                    fingerprint,
                    prefix,
                    tuple(candidates),
                    tuple(decision.indices),
                    candidate,
                    disposition,
                    successor_fingerprint,
                    result.diagnostics,
                    tuple(hit.item.transition.id for hit in decision.hits),
                    tuple(hit.item.transition.domain for hit in decision.hits),
                    tuple(hit.similarity for hit in decision.hits),
                    proof_need,
                    operation_seeds,
                )
            )
            if result.status is LeanStatus.INTERNAL_ERROR:
                status = "verifier_failure"
                failure = "; ".join(result.diagnostics) or "Lean verifier internal error"
                break
            if result.status is LeanStatus.TIMEOUT:
                status = "timeout"
                failure = "theorem timeout"
                break
        if status in {"timeout", "budget_exhausted", "verifier_failure"}:
            break
    else:
        status = "exhausted"
        failure = "search frontier exhausted"

    return TrialResult(
        problem.id,
        condition.value,
        seed,
        status,
        False,
        (),
        expansions,
        verifier_calls,
        rejected,
        repeated,
        novel,
        failure,
        time.monotonic() - started,
    )


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    problems: tuple[Problem, ...]
    reference_proofs: Mapping[str, ReferenceProof]
    transitions: tuple[Transition, ...]
    conditions: tuple[Condition, ...]
    seeds: tuple[int, ...]
    output_dir: Path
    cache_dir: Path | None
    hidden_layer: int
    retrieval_count: int
    search_limits: SearchLimits
    model_name: str
    device: str = "auto"
    max_new_tokens: int = 256
    model_timeout_seconds: float = 60.0
    input_manifest: Mapping[str, Any] = field(default_factory=dict)
    lean_configuration: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        problems = tuple(self.problems)
        references = dict(self.reference_proofs)
        transitions = tuple(self.transitions)
        conditions = tuple(self.conditions)
        seeds = tuple(self.seeds)
        if not problems or len({item.id for item in problems}) != len(problems):
            raise ExperimentError("experiment problems must be nonempty and unique")
        if set(references) != {item.id for item in problems}:
            raise ExperimentError("experiment reference coverage must match problems")
        if not conditions or Condition.NONE not in conditions or len(set(conditions)) != len(conditions):
            raise ExperimentError("experiment conditions must be unique and include none")
        if not seeds or any(isinstance(item, bool) or not isinstance(item, int) for item in seeds):
            raise ExperimentError("experiment seeds must be nonempty integers")
        if any(condition is not Condition.NONE for condition in conditions) and not transitions:
            raise ExperimentError("retrieval conditions require transitions")
        if self.retrieval_count <= 0:
            raise ExperimentError("retrieval_count must be positive")
        if any(condition is not Condition.NONE for condition in conditions) and self.retrieval_count > len(transitions):
            raise ExperimentError("retrieval_count exceeds transition count")
        _nonblank(self.model_name, "model_name")
        object.__setattr__(self, "problems", problems)
        object.__setattr__(self, "reference_proofs", MappingProxyType(references))
        object.__setattr__(self, "transitions", transitions)
        object.__setattr__(self, "conditions", conditions)
        object.__setattr__(self, "seeds", seeds)
        object.__setattr__(self, "output_dir", Path(self.output_dir))
        object.__setattr__(self, "cache_dir", None if self.cache_dir is None else Path(self.cache_dir))
        object.__setattr__(self, "input_manifest", MappingProxyType(dict(self.input_manifest)))
        object.__setattr__(self, "lean_configuration", MappingProxyType(dict(self.lean_configuration)))


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


class JsonlSink:
    """Append-only JSONL writer that flushes every complete row."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle = self.path.open("x", encoding="utf-8", newline="\n")

    def write(self, value: Any) -> None:
        self._handle.write(json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True))
        self._handle.write("\n")
        self._handle.flush()

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> JsonlSink:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def summarize_trials(trials: Sequence[TrialResult]) -> dict[str, object]:
    normalized = tuple(trials)
    by_key: dict[tuple[str, int, str], TrialResult] = {}
    for item in normalized:
        key = (item.problem_id, item.seed, item.condition)
        if key in by_key:
            raise ExperimentError(f"duplicate trial result {key}")
        by_key[key] = item
    baseline = {
        (item.problem_id, item.seed): item
        for item in normalized
        if item.condition == Condition.NONE.value
    }
    if not baseline:
        raise ExperimentError("trial summary requires no-retrieval baselines")
    seeds = {seed for _, seed in baseline}

    def label(key: tuple[str, int]) -> str:
        problem_id, seed = key
        return problem_id if len(seeds) == 1 else f"{problem_id}@{seed}"

    baseline_solved = sorted(label(key) for key, item in baseline.items() if item.solved)
    baseline_unsolved = sorted(label(key) for key, item in baseline.items() if not item.solved)
    conditions = sorted({item.condition for item in normalized})
    aggregates: dict[str, object] = {}
    for condition in conditions:
        selected = [item for item in normalized if item.condition == condition]
        solved_count = sum(item.solved for item in selected)
        aggregates[condition] = {
            "trial_count": len(selected),
            "solved_count": solved_count,
            "completion_rate": solved_count / len(selected),
            "verifier_call_count": sum(item.verifier_call_count for item in selected),
            "expansion_count": sum(item.expansion_count for item in selected),
        }

    comparisons: dict[str, object] = {}
    for condition in (Condition.RANDOM.value, Condition.ANALOGY.value):
        target = {
            (item.problem_id, item.seed): item
            for item in normalized
            if item.condition == condition
        }
        if not target:
            continue
        if set(target) != set(baseline):
            raise ExperimentError(f"condition {condition} is not paired with every baseline")
        newly_solved = sorted(
            label(key)
            for key in baseline
            if not baseline[key].solved and target[key].solved
        )
        regressed = sorted(
            label(key)
            for key in baseline
            if baseline[key].solved and not target[key].solved
        )
        target_solved = sum(item.solved for item in target.values())
        baseline_count = sum(item.solved for item in baseline.values())
        comparisons[f"{condition}_vs_none"] = {
            "completion_delta": target_solved - baseline_count,
            "completion_rate_delta": (target_solved - baseline_count) / len(baseline),
            "newly_solved": newly_solved,
            "regressed": regressed,
            "verifier_call_delta": sum(item.verifier_call_count for item in target.values())
            - sum(item.verifier_call_count for item in baseline.values()),
            "expansion_delta": sum(item.expansion_count for item in target.values())
            - sum(item.expansion_count for item in baseline.values()),
            "baseline_solved_completion_delta": sum(
                target[key].solved - baseline[key].solved
                for key in baseline
                if baseline[key].solved
            ),
            "baseline_unsolved_completion_delta": sum(
                target[key].solved - baseline[key].solved
                for key in baseline
                if not baseline[key].solved
            ),
        }
    operational_statuses = {"initialization_failed", "model_failure", "verifier_failure"}
    operational_failures = [
        {
            "problem_id": item.problem_id,
            "condition": item.condition,
            "seed": item.seed,
            "status": item.status,
            "failure": item.failure,
        }
        for item in normalized
        if item.status in operational_statuses
    ]
    return {
        "run_complete": not operational_failures,
        "operational_failures": operational_failures,
        "trial_count": len(normalized),
        "subgroups": {
            "baseline_solved": baseline_solved,
            "baseline_unsolved": baseline_unsolved,
        },
        "conditions": aggregates,
        "comparisons": comparisons,
    }


class ConditionRanker:
    def __init__(
        self,
        model: TextModel,
        index: tuple[IndexedTransition, ...],
        retrieval_count: int,
        layer: int,
    ) -> None:
        self.model = model
        self.index = index
        self.retrieval_count = retrieval_count
        self.layer = layer

    def rank(
        self,
        condition: Condition,
        problem: Problem,
        state: str,
        accepted: tuple[str, ...],
        failed: tuple[str, ...],
        candidates: tuple[str, ...],
        seed: int,
        deadline: float | None = None,
    ) -> RankingDecision:
        return rank_for_condition(
            condition,
            problem,
            state,
            candidates,
            None,
            self.index,
            self.model,
            self.retrieval_count,
            seed,
            layer=self.layer,
            accepted=accepted,
            failed=failed,
            deadline=deadline,
        )


def _run_config_payload(
    config: ExperimentConfig,
    *,
    complete: bool,
    model_revision: str | None = None,
    operational_failures: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    return {
        "run_complete": complete,
        "problems": [_jsonable(item) for item in config.problems],
        "conditions": [item.value for item in config.conditions],
        "seeds": list(config.seeds),
        "transition_count": len(config.transitions),
        "transition_counts": {
            domain: sum(item.domain == domain for item in config.transitions)
            for domain in ("poetry", "physics")
        },
        "inputs": _jsonable(config.input_manifest),
        "lean": _jsonable(config.lean_configuration),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
        },
        "model": {
            "name": config.model_name,
            "resolved_revision": model_revision,
            "device": config.device,
            "hidden_layer": config.hidden_layer,
            "max_new_tokens": config.max_new_tokens,
            "timeout_seconds": config.model_timeout_seconds,
        },
        "retrieval_count": config.retrieval_count,
        "search_limits": _jsonable(config.search_limits),
        "prompt_versions": dict(PROMPT_VERSIONS),
        "operation_seed_scheme": "sha256(base_seed, problem_id, state_sha256, operation)-u64-v1",
        "operational_failures": list(operational_failures),
    }


def run_experiment(
    config: ExperimentConfig,
    model_factory: Callable[[ExperimentConfig], TextModel],
    verifier_factory: Callable[[ExperimentConfig], Any],
) -> Path:
    output = config.output_dir
    if output.exists():
        raise ExperimentError(f"output directory already exists: {output}")
    output.mkdir(parents=True, exist_ok=False)
    _atomic_json(output / "config.json", _run_config_payload(config, complete=False))
    model = model_factory(config)
    verifier = verifier_factory(config)
    needs_index = any(condition is not Condition.NONE for condition in config.conditions)
    index = (
        build_index(config.transitions, model, config.cache_dir, config.hidden_layer)
        if needs_index
        else ()
    )
    pool_store = CandidatePoolStore(model)
    ranker = ConditionRanker(model, index, config.retrieval_count, config.hidden_layer)
    trials: list[TrialResult] = []
    condition_order = [Condition.NONE] + [
        condition for condition in config.conditions if condition is not Condition.NONE
    ]

    with (
        JsonlSink(output / "transitions.jsonl") as transition_sink,
        JsonlSink(output / "attempts.jsonl") as attempt_sink,
        JsonlSink(output / "trials.jsonl") as trial_sink,
    ):
        for item in index:
            transition_sink.write(
                {
                    "id": item.transition.id,
                    "domain": item.transition.domain,
                    "before": item.transition.before,
                    "after": item.transition.after,
                    "provenance": dict(item.transition.provenance),
                    "transformation": item.transformation,
                    "cache_key": item.cache_key,
                    "embedding": {
                        "model_revision": item.embedding.model_revision,
                        "layer": item.embedding.layer,
                        "token_index": item.embedding.token_index,
                        "dimension": item.embedding.dimension,
                        "text_hash": item.embedding.text_hash,
                    },
                }
            )
        for seed in config.seeds:
            for condition in condition_order:
                for problem in config.problems:
                    trial_attempts: list[AttemptRecord] = []

                    def record_attempt(record: AttemptRecord) -> None:
                        trial_attempts.append(record)
                        attempt_sink.write(record)

                    result = search_problem(
                        problem,
                        condition,
                        seed,
                        pool_store,
                        ranker,
                        verifier,
                        config.search_limits,
                        record_attempt,
                    )
                    reference = config.reference_proofs[problem.id]
                    candidate_coverage = any(
                        any(tactic in attempt.candidates for tactic in reference.tactics)
                        for attempt in trial_attempts
                    )
                    exact_overlap = sum(
                        index < len(reference.tactics) and tactic == reference.tactics[index]
                        for index, tactic in enumerate(result.tactics)
                    )
                    finalized = replace(
                        result,
                        reference_coverage=candidate_coverage,
                        exact_reference_overlap=exact_overlap,
                    )
                    trial_sink.write(finalized)
                    trials.append(finalized)
    expected = {
        (problem.id, seed, condition.value)
        for problem in config.problems
        for seed in config.seeds
        for condition in config.conditions
    }
    actual = {(item.problem_id, item.seed, item.condition) for item in trials}
    if actual != expected or len(trials) != len(expected):
        raise ExperimentError("run did not produce exactly one result per requested trial")
    summary = summarize_trials(trials)
    _atomic_json(output / "summary.json", summary)
    failures = summary["operational_failures"]
    complete = bool(summary["run_complete"])
    _atomic_json(
        output / "config.json",
        _run_config_payload(
            config,
            complete=complete,
            model_revision=model.model_revision,
            operational_failures=failures,
        ),
    )
    if not complete:
        raise ExperimentRunIncomplete(
            f"experiment preserved {len(failures)} operational failure(s) in {output}"
        )
    return output


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone poetry/physics retrieval experiment for Lean tactic recovery"
    )
    parser.add_argument("--dry-run", action="store_true", help="validate inputs without loading Qwen")
    parser.add_argument("--problems", type=Path, default=REPOSITORY_ROOT / "src/analogy/lean_gym/corpus/problems.json")
    parser.add_argument(
        "--reference-proofs",
        type=Path,
        default=REPOSITORY_ROOT / "src/analogy/lean_gym/corpus/reference_proofs.json",
    )
    parser.add_argument(
        "--lean-project",
        type=Path,
        default=REPOSITORY_ROOT / "src/analogy/lean_gym/lean_project",
    )
    parser.add_argument(
        "--poetry",
        type=Path,
        default=Path("/tmp/analogy-data/gutenberg-poetry-v001.ndjson.gz"),
    )
    parser.add_argument(
        "--physics",
        type=Path,
        default=Path("/tmp/analogy-data/trip-ClozeTrain.jsonl"),
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path("/tmp/analogy-data/standalone-cache"))
    parser.add_argument("--problem", action="append", dest="problem_ids")
    parser.add_argument("--condition", action="append", choices=[item.value for item in Condition])
    parser.add_argument("--seed", action="append", type=int)
    parser.add_argument("--poetry-limit", type=int, default=64)
    parser.add_argument("--physics-limit", type=int, default=64)
    parser.add_argument("--retrieval-count", type=int, default=4)
    parser.add_argument("--candidate-count", type=int, default=8)
    parser.add_argument("--max-expansions", type=int, default=32)
    parser.add_argument("--max-verifier-calls", type=int, default=256)
    parser.add_argument("--theorem-timeout", type=float, default=300.0)
    parser.add_argument("--lean-timeout", type=float, default=10.0)
    parser.add_argument("--lake-executable", default="lake")
    parser.add_argument("--model-name", default="Qwen/Qwen3-8B")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--hidden-layer", type=int, default=-1)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--model-timeout", type=float, default=60.0)
    return parser


def _require_positive(value: int | float, field: str) -> None:
    if isinstance(value, bool) or value <= 0:
        raise ExperimentError(f"{field} must be positive")


def _file_manifest(path: Path) -> dict[str, str]:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(Path(path).resolve()), "sha256": digest.hexdigest()}


def _probe_lean(project: Path, lake_path: str, timeout_seconds: float) -> dict[str, Any]:
    environment = {**os.environ, "ELAN_TOOLCHAIN": "leanprover/lean4:v4.29.1"}
    try:
        completed = subprocess.run(
            [lake_path, "env", "lean", "--version"],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ExperimentError(f"Lean probe failed: {error}") from error
    version = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0 or not version:
        raise ExperimentError(
            f"Lean probe failed with exit code {completed.returncode}: {version or '(no output)'}"
        )
    return {
        "project": str(Path(project).resolve()),
        "lake_executable": str(Path(lake_path).resolve()),
        "toolchain": environment["ELAN_TOOLCHAIN"],
        "version": version,
        "per_call_timeout_seconds": timeout_seconds,
    }


def _resolve_cli(args: argparse.Namespace) -> tuple[ExperimentConfig, Path, str, dict[str, int]]:
    for value, field in (
        (args.poetry_limit, "poetry-limit"),
        (args.physics_limit, "physics-limit"),
        (args.retrieval_count, "retrieval-count"),
        (args.candidate_count, "candidate-count"),
        (args.max_expansions, "max-expansions"),
        (args.max_verifier_calls, "max-verifier-calls"),
        (args.theorem_timeout, "theorem-timeout"),
        (args.lean_timeout, "lean-timeout"),
        (args.max_new_tokens, "max-new-tokens"),
        (args.model_timeout, "model-timeout"),
    ):
        _require_positive(value, field)
    if args.problem_ids and len(set(args.problem_ids)) != len(args.problem_ids):
        raise ExperimentError("problem selections must be unique")
    requested_conditions = args.condition or [item.value for item in Condition]
    if len(set(requested_conditions)) != len(requested_conditions):
        raise ExperimentError("condition selections must be unique")
    conditions = tuple(Condition(item) for item in requested_conditions)
    if Condition.NONE not in conditions:
        raise ExperimentError("conditions must include none for paired evaluation")
    if (
        any(condition is not Condition.NONE for condition in conditions)
        and args.retrieval_count > args.poetry_limit + args.physics_limit
    ):
        raise ExperimentError("retrieval-count exceeds the configured transition index")
    seeds = tuple(args.seed or [0])
    if len(set(seeds)) != len(seeds):
        raise ExperimentError("seed selections must be unique")
    if args.output_dir is not None and Path(args.output_dir).exists():
        raise ExperimentError(f"output directory already exists: {args.output_dir}")
    for path, field in (
        (args.problems, "problems"),
        (args.reference_proofs, "reference-proofs"),
    ):
        if not Path(path).is_file():
            raise ExperimentError(f"{field} file does not exist: {path}")
    needs_retrieval = any(condition is not Condition.NONE for condition in conditions)
    if needs_retrieval:
        for path, field in ((args.poetry, "poetry"), (args.physics, "physics")):
            if not Path(path).is_file():
                raise ExperimentError(f"{field} file does not exist: {path}")
    if not Path(args.lean_project).is_dir():
        raise ExperimentError(f"Lean project does not exist: {args.lean_project}")
    lake_path = shutil.which(args.lake_executable)
    if lake_path is None:
        raise ExperimentError(f"lake executable is unavailable: {args.lake_executable}")
    lean_configuration = _probe_lean(Path(args.lean_project), lake_path, args.lean_timeout)
    problems, references = load_benchmark(args.problems, args.reference_proofs)
    by_id = {item.id: item for item in problems}
    if args.problem_ids:
        unknown = sorted(set(args.problem_ids) - by_id.keys())
        if unknown:
            raise ExperimentError(f"unknown problem IDs: {unknown}")
        problems = tuple(by_id[item] for item in args.problem_ids)
        references = {item.id: references[item.id] for item in problems}
    poetry = (
        _reservoir_sample(
            iter_poetry_transitions(args.poetry),
            args.poetry_limit,
            random.Random(f"{seeds[0]}:poetry"),
        )
        if needs_retrieval
        else ()
    )
    physics = (
        _reservoir_sample(
            iter_physics_transitions(args.physics),
            args.physics_limit,
            random.Random(f"{seeds[0]}:physics"),
        )
        if needs_retrieval
        else ()
    )
    transitions = poetry + physics
    if any(condition is not Condition.NONE for condition in conditions) and args.retrieval_count > len(transitions):
        raise ExperimentError("retrieval-count exceeds the eligible transition index")
    output = args.output_dir or (
        REPOSITORY_ROOT / "results" / f"standalone-{uuid.uuid4().hex}"
    )
    if Path(output).exists():
        raise ExperimentError(f"output directory already exists: {output}")
    limits = SearchLimits(
        args.max_expansions,
        args.max_verifier_calls,
        args.theorem_timeout,
        top_k=args.candidate_count,
    )
    config = ExperimentConfig(
        problems=problems,
        reference_proofs=references,
        transitions=transitions,
        conditions=conditions,
        seeds=seeds,
        output_dir=output,
        cache_dir=args.cache_dir,
        hidden_layer=args.hidden_layer,
        retrieval_count=args.retrieval_count,
        search_limits=limits,
        model_name=args.model_name,
        device=args.device,
        max_new_tokens=args.max_new_tokens,
        model_timeout_seconds=args.model_timeout,
        input_manifest={
            "problems": _file_manifest(args.problems),
            "reference_proofs": _file_manifest(args.reference_proofs),
            **(
                {
                    "poetry": _file_manifest(args.poetry),
                    "physics": _file_manifest(args.physics),
                }
                if needs_retrieval
                else {}
            ),
        },
        lean_configuration=lean_configuration,
    )
    counts = {"poetry": len(poetry), "physics": len(physics)}
    return config, Path(args.lean_project), lake_path, counts


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = False
    try:
        config, lean_project, lake_path, transition_counts = _resolve_cli(args)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "problem_count": len(config.problems),
                        "condition_count": len(config.conditions),
                        "seed_count": len(config.seeds),
                        "transition_counts": transition_counts,
                        "lean_available": True,
                        "model_loaded": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        started = True
        output = run_experiment(
            config,
            model_factory=lambda resolved: QwenModel.load(
                resolved.model_name,
                resolved.device,
                resolved.max_new_tokens,
                resolved.model_timeout_seconds,
            ),
            verifier_factory=lambda _: LeanVerifier(
                lean_project,
                timeout_seconds=args.lean_timeout,
                lake_executable=lake_path,
            ),
        )
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "output_dir": str(output),
                    "trial_count": summary["trial_count"],
                    "conditions": summary["conditions"],
                },
                sort_keys=True,
            )
        )
        return 0
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except (ExperimentError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1 if started else 2


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


if __name__ == "__main__":
    raise SystemExit(main())

