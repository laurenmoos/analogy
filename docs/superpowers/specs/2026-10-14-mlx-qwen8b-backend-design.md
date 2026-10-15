# MLX Qwen3-8B Backend Design

## Goal

Run the existing surprise-driven analogy RL experiment with an 8B model at
usable speed on the 24 GB Apple M3 MacBook Air. Preserve the current experiment
semantics: frozen model, hidden-state policy action, proof embeddings,
teacher-forced tactic scoring, Lean verification, budgets, and immutable logs.

The selected model is `mlx-community/Qwen3-8B-4bit`, an MLX conversion of
`Qwen/Qwen3-8B`. Four-bit quantization changes numerical precision, not the
parameter count or architecture. The current full-precision Transformers path
remains available for reproducibility.

## Why a Backend Is Required

The current Transformers model occupies enough memory to trigger CPU/disk
offload on this host. Its ordinary tactic call reaches the 60-second timeout
before analogy rescue begins. MLX keeps a four-bit 8B model resident in unified
memory and executes it on Apple silicon.

A text-generation-only adapter is insufficient. The experiment also requires:

- final-token embeddings at a configured layer;
- one policy delta injected into the final prefill token at that layer;
- mean token log likelihood for tactic reranking;
- deterministic generation and provenance metadata.

No prompt-only approximation is permitted. If latent injection cannot be
validated against the selected layer, the backend must fail explicitly.

## Configuration

`ModelConfig` gains a required `backend` value:

- `transformers`: the existing `QwenGenerator` behavior;
- `mlx`: the new Apple-silicon backend.

The default configuration stays on `transformers`. A checked-in
`config.mlx-8b-fast.yaml` selects:

- `mlx-community/Qwen3-8B-4bit`;
- deterministic decoding;
- 96 output tokens and a 20-second call ceiling;
- search width 3 and 8 ordinary expansions;
- one rollout, one training epoch, and at most three analogy iterations;
- eight model calls and six Lean calls per rescue.

These are runtime limits, not changes to reward or stopping semantics.

## Backend Boundary

Both implementations satisfy one model boundary used by training and search:

```text
generate(messages, seed) -> text
generate_with_delta(messages, delta, layer, seed) -> text
embed_text(text, layer) -> normalized Embedding
score_completions(messages, completions) -> CompletionScore[]
hidden_size, model_revision, timeout_seconds
```

The CLI factory selects the backend from configuration. Callers do not branch
on MLX versus Transformers.

## MLX Implementation

The MLX backend loads model and tokenizer through `mlx_lm.load`. Parameters are
frozen by construction; no model optimizer is created.

For latent generation, the configured decoder layer is temporarily wrapped by
a small delegating module. On its first prefill invocation, the wrapper adds the
detached policy delta only to the final sequence position, then becomes inert
for cached token decoding. The original layer is restored in `finally`, even
when generation fails. Dimension, rank, and finiteness checks match the current
Transformers implementation.

Embedding extraction performs the same decoder prefix up to the configured
layer, reads the final non-padding token, converts it to float32, L2-normalizes
it, and records model revision, resolved layer, token index, and input hash.

Completion scoring batches all candidate continuations in one padded forward
pass. Only completion-token log probabilities contribute to each mean. One
batch is charged as one model call, matching the existing loop accounting.

MLX execution is lazy, so every timed operation explicitly evaluates its
result before recording elapsed time. A call that returns after its ceiling is
reported as a timeout; outer cancellation still prevents subsequent calls.

## Runtime Probe and Estimate

A small timing command loads the configured backend and runs a fixed proof
prompt through generation, embedding, scoring, and Lean verification. It emits
JSON containing cold-load time, each operation latency, peak resident-memory
observation when available, and projected counts for:

- a six-problem smoke covering train, validation, and test connectives;
- one full training epoch over 20 train/validation problems;
- matched evaluation over five test problems and all enabled conditions.

Projections use measured operation counts and report both expected and hard
budget upper bounds. The final user-facing estimate must come from this probe,
not a hardware guess.

## Failure Handling

- Missing MLX dependencies produce an actionable optional-extra error.
- Missing weights produce an error naming the exact model identifier.
- Unsupported model structure, layer, delta dimension, or non-finite values
  fail before generation.
- Timeouts and out-of-memory failures retain structured episode records.
- The runner never silently falls back to CPU Transformers or a smaller model.

## Testing

Unit tests use injected fake MLX modules and verify:

- backend selection and strict configuration;
- deterministic generation arguments;
- exactly-once final-prefill delta injection and layer restoration;
- normalized embedding provenance;
- batched completion likelihood ordering;
- timeout and malformed-model failures;
- timing projection math and JSON schema.

The full existing suite must remain green. The integration gate is a real local
MLX smoke that loads the 8B four-bit model, performs all four backend
operations, verifies one tactic with Lean, and records measured latency.

## Acceptance Criteria

1. The MLX path runs `mlx-community/Qwen3-8B-4bit`; no smaller model fallback.
2. Latent action, embeddings, and scoring remain functional and tested.
3. One bounded generation completes without swap-driven disk offload.
4. A measured smoke report gives expected and upper-bound runtimes.
5. Existing Transformers behavior and all existing tests are preserved.
