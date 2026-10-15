# MLX Qwen3-8B Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the existing analogy-RL pipeline with `mlx-community/Qwen3-8B-4bit` on Apple silicon and produce a measured runtime estimate.

**Architecture:** Introduce a strict model-backend protocol and keep the existing Transformers implementation unchanged behind it. Add an MLX implementation that wraps one Qwen3 decoder layer for exactly-once prefill activation injection, while providing equivalent generation, embedding, and batched completion-scoring methods. Add a bounded 8B configuration and a runtime probe that reports measured operation latency and projected experiment duration.

**Tech Stack:** Python 3.11+, PyTorch policy head, MLX, MLX-LM, Hugging Face tokenizer, Lean 4.29.1, pytest.

**Spec:** `docs/superpowers/specs/2026-10-14-mlx-qwen8b-backend-design.md`

## Global Constraints

- The model is exactly `mlx-community/Qwen3-8B-4bit`; never fall back to a smaller model.
- Preserve frozen-model generation, latent delta injection, proof embeddings, mean-token likelihood scoring, Lean verification, budgets, and immutable logs.
- Keep the existing Transformers backend and default `config.yaml` behavior.
- MLX is optional; a missing dependency must produce an actionable configuration error.
- Add the policy delta once, to the final token of the prefill at the configured decoder layer, and restore the original layer after every call.
- One batched completion-scoring forward equals one charged model call.
- Every measured MLX operation must be explicitly evaluated before elapsed time is recorded.

## Review Focus

- Negative layer indices must resolve identically in MLX and Transformers; Task 2 tests `-1`, zero, and out-of-range values.
- An exception during generation must still restore the original decoder layer; Task 2 tests the exception path.
- A reused MLX model must not retain a prior policy delta; Task 2 performs a steered call followed by an unsteered call.
- Unequal completion lengths must exclude padding and prompt tokens from scores; Task 3 tests both conditions.
- Offline or missing model weights must name the exact identifier without silently changing backends; Task 1 tests factory error propagation and Task 5 runs offline after download.

---

## File Map

- Create `src/analogy/model_backend.py`: shared protocol and backend factory.
- Create `src/analogy/mlx_model.py`: optional MLX implementation only.
- Create `src/analogy/runtime_probe.py`: timing records and projection math.
- Create `scripts/benchmark_mlx_runtime.py`: small executable probe.
- Create `config.mlx-8b-fast.yaml`: bounded full-corpus 8B configuration.
- Modify `src/analogy/config.py`: strict `model.backend` parsing.
- Modify `src/analogy/__main__.py`: construct the selected backend.
- Modify `src/analogy/model.py`: declare the Transformers backend name; no semantic rewrite.
- Modify `pyproject.toml`: `mlx` optional dependency group.
- Modify `README.md`: install, smoke, train, evaluate, and expected artifact commands.
- Create `tests/test_model_backend.py`, `tests/test_mlx_model.py`, and `tests/test_runtime_probe.py`.
- Modify `tests/test_config.py` and `tests/test_cli.py` for configuration/factory coverage.

### Task 1: Strict Backend Configuration and Factory

**Files:**
- Create: `src/analogy/model_backend.py`
- Modify: `src/analogy/config.py`
- Modify: `src/analogy/__main__.py`
- Modify: `pyproject.toml`
- Test: `tests/test_config.py`
- Test: `tests/test_model_backend.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: existing `ModelConfig` fields and `QwenGenerator.from_config(config)`.
- Produces: `ModelBackend` protocol and `load_model_backend(config: ModelConfig) -> ModelBackend`.

- [ ] **Step 1: Write failing strict-configuration tests**

Add `backend: transformers` to `_valid_yaml()` and assert it is parsed. Add:

```python
def test_model_backend_rejects_unknown_value(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_valid_yaml().replace("backend: transformers", "backend: metal"))
    with pytest.raises(ConfigError, match=r"config\.model\.backend"):
        load_config(path)
```

- [ ] **Step 2: Run the configuration test and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_config.py -q`

Expected: failure because `backend` is currently an unknown model key.

- [ ] **Step 3: Implement strict backend parsing**

Add `backend: str` to `ModelConfig`, accept the exact values `transformers` and
`mlx`, and add `backend: transformers` to every checked-in test/config fixture.

- [ ] **Step 4: Write failing factory-selection tests**

```python
def test_factory_selects_transformers(monkeypatch, config):
    monkeypatch.setattr(QwenGenerator, "from_config", lambda value: ("hf", value))
    assert load_model_backend(config)[0] == "hf"

def test_factory_reports_missing_mlx_extra(monkeypatch, mlx_config):
    monkeypatch.setattr("analogy.model_backend.import_module", side_effect=ImportError)
    with pytest.raises(ModelConfigurationError, match="analogy\\[mlx\\]"):
        load_model_backend(mlx_config)
```

- [ ] **Step 5: Run the factory tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_model_backend.py -q`

Expected: import failure because `model_backend.py` does not exist.

- [ ] **Step 6: Implement the protocol and lazy factory**

```python
class ModelBackend(Protocol):
    hidden_size: int
    model_revision: str
    timeout_seconds: float
    def generate(self, messages: tuple[Message, ...], *, seed: int) -> str: ...
    def generate_with_delta(self, messages, *, delta, layer: int, seed: int) -> str: ...
    def embed_text(self, text: str, *, layer: int) -> Embedding: ...
    def score_completions(self, messages, completions) -> tuple[CompletionScore, ...]: ...

def load_model_backend(config: ModelConfig) -> ModelBackend:
    if config.backend == "transformers":
        return QwenGenerator.from_config(config)
    try:
        module = import_module("analogy.mlx_model")
    except ImportError as error:
        raise ModelConfigurationError(
            "the MLX extra is not installed; install analogy[mlx]"
        ) from error
    return module.MLXQwenGenerator.from_config(config)
```

Update the CLI to call `load_model_backend`. Add
`mlx = ["mlx>=0.29,<0.33", "mlx-lm>=0.30,<0.32"]` to optional dependencies.

- [ ] **Step 7: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_config.py tests/test_model_backend.py tests/test_cli.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/analogy/config.py src/analogy/model_backend.py src/analogy/__main__.py tests/test_config.py tests/test_model_backend.py tests/test_cli.py config.yaml
git commit -m "feat: select model inference backend"
```

### Task 2: MLX Generation and Exactly-Once Latent Injection

**Files:**
- Create: `src/analogy/mlx_model.py`
- Create: `tests/test_mlx_model.py`

**Interfaces:**
- Consumes: `ModelConfig`, `Message`, `ModelConfigurationError`, and MLX-LM `load`, `generate`, `make_sampler`.
- Produces: `MLXQwenGenerator.from_config`, `generate`, and `generate_with_delta`.

- [ ] **Step 1: Write fake-runtime generation tests**

Use injected `mx`, loader, generator, and sampler objects. Assert chat-template
arguments, seed handling, `max_tokens=96`, deterministic sampler selection, and
decoded text. The fake model exposes `model.layers` and `args.hidden_size`.

- [ ] **Step 2: Write injection lifecycle tests**

```python
def test_delta_is_added_once_to_final_prefill_token_and_layer_is_restored(runtime):
    generator = runtime.generator()
    original = generator.model.model.layers[-1]
    generator.generate_with_delta(messages(), delta=runtime.delta([1, 2]), layer=-1, seed=7)
    assert runtime.layer_calls == [("prefill", [[0, 0], [1, 2]]), ("decode", None)]
    assert generator.model.model.layers[-1] is original

def test_layer_is_restored_when_generation_raises(runtime):
    runtime.generate_error = RuntimeError("boom")
    original = runtime.generator().model.model.layers[-1]
    with pytest.raises(RuntimeError, match="boom"):
        runtime.generator().generate_with_delta(messages(), delta=runtime.delta([1, 2]), layer=-1, seed=7)
    assert runtime.generator().model.model.layers[-1] is original
```

Also test out-of-range layers, rank/dimension mismatch, non-finite deltas, and a
steered call followed by an unsteered call.

- [ ] **Step 3: Run the tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mlx_model.py -q`

Expected: import failure because `mlx_model.py` does not exist.

- [ ] **Step 4: Implement construction and generation**

`from_config` imports MLX only inside the method and calls:

```python
model, tokenizer = mlx_lm.load(config.name)
sampler = make_sampler(temp=0.0) if not config.do_sample else make_sampler(
    temp=config.temperature, top_p=config.top_p
)
```

Render with `enable_thinking=False`. Before each call, seed `mx.random` and time
the fully evaluated result. Raise `TimeoutError` if elapsed time meets or exceeds
`timeout_seconds`.

- [ ] **Step 5: Implement `_DeltaLayer` and restoration**

```python
class _DeltaLayer:
    def __init__(self, inner, delta, mx):
        self.inner, self.delta, self.mx, self.applied = inner, delta, mx, False
    def __call__(self, hidden, mask=None, cache=None):
        output = self.inner(hidden, mask, cache)
        if not self.applied and output.shape[1] > 1:
            output = self.mx.concatenate(
                [output[:, :-1, :], output[:, -1:, :] + self.delta], axis=1
            )
            self.applied = True
        return output
```

Install the wrapper only inside `try/finally`; force `mx.eval(output)` before
restoration. If the first call has sequence length one, inject once there so a
single-token prompt remains valid.

- [ ] **Step 6: Run MLX unit tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mlx_model.py -q`

Expected: PASS without importing real MLX.

- [ ] **Step 7: Commit**

```bash
git add src/analogy/mlx_model.py tests/test_mlx_model.py
git commit -m "feat: generate with MLX latent steering"
```

### Task 3: MLX Embeddings and Batched Completion Scoring

**Files:**
- Modify: `src/analogy/mlx_model.py`
- Modify: `tests/test_mlx_model.py`

**Interfaces:**
- Consumes: loaded Qwen3 `model.model.embed_tokens`, `model.model.layers`, `model.model.norm`, tokenizer IDs, and `CompletionScore`.
- Produces: `embed_text(text, layer)` and `score_completions(messages, completions)` compatible with current callers.

- [ ] **Step 1: Write embedding tests**

Build a two-layer fake decoder whose final token at layer one is `[3, 4]`.
Assert normalized values `(0.6, 0.8)`, resolved layer `1`, token index, SHA-256
text hash, and model revision. Assert blank text and zero/non-finite vectors fail.

- [ ] **Step 2: Run the embedding tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mlx_model.py -k embed -q`

Expected: `AttributeError` for missing `embed_text`.

- [ ] **Step 3: Implement explicit decoder-prefix embeddings**

Tokenize one unpadded sequence, call `embed_tokens`, construct the causal mask
through the loaded model module, execute layers through the resolved index, read
`hidden[0, -1]`, cast to float32, normalize, call `mx.eval`, and construct the
existing immutable `Embedding` record.

- [ ] **Step 4: Write unequal-length batched scoring tests**

Use two completions of lengths one and three. Give padding logits deliberately
high values and assert they do not change either score. Give prompt-token logits
different values and assert they also do not change scores. Assert the shorter
candidate wins according to completion-token mean likelihood.

- [ ] **Step 5: Run scoring tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mlx_model.py -k score -q`

Expected: `AttributeError` for missing `score_completions`.

- [ ] **Step 6: Implement one-forward scoring**

Render the prompt once, tokenize each completion separately, right-pad combined
sequences, invoke the model exactly once, apply `mx.log_softmax`, and gather
positions `[prompt_length - 1 : prompt_length + completion_length - 1]`.
Evaluate the gathered arrays before converting to Python floats.

- [ ] **Step 7: Run focused and caller tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_mlx_model.py tests/test_model.py tests/test_proof_views.py tests/test_training.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/analogy/mlx_model.py tests/test_mlx_model.py
git commit -m "feat: embed and score proofs with MLX"
```

### Task 4: Bounded 8B Profile and Runtime Projection

**Files:**
- Create: `config.mlx-8b-fast.yaml`
- Create: `src/analogy/runtime_probe.py`
- Create: `scripts/benchmark_mlx_runtime.py`
- Create: `tests/test_runtime_probe.py`
- Modify: `tests/test_config.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `load_config`, `load_model_backend`, `LeanSubprocessVerifier`, and one corpus `Problem`.
- Produces: `RuntimeMeasurement`, `RuntimeProjection`, `project_runtime`, and a JSON command-line report.

- [ ] **Step 1: Write the fast-profile test**

Assert the checked-in profile selects backend `mlx`, exact 8B identifier, 96
tokens, 20-second timeout, width 3, 8 expansions, 3 iterations, 8 model calls,
6 Lean calls, 1 rollout, and 1 epoch.

- [ ] **Step 2: Run the profile test and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_config.py::test_mlx_8b_fast_profile -q`

Expected: failure because the profile does not exist.

- [ ] **Step 3: Add `config.mlx-8b-fast.yaml`**

Copy the complete strict schema from `config.yaml`, select
`mlx-community/Qwen3-8B-4bit`, and apply only the limits listed in Step 1. Keep
the full 25-problem corpus and original connective split.

- [ ] **Step 4: Write projection tests**

```python
def test_projection_uses_measured_calls_and_reports_hard_bound():
    measured = RuntimeMeasurement(load=4, generation=6, embedding=1, scoring=2, lean=1)
    counts = OperationCounts(generations=10, embeddings=20, scorings=2, lean_calls=12)
    result = project_runtime(measured, counts, hard_timeout_seconds=300)
    assert result.expected_seconds == 96
    assert result.hard_upper_seconds == 300
```

Also assert negative measurements/counts are rejected and JSON contains the
model identifier, machine description, operation timings, counts, expected
seconds, and hard upper seconds.

- [ ] **Step 5: Run projection tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_runtime_probe.py -q`

Expected: import failure because `runtime_probe.py` does not exist.

- [ ] **Step 6: Implement measurement records and projection**

Use frozen dataclasses with non-negative finite validation. Compute expected
seconds as the operation-count dot product, excluding cold load; report cold
load separately. Derive hard upper bounds from configured per-call and loop
budgets rather than measured speed.

- [ ] **Step 7: Implement the runtime probe script**

The script loads the first implication problem, measures cold load, one normal
generation, one delta generation with a zero policy vector, two embeddings, one
two-candidate score batch, and one Lean tactic attempt. Write the report to a
new UUID directory under `runs-fast/probes/` and print its path.

- [ ] **Step 8: Document exact commands**

Add:

```bash
pip install -e '.[dev,model,mlx]'
PYTHONPATH=src python scripts/benchmark_mlx_runtime.py --config config.mlx-8b-fast.yaml
PYTHONPATH=src python -m analogy --config config.mlx-8b-fast.yaml
```

Document that the probe supplies the estimate and that training produces the
checkpoint used by a copied evaluation config with `stage: evaluate_rescue`.

- [ ] **Step 9: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_config.py tests/test_runtime_probe.py tests/test_cli.py -q`

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add config.mlx-8b-fast.yaml src/analogy/runtime_probe.py scripts/benchmark_mlx_runtime.py tests/test_runtime_probe.py tests/test_config.py README.md
git commit -m "feat: add bounded MLX 8B runtime probe"
```

### Task 5: Install, Download, Integrate, and Measure

**Files:**
- Modify only if the real API differs: `src/analogy/mlx_model.py`, corresponding tests, and dependency bounds.
- Create: `docs/benchmarks/2026-10-14-mlx-qwen8b-runtime.md`

**Interfaces:**
- Consumes: all prior tasks and the real `mlx-community/Qwen3-8B-4bit` weights.
- Produces: verified runtime measurements and the final experiment estimate.

- [ ] **Step 1: Install the approved optional dependencies**

Run: `.venv/bin/pip install -e '.[dev,model,mlx]'`

Expected: MLX and MLX-LM import successfully without replacing the PyTorch
policy dependency.

- [ ] **Step 2: Run the full suite before downloading weights**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: all tests pass; record the count.

- [ ] **Step 3: Download and load exactly the selected model**

Run the runtime probe online once. Confirm its metadata identifier equals
`mlx-community/Qwen3-8B-4bit` and the local snapshot is about 4.61 GB.

- [ ] **Step 4: Re-run the probe offline**

Run:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
  .venv/bin/python scripts/benchmark_mlx_runtime.py \
  --config config.mlx-8b-fast.yaml
```

Expected: generation, delta generation, embeddings, scoring, and Lean all
complete from local files; no network fallback.

- [ ] **Step 5: Repair only evidence-backed API mismatches with TDD**

For each mismatch, first add a focused regression test reproducing the real
shape/signature, run it RED, apply the smallest adapter fix, then rerun it GREEN.
Do not weaken latent injection or replace it with prompt steering.

- [ ] **Step 6: Run a six-problem smoke**

Use two implication, two negation, and two iff problems so shuffled evaluation
has two compatible test traces. Record completion, model calls, Lean calls,
iteration counts, and wall time. Do not claim quality improvement from six
problems.

- [ ] **Step 7: Write the measured benchmark note**

Record hardware, model revision, quantization, dependency versions, cold load,
operation p50 values, smoke duration, expected full duration, hard upper bound,
and artifact paths in `docs/benchmarks/2026-10-14-mlx-qwen8b-runtime.md`.

- [ ] **Step 8: Final verification**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m compileall -q src tests scripts
git diff --check
```

Expected: zero failures and zero diff errors.

- [ ] **Step 9: Commit**

```bash
git add src/analogy/mlx_model.py tests/test_mlx_model.py pyproject.toml docs/benchmarks/2026-10-14-mlx-qwen8b-runtime.md
git commit -m "test: verify MLX Qwen3-8B runtime"
```

### Task 6: Independent Review

**Files:**
- Review the complete diff from `f0e44f6` through `HEAD`.

**Interfaces:**
- Consumes: implementation and test evidence from Tasks 1–5.
- Produces: severity-ranked findings or an explicit no-blocker result.

- [ ] **Step 1: Request a fresh whole-branch review**

Ask the reviewer to prioritize latent injection timing, lazy-evaluation lifetime,
score masking, model-call accounting, backend fallback, timeout behavior, and
experiment comparability.

- [ ] **Step 2: Resolve Important findings with TDD**

For every Important finding, add a failing regression test, apply the minimal
fix, rerun focused tests, and request re-review.

- [ ] **Step 3: Re-run final verification**

Run the full suite, compileall, `git diff --check`, and the offline runtime probe.
Report the fresh outputs and the measured estimate.
