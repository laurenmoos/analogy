# Previous training and inference runs in plain language

This is a retrospective logbook of the saved training and inference runs through
October 16, 2026. The newest real-Qwen runs are shown problem by problem. Older
runs are summarized from their immutable `summary.json`, JSONL, benchmark, and
readable-trace artifacts because `run.log` did not exist when they ran.

The main conclusion is simple: the current one-call implementation executes end
to end, but the story-mediated choice did not beat direct operator selection in
the bounded real-Qwen comparison. Earlier training runs established integration
and exposed useful failure modes, but none demonstrated a learned held-out
advantage.

## October 16 current one-call runs

### Real-Qwen production inference

Configuration: `mlx-community/Qwen3-8B-4bit`, Metal, one model call per state,
thinking disabled, `story-operator-v2`, deterministic grounding, and real Lean.

#### Problem 1 of 3: component or apply

```text
Expected operation: select_left
Story: A person finds a broken key made of two parts. They try to use one part
       to open a door, but it does not work. They then try the other part and
       it works.
Model operation: split
Grounding: 0 valid applications
Compiled candidates: 0
Lean checks: 0
Result: not solved
Reason: split cannot be applied to this current proof state
Model time: 7.763 seconds
```

#### Problem 2 of 3: component or exact

```text
Expected operation: exact_local
Story: A person needed a tool to fix a broken chair. They found a hammer in the
       workshop. The hammer matched the needed tool.
Model operation: exact_local
Grounding: 1 valid application
Candidate: exact hq
Lean: exact hq -> complete
Result: SOLVED
Model time: 5.227 seconds
```

#### Problem 3 of 3: equality or apply

```text
Expected operation: apply_implication
Story: A person finds a key that fits a lock. They use the key to open the lock
       and retrieve a hidden item.
Model operation: exact_local
Grounding: 0 valid applications
Compiled candidates: 0
Lean checks: 0
Result: not solved
Reason: no local item exactly matches the goal
Model time: 6.574 seconds
```

#### Production result

```text
Problems: 3
Model calls: 3
Valid model outputs: 3
Repair calls: 0
Grounded candidates: 1
Lean checks: 1
Solved: 1 of 3
```

The two failed choices stopped before Lean. Deterministic grounding did not
invent a tactic for an inapplicable operation.

### Real-Qwen direct choice versus story-mediated choice

Each of three fixtures was run twice with matched latent nudges. Both arms made
six model calls.

#### component or apply

```text
Direct choice, samples 1 and 2
Operation: select_left
Result: 2 of 2 successful

Story choice, samples 1 and 2
Story: A carpenter had a wooden plank and a nail. He needed to build a shelf,
       so he decided to use the plank as the base and the nail to secure it to
       the wall.
Operation: select_left
Result: 2 of 2 successful
Story diversity: the two stories were identical
```

#### component or exact

```text
Direct choice, samples 1 and 2
Operation: exact_local
Result: 2 of 2 successful

Story choice, samples 1 and 2
Story: A person needed a specific item to complete a task. They found a matching
       item in their local storage, which was exactly what they required.
Operation: exact_local
Result: 2 of 2 successful
Story diversity: the two stories were identical
```

#### equality or apply

```text
Direct choice, samples 1 and 2
Operation: apply_implication
Result: 2 of 2 successful

Story choice, samples 1 and 2
Story: A person found a box with a red key inside. They used the key to open a
       locked drawer, revealing a small, glowing stone. The stone was the only
       item they needed to complete their task.
Operation: exact_local
Grounding: 0 valid applications in both samples
Result: 0 of 2 successful
Story diversity: the two stories were identical
```

#### Ablation result

| Measure | Direct choice | Story-mediated choice |
|---|---:|---:|
| Successful first picks | 6 / 6 | 4 / 6 |
| Successful candidate sets | 6 / 6 | 4 / 6 |
| Invalid model outputs | 0 | 0 |
| Zero-application choices | 0 | 2 |
| Prompt tokens | 1,048 | 2,414 |
| Output tokens | 44 | 400 |
| Model time | 13.039 s | 53.779 s |
| Lean calls | 8 | 4 |

```text
Story/direct prompt-token ratio: 2.30x
Story/direct output-token ratio: 9.09x
Story/direct model-time ratio: 4.12x
Mean unique stories per fixture: 1.0
Fixtures with multiple stories: 0 of 3
```

Source: [single-call grounding smoke](2026-10-16-single-call-grounding-smoke.md).

## October 15 historical training and inference

These runs used older staged or legacy paths. They remain useful diagnostic
evidence, but their checkpoints and trace schemas are intentionally incompatible
with the current one-call implementation.

### Analogy repair training

```text
TRAINING RUN
Epochs: 1
Training implication problems solved: 1 of 2
Validation problems solved: 0 of 2

Learning update 1
Mean reward: -0.100
Loss: +1963.697
Gradient norm: 4291.837

Learning update 2
Mean reward: +1.000
Loss: -21367.852
Gradient norm: 47434.973

Checkpoint: epoch-0001.pt
TRAINING COMPLETE
```

### Analogy repair matched inference

| Condition | Solved | Lean checks | What happened |
|---|---:|---:|---|
| Conventional | 0 / 2 | 12 | No rescue path |
| Learned direct | 0 / 2 | 13 | No accepted rescue tactic |
| Analogy loop | 0 / 2 | 28 | Some verified progress, no completion |
| Shuffled analogy | 0 / 2 | 27 | Similar progress to the unshuffled analogy |

```text
Total wall time: 635 seconds
Training episodes: 4
Evaluation episodes: 8
Episode-reported Lean checks: 117
Generation attempts: 27
Analogy iterations: 9
Final held-out result: 0 of 2 in every condition
```

The analogy and shuffled conditions both produced a verified successor, so this
run crossed an earlier mechanical bottleneck. Because shuffled analogy improved
similarly, it did not establish that meaningful analogy selection caused the
progress. Full source: [analogy repair smoke](2026-10-15-analogy-repair-ablation.md).

### Generated-domain training variants

#### Primary 256-token training

```text
Epochs: 1
Training implications solved: 1 of 2
Validation negations solved: 0 of 1

Learning update 1: mean reward -0.050; loss +991.531; gradient norm 2358.026
Learning update 2: mean reward +1.000; loss -20394.598; gradient norm 50088.668
Checkpoint: epoch-0001.pt
```

#### Repaired 256-token training

```text
Epochs: 1
Training implications solved: 0 of 2
Validation negations solved: 0 of 1

Learning update 1: mean reward -0.050; loss +991.531; gradient norm 2358.026
Learning update 2: mean reward -0.050; loss +19.414; gradient norm 0.108
Main failure: outputs repeatedly reached or approached the token cap before a
              usable staged result was produced
```

#### Repaired 512-token training

```text
Epochs: 1
Training implications solved: 0 of 2
Validation negations solved: 0 of 1
Token-cap bottleneck: removed in the observed outputs
Remaining failures: source-language constraints, duplicate tactics, and one
                    structure-extraction timeout
```

#### Held-out inference

```text
Held-out problems: 2 iff theorems
Primary run: 0 of 2 solved in every condition
Repaired 256 run: 0 of 2 solved in every condition
Repaired 512 run: 0 of 2 solved in every condition
Novel open rescue successors: 0
Second analogies after verified progress: 0
```

The primary all-five diagnostic solved one in-sample implication with `exact p`,
but that was not held-out evidence. Full source and linked per-theorem traces:
[generated-domain analogy smoke](2026-10-15-generated-domain-analogy-smoke.md).

### Grounded transition training

#### Default retained-state run

```text
Training states solved conventionally: 2 of 5
Validation states solved: 0 of 1
Policy updates: 0
Grounded held-out model calls: censored by generation timeouts
Real grounded compiler candidates reaching Lean: 0
```

#### Curated bounded run

```text
Training states solved conventionally: 2 of 2
Validation states solved: 1 of 2
Policy updates: 0
Held-out states: 2
Matched inference conditions: 8
Solved: 0 of 2 in every condition
Direct rescue: one verified novel open successor
Generated analogy conditions: zero verified novel open successors
Second analogies: 0
```

The real-model grounded generations repeatedly timed out before validated plans
could reach the compiler. This measured a latency/schema-throughput limit, not a
0% compiler acceptance rate. Full source: [grounded transition smoke](2026-10-15-grounded-transition-smoke.md).

## Earlier direct-rescue pilot

```text
TRAINING RUN
Epochs: 1
Learning updates: 3
Mean rewards: +1.000, 0.000, 0.000
Checkpoint: epoch-0001.pt

INFERENCE RUN
Held-out problems: 1
Conventional: 0 of 1 solved; 4 Lean checks
Unperturbed direct rescue: 0 of 1 solved; 4 Lean checks
Random direct rescue: 0 of 1 solved; 4 Lean checks
Learned direct rescue: 0 of 1 solved; 4 Lean checks
```

Artifacts: [`runs/rescue-benchmark-pilot/rescue`](../../runs/rescue-benchmark-pilot/rescue).

## Where to inspect full historical traces

- [Primary generated-domain training traces](../../runs-fast/generated-domain-20261015/primary-256-gpu/rescue/train/readable_traces.md)
- [Primary generated-domain matched inference traces](../../runs-fast/generated-domain-20261015/primary-256-gpu/rescue/matched-five-diagnostic/readable_traces.md)
- [Repaired 256-token training traces](../../runs-fast/generated-domain-20261015/repaired-256-gpu/rescue/train/readable_traces.md)
- [Repaired 256-token held-out traces](../../runs-fast/generated-domain-20261015/repaired-256-gpu/rescue/matched-heldout-retry/readable_traces.md)
- [Repaired 512-token training traces](../../runs-fast/generated-domain-20261015/repaired-512-gpu/rescue/train/readable_traces.md)
- [Repaired 512-token held-out traces](../../runs-fast/generated-domain-20261015/repaired-512-gpu/rescue/matched-heldout-retry/readable_traces.md)
- [Separate timeout diagnostic trace](../../runs-fast/generated-domain-20261015/repaired-512-timeout-diagnostic/rescue/timeout-diagnostic/readable_traces.md)

Future training and `evaluate_rescue` runs write the same plain-English live
output directly to `run.log`; this document is the one-time retrospective bridge
for runs completed before that feature existed.
