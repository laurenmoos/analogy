# Self-Contained Lean REPL Design

Date: 2026-10-12

## Objective

Build a small, self-contained Python gym for interactively solving constructive propositional-logic theorems with Lean 4. The first milestone supplies 20–30 existing problems from *A Lean Intro to Logic*, accepts one tactic at a time, verifies every proposed step with Lean, displays the resulting proof state, and records both successful and failed attempts as replayable traces.

The milestone is successful when a user can select a bundled problem, work through it in a terminal REPL, save the proof and trace, and replay the trace with the same Lean outcomes.

## Scope

The milestone includes:

- A Python package and command-line REPL under `analogy/lean_gym/`.
- A pinned, dependency-light Lean 4 project.
- Between 20 and 30 normalized constructive-logic problems.
- Stepwise tactic verification and proof-state display.
- Commands for inspecting and editing an interactive session.
- Append-only JSONL traces containing accepted and rejected attempts.
- Reference proofs used only for corpus validation and automated tests.
- A small Python API exposing the same session behavior as the terminal REPL.

The milestone excludes:

- LLM calls or OpenAI dependencies.
- Embeddings, retrieval, or analogical daydreaming.
- Training, evaluation comparisons, or benchmark dashboards.
- A persistent Lean server, editor protocol integration, or custom Lean metaprogramming REPL.
- Arbitrary user-authored imports or theorem declarations.

These excluded capabilities may be added later without changing the public verifier and session interfaces.

## Source Corpus

The problems come from the MIT-licensed [Trequetrum/lean4game-logic](https://github.com/Trequetrum/lean4game-logic) repository. The source game contains introductory and tactic-oriented worlds for conjunction, implication, negation, disjunction, and biconditional reasoning.

The gym vendors normalized theorem statements and metadata rather than depending on the game engine at runtime. Each problem record contains:

- A stable gym-specific identifier.
- Source repository path and level identifier.
- Title and optional short description.
- Lean binders and target proposition.
- Allowed or expected connective family.
- Difficulty/order metadata.
- A reference proof stored outside the model- or user-facing problem payload.

The corpus importer will select 20–30 levels that compile using Lean core and do not require the Lean Game Server. Reference proofs are retained for validation but never inserted into an interactive session.

## Project Layout

```text
analogy/
  lean_gym/
    pyproject.toml
    README.md
    lean-toolchain
    lakefile.toml
    LeanGym/
      Prelude.lean
    corpus/
      problems.json
      reference_proofs.json
      LICENSE-source
    src/lean_gym/
      __init__.py
      __main__.py
      cli.py
      corpus.py
      models.py
      verifier.py
      session.py
      traces.py
      replay.py
    tests/
      test_corpus.py
      test_verifier.py
      test_session.py
      test_replay.py
      fixtures/
    traces/
      .gitkeep
```

The Lean project pins `leanprover/lean4:v4.29.1`, matching the installed toolchain at design time. The logic problems require only Lean core, so the project does not add Mathlib or the Lean Game Server.

## Python Interfaces

### Verifier

```python
class Verifier(Protocol):
    def initial_state(self, problem: Problem) -> VerificationResult: ...
    def try_tactic(
        self,
        problem: Problem,
        accepted_tactics: tuple[str, ...],
        candidate: str,
    ) -> VerificationResult: ...
    def verify_complete(
        self,
        problem: Problem,
        tactics: tuple[str, ...],
    ) -> VerificationResult: ...
```

`LeanSubprocessVerifier` is the only milestone implementation. The protocol keeps the session independent of the verification mechanism and permits a persistent Lean process later.

### Session

`ProofSession` owns a problem, the accepted tactic prefix, the current proof state, and event history. It exposes operations for submitting a tactic, undoing the last accepted tactic, resetting the session, inspecting history, saving a proof, and quitting.

A rejected tactic returns a result and emits a trace event but does not mutate the accepted prefix or current state.

### Trace Writer

`JsonlTraceWriter` appends one complete JSON object per event and flushes after every write. Abrupt termination may lose an in-progress event but cannot corrupt preceding events.

## Lean Verification Strategy

Each tactic attempt is checked in a fresh temporary directory. The verifier generates a Lean file containing the normalized theorem and the accepted tactic prefix.

For a non-final step, the proof body is:

```lean
theorem current_problem ... : target := by
  <accepted tactics>
  <candidate tactic>
  trace_state
  sorry
```

Lean is invoked with JSON diagnostics:

```text
lake env lean --json <temporary-file>
```

The installed Lean 4.29.1 emits the `trace_state` proof state as a JSON information message and reports the intentional `sorry` separately as a warning. For a still-open proof, the verifier accepts the tactic only when:

- Lean exits successfully.
- No error-severity diagnostic is present.
- The only permitted `sorry` diagnostic belongs to the generated tail.
- A proof-state trace is present, unless the candidate closed all goals.

If the candidate closes every goal, the generated `trace_state` emits the specific `No goals to be solved` error at the verifier-controlled trace line. The verifier treats only that exact location and diagnostic as a completion candidate, then immediately runs final verification without `trace_state` or `sorry`:

```lean
theorem current_problem ... : target := by
  <accepted tactics>
  <candidate tactic>
```

Only this clean second pass marks the session complete.

Python enforces a wall-clock subprocess timeout. Lean's `-T` allocation limit may also be configured, but the wall-clock timeout remains authoritative for the REPL.

## REPL Behavior

Starting the REPL presents the corpus and asks the user to select a problem. The session then displays the theorem and current proof state.

Any non-command line is treated as one Lean tactic. Commands begin with `:`:

- `:show` — display the theorem, accepted prefix, and current goals.
- `:history` — display accepted and rejected tactic attempts in order.
- `:undo` — remove the last accepted tactic and recompute the state.
- `:reset` — clear the accepted prefix and restore the initial state.
- `:save [path]` — write the completed or partial proof script.
- `:problems` — list bundled problems.
- `:load <id>` — start a fresh session on another problem.
- `:help` — show command help.
- `:quit` — flush the trace and exit.

The REPL clearly distinguishes accepted tactics, rejected tactics, completed proofs, verifier timeouts, and internal errors. It never hides Lean's diagnostic text.

## Trace Format

Each REPL session writes an append-only JSONL file under `lean_gym/traces/` unless the caller supplies another path. Every event contains:

```json
{
  "schema_version": 1,
  "session_id": "uuid",
  "problem_id": "and_tactic_01",
  "event_index": 3,
  "event_type": "tactic_attempt",
  "timestamp": "RFC3339 UTC",
  "tactic": "constructor",
  "accepted": true,
  "state_before": "...",
  "state_after": "...",
  "accepted_prefix": ["intro h", "constructor"],
  "lean_exit_code": 0,
  "diagnostics": [],
  "duration_ms": 42
}
```

Session-start, undo, reset, save, completion, and session-end events use the same envelope with event-specific payload fields. Trace files contain no secrets and no environment-variable values.

Replay reads events in order, resubmits tactic attempts through the verifier, and compares acceptance status plus normalized proof states. Replay reports divergence with the first mismatching event index.

## Data Models

Frozen standard-library dataclasses define the in-memory records:

- `Problem`
- `ProofState`
- `Diagnostic`
- `VerificationResult`
- `TraceEvent`
- `SessionSnapshot`

Explicit parsing functions validate corpus JSON and trace JSONL at the system boundary. The milestone therefore has no required third-party Python runtime dependency. Proof-state text is preserved verbatim for display and trace fidelity. A normalized representation removes generated file paths, source positions, and unstable whitespace before replay comparisons.

## Error Handling and Safety

- Tactic syntax errors, tactic execution failures, timeouts, unsolved goals, corpus errors, and internal errors have distinct statuses.
- Rejected or timed-out attempts never mutate the accepted tactic prefix.
- Temporary paths are created with Python's `tempfile` APIs and removed after each check.
- Tactic text is inserted only inside the generated `by` block. The gym does not accept imports, declarations, or arbitrary file paths from the REPL.
- The verifier uses argument arrays rather than shell command strings.
- Subprocess output is bounded before being retained in traces.
- A corrupted corpus or trace fails with a precise validation error instead of partial execution.
- `Ctrl-C` interrupts the current check, flushes a session-end event when possible, and returns control without deleting saved traces.

## Testing Strategy

Unit tests cover corpus parsing, state normalization, trace serialization, command parsing, and session mutation rules.

Integration tests invoke the real pinned Lean toolchain and establish that:

- Every bundled theorem statement compiles with its reference proof.
- Initial-state extraction returns the expected goal.
- An accepted tactic changes the state as expected.
- A rejected tactic preserves the prior state.
- Multiple goals are retained and displayed.
- `undo` and `reset` reconstruct the correct state.
- A completed proof passes a clean check without `sorry`.
- Timeouts receive the timeout status.
- A saved trace replays without divergence.

Tests use small fixed fixtures for verifier behavior and the full 20–30 problem corpus for a slower corpus-validation suite.

## Milestone Acceptance Criteria

The milestone is complete when:

1. The project installs as a Python package and the pinned Lean project builds.
2. The corpus contains between 20 and 30 attributed logic problems.
3. Every corpus problem has a reference proof that Lean verifies without `sorry`.
4. A terminal user can select a problem and submit one tactic at a time.
5. Accepted tactics advance the displayed proof state; rejected tactics do not.
6. `undo`, `reset`, `show`, `history`, `save`, and `quit` behave as documented.
7. Completed proofs are rechecked without `sorry`.
8. Successful and failed attempts are recorded in versioned JSONL.
9. Recorded traces replay without divergence under the pinned toolchain.
10. The automated test suite passes using the real Lean executable.

## Future Extension Boundary

Future automation will call `ProofSession.submit_tactic` and consume `VerificationResult`; it will not bypass the verifier. Retrieval or analogical systems may index trace events later, but trace schema version 1 contains no embedding-specific fields and makes no assumptions about an automated prover.
