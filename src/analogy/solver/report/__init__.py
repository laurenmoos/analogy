"""Public construction and storage API for solver reports."""

from .build import build_problem_record, build_run_record
from .records import ProblemRunRecord, RunState, SolveRunRecord
from .storage import start_run, write_problem_record, write_run_record
from .evaluation import EvaluationRecord, finalize_evaluation

__all__ = [
    "ProblemRunRecord",
    "RunState",
    "SolveRunRecord",
    "EvaluationRecord",
    "build_problem_record",
    "build_run_record",
    "start_run",
    "finalize_evaluation",
    "write_problem_record",
    "write_run_record",
]
