"""Identity helpers for solver proof states."""

from __future__ import annotations

import hashlib
import re

from ...lean_gym.models import normalize_proof_state_text

_GENERATED_CASE_HEADER = re.compile(r"case [A-Za-z0-9_'.]+\Z")


def canonical_proof_state(text: str) -> str:
    """Normalize display-only Lean case labels without rewriting Lean syntax."""

    lines = normalize_proof_state_text(text).splitlines()
    output: list[str] = []
    removed_header = False
    for line in lines:
        if _GENERATED_CASE_HEADER.fullmatch(line):
            while output and not output[-1].strip():
                output.pop()
            removed_header = True
            continue
        if removed_header and not line.strip():
            continue
        removed_header = False
        output.append(line)
    return "\n".join(output).strip("\n")


def proof_state_fingerprint(text: str) -> str:
    return hashlib.sha256(canonical_proof_state(text).encode("utf-8")).hexdigest()


__all__ = ["canonical_proof_state", "proof_state_fingerprint"]
