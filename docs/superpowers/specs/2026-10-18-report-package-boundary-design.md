# Solver Report Package Boundary

## Intent

Keep proof-search case classes focused on computation. Anything whose purpose is to describe, display, serialize, or persist what happened during a solver run belongs under `solver/report/`.

## Package boundary

`solver/report/` is the only solver package that owns completed-run records, conversion of search results into report data, human-readable rendering, JSON serialization, report files, and checkpoint updates.

The public report API remains the five high-level operations used by the two run recipes:

- `start_run`
- `build_problem_record`
- `write_problem_record`
- `build_run_record`
- `write_run_record`

`solver/run.py` imports these operations from `.report`, so its two recipes remain readable and unchanged in shape.

## File responsibilities

- `solver/report/records.py`: `SolveRunRecord`, `ProblemRunRecord`, and `RunState` data classes.
- `solver/report/build.py`: convert completed or failed searches into structured problem and run records.
- `solver/report/render.py`: render problem traces, complete run reports, and the CLI summary as strings.
- `solver/report/storage.py`: create output directories and write JSON, Markdown, checkpoints, and accumulated in-memory report state.
- `solver/report/__init__.py`: expose the small public API used by `run.py` and the CLI.

The current `solver/report.py` and `solver/run_records.py` modules are removed.

## Computational state

`AnalogyState` and `AnalogyScratchpad` retain only validated data and lifecycle transitions. They do not render model-facing blocks or prompt context and do not parse formatted model output.

Model-facing analogy text is a protocol concern, not run reporting. It moves to `solver/analogy/protocol.py`, which owns:

- analogy block rendering and parsing;
- removal of analogy blocks from model history;
- rendering scratchpad context for a prompt;
- recording sanitized model history into a scratchpad.

This keeps report presentation separate from model protocol presentation without putting either concern into computational state classes.

## Dependency direction

- Run recipes may import the public `solver.report` API.
- Report builders may inspect solver results.
- Report storage may depend on records and renderers.
- Solver decisions never inspect reports or rendered strings.
- Analogy computation may use `analogy.protocol` at model I/O boundaries, while `analogy.state` does not depend on prompt templates or formatted text.

## Compatibility

Generated JSON and Markdown content, checkpoint updates, CLI text, and both run recipes retain their current behavior. Imports are updated to the new package; the retired module paths are intentionally removed.
