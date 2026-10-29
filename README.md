# Analogy-guided Lean proof search

Humans often get unstuck by moving a problem sideways: an electrical circuit becomes flowing water,
a difficult route becomes a delivery plan, and an abstract dependency becomes a set of boxes inside
boxes. The objects change while the relationships stay recognizable. This is the central idea in
[structure-mapping theory](https://doi.org/10.1207/s15516709cog0702_3): useful analogies preserve
relational structure across domains. Accounts of mathematical discovery often place insight after a
change of scene.

Classic experiments found that concrete source stories can support transfer to new problems
([Gick and Holyoak, 1983](https://doi.org/10.1016/0010-0285(83)90002-6)), and constraint-based
accounts formalized analogical mapping as a balance of structural, semantic, and pragmatic fit
([Holyoak and Thagard, 1989](https://doi.org/10.1207/s15516709cog1303_1)). Automated theorem proving
has also used source proof plans to guide target proofs
([Melis, 1995](https://www.ijcai.org/Proceedings/95-1/Papers/024.pdf)). This project tests a related
idea with dynamically generated everyday stories: can analogy help a small language model choose
better Lean tactics, with Lean verifying every proposed step?

Mathematics offers an unusually clean test bed for general analogical reasoning: Lean supplies an
exact evaluator.

Analogy creates **reflective access** by turning a relationship the model already carries into an
explicit representation it can inspect, revise, and reuse. The working hypothesis is that this
exposes relational knowledge already available to the model and helps it generalize that knowledge
into a new domain.

When SAT returns a countermodel for the current route, the harness changes the model's frame of
reference by assigning a new everyday domain. Structural ranking then favors the story whose pattern
of changes best matches the proof trajectory, giving the model a fresh route through tactic search.
Lean determines whether that reframing yields a valid proof step. The control flow below makes this
hypothesis operational.

## Basic control flow

Both evaluation conditions start with the same vanilla solver. They differ in what happens
after that solver runs out of useful tactics at a state.

```text
problem
  -> vanilla tactic search
  -> Lean verifies each tactic
  -> accepted tactics create the next proof states to try
  -> if the proof completes: stop
  -> if a state is stuck: run semantic recovery
       -> deterministic move available: try it, then resume vanilla search
       -> model intervention required:
            deliberation control -> direct proof plan -> tactic -> Lean
            ranked analogy       -> several story revisions -> structural ranking -> tactic -> Lean
```

Any intervention tactic accepted by Lean produces an ordinary proof state and returns control to the
same vanilla search loop. Ordinary search, intervention, and Lean verification run in sequence, with
Lean verifying every tactic.

## Problems

The benchmark contains 30 synthetic Lean theorems over five atomic propositions from PropL's
[`test_ood`](https://huggingface.co/datasets/KomeijiForce/PropL) split, pinned to revision
`62a13de410999c20f60ec2f3be93457dcaac0d62`. Each combines `∧`, `∨`, `→`, `True`, and `False` with
branch choices and multi-step hypothesis use. Deterministic selection requires a flat source trace,
a verifier-accepted reference proof of at least 13 tactics, and reasoning beyond one `simp`. The
exact corpus and selection record are in
[`problems.json`](src/analogy/lean_gym/corpus/problems.json) and
[`benchmark_metadata.json`](src/analogy/lean_gym/corpus/benchmark_metadata.json). The model receives
the theorem and live search context; selection uses the reference proofs and metadata.

## Vanilla solver

Vanilla search is the shared foundation of both conditions, and each accepted intervention returns
to it.

1. **Apply safe deterministic moves.** Before asking the model, the controller handles immediately
   recognizable cases such as an exact matching hypothesis, a directly available conjunction
   component, implication introduction, `True`, conjunction construction, and disjunctions whose
   two sides are identical.
2. **Generate tactic candidates.** The model sees the overall theorem, the current focused Lean
   state, the accepted path, Lean feedback from that state, and a compact tactic card. It returns JSON
   tactics. The configured controller permits up to three generation or repair calls per state.
3. **Validate and pool.** Tactics are normalized and deduplicated. Admission requires a complete
   single tactic with a supported head, visible identifiers, genuine evidence, and a fresh tactic for
   the normalized state. The model receives repair feedback for schema corrections.
4. **Rank by model likelihood.** The harness asks the model provider for each candidate completion's
   mean token log-likelihood, converts those values into normalized weights, and keeps the top three.
5. **Preflight branch choices.** For `left` and `right`, the propositional checker evaluates the
   selected disjunct before Lean is called. Branches supported across the exhaustive valuation check
   proceed to Lean; a countermodel for `left` triggers evaluation of `right`.
6. **Ask Lean.** Candidates are submitted one at a time. Lean errors, completed proofs, open
   successors, repeated states, and unchanged states are recorded separately. Normalized
   `(state, tactic)` results are cached.
7. **Prune and prioritize.** Every Lean-accepted open state in the supported propositional fragment
   receives an exhaustive Boolean validity check. States that remain valid enter the search pool. The
   solver chooses the next open proof state primarily from tactic likelihood with a small depth
   penalty; frozen-model embedding novelty is the secondary key.
8. **Declare stuckness.** A state becomes stuck when every candidate yields an error, a repeated
   state, an unchanged state, or a semantic countermodel. Quantified states remain searchable.

In short:

```text
proof state -> deterministic move or model candidates -> likelihood ranking
            -> SAT preflight -> Lean -> semantic pruning -> next open proof state
```

## Analogy condition

Analogy is a stuck-state intervention layered on the same search. One persistent mental model is
used per problem, and later calls revise that story.

### 1. Build a story from abstract actions

At the first recoverable stuck state, the harness reconstructs the root-to-stuck proof path.
Accepted tactics on that path are converted deterministically into abstract actions—for example,
`intro` becomes “take on an assumption,” `rcases` becomes “break a combined fact into its parts,”
and `left` becomes “commit to the left available choice.”

The story-writing input contains those abstract actions and a harness-assigned everyday domain. It
must produce one concrete state transition per accepted proof transition. Domains come from a fixed
card including cooking, travel, construction, games, gardening, logistics, household organization,
and mechanical repair.

For example, suppose the accepted Lean path is:

```lean
intro h
obtain ⟨hp1, hp2⟩ := h
left
exact hp1
```

The harness retains that code, converts it into the following story-writing input, and assigns the
domain `travel or navigation`:

```text
1. take on an assumption
2. break a combined fact into its parts
3. commit to the left available choice
4. finish using something already in hand
```

A valid story response could be:

```json
{
  "domain": "travel or navigation",
  "initial_state": "a traveler is waiting at a station to receive an assigned trip",
  "transitions": [
    {
      "domain_transition": "the traveler accepts a sealed travel packet",
      "domain_after": "the traveler is responsible for the trip and holds the sealed packet"
    },
    {
      "domain_transition": "the traveler opens the packet and separates the ticket from the map",
      "domain_after": "the traveler holds the ticket and map as separate items"
    },
    {
      "domain_transition": "the traveler enters the left departure lane toward the train platform",
      "domain_after": "the traveler stands at the train-platform checkpoint"
    },
    {
      "domain_transition": "the traveler presents the ticket already in hand",
      "domain_after": "the checkpoint opens for the traveler"
    }
  ],
  "abandoned_domains": []
}
```

After validation, the harness pairs these four everyday transitions with the four actual proof
transitions and states.

### 2. Validate and revise

The harness checks the JSON schema, exact transition count, assigned domain, domain ledger, and
concrete everyday vocabulary. A response requiring repair receives the validator's reason once as
feedback. Every initialization outcome is cached by normalized stuck-state fingerprint.

At later stuck states, the model may generate several revisions of the same mental model. A SAT
countermodel forces a domain change; the harness selects the next domain in the canonical sequence
and fills the abandoned-domain ledger.

### 3. Rank analogies by structural correspondence

Analogy ranking is independent of tactic ranking:

1. Embed the ordered everyday states and the ordered proof states with mean-token embeddings from
   layer 12.
2. Subtract a frozen corpus mean and remove the top two principal components. These frozen
   statistics reduce embedding anisotropy and were fitted on 838 calibration texts.
3. Convert consecutive states into transition vectors (`after - before`).
4. Build a cosine-distance matrix between transitions in each sequence and mean-normalize it.
5. Resize both ordered distance structures to `24 × 24`.
6. Compare their upper triangles using correlation distance. Lower distance means that the story's
   pattern of changes more closely matches the proof trajectory's pattern of changes.
7. Rank valid story revisions by this distance, with generation order as the tie-breaker.

Structural ranking begins with three transitions. Shorter trajectories receive a recorded neutral
score.

### 4. Select a tactic after ranking

After structural ranking, the tactic-selection call receives the current Lean state and the
best-ranked remaining story. For a disjunction it must spell out the everyday and proof consequences
of both `left` and `right`, then select one. A tactic that produces an error or unchanged state moves
recovery to the next ranked revision. The current proof state and visible hypotheses are
authoritative; the story is secondary context.

The selected tactic passes through the same syntax filter, SAT preflight, Lean verifier, repeated-
state checks, and semantic pruning as an ordinary tactic. Tactic likelihood is logged afterward for
analysis. The structural ordering of the analogies remains fixed for that recovery attempt.

```text
stuck state -> exact path -> abstract actions -> story revisions
            -> structural ranking -> current-goal tactic -> Lean
```

## Run the solver

The MLX backend requires Python 3.11+, Apple Silicon, and Lean 4/Lake. Install the environment:

```bash
uv sync --extra mlx --extra dev
```

The experiment uses `Qwen3-14B-4bit` for every model role in both conditions: ordinary tactic
generation, direct deliberation, analogy story and tactic calls, likelihood scoring, and state
embeddings. The structural scorer uses the packaged layer-12 calibration from the same model at
revision `a4d9b2df59d2c150bef02fcbe0d91046b7ca33a4`. [`config.yaml`](config.yaml) provides a complete
14B default setup, and [`prompts.yaml`](prompts.yaml) contains every model-facing prompt.

Both evaluation conditions share ordinary search, deterministic moves, SAT checks, Lean verification, and the
intervention-generation envelope. Analogy also performs structural embedding and ranking; later
search work varies with the proof states produced by each intervention. Run both conditions on the
30-problem corpus with:

```bash
uv run python -m analogy solve --deliberation --all-problems
uv run python -m analogy solve --analogy --all-problems
```

Each command writes `report.md`, `run.json`, and per-problem records under
`.artifacts/runs/solve/<run-id>/`. Current run records retain the effective configuration, model
revisions, file hashes, run seed, and individual generation seeds.

## Run the two-condition probe

The probe runs three fixed PropL cases under direct deliberation and structurally ranked analogy. It
is a plumbing and reproducibility check rather than an efficacy estimate. Both conditions use seed
`0`, the same Qwen3-14B-4bit instance, the same Lean verifier, and the same ordered problems.

At the first recoverable stuck state, the analogy condition uses its planning slots to generate
several story revisions, scores their transition structures, and submits the tactic from the
highest-ranked valid story to the live search. Tactics implied by the unused stories are checked in
isolation and logged as counterfactual evidence; they never change the search. The direct condition
uses the same ordinary solver and intervention envelope without a story. Realized model calls,
verifier calls, selected scores, and counterfactual outcomes are recorded.

Run or safely resume the six trials with:

```bash
uv run python -m analogy evaluate --phase probe --config config.yaml
```

The combined `manifest.json` and `report.md` appear under
`.artifacts/evaluations/<evaluation-id>/`. Each manifest references the six ordinary solve-run
directories, records input and environment hashes, and refuses completion when the two arms have
different ordinary prefixes or the analogy arm lacks its initial candidate and selection evidence.
