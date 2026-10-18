"""Identity helpers for solver proof states."""

from __future__ import annotations

import hashlib

from ...lean_gym.models import normalize_proof_state_text


def proof_state_fingerprint(text: str) -> str:
    return hashlib.sha256(
        normalize_proof_state_text(text).encode("utf-8")
    ).hexdigest()


__all__ = ["proof_state_fingerprint"]
