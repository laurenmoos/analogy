"""Durable raw story generations shared by paired analogy conditions."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from ...config import AppConfig
from ...utils.records import Message
from ..proof.state import canonical_proof_state, proof_state_fingerprint


SCHEMA_VERSION = 1


class StoryCandidateCacheError(RuntimeError):
    """Raised when durable candidate evidence cannot be trusted."""


@dataclass(frozen=True, slots=True)
class CandidateCacheKey:
    model_revision: str
    prompt_sha256: str
    corpus_sha256: str
    problem_id: str
    run_seed: int
    trajectory_sha256: str
    state_fingerprint: str
    domain: str
    generation_index: int
    messages_sha256: str

    def __post_init__(self) -> None:
        for field in (
            "model_revision",
            "prompt_sha256",
            "corpus_sha256",
            "problem_id",
            "trajectory_sha256",
            "state_fingerprint",
            "domain",
            "messages_sha256",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be nonblank text")
        for field in ("run_seed", "generation_index"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")

    @property
    def sha256(self) -> str:
        return _sha256_json(asdict(self))


@dataclass(frozen=True, slots=True)
class CachedStoryGeneration:
    raw_text: str
    content_sha256: str
    key_sha256: str
    cache_hit: bool


class StoryCandidateCache:
    """Serialize story generation once for an exact experimental identity."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def get_or_generate(
        self,
        key: CandidateCacheKey,
        generate: Callable[[], str],
    ) -> CachedStoryGeneration:
        if not isinstance(key, CandidateCacheKey):
            raise TypeError("key must be a CandidateCacheKey")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            payload = self._load()
            entry = payload["entries"].get(key.sha256)
            if entry is not None:
                raw_text, content_sha256 = _validate_entry(entry, key)
                return CachedStoryGeneration(
                    raw_text, content_sha256, key.sha256, True
                )

            raw_text = generate()
            if not isinstance(raw_text, str):
                raise StoryCandidateCacheError(
                    "story generator must return text"
                )
            content_sha256 = text_sha256(raw_text)
            payload["entries"][key.sha256] = {
                "key": asdict(key),
                "raw_text": raw_text,
                "content_sha256": content_sha256,
            }
            self._atomic_write(payload)
            return CachedStoryGeneration(
                raw_text, content_sha256, key.sha256, False
            )

    def _load(self) -> dict[str, object]:
        if not self.path.exists():
            return {"schema_version": SCHEMA_VERSION, "entries": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise StoryCandidateCacheError(
                f"candidate cache is corrupt: {self.path}: {error}"
            ) from error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "entries"}
            or payload.get("schema_version") != SCHEMA_VERSION
            or not isinstance(payload.get("entries"), dict)
        ):
            raise StoryCandidateCacheError(
                f"candidate cache is corrupt: {self.path}: invalid schema"
            )
        return payload

    def _atomic_write(self, payload: dict[str, object]) -> None:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def story_candidate_cache_path(config: AppConfig) -> Path:
    return config.run.output_dir / "story-candidates-v1.json"


def build_candidate_cache_key(
    *,
    model_revision: str,
    prompt_sha256: str,
    corpus_sha256: str,
    state_id: str,
    state_expression: str,
    run_seed: int,
    trajectory_states: tuple[str, ...],
    trajectory_tactics: tuple[str, ...],
    domain: str,
    generation_index: int,
    messages: tuple[Message, ...],
) -> CandidateCacheKey:
    """Build one cache key from the complete story-call identity."""

    trajectory = {
        "states": [canonical_proof_state(item) for item in trajectory_states],
        "tactics": [" ".join(item.split()) for item in trajectory_tactics],
    }
    message_payload = [
        {"role": item.role, "content": item.content} for item in messages
    ]
    return CandidateCacheKey(
        model_revision=model_revision,
        prompt_sha256=prompt_sha256,
        corpus_sha256=corpus_sha256,
        problem_id=state_id.rsplit(":", 1)[0],
        run_seed=run_seed,
        trajectory_sha256=_sha256_json(trajectory),
        state_fingerprint=proof_state_fingerprint(state_expression),
        domain=domain,
        generation_index=generation_index,
        messages_sha256=_sha256_json(message_payload),
    )


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return text_sha256(encoded)


def _validate_entry(
    entry: object, key: CandidateCacheKey
) -> tuple[str, str]:
    if not isinstance(entry, dict) or set(entry) != {
        "key",
        "raw_text",
        "content_sha256",
    }:
        raise StoryCandidateCacheError("candidate cache is corrupt: invalid entry")
    if entry["key"] != asdict(key):
        raise StoryCandidateCacheError(
            "candidate cache is corrupt: key digest collision"
        )
    raw_text = entry["raw_text"]
    content_sha256 = entry["content_sha256"]
    if (
        not isinstance(raw_text, str)
        or not isinstance(content_sha256, str)
        or text_sha256(raw_text) != content_sha256
    ):
        raise StoryCandidateCacheError(
            "candidate cache is corrupt: content hash mismatch"
        )
    return raw_text, content_sha256


__all__ = [
    "CandidateCacheKey",
    "CachedStoryGeneration",
    "SCHEMA_VERSION",
    "StoryCandidateCache",
    "StoryCandidateCacheError",
    "build_candidate_cache_key",
    "file_sha256",
    "story_candidate_cache_path",
    "text_sha256",
]
