# Solver Search Engine Package

## Intent

Make the architectural boundary visible in the filesystem: `run.py` is the high-level recipe layer, while `solver/search/` is the proof-search engine used by those recipes.

## Package structure

```text
solver/
  run.py
  search/
    __init__.py
    result.py
    policies.py
    session.py
    setup.py
```

- `search/__init__.py` is the engine facade. `run.py` imports `initialize_search` and `update_search` only from this facade.
- `search/result.py` owns `SearchResult`, the immutable terminal evidence produced by a search.
- `search/policies.py` owns the small search protocols and default policies formerly in `search.py`.
- `search/session.py` owns `EpisodeContext`, `SearchSession`, tactic batches, the frontier, and all state transitions formerly in `episode.py`.
- `search/setup.py` selects concrete proof services and constructs a configured `SearchSession` formerly handled by `search_setup.py`.

The old top-level `solver/search.py`, `solver/episode.py`, and `solver/search_setup.py` modules are removed.

## Responsibility boundary

`run.py` decides the visible order of operations across problems: initialize proof and search, generate tactics, submit them to Lean, update search, perform analogy rescue, and write reports.

The search package owns the lifecycle of one problem search: frontier selection, visited-state tracking, tactic-result classification, semantic pruning and recovery, stuckness, rescue integration, budgets, cancellation, and construction of the terminal `SearchResult`.

The proof package owns Lean-facing and proof-domain services: initial and goal states, tactic submission, proof-state parsing and identity, semantic checking, and diversity measurements.

## Dependency direction

- `run.py` depends on the public `search` facade and on proof submission operations.
- `search` may depend on proof services and their protocols.
- `proof` must not import `search`.
- External solver modules and tests use `analogy.solver.search`, not `search.session`, unless they are specifically testing session internals.
- No compatibility shims remain at the retired top-level module paths.

## Compatibility

Both visible run recipes remain the only functions in `run.py`. Search behavior, result schemas, analogy rescue behavior, reporting, CLI behavior, and public imports from `analogy.solver.search` remain unchanged.
