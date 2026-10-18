# Remove the Inference Namespace Design

## Intent

Remove `inference` as an architectural name because the repository now has one proof-search runtime rather than a separate inference subsystem. Preserve all model generation, prompt, parsing, solver, reporting, and restart behavior.

## Design

- Move `src/analogy/inference/` to `src/analogy/model/`. The package continues to own model providers, the MLX backend, prompt catalog and runner, and generated-output parsing.
- Move `src/analogy/solver/inference.py` to `src/analogy/solver/run.py`. This module continues to compose proof search, write artifacts, and render reports.
- Rename result vocabulary in `records.py` from `InferenceStatus`, `InferenceProblemResult`, and `InferenceRun` to `SolveStatus`, `ProblemResult`, and `SolveSummary`.
- Rename `SolveRun.inference` to `SolveRun.summary`.
- Write new `run.json` files with a top-level `summary` object. When rebuilding the persistent problem index, accept both `summary` and the legacy `inference` key so existing artifacts remain usable.

## Constraints

- This is a terminology and ownership refactor only; proof-search behavior and command-line behavior must not change.
- Do not retain compatibility import aliases for `analogy.inference` or `analogy.solver.inference`; removing those namespaces is the goal.
- Historical specs and plans remain historical records and are not rewritten.
- Semantically correct third-party or generic uses such as `torch.inference_mode()` do not need renaming.

## Success Criteria

- No production or test import targets `analogy.inference` or `analogy.solver.inference`.
- `src/analogy/inference/` and `src/analogy/solver/inference.py` no longer exist.
- New run artifacts use `summary`, and problem-index reconstruction works for both new and old run artifacts.
- The full test suite passes.
