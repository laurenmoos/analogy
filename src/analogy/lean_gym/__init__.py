"""A small, verified Lean 4 logic proof gym."""

from .corpus import load_problems
from .models import Problem, VerificationResult, VerificationStatus
from .replay import replay_trace
from .resources import CORPUS_ROOT, LEAN_PROJECT_ROOT
from .session import ProofSession, SessionSnapshot
from .verifier import LeanSubprocessVerifier

__all__ = [
    "LeanSubprocessVerifier",
    "CORPUS_ROOT",
    "LEAN_PROJECT_ROOT",
    "Problem",
    "ProofSession",
    "SessionSnapshot",
    "VerificationResult",
    "VerificationStatus",
    "load_problems",
    "replay_trace",
]
