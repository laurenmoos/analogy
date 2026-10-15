# Analogy

This repository explores whether analogies from poetry, physics, and other domains can help a Lean model choose its next tactic when ordinary proof search is stuck.

The current prototype does not synthesize programs. A conventional policy supplies a credible set of tactic candidates; a cheap daydream process explores unusual relations; an LLM may use a pertinent analogy to choose among the original candidates; and Lean verifies the result.

## What is an analogy?

Conventionally, an analogy compares different things through a relationship they share.

For example, `seed → plant` and `idea → project` involve different objects but preserve the same everyday relation: something latent becomes realized. We write this compactly as

$$(a \rightarrow b) :: (c \rightarrow d).$$

Operationally, an analogy preserves enough relational structure to support transfer into a new context. Embeddings do not redefine that idea; they let a model search for possible correspondences before it can name them.

## Representation

The basic object is a directed transition between states:

$$s_{ab}=E_{\Delta}(a,b).$$

A transition may be a poetic image before and after a metaphorical turn, a physical scene before and after an event, or a Lean proof state before and after a tactic. A higher-order encoder can then place transformations in a shared relation space:

$$z_{ab}=H(s_{ab}).$$

Relations do not need fixed names such as *splitting* or *revealing*. They may exist only as directions or neighborhoods in the learned space. Cross-domain transfer still requires a model to interpret a retrieved relation in the context of the proof.

The intended analogy matches the proof trajectory up to the stuck point, then treats the analogy's continuation as a hypothesis for what the proof should do next:

```text
analogy:  a₀ → a₁ → a₂ → a₃
proof:    s₀ → s₁ → s₂ →  ?
                       alignment
prediction: map a₃ back into proof space → next tactic
```

The transferable object is therefore a line of reasoning, not a similar theorem. Lean decides whether the proposed continuation is useful.

## Stuck-only tactic discovery

The readable control flow is:

```text
propose high-likelihood tactics
→ try the top candidate normally
→ detect local stuckness
→ sample and refine higher-entropy analogies
→ discard analogies that are not pertinent
→ use surviving analogies to choose among the untried original tactics
→ verify the selected tactic with Lean
```

Curiosity can change the ranking, but it cannot invent tactic text or escape the policy's candidate pool. The main loop is in [`src/analogy/loop.py`](src/analogy/loop.py), and relation exploration is in [`src/analogy/daydream.py`](src/analogy/daydream.py).

## Repository

All Python code lives in one package:

- `src/analogy/records.py` — immutable shared records.
- `src/analogy/loop.py` — best-first search, progress, and stuckness.
- `src/analogy/daydream.py` — analogy sampling, refinement, pertinence, and constrained choice.
- `src/analogy/rescue.py` — generic rescue records, prompts, parsing, and bounded rollouts.
- `src/analogy/rl.py` — the trainable latent policy and verifier-only learning rule.
- `src/analogy/training.py` — structural splits, checkpoints, run artifacts, and matched controls.
- `src/analogy/lean_gym/` — the 25-problem corpus, pinned Lean project, verifier, sessions, traces, and replay.
- `config.yaml` — the single experiment, model, search, analogy, RL, run, and data configuration.
- `docs/plans/` — numbered implementation plans.

Large or disposable datasets stay outside the repository. `data.root` in `config.yaml` currently points to `/tmp/analogy-data`; change that one value if the poetry or TRIP files move.

## Run

Use Python 3.11 or newer, Lean 4.29.1 through `elan`, and install the package in a virtual environment:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev,model]'
.venv/bin/python -m analogy --config config.yaml
```

The `capability` stage loads frozen Qwen3-8B locally, asks it for one JSON tactic sequence per benchmark problem, executes each sequence in Lean, and writes immutable records under `runs/capability/`.

The original one-shot baseline solved 4 of 25 problems. A benchmark-agnostic Lean syntax primer and clearer prompt layout raised the deterministic result to 14 of 25 with seed 0 (run `b895bf8c77e04df493a922e0f9cc9374`). This clears Step 0: the frozen small model can solve a meaningful portion of the corpus before analogy or RL is introduced.

The next-tactic baseline was exercised as a diagnostic pilot rather than a definitive benchmark. Run `98d06bc80dff4c23b17ab32b0b68f3c3` solved 13 of the 19 attempted problems and produced 7 explicit stuck states. It was intentionally stopped before the last six problems, so 13/19 should not be compared as a final success rate. The useful finding is that the search loop, Lean verification, and stuck-state capture work end to end; 198 of 254 checked proposals were invalid, making conventional tactic quality the next obvious bottleneck.

## Generic rescue RL and analogy streams

The first trainable checkpoint deliberately tests rescue without claiming an analogy benefit:

```text
conventional proof search
→ observable stuckness
→ direct latent rescue rollout
→ predicted next state + generated tactics
→ Lean verification
→ return progress to conventional search
```

Qwen stays frozen. A small direction/radius policy changes one final-token prefill activation, and Lean supplies completion, novel-state, or structured failure reward. Raw generations, bounded repairs, prediction error, and policy diagnostics are preserved. Verified stuck states can be reconstructed into a deterministic, split-safe replay curriculum.

The corpus-free analogy path emits readable cards—domain, situation, relation chain, mapping, continuation, predicted consequence, and rationale—then chains those cards before a separate tactic decoder cites the motivating hop. Lean still decides whether the decoded continuation completes the proof or creates real progress. Discounted verified return propagates through every analogy action in the stream.

> Match the proof’s reasoning up to the stuck point; treat the analogy’s next move as a testable guess for the proof’s next move.

The current rescue loop is deliberately literal and editable:

```text
proof state + Lean shape + prior verified iterations
→ generate one analogy card
→ generate tactics conditioned on the complete card stream
→ let Lean verify the first real successor
→ compare predicted and actual proof transitions
→ refine while that transition remains usefully surprising
```

For example, a card can map a blocked river splitting around a rock to a proof
state whose implication cannot advance directly. It predicts two remaining
channels, proposes `constructor`, observes Lean's actual two-goal successor, and
records the embedding distance between those predicted and actual transitions.
The next card sees that complete record rather than starting a fresh metaphor.

The loop lives in [`src/analogy/analogy_loop.py`](src/analogy/analogy_loop.py).
Every iteration can be flushed to `analogy_iterations.jsonl`, including proof
views, Lean structure, raw generation attempts, card, action diagnostics,
tactics, Lean outcomes, transition embeddings, surprise, stop decision,
budgets, and elapsed time.

Training and evaluation also finalize `readable_traces.md` in the run directory,
after closing the JSONL records and writing `summary.json`. The Markdown groups
proof paths by theorem, phase, and epoch. `THEOREM TARGET (fixed)` stays above
the evolving Lean states; states start at zero and human analogy labels at one.
Every tactic names its source state. Accepted tactics point to their verified
successor, errors return to `[State n unchanged]`, repeated successors are shown
as candidates followed by `[DISCARDED: repeated_state]`, and verified completion
shows no remaining subgoals. Multiple open goals are numbered explicitly.

Generated interventions nest `SOURCE ANALOGY` → `ALIGNMENT` →
`TRANSFERRED PROOF REASONING` → `TACTIC` → `LEAN RESULT`; legacy cards are labeled
separately. Ordinary search and conventional finishing attempts retain their
own stage labels. Optional `proof_trace` metadata in `problems.jsonl` preserves
the theorem, initial state, and ordinary expansions; a `proof_path_id` joins
only the corresponding existing rollout rows, including repeated curriculum
invocations of one theorem. Metrics still use the original episode fields.
Replay prefixes without recorded intermediate states are identified explicitly.
Older records without this metadata display an unavailable-trace notice.

The renderer reads machine records without changing them, builds the complete
Markdown first, then flushes and atomically replaces a temporary file in the
same directory. A rendering or replacement failure warns and preserves any
previous Markdown plus all JSONL and summary metrics. The five fixed snapshots
in `tests/fixtures/generated_analogy_traces/` preserve October 15 Lean outcomes
with deterministic generated-analogy prose; they test readability, not solver
performance.

The main `config.yaml` controls are `min_iterations` / `max_iterations`,
`surprise_threshold` / `surprise_patience`, `min_surprise_improvement` /
`improvement_patience`, per-iteration tactics, total model and Lean budgets,
transition embedding layer, reward `discount`, and `iteration_cost`. A model
stop is advisory; cancellation and hard budgets win.

Matched evaluation reports `conventional`, `learned_direct`, `analogy_loop`,
and `shuffled_analogy` under the same search seed and verifier budgets. The
shuffle helper only permutes compatible records within one split and
connective, and refuses singleton groups.

### Fast local 8B profile on Apple silicon

The bounded MLX profile keeps the full Qwen3-8B architecture but uses the
four-bit `mlx-community/Qwen3-8B-4bit` conversion so the model remains resident
in unified memory instead of falling back to CPU/disk offload. It never falls
back to a smaller model.

```bash
pip install -e '.[dev,model,mlx]'
PYTHONPATH=src python scripts/benchmark_mlx_runtime.py \
  --config config.mlx-8b-fast.yaml
PYTHONPATH=src python -m analogy --config config.mlx-8b-fast.yaml
```

Run the probe first. Its `runtime.json` measures cold loading, ordinary and
latent-steered generation, embeddings, batched tactic scoring, and Lean, then
reports transparent six-problem and full-run estimates. Training writes its
best checkpoint under `runs-fast/rescue/<run-id>/checkpoints/`. To run matched
evaluation, copy the profile, set `experiment.stage: evaluate_rescue`, and set
`rl.checkpoint` to that checkpoint; the original training profile remains an
immutable record of the run.

The generated-domain profile uses the same model and call caps, with a minimum
of two analogy iterations (when budgets, schema validation, and completion
permit):

```bash
PYTHONPATH=src python -m analogy --config config.mlx-8b-generated-analogy.yaml
```

It separates concrete source generation, structural alignment, tactic-free
proof reasoning, tactic decoding, and Lean verification. Only source generation
receives the policy delta. A verified open successor gets an `assumption`
finishing attempt before another analogy. `legacy` remains the default strategy.
When used with `evaluate_rescue`, the generated profile evaluates conventional
search, learned direct rescue,
legacy cards, and generated unsteered, random, learned, and shuffled-source
controls with matched configured call budgets. Actual calls can differ because
of validation failure, early completion, or unavailable compatible shuffle donors.

The [five-example generated-domain smoke report](docs/benchmarks/2026-10-15-generated-domain-analogy-smoke.md)
records the exact configuration, immutable artifacts, relative control metrics,
and connected traces. Its held-out results concern only two iff examples;
the additional training/validation comparisons are in-sample diagnostics.

See the [quality and stream design](docs/superpowers/specs/2026-10-14-rescue-quality-and-analogy-stream-design.md) and [implementation plan](docs/superpowers/plans/2026-10-14-rescue-quality-and-analogy-stream.md).

### Post-hoc LLM audit

The audit stage explains a completed analogy run without participating in it.
Training, search, Lean verification, surprise, rewards, and policy updates finish
before the auditor starts. The source run is read-only and its hash is checked
again after judging.

Copy the matching model profile, set `experiment.stage: audit_analogy`, then run
one audit for the training run and another for its held-out evaluation:

```bash
PYTHONPATH=src .venv/bin/python -m analogy \
  --config config.mlx-8b-audit.yaml \
  --audit-source runs-fast/EXPERIMENT/rescue/TRAIN_RUN \
  --audit-output runs-fast/EXPERIMENT/audits/train \
  --audit-max-calls 100

PYTHONPATH=src .venv/bin/python -m analogy \
  --config config.mlx-8b-audit.yaml \
  --audit-source runs-fast/EXPERIMENT/rescue/EVALUATION_RUN \
  --audit-output runs-fast/EXPERIMENT/audits/test \
  --audit-max-calls 100
```

Only `train`, `validation`, and `analogy_loop` iteration rows are audited; direct,
conventional, and shuffled-control phases are excluded. Each separate audit
writes `config.json`, append-only `judgments.jsonl`, and `summary.json`. A row
classifies the earliest apparent failure as analogy choice, tactic translation,
Lean execution, or proof progress (or `working`) and preserves the raw model
output plus every executed tactic/result pair, including repair tactics. The
judge never sees reference proofs and its scores are descriptive—not rewards.
`--audit-max-calls` bounds total judge calls, including schema repairs. Re-running
the same output directory resumes by iteration key without duplicating completed
rows; an interrupted final JSONL record is quarantined before resumption.

Run the complete preservation suite with:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
```

## Standalone retrieval experiment

The retrieval comparison is independent of the numbered implementation plans and does not import the `analogy` package. It is a tactic-recovery experiment with three paired conditions:

- `none`: Qwen scores the shared tactic strings with no corpus context;
- `random`: Qwen sees a deterministic random sample from the structurally eligible poetry/physics shortlist;
- `analogy`: Qwen sees the highest teacher-forced proof–transformation pair scores from that same shortlist.

Lean derives a name-free structural signature from each elaborated proof state. Qwen remains frozen and local: it extracts corpus records, scores relevance labels, and scores the actual tactic-string completions in batched forward passes. Lean is the only correctness authority. Optional hidden vectors are diagnostic artifacts and never select retrieved examples.

The launcher requires Apple Silicon MPS and fails instead of silently using CPU. It also uses offline-safe, non-overwriting result directories and a 30-second per-call Lean ceiling inside a 600-second theorem budget, preventing cold-start compilation from being mistaken for a condition failure. Install or cache `Qwen/Qwen3-8B` locally before the first offline run.

Generation budgets follow the current Lean proof signature rather than the benchmark's curriculum level: atomic elimination states answer directly, while nested or branching states may use a bounded thinking phase. Valid corpus transformations and deterministic schema exclusions are both cached in `/tmp/analogy-data/standalone-cache`. Rescue remains a direct, non-thinking generation of at most two tactics; the main condition comparison uses teacher-forced likelihoods.

Run the default three-problem, three-condition smoke experiment with:

```bash
./run_experiment.sh
```

Validate the inputs and Lean setup without loading Qwen:

```bash
./run_experiment.sh --dry-run
```

Run all twenty-five mixed-difficulty benchmark problems with:

```bash
./run_experiment.sh \
  --problem and_intro_01 --problem and_intro_02 --problem and_intro_03 \
  --problem and_intro_04 --problem and_intro_05 \
  --problem imp_intro_01 --problem imp_intro_02 --problem imp_intro_03 \
  --problem imp_intro_04 --problem imp_intro_05 \
  --problem or_intro_01 --problem or_intro_03 --problem or_intro_04 \
  --problem or_intro_05 --problem or_intro_06 \
  --problem iff_intro_01 --problem iff_intro_02 --problem iff_intro_03 \
  --problem iff_intro_04 --problem iff_intro_05 \
  --problem not_intro_01 --problem not_intro_02 --problem not_intro_03 \
  --problem not_intro_04 --problem not_intro_05
```

After the 25-problem run is operationally sound, run a three-seed replication by adding the same problem list to:

```bash
./run_experiment.sh --seed 0 --seed 1 --seed 2
```

Additional arguments are passed through after launcher defaults, so user values override them. For example, `./run_experiment.sh --problem imp_intro_05 --retrieval-count 2` replaces the mixed default set and retrieval count.

Each result directory contains:

- `config.json`: immutable inputs, Lean/model provenance, hybrid budgets, warm-up status, and counterbalanced condition order;
- `transitions.jsonl`: validated corpus schemas and optional diagnostic embeddings;
- `attempts.jsonl`: proof-signature fingerprints, shortlist IDs, compatibility components, pair margins, tactic likelihoods, and Lean outcomes;
- `trials.jsonl`: one finalized theorem/condition/seed result, including the fixed-pool/rescue split;
- `generations.jsonl`: bounded corpus extraction and rescue calls plus teacher-forced scoring batches;
- `summary.json`: paired condition aggregates, fixed-pool solves, separately counted rescue solves, and operational fallbacks.

The primary metric is Lean-verified solve rate within the identical fixed candidate and verifier budget. Compare `analogy` with both `none` and structurally matched `random`; do not fold rescue solves into the fixed-pool result. A null result—no eligible analogy, no improvement, or equal rankings—is valid evidence rather than an experiment failure. Rescue proposes at most two new tactics only after the fixed pool is exhausted and is reported separately.
