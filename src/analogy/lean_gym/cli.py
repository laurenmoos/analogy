from __future__ import annotations

import argparse
import shlex
import sys
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from .corpus import load_problems
from .models import Problem, VerificationStatus
from .replay import TraceValidationError, replay_trace
from .resources import CORPUS_ROOT, LEAN_PROJECT_ROOT
from .session import ProofSession, SessionCommandError, SessionError
from .traces import JsonlTraceWriter, TraceWriter
from .verifier import LeanSubprocessVerifier, Verifier


HELP = """Commands:
  :show              show theorem, accepted tactics, and goals
  :history           show all tactic attempts
  :undo              remove the last accepted tactic
  :reset             restore the initial state
  :save [path]       save the current proof without overwriting
  :problems          list bundled problems
  :load <id>         start a new session on another problem
  :help              show this help
  :quit              flush the trace and exit
Any other nonblank line is submitted as one Lean tactic.
"""


def _write(stream: TextIO, text: str = "") -> None:
    stream.write(text + "\n")
    stream.flush()


def _list_problems(problems: tuple[Problem, ...], output: TextIO) -> None:
    for index, problem in enumerate(problems, start=1):
        _write(output, f"{index:2}. {problem.id:16} {problem.title}")


def _select_problem(selection: str, problems: tuple[Problem, ...]) -> Problem | None:
    by_id = {problem.id: problem for problem in problems}
    if selection in by_id:
        return by_id[selection]
    try:
        index = int(selection)
    except ValueError:
        return None
    return problems[index - 1] if 1 <= index <= len(problems) else None


def _show(session: ProofSession, output: TextIO) -> None:
    snapshot = session.snapshot()
    _write(output, snapshot.problem.theorem_header())
    if snapshot.accepted_tactics:
        _write(output, "Accepted prefix:")
        for tactic in snapshot.accepted_tactics:
            _write(output, f"  {tactic}")
    _write(output, "Goals:")
    _write(output, snapshot.proof_state.text if snapshot.proof_state else "  no goals")


def _history(session: ProofSession, output: TextIO) -> None:
    attempts = [event for event in session.events if event.event_type == "tactic_attempt"]
    if not attempts:
        _write(output, "No tactic attempts yet.")
        return
    for event in attempts:
        disposition = "accepted" if event.payload["accepted"] else "rejected"
        _write(output, f"{event.event_index}: {disposition}: {event.payload['tactic']}")


def run_repl(
    problems: tuple[Problem, ...],
    verifier: Verifier,
    trace_factory: Callable[[Problem], TraceWriter],
    input_stream: TextIO,
    output_stream: TextIO,
) -> int:
    if not problems:
        _write(output_stream, "No problems are available.")
        return 1
    session: ProofSession | None = None
    try:
        _list_problems(problems, output_stream)
        while session is None:
            output_stream.write("problem> ")
            output_stream.flush()
            selection = input_stream.readline()
            if selection == "":
                return 0
            problem = _select_problem(selection.strip(), problems)
            if problem is None:
                _write(output_stream, "Unknown problem. Enter its ID or list number.")
                continue
            session = ProofSession.start(problem, verifier, trace_factory(problem))
        _show(session, output_stream)

        while True:
            output_stream.write("lean> ")
            output_stream.flush()
            raw = input_stream.readline()
            if raw == "":
                session.close("eof")
                return 0
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            if line.startswith(":"):
                try:
                    parts = shlex.split(line)
                except ValueError as exc:
                    _write(output_stream, f"Command error: {exc}")
                    continue
                command = parts[0]
                try:
                    if command == ":quit":
                        session.close("quit")
                        return 0
                    if command == ":help":
                        _write(output_stream, HELP.rstrip())
                    elif command == ":show":
                        _show(session, output_stream)
                    elif command == ":history":
                        _history(session, output_stream)
                    elif command == ":problems":
                        _list_problems(problems, output_stream)
                    elif command == ":undo":
                        session.undo()
                        _show(session, output_stream)
                    elif command == ":reset":
                        session.reset()
                        _show(session, output_stream)
                    elif command == ":save":
                        destination = Path(parts[1]) if len(parts) == 2 else Path.cwd() / "proofs" / f"{session.problem.id}-{session.session_id}.lean"
                        saved = session.save(destination)
                        _write(output_stream, f"Saved {saved}")
                    elif command == ":load" and len(parts) == 2:
                        problem = _select_problem(parts[1], problems)
                        if problem is None:
                            raise SessionCommandError(f"unknown problem {parts[1]!r}")
                        session.close("load")
                        session = ProofSession.start(problem, verifier, trace_factory(problem))
                        _show(session, output_stream)
                    else:
                        _write(output_stream, "Unknown command. Use :help.")
                except (SessionCommandError, OSError) as exc:
                    _write(output_stream, f"Command error: {exc}")
                continue

            try:
                result = session.submit_tactic(line)
            except SessionCommandError as exc:
                _write(output_stream, f"Command error: {exc}")
                continue
            if result.status is VerificationStatus.OPEN:
                _write(output_stream, "Accepted")
                _write(output_stream, result.proof_state.text if result.proof_state else "")
            elif result.status is VerificationStatus.COMPLETE:
                _write(output_stream, f"Proof complete ({len(session.snapshot().accepted_tactics)} tactics)")
            elif result.status is VerificationStatus.TIMEOUT:
                _write(output_stream, "Lean timed out")
            else:
                _write(output_stream, f"Rejected ({result.status.value})")
                for diagnostic in result.diagnostics:
                    _write(output_stream, diagnostic.message)
    except KeyboardInterrupt:
        _write(output_stream, "Interrupted")
        if session is not None:
            session.close("interrupt")
        return 130
    except SessionError as exc:
        _write(output_stream, f"Session error: {exc}")
        if session is not None:
            session.close("error")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lean logic proof REPL")
    parser.add_argument("--trace", type=Path, help="write the first session trace to this path")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("list", help="list bundled problems")
    replay = subparsers.add_parser("replay", help="re-verify a JSONL trace")
    replay.add_argument("trace_path", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None and args.trace is not None:
        try:
            if args.trace.exists() and args.trace.stat().st_size > 0:
                _write(sys.stderr, f"Trace path already exists and is nonempty: {args.trace}")
                return 2
        except OSError as exc:
            _write(sys.stderr, f"Cannot inspect trace path: {exc}")
            return 2
    problems = load_problems(CORPUS_ROOT / "problems.json")
    if args.command == "list":
        _list_problems(problems, sys.stdout)
        return 0
    verifier = LeanSubprocessVerifier(LEAN_PROJECT_ROOT)
    if args.command == "replay":
        try:
            result = replay_trace(args.trace_path, {problem.id: problem for problem in problems}, verifier)
        except TraceValidationError as exc:
            _write(sys.stderr, f"Invalid trace: {exc}")
            return 2
        if result.matched:
            _write(sys.stdout, f"Replay matched ({result.checked_events} tactic attempts)")
            return 0
        _write(sys.stdout, f"Replay diverged at event {result.divergent_event_index}")
        return 1

    counter = 0

    def trace_factory(problem: Problem) -> JsonlTraceWriter:
        nonlocal counter
        counter += 1
        if args.trace is not None and counter == 1:
            path = args.trace
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = Path.cwd() / "traces" / f"{stamp}-{problem.id}-{uuid.uuid4()}.jsonl"
        return JsonlTraceWriter(path)

    return run_repl(problems, verifier, trace_factory, sys.stdin, sys.stdout)
