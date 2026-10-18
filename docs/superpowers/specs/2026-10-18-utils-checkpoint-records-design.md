# Utilities Package for Checkpointing and Records

## Intent

Make the repository layout reflect that checkpointing and shared records are internal support code rather than top-level application subsystems. Preserve proof-search, CLI, persistence, restart, and artifact behavior.

## Package Structure

```text
src/analogy/utils/
├── __init__.py
├── checkpoint.py
└── records.py
```

`utils.records` owns the existing proof-search enums, dataclasses, and protocols. `utils.checkpoint` owns the checkpoint-specific `Checkpoint` dataclass, experiment-signature calculation, problem fingerprints, atomic persistence, run-artifact reconstruction, problem selection, and per-problem status updates. `Checkpoint` is imported directly from `analogy.utils.checkpoint`, not re-exported through `utils.records` or `utils.__init__`.

## Checkpoint Interface

`ProblemIndex` is replaced by a plain `Checkpoint` dataclass with the exact fields `path: Path`, `signature: str`, `problems: tuple[Problem, ...]`, `fingerprints: dict[str, str]`, and `statuses: dict[str, str]`. The problem tuple and fingerprint mapping describe the loaded experiment; `record_status` mutates only the statuses mapping before persisting it. Behavior is exposed through module functions:

- `load_checkpoint(config: AppConfig, problems: Sequence[Problem]) -> Checkpoint`
- `select_problems(checkpoint: Checkpoint, mode: str) -> tuple[Problem, ...]`
- `record_status(checkpoint: Checkpoint, problem_id: str, status: str) -> None`

The checkpoint remains mutable only where status recording requires it. Selection and persistence are utility operations rather than methods on a domain-style index object.

Loading preserves the current recovery behavior: malformed or schema-incompatible checkpoint JSON is treated as an empty checkpoint; stored statuses survive only when their stored problem fingerprint matches the current problem; when no valid statuses remain, compatible `run.json` artifacts are scanned in modification-time order so the latest compatible result wins; and the normalized checkpoint is atomically persisted before loading returns.

## Compatibility

- Keep the existing `.artifacts/problem-index.json` path and schema.
- Keep `problem_index_signature` and `problem_index_fingerprints` in `run.json` so existing artifacts remain compatible.
- Keep reconstruction support for both `summary` and the legacy `inference` result key.
- Do not retain Python import aliases for `analogy.records`, `analogy.problem_index`, or `ProblemIndex`.

## Runtime Integration

`Runtime.problem_index` becomes `Runtime.checkpoint`. Solver functions accept `checkpoint: Checkpoint | None`, call `record_status` after each problem, and read the checkpoint fields when writing the existing artifact keys.

## Success Criteria

- Shared records are imported only through `analogy.utils.records`.
- Checkpoint behavior is imported only through `analogy.utils.checkpoint`.
- The old top-level modules and `ProblemIndex` class are absent.
- Existing checkpoint and run artifacts remain readable without migration.
- The full test suite passes without proof-search or CLI behavior changes.
