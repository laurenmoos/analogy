# Visible Solver Recipes Design

## Intent

Make the two proof-solving modes understandable by reading one small file. The orchestration file must show the actual high-level control flow; it must not hide the search loop behind `run_episode()` or fill the file with helper implementations.

## Required shape

`src/analogy/solver/run.py` contains exactly two function definitions:

- `run_vanilla(...)`
- `run_analogy_assisted(...)`

Both functions visibly own the problem loop and search loop. Their bodies read as recipes composed from imported operations.

The vanilla recipe visibly performs, in order:

1. initialize a proof;
2. start a search;
3. while the search has work, generate model-policy tactics, submit them to Lean, and update the search;
4. build and write a problem record;
5. after all problems, build and write a run record;
6. return the stored run record.

The analogy-assisted recipe visibly performs the same steps while additionally:

1. initializing one analogy scratchpad per problem;
2. generating ordinary tactics with the analogy-aware policy;
3. when search is stuck, updating analogy state, generating an analogy rescue, submitting that rescue to Lean, and updating search with the rescue result.

## Ownership

- `solver/run.py` owns only the two visible recipes. It contains no classes, rendering functions, persistence helpers, outcome converters, or private wrapper functions.
- `solver/episode.py` owns the mutable proof-search state machine and the operations that advance it. The monolithic `run_episode()` entry point is retired; its loop is expressed by the two recipes.
- `solver/model_policy.py` owns ordinary and analogy-aware model-policy tactic generation.
- `solver/proof/adapter.py` owns proof initialization and submission of ordinary tactics to Lean.
- `solver/analogy/state.py` owns scratchpad initialization and updates.
- `solver/analogy/rescue.py` owns analogy rescue generation and Lean submission behavior specific to rescue attempts.
- New focused top-level solver modules may own run/problem recording and trace rendering. Moving the existing helpers into one undifferentiated replacement file is not acceptable.
- The CLI selects `run_vanilla` or `run_analogy_assisted` directly; there is no third `solve_problems()` orchestration function.

The incremental search API uses typed tactic batches tied to one current search node so stale or mismatched Lean results cannot be applied. Semantic forced moves re-enter the same visible tactic-submission boundary; `update_search` must not make hidden Lean calls.

Analogy rescue is incremental across outer-loop iterations. Generation advances only until it yields one Lean-submittable proposal or terminates; submission applies that proposal once; `update_search` feeds the result back and leaves the search stuck when another bounded correction attempt remains. `update_analogy` prepares the scratchpad before rescue, while result-dependent feedback and commits occur when `update_search` consumes the rescue result.

## Behavioral constraints

- Preserve search results, model/Lean call ordering, semantic recovery, forced moves, analogy retry budgets, scratchpad behavior, checkpoint updates, JSON artifacts, Markdown reports, and CLI summaries.
- A failure for one problem remains a recorded problem outcome and does not abort later problems.
- Vanilla search creates no analogy scratchpad and performs no analogy rescue work.
- Analogy scratchpads are shared by the ordinary analogy-aware policy and rescue behavior within one problem, and isolated between problems.
- `SolveRunRecord` continues to describe stored run information; it does not perform proof computation.
- Python 3.11 remains supported.

## Anti-goals

- Do not create tiny forwarding wrappers solely to make `run.py` read well.
- Do not duplicate the search algorithm between `run.py` and `episode.py`.
- Do not keep `run_episode()` as a hidden copy of the real loop.
- Do not make hidden Lean calls from `update_search` or another bookkeeping operation.
- Do not put Lean engine mechanics or proof embeddings into solver orchestration.
