# Run-Rooted Solver Cleanup Design

## Intent

Treat `run_vanilla` and `run_analogy_assisted` as the only production roots for `src/analogy/solver`. Remove solver modules, classes, functions, protocol hooks, and compatibility modes that cannot be reached from either recipe.

## Boundary

A solver capability remains only when one of the recipes reaches it through a production call path. A test importing a symbol directly does not make that symbol production-reachable. Shared helpers used by both recipes remain shared. Analogy-only retry, scratchpad, repair, and rollout behavior remains under `solver/analogy`.

## Cleanup

- Restore the committed two-function `run.py` and preserve its architecture guard.
- Delete the detached replay/curriculum and proof-view subsystems.
- Replace the oversized inventory module with a focused proof-syntax parser containing only the goal/connective parsing used by prompts and semantic checking.
- Keep proof-state fingerprinting as a small live proof-state utility used by the search fingerprinter.
- Remove unused replay/inventory methods from the Lean adapter and the unconfigured stuck-state capture hook from the episode.
- Remove scratchpad-free generic rescue behavior and records that the analogy-assisted recipe never constructs.
- Remove or update tests that exist only for deleted APIs; retain characterization tests for every live run path.

## Constraints

- `run.py` continues to contain exactly the two recipe functions and no classes.
- Vanilla and analogy-assisted behavior, records, reports, and CLI output remain unchanged.
- The full suite passes after cleanup.
- Existing untracked `docs/paper/` remains untouched.
