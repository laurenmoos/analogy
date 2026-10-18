# SAT-Guided Analogy Restart Design

## Intent

Analogy must never be evaluated on a proof state that is propositionally false. This entire
mechanism is a stuck-search recovery path: while ordinary search is making forward progress, there
is no SAT check, semantic backtracking, or analogy call. Only after ordinary search becomes stuck
does a deterministic SAT check decide where analogy starts, not whether analogy runs. This keeps
analogy measurements about tactic quality rather than asking the model to solve an impossible
branch.

## Required behavior

The controller first runs its existing ordinary search unchanged. A rejected tactic, an open
successor, or any other individual event does not by itself trigger this mechanism. The trigger is
the controller's existing stuck condition: it cannot make another ordinary forward move under the
configured search policy and budget from the state/path being recovered.

Only at that point, for the stuck ordinary state, the controller performs a semantic check on each
active Lean goal. A goal is valid exactly when the conjunction of its local propositional
hypotheses and the negation of its target is unsatisfiable. A multi-goal proof state is valid only
when every active goal is valid.

If the stuck state is valid, analogy starts from that state. Its guidance says that no
countermodel exists and asks for the next move.

If the stuck state is invalid, the controller follows `SearchNode.parent_state_id` toward the root
until it finds the nearest valid ancestor. Analogy starts from that ancestor. Its guidance names
the abandoned tactic/path edge and includes the countermodel from the invalid descendant, for
example: `G=false`; the prompt asks for a different plan from the valid restart state.

When analogy is enabled, it fires after every stuck expansion once a valid rescue origin has been
resolved. SAT never suppresses analogy. When analogy is disabled, the same semantic check and trace
record are produced, but no analogy generation occurs and the ordinary frontier is unchanged.

## Propositional checker

Add a deterministic, dependency-free checker for the benchmark's propositional Lean fragment:
parentheses, `¬`, `∧`, `∨`, `→`, and `↔`. Reuse the repository's conservative
`proposition_root` parsing rules so quoted identifiers, literals, and nested syntax are not split
incorrectly. Unsupported/opaque subexpressions are Boolean atoms; sort declarations such as
`P : Prop` introduce atoms but are not assumptions. Local proof hypotheses are assumptions.

The checker evaluates all valuations for the distinct atoms in the state and returns either
`valid` or the first deterministic countermodel in lexicographic atom order. The initial target
corpus is intentionally small enough for exhaustive propositional evaluation. A configurable SAT
dependency and analogy-model-based truth judgment are out of scope.

If the proof state cannot be parsed into active goals and local declarations, fail closed with an
explicit semantic-check error. Do not silently run analogy on an unverified state.

## Search integration

The episode keeps indexed nodes and ordinary expansion outcomes by state ID. It does not request a
semantic assessment when an ordinary tactic is available or the ordinary search can otherwise
continue. Once the existing stuck condition fires, semantic assessments are cached by state ID so
walking ancestors does not repeat checks. Resolving a false stuck state returns three things: the
nearest valid `SearchNode`, that node's earlier `ExpansionOutcome`, and countermodel guidance
describing why the descendant branch was abandoned.

The analogy evaluator is anchored to the resolved rescue node, so an accepted analogy tactic is
verified from the valid restart state and its successor returns to the same shared frontier. The
ordinary expansion budget, breadth, ranking, and frontier ordering remain unchanged in both arms.
Analogy's existing malformed/duplicate correction ceiling remains unchanged and still yields at
most one valid submitted tactic per rescue event.

## Prompt behavior

Add one compact `semantic_guidance` field to the analogy prompt:

- Valid current state: `SAT: no countermodel; continue from this state.`
- Invalid descendant: `SAT restart: <edge tactic> led to a countermodel (<assignments>); choose a
  different plan from this last valid state.`

The field is deterministic and bounded. It does not add another model call.

## Records and measurement

Persist one semantic assessment per stuck event with:

- stuck/trigger state ID;
- `valid` or `countermodel` result;
- deterministic atom assignments when invalid;
- selected rescue state ID;
- abandoned edge tactics between the rescue origin and trigger state.

Reports show the SAT result and rescue origin before the associated analogy rollout. Analogy
generation/submission counts retain their current meanings. A rejected analogy tactic counts
against analogy only because it was attempted from a SAT-validated state.

## Non-goals

- Analogy refinement after a valid tactic is rejected.
- Additional search steps or a larger budget for the analogy arm.
- Pruning or reordering the ordinary frontier based on SAT.
- Running SAT proactively, after each tactic, or anywhere before ordinary search is stuck.
- First-order, arithmetic, or quantified satisfiability.
- Replacing Lean as the final tactic/proof judge.

## Acceptance criteria

1. A valid stuck state invokes analogy on itself with no-countermodel guidance.
2. A false stuck state invokes analogy on its nearest valid ancestor with countermodel guidance.
3. A multi-level false path walks back through every false ancestor to the last valid node.
4. Both arms retain identical ordinary search configuration and SAT tracing.
5. No analogy call is made on a state that lacks a successful semantic validation.
6. Per-problem JSON and Markdown reports make trigger state, SAT result, countermodel, and rescue
   state explicit.
7. A run that keeps making ordinary progress performs no SAT checks, semantic backtracking, or
   analogy calls; an individual rejected tactic does not activate stuck recovery.
