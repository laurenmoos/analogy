# Lean Logic REPL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-contained Python REPL under `analogy/lean_gym/` that presents 25 constructive-logic theorems, checks one Lean 4 tactic per input line, displays the resulting goals, and records replayable successful and failed traces.

**Architecture:** A frozen domain-model and corpus layer feeds a subprocess verifier that creates a fresh Lean source file for every check. `ProofSession` is the single state machine used by both the Python API and terminal CLI. An append-only JSONL writer records every transition, and a separate replay service re-verifies tactic events and detects the first divergence.

**Tech Stack:** Python 3.11+, standard library runtime, pytest as a development dependency, Lean 4.29.1, Lake 5.0.0, JSON/JSONL.

**Spec:** [`analogy/docs/2026-10-12-lean-repl-design.md`](./2026-10-12-lean-repl-design.md)

## Global Constraints

- Keep all new runtime code, Lean files, corpus data, tests, traces, and documentation inside `analogy/lean_gym/`.
- Do not add OpenAI calls, LLM orchestration, embeddings, retrieval, databases, evaluation dashboards, Mathlib, or Lean Game Server dependencies.
- Pin `leanprover/lean4:v4.29.1`; invoke Lean through `lake env lean --json` from the package root.
- Treat corpus binders, targets, and reference proofs as trusted vendored data. Treat interactive tactics, trace paths, and saved-proof paths as untrusted input.
- Accept exactly one physical input line per tactic. Reject carriage returns, newlines, NUL bytes, blank tactics, and inputs longer than 4,096 Unicode code points before starting Lean.
- Use argument arrays for subprocesses, a fresh `TemporaryDirectory` per verification, UTF-8 text, a default 10-second wall timeout, and a 1 MiB combined stdout/stderr limit.
- Never mutate the accepted tactic prefix after a rejected tactic, timeout, or internal verifier failure.
- Normalize proof states and diagnostics before persisting or comparing them: LF line endings, trimmed trailing whitespace, temporary paths replaced with `<temp>`, and verifier-generated line/column positions removed from replay comparisons.
- The current workspace is not a Git repository. Each commit step below is a required checkpoint once the directory is placed under Git; during implementation in the present workspace, record the checkpoint in the execution notes and continue without initializing Git unless the user requests it.

## File and Responsibility Map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, console entry point, pytest settings |
| `lean-toolchain`, `lakefile.toml`, `LeanGym/Prelude.lean` | Pinned Lean environment and core-only prelude |
| `src/lean_gym/models.py` | Frozen domain objects, enums, JSON validation helpers |
| `src/lean_gym/corpus.py` | Load and validate public problems and private reference proofs |
| `src/lean_gym/verifier.py` | Generate Lean source, run Lean, classify diagnostics and proof states |
| `src/lean_gym/traces.py` | Versioned event creation and durable append-only JSONL writing |
| `src/lean_gym/session.py` | Proof-session state machine and proof-script rendering |
| `src/lean_gym/replay.py` | Read traces, re-run tactic attempts, report first divergence |
| `src/lean_gym/cli.py`, `__main__.py`, `__init__.py` | Terminal interface and stable Python API exports |
| `corpus/problems.json` | User-visible 25-problem corpus without solutions |
| `corpus/reference_proofs.json` | Test-only gold tactic sequences keyed by problem ID |
| `corpus/LICENSE-source` | Preserved upstream MIT license and source attribution |
| `tests/` | Unit, real-Lean integration, CLI, and end-to-end replay tests |

## Review Focus

The five highest-risk failure classes must each have an owning regression test:

1. **Input escaping:** multiline or NUL-containing tactics could escape the generated proof body. `test_verifier.py` must prove they are rejected before subprocess invocation.
2. **False completion:** Lean's controlled `No goals to be solved` diagnostic could be confused with a genuine tactic error. `test_verifier.py` must prove that only the controlled trace location followed by a clean second pass yields `COMPLETE`.
3. **State corruption:** rejected tactics and timeouts could accidentally enter the accepted prefix. `test_session.py` must assert byte-for-byte state and prefix preservation.
4. **Replay instability:** temporary paths and generated source positions could create false divergence. `test_replay.py` must cover normalization and a real semantic divergence.
5. **Corpus/source defects:** an upstream statement may not compile, as in `OrIntro/L02`'s undeclared `K`. `test_corpus.py` must validate all 25 gold proofs and explicitly document that this source level is excluded.

---

## Task 1: Scaffold the Python and Lean Projects

**Files:**

- Create: `analogy/lean_gym/pyproject.toml`
- Create: `analogy/lean_gym/lean-toolchain`
- Create: `analogy/lean_gym/lakefile.toml`
- Create: `analogy/lean_gym/LeanGym/Prelude.lean`
- Create: `analogy/lean_gym/src/lean_gym/__init__.py`
- Create: `analogy/lean_gym/src/lean_gym/__main__.py`
- Create: `analogy/lean_gym/tests/test_project.py`
- Create: `analogy/lean_gym/traces/.gitkeep`

**Interfaces:**

- `python -m lean_gym --help` delegates to `lean_gym.cli.main` once the CLI exists.
- `lake env lean LeanGym/Prelude.lean` succeeds without network access or third-party Lean packages.

- [ ] Write `tests/test_project.py` first. Assert that the toolchain is exactly `leanprover/lean4:v4.29.1`, `lakefile.toml` declares package `LeanGym`, and `Prelude.lean` contains no `sorry` or external imports.

- [ ] Run `python3 -m pytest tests/test_project.py -q` from `analogy/lean_gym/`. Expect failure because the project files do not exist.

- [ ] Create `pyproject.toml` with a `src` layout, Python `>=3.11`, no runtime dependencies, optional `dev = ["pytest>=8,<9"]`, console script `lean-gym = "lean_gym.cli:main"`, and pytest `testpaths = ["tests"]`.

- [ ] Create `lean-toolchain` containing only:

  ```text
  leanprover/lean4:v4.29.1
  ```

- [ ] Create this minimal Lake project:

  ```toml
  name = "LeanGym"
  version = "0.1.0"
  defaultTargets = ["LeanGym"]

  [[lean_lib]]
  name = "LeanGym"
  ```

- [ ] Create `LeanGym/Prelude.lean` with only core declarations that make generated files consistent:

  ```lean
  namespace LeanGym

  set_option autoImplicit false

  end LeanGym
  ```

- [ ] Create `__main__.py` as a real delegation, not a second command implementation:

  ```python
  from lean_gym.cli import main

  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [ ] Run `lake env lean LeanGym/Prelude.lean` and `python3 -m pytest tests/test_project.py -q`. Expect both to pass.

- [ ] Checkpoint commit when Git is available: `git add analogy/lean_gym && git commit -m "build: scaffold lean logic gym"`.

## Task 2: Define Immutable Models and Strict Corpus Loading

**Files:**

- Create: `analogy/lean_gym/src/lean_gym/models.py`
- Create: `analogy/lean_gym/src/lean_gym/corpus.py`
- Create: `analogy/lean_gym/tests/test_models.py`
- Create: `analogy/lean_gym/tests/test_corpus.py`
- Create: `analogy/lean_gym/tests/fixtures/problems-valid.json`
- Create: `analogy/lean_gym/tests/fixtures/problems-invalid.json`

**Interfaces:**

```python
class VerificationStatus(str, Enum):
    OPEN = "open"
    COMPLETE = "complete"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"

@dataclass(frozen=True, slots=True)
class Problem:
    id: str
    title: str
    binders: str
    target: str
    source_path: str
    source_level: str
    connective: str
    difficulty: int

    def theorem_header(self, name: str = "current_problem") -> str:
        binder_segment = f" {self.binders}" if self.binders else ""
        return f"theorem {name}{binder_segment} : {self.target} := by"

@dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: str
    kind: str | None
    message: str
    line: int | None
    column: int | None

@dataclass(frozen=True, slots=True)
class ProofState:
    text: str
    normalized: str

@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: VerificationStatus
    accepted: bool
    proof_state: ProofState | None
    diagnostics: tuple[Diagnostic, ...]
    exit_code: int | None
    duration_ms: int

@dataclass(frozen=True, slots=True)
class ReferenceProof:
    problem_id: str
    tactics: tuple[str, ...]
```

The corpus module exposes `load_problems(path: Path) -> tuple[Problem, ...]`, `load_reference_proofs(path: Path) -> dict[str, ReferenceProof]`, and `validate_reference_coverage(problems, proofs) -> None`.

- [ ] Write model tests proving frozen instances cannot be mutated, `theorem_header()` renders binderless and bound theorems correctly, and enum values serialize as the specified lowercase strings.

- [ ] Write corpus tests for duplicate IDs, missing keys, unknown keys, wrong scalar types, nonpositive difficulty, empty binders/target rules, invalid tactic arrays, and references to unknown problem IDs.

- [ ] Run `python3 -m pytest tests/test_models.py tests/test_corpus.py -q`. Expect import failures.

- [ ] Implement `models.py` with the exact frozen classes above plus `TraceEvent` and `ReplayResult` frozen models used by later tasks. `TraceEvent` must carry the common envelope and an immutable `payload: Mapping[str, object]`; `ReplayResult` must carry `matched`, `checked_events`, `divergent_event_index`, `expected`, and `actual`. Add `normalize_proof_state_text(value: str) -> str` and `ProofState.from_text(value)` so verifier output and replay comparison use one implementation.

- [ ] Implement strict helpers in `corpus.py`: `_expect_object`, `_expect_exact_keys`, `_expect_string`, `_expect_int`, and `_expect_string_list`. Every validation exception must include the JSON path, such as `problems[2].difficulty`.

- [ ] Implement `load_problems()` to reject duplicate IDs and return records sorted by `(difficulty, id)`. Implement `load_reference_proofs()` to return a dictionary keyed by problem ID. Add `validate_reference_coverage()` that reports missing and extra proof IDs in one exception.

- [ ] Run the focused tests and then `python3 -m pytest -q`. Expect all current tests to pass.

- [ ] Checkpoint commit when Git is available: `git add analogy/lean_gym && git commit -m "feat: add immutable corpus models"`.

## Task 3: Implement the Real Lean Subprocess Verifier

**Files:**

- Create: `analogy/lean_gym/src/lean_gym/verifier.py`
- Create: `analogy/lean_gym/tests/test_verifier.py`
- Create: `analogy/lean_gym/tests/fixtures/fake_lake.py`

**Interfaces:**

The `Verifier` protocol exposes `initial_state(problem)`, `state_after(problem, accepted_tactics)`, `try_tactic(problem, accepted_tactics, candidate)`, and `verify_complete(problem, tactics)`, all returning `VerificationResult`. `LeanSubprocessVerifier(project_root, timeout_seconds=10.0, max_output_bytes=1_048_576, lake_executable="lake")` implements it.

- [ ] Write unit tests against `fake_lake.py` for JSON diagnostic parsing, warning preservation, non-JSON output, output-limit failure, timeout classification, and subprocess launch failure.

- [ ] Add the input-escaping regression: patch `subprocess.run`, submit `"intro h\nend"`, `"intro h\rend"`, `"intro\x00h"`, an empty string, and 4,097 characters, and assert `subprocess.run` was never called.

- [ ] Add real-Lean tests using `Problem(id="identity", title="Identity", binders="(P : Prop)", target="P → P", source_path="test", source_level="test", connective="implication", difficulty=1)`:

  - `initial_state()` returns `OPEN` with goal `P → P`.
  - `intro h` returns `OPEN` with hypothesis `h : P` and goal `P`.
  - `exact h` after `intro h` returns `COMPLETE`.
  - `exact False.elim` returns `REJECTED` with Lean's diagnostic.
  - `constructor` on `(P Q : Prop) : P → Q → P ∧ Q` produces two goals and later tactics preserve both correctly.

- [ ] Add the false-completion regression. Feed a fake `No goals to be solved` at a non-trace line and assert `REJECTED`; feed it at the controlled trace line but make the clean verification fail and assert `REJECTED`; only the controlled error plus a successful clean pass may return `COMPLETE`.

- [ ] Run `python3 -m pytest tests/test_verifier.py -q`. Expect failure because the verifier does not exist.

- [ ] Implement `validate_tactic()` with the exact one-line, nonblank, NUL-free, 4,096-code-point policy. Return a rejected `VerificationResult` with `kind="input_validation"` rather than throwing for user input.

- [ ] Implement source rendering with deterministic line accounting:

  ```python
  def _render_probe(problem: Problem, tactics: tuple[str, ...]) -> RenderedSource:
      body = [problem.theorem_header()]
      body.extend(f"  {tactic}" for tactic in tactics)
      trace_line = len(body) + 1
      body.extend(("  trace_state", "  sorry"))
      return RenderedSource(text="\n".join(body) + "\n", trace_line=trace_line)
  ```

  `RenderedSource` is a private frozen dataclass containing `text` and `trace_line`.

- [ ] Implement `_run_lean()` using `TemporaryDirectory(prefix="lean-gym-")`, `Path.write_text(..., encoding="utf-8")`, and:

  ```python
  subprocess.run(
      [self.lake_executable, "env", "lean", "--json", str(source_path)],
      cwd=self.project_root,
      capture_output=True,
      text=False,
      timeout=self.timeout_seconds,
      check=False,
  )
  ```

  Measure duration with `time.monotonic_ns()`. Reject output beyond the configured byte limit before decoding with UTF-8 replacement.

- [ ] Parse one JSON object per output line. Preserve unparseable nonblank lines as `Diagnostic(severity="error", kind="protocol", message=line, line=None, column=None)`. Identify the `trace_state` information message by its controlled source position and normalize its message into `proof_state`.

- [ ] Implement the classification sequence exactly: ordinary errors mean `REJECTED`; timeout means `TIMEOUT`; valid trace plus only the generated `sorry` warning means `OPEN`; controlled `No goals to be solved` triggers `_render_final()` and a second Lean run; only a clean second run with exit code zero and no errors or `sorry` means `COMPLETE`.

- [ ] Implement `initial_state(problem)` as `state_after(problem, ())`; implement `state_after(problem, accepted_tactics)` as a probe without a new candidate; and implement `verify_complete(problem, tactics)` as a single clean final run.

- [ ] Run `python3 -m pytest tests/test_verifier.py -q` and then the full test suite. Expect all tests to pass with real Lean 4.29.1.

- [ ] Checkpoint commit when Git is available: `git add analogy/lean_gym && git commit -m "feat: verify tactics with lean subprocess"`.

## Task 4: Vendor and Verify the 25-Problem Logic Corpus

**Files:**

- Create: `analogy/lean_gym/corpus/problems.json`
- Create: `analogy/lean_gym/corpus/reference_proofs.json`
- Create: `analogy/lean_gym/corpus/LICENSE-source`
- Modify: `analogy/lean_gym/tests/test_corpus.py`

**Interfaces:**

- `problems.json` is an object with `schema_version: 1`, source repository metadata, and a `problems` array.
- `reference_proofs.json` is an object with `schema_version: 1` and a `proofs` array containing `problem_id` and a one-line `tactics` array.
- Public problem loading never opens or returns `reference_proofs.json`.

- [ ] Add a manifest assertion to `test_corpus.py` requiring exactly these stable IDs and upstream paths:

  | IDs | Upstream levels |
  |---|---|
  | `and_intro_01`–`and_intro_05` | `Game/Levels/AndIntro/L01.lean`–`L05.lean` |
  | `imp_intro_01`–`imp_intro_05` | `Game/Levels/ImpIntro/L01.lean`–`L05.lean` |
  | `or_intro_01`, `or_intro_03`–`or_intro_06` | `Game/Levels/OrIntro/L01.lean`, `L03.lean`–`L06.lean` |
  | `iff_intro_01`–`iff_intro_05` | `Game/Levels/IffIntro/L01.lean`–`L05.lean` |
  | `not_intro_01`–`not_intro_05` | `Game/Levels/NotIntro/L01.lean`–`L05.lean` |

- [ ] Add assertions that all five connective families contain five problems, IDs are unique, difficulties run in pedagogical order, source paths are present, and user-visible JSON contains none of the reference tactic strings.

- [ ] Add a real-Lean parametrized test that loads every reference proof and requires `verifier.verify_complete(problem, proof.tactics).status is COMPLETE`. The assertion message must include the problem ID and formatted diagnostics.

- [ ] Add the source-defect regression as a test comment and explicit negative assertion: `or_intro_02` is absent because upstream `OrIntro/L02` states `K ∨ S` while declaring only `O S : Prop`.

- [ ] Run `python3 -m pytest tests/test_corpus.py -q`. Expect failure because the production corpus is absent.

- [ ] Populate `problems.json` with these exact normalized theorem signatures:

  | ID | Binders | Target |
  |---|---|---|
  | `and_intro_01` | `(P : Prop) (todo_list : P)` | `P` |
  | `and_intro_02` | `(P S : Prop) (p : P) (s : S)` | `P ∧ S` |
  | `and_intro_03` | `(A I O U : Prop) (a : A) (i : I) (o : O) (u : U)` | `(A ∧ I) ∧ O ∧ U` |
  | `and_intro_04` | `(P S : Prop) (vm : P ∧ S)` | `P` |
  | `and_intro_05` | `(P Q : Prop) (h : P ∧ Q)` | `Q` |
  | `imp_intro_01` | `(P C : Prop) (p : P) (bakery_service : P → C)` | `C` |
  | `imp_intro_02` | `(C : Prop)` | `C → C` |
  | `imp_intro_03` | `(I S : Prop)` | `I ∧ S → S ∧ I` |
  | `imp_intro_04` | `(C A S : Prop) (h1 : C → A) (h2 : A → S)` | `C → S` |
  | `imp_intro_05` | `(P Q R S T U : Prop) (p : P) (h1 : P → Q) (h2 : Q → R) (h3 : Q → T) (h4 : S → T) (h5 : T → U)` | `U` |
  | `or_intro_01` | `(O S : Prop) (s : S)` | `S ∨ O` |
  | `or_intro_03` | `(B C I : Prop) (h1 : C → B) (h2 : I → B) (h3 : C ∨ I)` | `B` |
  | `or_intro_04` | `(C O : Prop) (h : C ∨ O)` | `O ∨ C` |
  | `or_intro_05` | `(C J R : Prop) (h1 : C → J) (h2 : C ∨ R)` | `J ∨ R` |
  | `or_intro_06` | `(G H U : Prop) (h : G ∨ H ∧ U)` | `(G ∨ H) ∧ (G ∨ U)` |
  | `iff_intro_01` | `(J S : Prop) (hsj : S → J) (hjs : J → S)` | `S ↔ J` |
  | `iff_intro_02` | `(B P : Prop) (h : B ↔ ¬P)` | `(B → ¬P) ∧ (¬P → B)` |
  | `iff_intro_03` | `(P Q R : Prop) (h1 : Q ↔ R) (h2 : P → Q)` | `P → R` |
  | `iff_intro_04` | `(P Q R : Prop) (h1 : P ↔ R) (h2 : P → Q)` | `R → Q` |
  | `iff_intro_05` | `(A C L P : Prop) (h1 : L ↔ P) (h2 : ¬((A → C ∨ ¬P) ∧ (P ∨ A → ¬C) → (P → C)) ↔ A ∧ C ∧ ¬P)` | `¬((A → C ∨ ¬L) ∧ (L ∨ A → ¬C) → (L → C)) ↔ A ∧ C ∧ ¬L` |
  | `not_intro_01` | empty string | `¬False` |
  | `not_intro_02` | `(B S : Prop) (h : ¬S)` | `S → B` |
  | `not_intro_03` | `(P : Prop) (p : P)` | `¬¬P` |
  | `not_intro_04` | `(L : Prop)` | `¬(L ∧ ¬L)` |
  | `not_intro_05` | `(B S : Prop) (h1 : B → S) (h2 : ¬S)` | `¬B` |

- [ ] Use Lean core names in gold proofs so runtime has no GameLogic dependency. Store these exact reference tactic sequences:

  ```text
  and_intro_01: exact todo_list
  and_intro_02: exact ⟨p, s⟩
  and_intro_03: exact ⟨⟨a, i⟩, ⟨o, u⟩⟩
  and_intro_04: exact vm.left
  and_intro_05: exact h.right
  imp_intro_01: exact bakery_service p
  imp_intro_02: exact fun h => h
  imp_intro_03: exact fun h => ⟨h.right, h.left⟩
  imp_intro_04: exact fun c => h2 (h1 c)
  imp_intro_05: exact h5 (h3 (h1 p))
  or_intro_01: exact Or.inl s
  or_intro_03: exact h3.elim h1 h2
  or_intro_04: exact h.elim Or.inr Or.inl
  or_intro_05: exact h2.elim (fun c => Or.inl (h1 c)) Or.inr
  or_intro_06: rcases h with g | hu
  or_intro_06: · exact ⟨Or.inl g, Or.inl g⟩
  or_intro_06: · exact ⟨Or.inr hu.left, Or.inr hu.right⟩
  iff_intro_01: exact ⟨hsj, hjs⟩
  iff_intro_02: exact ⟨h.mp, h.mpr⟩
  iff_intro_03: exact fun p => h1.mp (h2 p)
  iff_intro_04: exact fun r => h2 (h1.mpr r)
  iff_intro_05: rw [h1]
  iff_intro_05: exact h2
  not_intro_01: exact fun f => f
  not_intro_02: exact fun s => False.elim (h s)
  not_intro_03: exact fun np => np p
  not_intro_04: exact fun h => h.right h.left
  not_intro_05: exact fun b => h2 (h1 b)
  ```

- [ ] Copy the upstream MIT license verbatim into `LICENSE-source`, followed by a short attribution line naming `https://github.com/Trequetrum/lean4game-logic` and the vendored level paths.

- [ ] Run the corpus tests. If a normalized theorem or gold tactic fails, correct the vendored record against its named upstream file; do not add a dependency on the source game.

- [ ] Run `python3 -m pytest -q`. Expect all 25 clean proof checks and all existing tests to pass.

- [ ] Checkpoint commit when Git is available: `git add analogy/lean_gym && git commit -m "data: add verified constructive logic corpus"`.

## Task 5: Add Durable Versioned JSONL Tracing

**Files:**

- Create: `analogy/lean_gym/src/lean_gym/traces.py`
- Create: `analogy/lean_gym/tests/test_traces.py`

**Interfaces:**

`TraceWriter` exposes `write(event: TraceEvent) -> None` and `close() -> None`. `JsonlTraceWriter(path: Path)` implements that protocol. `make_event(session_id, problem_id, event_index, event_type, payload) -> TraceEvent` creates the versioned envelope.

- [ ] Write tests proving one compact JSON object is appended per call, each write is flushed, Unicode tactics round-trip, UTC timestamps end in `Z`, the parent directory is created, writes after close fail clearly, and no environment variables are serialized implicitly.

- [ ] Write an interrupted-write test with an injected file object whose second write raises. Reopen the file and assert the first event remains a valid complete JSON line.

- [ ] Run `python3 -m pytest tests/test_traces.py -q`. Expect import failure.

- [ ] Implement `TraceEvent.to_dict()` with the fixed keys `schema_version`, `session_id`, `problem_id`, `event_index`, `event_type`, `timestamp`, followed by event payload. Reject payload keys that collide with the envelope.

- [ ] Implement `make_event()` with `datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")` and `schema_version=1`.

- [ ] Implement `JsonlTraceWriter` using append mode, UTF-8, newline `"\n"`, `json.dumps(..., ensure_ascii=False, separators=(",", ":"), sort_keys=True)`, and `flush()` after every event. Support context-manager entry and exit.

- [ ] Run focused and full tests. Expect all tests to pass.

- [ ] Checkpoint commit when Git is available: `git add analogy/lean_gym && git commit -m "feat: record durable proof traces"`.

## Task 6: Build the ProofSession State Machine and Proof Saving

**Files:**

- Create: `analogy/lean_gym/src/lean_gym/session.py`
- Create: `analogy/lean_gym/tests/test_session.py`

**Interfaces:**

`SessionSnapshot` is a frozen dataclass with `problem`, `accepted_tactics`, `proof_state`, and `complete`. `ProofSession.start(problem, verifier, trace_writer, session_id=None)` constructs a session. Its public methods are `submit_tactic(tactic) -> VerificationResult`, `undo() -> SessionSnapshot`, `reset() -> SessionSnapshot`, `save(path) -> Path`, `snapshot() -> SessionSnapshot`, and `close() -> None`.

- [ ] Write tests with a deterministic fake verifier for session start, accepted open steps, completion, rejected steps, undo, reset, save, submit-after-completion, and event indexes beginning at zero and increasing once per emitted event.

- [ ] Add the state-corruption regressions. Capture `snapshot` before a rejection and timeout; assert the snapshot and accepted prefix are unchanged afterward while two tactic-attempt trace events are still emitted with `accepted=false`.

- [ ] Add a real-verifier lifecycle test for identity: start, call `submit_tactic("intro h")`, reject `submit_tactic("exact missing")`, complete with `submit_tactic("exact h")`, save, and compile the saved `.lean` file cleanly.

- [ ] Run `python3 -m pytest tests/test_session.py -q`. Expect import failure.

- [ ] Implement `ProofSession.start()` so initialization failure raises `SessionInitializationError` with formatted diagnostics and does not emit a misleading session-start event.

- [ ] Implement `submit_tactic()` as the sole tactic transition: snapshot before state, call verifier, mutate only for `OPEN` or `COMPLETE`, append a complete trace payload with tactic, accepted flag, state before/after, accepted prefix, exit code, diagnostics, and duration, then emit a separate completion event for a newly completed proof.

- [ ] Implement `undo()` to remove only the last accepted tactic, recompute state from the remaining prefix through `verifier.state_after()`, and emit `undo`. Reject undo on an empty prefix with `SessionCommandError` and no state mutation.

- [ ] Implement `reset()` by recomputing the initial state and clearing the prefix only after recomputation succeeds. Emit `reset` with the removed tactic count.

- [ ] Implement proof rendering and saving:

  ```python
  def render_proof(problem: Problem, tactics: tuple[str, ...]) -> str:
      lines = [problem.theorem_header()]
      lines.extend(f"  {tactic}" for tactic in tactics)
      return "\n".join(lines) + "\n"
  ```

  Resolve the destination with `Path.expanduser()` only; do not execute shell expansion. Create only its immediate parent with `parents=True`, write UTF-8, and emit a `save` event containing the resulting path and completion flag.

- [ ] Implement `close()` idempotently, emit one `session_end` event, flush/close the writer, and reject later mutation methods with `SessionClosedError`.

- [ ] Run focused and full tests. Expect all tests to pass.

- [ ] Checkpoint commit when Git is available: `git add analogy/lean_gym && git commit -m "feat: add interactive proof sessions"`.

## Task 7: Re-verify Traces and Report the First Divergence

**Files:**

- Create: `analogy/lean_gym/src/lean_gym/replay.py`
- Create: `analogy/lean_gym/tests/test_replay.py`

**Interfaces:**

`normalize_state(value: str | None) -> str | None` produces the comparison form. `replay_trace(path: Path, problems: Mapping[str, Problem], verifier: Verifier) -> ReplayResult` performs strict validation and deterministic re-verification.

- [ ] Write tests for malformed JSON, unsupported schema versions, missing session start, mixed session IDs, unknown problem IDs, nonmonotonic event indexes, and command events interleaved with tactic events.

- [ ] Write a fake-verifier happy-path test that replays both accepted and rejected tactics and requires matching acceptance plus normalized post-state.

- [ ] Add the replay-instability regression: expected state contains a real temp path and CRLF/trailing spaces while actual state contains another temp path and LF; assert a match. Then change a goal proposition and assert `divergent_event_index` identifies the first semantic mismatch.

- [ ] Add a real-Lean replay test that writes a trace through `ProofSession`, replays it, and expects `matched=True` with the exact number of tactic events checked.

- [ ] Run `python3 -m pytest tests/test_replay.py -q`. Expect import failure.

- [ ] Implement `normalize_state()` as the optional-value adapter around `models.normalize_proof_state_text()`. The shared helper normalizes newlines, right-strips each line, removes leading/trailing blank lines, replaces `lean-gym-*` temporary source paths with `<temp>`, and preserves identifiers and propositions unchanged.

- [ ] Implement a strict streaming JSONL reader. Validate envelope keys and types before replaying. Do not load or execute paths, commands, or Python values from the trace.

- [ ] Reconstruct one session from `session_start`; for each `tactic_attempt`, call the verifier with the reconstructed accepted prefix, compare `accepted`, status class, and normalized `state_after`, and add a tactic to the prefix only when both expected and actual attempts are accepted.

- [ ] Return immediately on the first mismatch with concise expected/actual dictionaries. Return `matched=True` only after the complete file is validated and all tactic attempts agree.

- [ ] Run focused and full tests. Expect all tests to pass.

- [ ] Checkpoint commit when Git is available: `git add analogy/lean_gym && git commit -m "feat: replay and verify proof traces"`.

## Task 8: Add the Terminal REPL, Public API, and End-to-End Documentation

**Files:**

- Create: `analogy/lean_gym/src/lean_gym/cli.py`
- Modify: `analogy/lean_gym/src/lean_gym/__init__.py`
- Modify: `analogy/lean_gym/src/lean_gym/__main__.py`
- Create: `analogy/lean_gym/tests/test_cli.py`
- Create: `analogy/lean_gym/tests/test_end_to_end.py`
- Create: `analogy/lean_gym/README.md`

**Interfaces:**

```text
lean-gym [--trace PATH]
lean-gym replay TRACE_PATH
lean-gym list
```

The interactive commands are `:show`, `:history`, `:undo`, `:reset`, `:save [path]`, `:problems`, `:load <id>`, `:help`, and `:quit`.

- [ ] Write CLI tests using injected input/output streams. Cover numbered and ID-based initial selection; every command; unknown commands; blank input; EOF; KeyboardInterrupt; rejected tactics; completion; switching problems; and paths containing spaces.

- [ ] Assert `:load <id>` closes the current session trace and starts a new session ID, while `:problems` never reveals reference proofs.

- [ ] Write an end-to-end subprocess test that runs `python -m lean_gym` with scripted stdin for `imp_intro_02`, submits `intro h`, one failing tactic, and `exact h`, then checks exit code, visible goal transitions, completion text, JSONL validity, replay success, and a compilable saved proof.

- [ ] Run `python3 -m pytest tests/test_cli.py tests/test_end_to_end.py -q`. Expect failure because the CLI does not exist.

- [ ] Implement `argparse` subcommands and dependency injection. The two entry functions are `run_repl(problems, verifier, trace_factory, input_stream, output_stream) -> int` and `main(argv: Sequence[str] | None = None) -> int`. `run_repl` owns the tested command loop; `main` only loads paths/dependencies, dispatches `list` or `replay`, and delegates interactive operation to `run_repl`.

- [ ] Render accepted steps with the new proof state, rejected steps with all Lean diagnostics, timeouts distinctly, and completion with the accepted tactic count. `:show` prints theorem header, accepted prefix, and current goals; `:history` prints every attempt and disposition in event order.

- [ ] Catch `KeyboardInterrupt` around both input collection and tactic verification. Print an interruption message, emit and flush one `session_end` event with reason `interrupt`, leave existing trace/proof files intact, and exit with status 130. Treat EOF as a normal `:quit` with status zero.

- [ ] Default traces to `traces/<UTC timestamp>-<problem ID>-<session UUID>.jsonl`. Default `:save` to `proofs/<problem ID>-<session UUID>.lean`. Never overwrite a caller-specified existing save path; return a command error unless the user chooses a new path.

- [ ] Export only the stable Python API from `__init__.py`: `Problem`, `ProofSession`, `SessionSnapshot`, `VerificationResult`, `VerificationStatus`, `LeanSubprocessVerifier`, `load_problems`, and `replay_trace`.

- [ ] Write `README.md` with prerequisites, exact setup commands, CLI examples, all REPL commands, Python API example, trace/replay example, corpus attribution, architecture limits, and a clear statement that this milestone has no LLM or analogy layer.

- [ ] Run `python3 -m pip install -e '.[dev]'`, `python3 -m pytest -q`, `lake env lean LeanGym/Prelude.lean`, `python3 -m lean_gym list`, and a manual piped identity proof. Record exact command output in the execution notes.

- [ ] Checkpoint commit when Git is available: `git add analogy/lean_gym && git commit -m "feat: ship lean logic repl milestone"`.

## Final Verification Checklist

- [ ] Run the complete suite twice: once normally and once with `PYTHONHASHSEED=1` to expose ordering assumptions.
- [ ] Confirm every one of the 25 reference proofs returns `COMPLETE` with no `sorry` diagnostics.
- [ ] Confirm at least one trace containing accepted, rejected, undo, reset, save, completion, and session-end events replays successfully.
- [ ] Confirm the public `problems.json`, CLI output, saved traces, and README do not disclose reference proofs except where the README intentionally demonstrates a theorem unrelated to a bundled answer.
- [ ] Search runtime code for excluded dependencies and unfinished markers:

  ```text
  rg -n "openai|embedding|daydream|sqlite|mathlib|lean4game|TODO|TBD|NotImplementedError" analogy/lean_gym/src analogy/lean_gym/LeanGym
  ```

  Expect no matches after implementation.

- [ ] Review the five risk tests listed under **Review Focus** and include their test node IDs in the completion report.
