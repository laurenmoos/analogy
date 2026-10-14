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

Run the complete preservation suite with:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
```

## Standalone retrieval experiment

The retrieval comparison is independent of the numbered implementation plans and does not import the `analogy` package. Run the one-problem, three-condition smoke experiment with:

```bash
./run_experiment.sh
```

Validate the inputs and Lean setup without loading Qwen:

```bash
./run_experiment.sh --dry-run
```

Additional options are passed to the standalone Python program. For example,
`./run_experiment.sh --problem imp_elim_01` selects another benchmark problem.
