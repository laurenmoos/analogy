"""Low-level Lean corpus and verifier boundary."""

from .corpus import load_problems
from .models import (
    Problem,
    ProofStructure,
    ProofStructureError,
    VerificationResult,
    VerificationStatus,
    has_unresolved_metavariable,
    normalize_proof_state_text,
)
from .resources import CORPUS_ROOT, LEAN_PROJECT_ROOT
from .verifier import LeanSubprocessVerifier

__all__ = [
    "LeanSubprocessVerifier",
    "CORPUS_ROOT",
    "LEAN_PROJECT_ROOT",
    "Problem",
    "ProofStructure",
    "ProofStructureError",
    "VerificationResult",
    "VerificationStatus",
    "has_unresolved_metavariable",
    "load_problems",
    "normalize_proof_state_text",
]
