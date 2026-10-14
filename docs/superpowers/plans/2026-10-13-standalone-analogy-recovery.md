# Standalone Analogy Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one standalone, inspectable script that compares no retrieval, random poetry/physics retrieval, and structural analogy retrieval for Lean theorem recovery.

**Architecture:** All production logic lives in `scripts/standalone_analogy_recovery.py` and imports no `analogy` modules. The script loads raw benchmark and cross-domain data, owns the frozen Qwen and Lean subprocess boundaries, shares fixed tactic pools across conditions, performs deterministic best-first search, and emits auditable JSONL/JSON records.

**Tech Stack:** Python 3.11+, standard library, PyTorch, Transformers, Qwen3-8B, Lean 4.29.1, pytest

**Spec:** `docs/superpowers/specs/2026-10-13-standalone-analogy-recovery-design.md`

## Global Constraints

- Production behavior is contained in `scripts/standalone_analogy_recovery.py`; tests may import that file.
- The script imports no module from `src/analogy` and consumes no earlier plan output or run artifact.
- Target reference tactics never appear in model prompts, candidate-generation inputs, or retrieval records.
- At a given normalized Lean state, all conditions receive the same immutable conventional candidate tuple.
- Retrieval may reorder candidates only; it may not invent, rewrite, remove, or add tactic text.
- Lean theorem completion is the only success classification.
- No retrieval, random retrieval, and analogy retrieval use matched seeds, budgets, model settings, and verifier settings.
- Model parameters remain frozen; generation and embedding use no gradients.
- The cross-domain index is bounded, balanced when possible, deterministic, and reproducible without its cache.
- Existing output directories and finalized records are never overwritten.

## Review Focus

- Reference leakage: a target tactic that contains a distinctive identifier must be absent from every captured prompt and retrieval record.
- State divergence: candidate pools must be shared by state fingerprint, not accidentally by search step number across different states.
- False progress: Lean-accepted tactics that reproduce an existing state must not enter the frontier or count as success.
- Retrieval fairness: random and analogy conditions must retrieve the same count from the same eligible index and only change candidate order.
- Interrupted or failed runs: completed JSONL rows must remain parseable while `summary.json` must never claim a complete run.

## File Structure

- Create `scripts/standalone_analogy_recovery.py` — every production record, parser, prompt, model adapter, retriever, Lean verifier, search loop, reporter, and CLI.
- Create `tests/test_standalone_analogy_recovery.py` — focused unit and real-Lean integration coverage for the script.
- Modify `README.md` — add one command and a short statement that this experiment is standalone from the numbered plans.

---

### Task 1: Standalone records, benchmark masking, and cross-domain transitions

**Files:**
- Create: `scripts/standalone_analogy_recovery.py`
- Create: `tests/test_standalone_analogy_recovery.py`

**Interfaces:**
- Produces `Problem`, `ReferenceProof`, `Transition`, `Condition`, and `ExperimentError`.
- Produces `load_benchmark(problem_path: Path, proof_path: Path) -> tuple[tuple[Problem, ...], dict[str, ReferenceProof]]`.
- Produces `iter_poetry_transitions(path: Path) -> Iterator[Transition]`.
- Produces `iter_physics_transitions(path: Path) -> Iterator[Transition]`.
- Produces `sample_transitions(poetry, physics, per_domain: int, seed: int) -> tuple[Transition, ...]`.
- Produces `masked_problem_payload(problem: Problem) -> dict[str, str]` with no reference-proof argument.

- [ ] **Step 1: Write failing tests for masking and transition construction**

```python
def test_masked_problem_payload_has_no_reference_channel(tmp_path: Path) -> None:
    module = load_script()
    problem = module.Problem("secret", "(P : Prop)", "P → P")
    payload = module.masked_problem_payload(problem)
    rendered = json.dumps(payload, sort_keys=True)
    assert rendered == '{"binders": "(P : Prop)", "problem_id": "secret", "target": "P \\u2192 P"}'
    assert "intro hidden_name" not in rendered


def test_poetry_pairs_only_adjacent_lines_from_same_work(tmp_path: Path) -> None:
    module = load_script()
    path = tmp_path / "poetry.ndjson.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in ({"gid": "a", "s": "first"}, {"gid": "a", "s": "second"}, {"gid": "b", "s": "third"}):
            handle.write(json.dumps(row) + "\n")
    transitions = tuple(module.iter_poetry_transitions(path))
    assert [(item.before, item.after) for item in transitions] == [("first", "second")]


def test_balanced_sampling_is_deterministic_and_deduplicated() -> None:
    module = load_script()
    poetry = tuple(module.Transition(f"p{i}", "poetry", "a", f"b{i}", {}) for i in range(4))
    physics = tuple(module.Transition(f"f{i}", "physics", "a", f"b{i}", {}) for i in range(4))
    first = module.sample_transitions(poetry, physics, per_domain=2, seed=7)
    second = module.sample_transitions(poetry, physics, per_domain=2, seed=7)
    assert first == second
    assert [item.domain for item in first].count("poetry") == 2
    assert [item.domain for item in first].count("physics") == 2
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_standalone_analogy_recovery.py -q`

Expected: FAIL because `scripts/standalone_analogy_recovery.py` does not exist.

- [ ] **Step 3: Implement strict records and loaders in the one script**

Implement frozen dataclasses with field validation. Parse the exact bundled JSON schemas but copy no implementation from `src/analogy`. Poetry pairing must reset at `gid` boundaries. Physics pairing must produce adjacent sentence transitions from each story and retain `example_id`, `story_id`, `plausible`, `breakpoint`, and conflict fields in provenance. Use stable SHA-256 IDs over domain, source identity, before, and after text. Reservoir-sample each domain with `random.Random(seed)` so the 52 MB archive is not loaded into memory.

The benchmark loader must validate one-to-one problem/reference coverage, but `masked_problem_payload` must accept only a `Problem`; its API must make accidental proof inclusion impossible.

- [ ] **Step 4: Run focused tests and the preservation suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_standalone_analogy_recovery.py -q`

Expected: PASS.

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py
git commit -m "feat: load standalone analogy experiment data"
```

---

### Task 2: Frozen Qwen prompts, strict output parsers, and embeddings

**Files:**
- Modify: `scripts/standalone_analogy_recovery.py`
- Modify: `tests/test_standalone_analogy_recovery.py`

**Interfaces:**
- Produces `TextModel` protocol with `generate(messages: tuple[Message, ...], seed: int) -> str` and `embed(text: str, layer: int) -> Embedding`.
- Produces `QwenModel.load(name, device, max_new_tokens, timeout_seconds) -> QwenModel`.
- Produces `candidate_prompt(problem, state, accepted, failed, top_k) -> tuple[Message, ...]`.
- Produces `proof_need_prompt(problem, state, accepted, failed) -> tuple[Message, ...]`.
- Produces `transformation_prompt(transition: Transition) -> tuple[Message, ...]`.
- Produces `ranking_prompt(problem, state, candidates, transformations) -> tuple[Message, ...]`.
- Produces `parse_candidates(text: str, top_k: int) -> tuple[str, ...]`.
- Produces `parse_transformation(text: str) -> str`.
- Produces `parse_ranking(text: str, candidate_count: int) -> tuple[int, ...]`.

- [ ] **Step 1: Write failing prompt-isolation and parser tests**

```python
def test_prompts_cannot_receive_reference_proof() -> None:
    module = load_script()
    problem = module.Problem("identity", "(P : Prop)", "P → P")
    prompt = module.candidate_prompt(problem, "⊢ P → P", (), (), 4)
    rendered = "\n".join(message.content for message in prompt)
    assert "P → P" in rendered
    assert "reference" not in rendered.casefold()
    assert "exact hidden" not in rendered


@pytest.mark.parametrize("text", ["{}", "[]", '["intro h", "intro h"]', '["bad\\nline"]'])
def test_candidate_parser_rejects_malformed_or_duplicate_tactics(text: str) -> None:
    module = load_script()
    with pytest.raises(module.ModelOutputError):
        module.parse_candidates(text, top_k=4)


def test_ranking_parser_appends_omitted_indices_in_original_order() -> None:
    module = load_script()
    assert module.parse_ranking("[2, 0]", candidate_count=4) == (2, 0, 1, 3)
```

- [ ] **Step 2: Write failing synthetic embedding tests**

Use a fake tokenizer whose attention mask is `[[1, 1, 0]]` and a fake model exposing three hidden-state layers. Assert that `embed` selects token index 1, resolves negative layers, converts to float32, returns unit L2 norm, records model revision/layer/token/dimension/text hash, and rejects zero, NaN, and out-of-range values.

- [ ] **Step 3: Run focused tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_standalone_analogy_recovery.py -q`

Expected: FAIL on missing prompt, parser, and Qwen interfaces.

- [ ] **Step 4: Implement the model boundary and versioned prompts**

Freeze every model parameter and call `eval()`. Generation uses the tokenizer chat template with thinking disabled and decodes only newly generated tokens. Embedding uses `output_hidden_states=True`, `use_cache=False`, `torch.inference_mode()`, the final attention-mask token, float32 conversion, and L2 normalization.

Candidate output is a nonempty JSON string array of single-line tactics. Transformation output has the exact shape `{"transformation": "one element becomes available"}` with one nonblank string and no Lean tactic vocabulary. Ranking output is a JSON integer array; duplicate, Boolean, negative, and out-of-range indices are rejected, and omitted indices are appended in original order.

Define prompt version constants `candidate-v1`, `proof-need-v1`, `transformation-v1`, and `ranking-v1` and expose them in run configuration.

- [ ] **Step 5: Run focused and full tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_standalone_analogy_recovery.py -q`

Expected: PASS.

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py
git commit -m "feat: add standalone Qwen representation boundary"
```

---

### Task 3: Cached structural index and fair retrieval conditions

**Files:**
- Modify: `scripts/standalone_analogy_recovery.py`
- Modify: `tests/test_standalone_analogy_recovery.py`

**Interfaces:**
- Produces `IndexedTransition(transition, transformation, embedding, cache_key)`.
- Produces `build_index(transitions, model, cache_dir, layer) -> tuple[IndexedTransition, ...]`.
- Produces `cosine_retrieve(query, index, count) -> tuple[RetrievalHit, ...]`.
- Produces `random_retrieve(index, count, seed) -> tuple[RetrievalHit, ...]`.
- Produces `rank_for_condition(condition, problem, state, candidates, proof_need, index, model, retrieval_count, seed) -> RankingDecision`.

- [ ] **Step 1: Write failing retrieval and cache tests**

```python
def test_cosine_and_random_retrieval_use_same_eligible_index() -> None:
    module = load_script()
    index = indexed_fixture(module)
    nearest = module.cosine_retrieve((1.0, 0.0), index, count=2)
    random_hits = module.random_retrieve(index, count=2, seed=11)
    assert [hit.item.transition.id for hit in nearest] == ["x", "y"]
    assert len(random_hits) == len(nearest) == 2
    assert {hit.item.transition.id for hit in random_hits} <= {item.transition.id for item in index}


def test_cache_key_changes_with_prompt_model_layer_or_source(module) -> None:
    base = module.transformation_cache_key(transition_fixture(module), "rev-a", -1, "transformation-v1")
    assert base != module.transformation_cache_key(transition_fixture(module), "rev-b", -1, "transformation-v1")
    assert base != module.transformation_cache_key(transition_fixture(module), "rev-a", -2, "transformation-v1")
    assert base != module.transformation_cache_key(transition_fixture(module), "rev-a", -1, "transformation-v2")


def test_no_retrieval_never_calls_embedding_or_retriever() -> None:
    module = load_script()
    model = SpyModel(candidate_response='[0]')
    decision = module.rank_for_condition(module.Condition.NONE, problem_fixture(module), "goal", ("exact h",), None, (), model, 2, 0)
    assert decision.indices == (0,)
    assert model.embedding_calls == []
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_standalone_analogy_recovery.py -q`

Expected: FAIL on missing index, cache, and retrieval functions.

- [ ] **Step 3: Implement deterministic indexing and retrieval**

Write one JSON cache entry per transition through a temporary sibling followed by `Path.replace`. Validate every cached field and vector before reuse. Stable sorting for cosine retrieval is `(-similarity, transition.id)`. Random retrieval uses `random.Random(derived_seed).sample` and reports similarity as `None`. Raise `ExperimentError` if `count` exceeds the eligible index or if retrieval is requested from an empty index.

For analogy and random conditions, render the same ranking prompt shape and vary only the selected transformations. For no retrieval, provide the same proof and candidate content with an explicit empty transformation section. Capture the exact candidate tuple, returned indices, selected hit IDs, domains, and scores in `RankingDecision`.

- [ ] **Step 4: Add a property-style shared-pool test**

Create a `CandidatePoolStore` keyed by `(problem.id, normalized_state_hash, seed, candidate_prompt_version, top_k)`. Call it from all three conditions and assert the model candidate-generation method executes once while each returned tuple has object-equal text and order. Assert different state hashes create different pools.

- [ ] **Step 5: Run focused and full tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_standalone_analogy_recovery.py -q`

Expected: PASS.

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py
git commit -m "feat: retrieve structural cross-domain analogies"
```

---

### Task 4: Self-contained Lean verification and deterministic proof search

**Files:**
- Modify: `scripts/standalone_analogy_recovery.py`
- Modify: `tests/test_standalone_analogy_recovery.py`

**Interfaces:**
- Produces `LeanStatus`, `LeanResult`, and `LeanVerifier`.
- Produces `LeanVerifier.initial_state(problem) -> LeanResult`.
- Produces `LeanVerifier.try_tactic(problem, prefix, candidate) -> LeanResult`.
- Produces `search_problem(problem, condition, seed, pool_store, ranker, verifier, limits, attempt_sink) -> TrialResult`.

- [ ] **Step 1: Write failing fake-verifier search tests**

```python
def test_search_counts_only_completion_as_solved() -> None:
    module = load_script()
    verifier = ScriptedVerifier({
        ((), "idle"): open_result("goal"),
        ((), "intro h"): open_result("h : P\n⊢ P"),
        (("intro h",), "exact h"): complete_result(),
    })
    result = module.search_problem(
        problem_fixture(module), module.Condition.NONE, 0,
        FixedPoolStore(("idle", "intro h", "exact h")),
        IdentityRanker(), verifier, module.SearchLimits(8, 16, 5.0), list().append,
    )
    assert result.solved is True
    assert result.tactics == ("intro h", "exact h")
    assert result.repeated_state_count == 1


def test_open_novel_state_at_budget_is_not_solved() -> None:
    module = load_script()
    verifier = ScriptedVerifier({
        ((), "intro h"): open_result("h : P\n⊢ P"),
    })
    result = module.search_problem(
        problem_fixture(module),
        module.Condition.NONE,
        0,
        FixedPoolStore(("intro h",)),
        IdentityRanker(),
        verifier,
        module.SearchLimits(
            max_expansions=1,
            max_verifier_calls=1,
            theorem_timeout_seconds=5.0,
        ),
        list().append,
    )
    assert result.solved is False
    assert result.status == "budget_exhausted"
```

- [ ] **Step 2: Write a failing real-Lean integration test**

Use `Problem("identity", "(P : Prop)", "P → P")`, the pinned project, and candidates `("exact missing", "intro h", "exact h")`. Assert the standalone verifier reports rejected, open, and complete in sequence and normalizes temporary paths from diagnostics.

- [ ] **Step 3: Run the tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_standalone_analogy_recovery.py -q`

Expected: FAIL on missing verifier and search functions.

- [ ] **Step 4: Implement the independent Lean protocol**

Render probes with the theorem header, accepted one-line tactics, `trace_state`, and `all_goals sorry`. Invoke `[lake, "env", "lean", "--json", source_path]` with the pinned `ELAN_TOOLCHAIN`, bounded output, and timeout. Treat an empty controlled trace as a possible completion only after a second clean proof run succeeds without errors or `hasSorry`.

Validate every tactic before starting Lean: nonblank, one line, no NUL, at most 4096 characters. Normalize CRLF, trailing whitespace, and temporary file paths. Never infer completion from goal count or trace absence alone.

- [ ] **Step 5: Implement deterministic best-first search**

Frontier priority is `(-accepted_depth, insertion_order)` so deeper verified prefixes are explored first with stable ties. State identity is SHA-256 over problem ID and normalized goal text. Record rejected, repeated, novel-open, timeout, internal-error, and complete attempts. Apply both expansion and verifier-call budgets plus a theorem monotonic deadline. A repeated state is recorded but never re-enqueued.

- [ ] **Step 6: Run focused and full tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_standalone_analogy_recovery.py -q`

Expected: PASS, including the real Lean test.

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py
git commit -m "feat: verify standalone recovery search with Lean"
```

---

### Task 5: Three-condition orchestration, audit records, and paired metrics

**Files:**
- Modify: `scripts/standalone_analogy_recovery.py`
- Modify: `tests/test_standalone_analogy_recovery.py`

**Interfaces:**
- Produces `JsonlSink` with flush-after-row behavior.
- Produces `summarize_trials(trials: Sequence[TrialResult]) -> dict[str, object]`.
- Produces `run_experiment(config: ExperimentConfig, model_factory, verifier_factory) -> Path`.
- Produces output files `config.json`, `transitions.jsonl`, `attempts.jsonl`, `trials.jsonl`, and `summary.json`.

- [ ] **Step 1: Write failing subgroup and paired-summary tests**

```python
def test_summary_uses_no_retrieval_to_define_solved_subgroups() -> None:
    module = load_script()
    trials = (
        trial(module, "a", "none", True),
        trial(module, "a", "random", False),
        trial(module, "a", "analogy", True),
        trial(module, "b", "none", False),
        trial(module, "b", "random", False),
        trial(module, "b", "analogy", True),
    )
    summary = module.summarize_trials(trials)
    assert summary["subgroups"]["baseline_solved"] == ["a"]
    assert summary["subgroups"]["baseline_unsolved"] == ["b"]
    assert summary["comparisons"]["analogy_vs_none"]["newly_solved"] == ["b"]
    assert summary["comparisons"]["random_vs_none"]["regressed"] == ["a"]
```

- [ ] **Step 2: Write failing output durability and leakage-audit tests**

Run a two-problem fake-model/fake-verifier experiment into `tmp_path`. Assert every line in each JSONL file parses after each sink close, `summary.json` has `run_complete: true`, and rerunning into the same explicit directory raises `ExperimentError`.

Give one reference tactic the sentinel `exact DO_NOT_LEAK`. Capture every fake-model prompt and every output row except finalized evaluation-only reference diagnostics. Assert the sentinel appears in none of them. Then assert the post-finalization diagnostic reports exact overlap only when the final discovered tactic actually equals the sentinel.

- [ ] **Step 3: Run tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_standalone_analogy_recovery.py -q`

Expected: FAIL on missing orchestration, sinks, and summary functions.

- [ ] **Step 4: Implement run ordering and immutable audit output**

For each seed, run all `none` trials before `random` and `analogy`. Reuse one `CandidatePoolStore` across conditions. Derive operation seeds with SHA-256 over run seed, problem ID, state fingerprint, condition, and operation string. Write `config.json` before trials with `run_complete: false`; write finalized `summary.json` and atomically replace `config.json` with `run_complete: true` only after all requested trials finish.

Trial rows include condition, seed, status, solved, tactics, expansion/verifier counts, candidate-coverage diagnostic, exact reference overlap, elapsed time, and failure classification. Attempt rows include state fingerprint, candidate tuple, conventional order, selected order, retrieval hits, Lean disposition, and successor fingerprint, but not reference tactics.

- [ ] **Step 5: Implement paired summary validation**

Require exactly one trial for every requested `(problem_id, condition, seed)` key before marking a run complete. Group baseline solved/unsolved separately per seed. Report counts, rates, newly solved, regressed, verifier-call delta, and expansion delta for random-vs-none and analogy-vs-none. Sort every identifier list.

- [ ] **Step 6: Run focused and full tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_standalone_analogy_recovery.py -q`

Expected: PASS.

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py
git commit -m "feat: compare standalone retrieval conditions"
```

---

### Task 6: CLI, dry run, documentation, and end-to-end smoke verification

**Files:**
- Modify: `scripts/standalone_analogy_recovery.py`
- Modify: `tests/test_standalone_analogy_recovery.py`
- Modify: `README.md`

**Interfaces:**
- Produces `build_parser() -> argparse.ArgumentParser`.
- Produces `main(argv: Sequence[str] | None = None) -> int`.
- Supports `--dry-run`, `--problem`, `--condition`, `--seed`, all path flags, transition limits, retrieval count, candidate count, search budgets, Lean settings, and model settings.

- [ ] **Step 1: Write failing CLI validation and dry-run tests**

```python
def test_dry_run_validates_real_inputs_without_loading_model(monkeypatch, capsys) -> None:
    module = load_script()
    monkeypatch.setattr(module.QwenModel, "load", classmethod(lambda cls, *args, **kwargs: pytest.fail("model must not load")))
    status = module.main(["--dry-run", "--poetry-limit", "2", "--physics-limit", "2", "--problem", "imp_intro_01"])
    output = json.loads(capsys.readouterr().out)
    assert status == 0
    assert output["problem_count"] == 1
    assert output["transition_counts"] == {"physics": 2, "poetry": 2}
    assert output["lean_available"] is True
```

Add table-driven parser failures for nonpositive limits, retrieval count greater than per-domain total, duplicate conditions, unknown problem IDs, missing files, unavailable Lean, and an explicit nonempty output directory.

- [ ] **Step 2: Run CLI tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_standalone_analogy_recovery.py -q`

Expected: FAIL on missing CLI options and dry-run behavior.

- [ ] **Step 3: Implement the complete CLI**

Resolve defaults from the repository root discovered from `Path(__file__).resolve().parents[1]`, not the current directory. Repeatable flags select problems, conditions, and seeds. Validate all arguments before loading Qwen or creating a run directory. Print one compact JSON object on dry run and one compact JSON object containing the completed run path and headline metrics on success. Return exit code 2 for configuration errors, 1 for a started but incomplete run, and 130 for interruption.

- [ ] **Step 4: Document the standalone command**

Add this README section:

````markdown
## Standalone retrieval experiment

The retrieval comparison is independent of the numbered implementation plans and does not import the `analogy` package. Validate its raw benchmark, poetry, physics, and Lean inputs without loading Qwen:

```bash
.venv/bin/python scripts/standalone_analogy_recovery.py --dry-run
```

Run a one-problem, three-condition smoke experiment:

```bash
.venv/bin/python scripts/standalone_analogy_recovery.py --problem imp_intro_01 --poetry-limit 8 --physics-limit 8 --retrieval-count 2
```
````

- [ ] **Step 5: Run the real-input dry run**

Run: `.venv/bin/python scripts/standalone_analogy_recovery.py --dry-run --poetry-limit 8 --physics-limit 8 --problem imp_intro_01`

Expected: exit 0; JSON reports one problem, eight poetry transitions, eight physics transitions, three conditions, and available Lean.

- [ ] **Step 6: Run the real-Lean three-condition smoke experiment**

Use a test-only deterministic model adapter through the script's injected `run_experiment` boundary so this verification exercises actual Lean and output orchestration without requiring a lengthy 8B generation run. Run the dedicated pytest integration test and assert all three trial rows exist, share the initial candidate pool, contain no reference prompt leakage, and classify completion only from Lean.

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_standalone_analogy_recovery.py -q`

Expected: PASS.

- [ ] **Step 7: Run complete verification**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: all tests pass with zero failures.

Run: `.venv/bin/python -m py_compile scripts/standalone_analogy_recovery.py`

Expected: exit 0 with no output.

Run: `rg -n "from analogy|import analogy|DO_NOT_LEAK|TODO|TBD|NotImplementedError" scripts/standalone_analogy_recovery.py README.md`

Expected: no matches in the script; README may contain the phrase ``analogy package`` only as prose.

- [ ] **Step 8: Commit**

```bash
git add scripts/standalone_analogy_recovery.py tests/test_standalone_analogy_recovery.py README.md
git commit -m "docs: expose standalone analogy recovery experiment"
```

---

## Final verification checklist

- [ ] Re-read the design spec and map every acceptance criterion to a passing test or command above.
- [ ] Confirm `git status --short` is empty.
- [ ] Confirm `git log --oneline --max-count=8` contains the design, baseline, and six implementation commits.
- [ ] Record the exact pytest count, dry-run output, and smoke-test outcome in the completion report.
- [ ] Do not claim the full Qwen benchmark result unless the expensive model-backed command was actually run to completion; distinguish implementation verification from experimental findings.
