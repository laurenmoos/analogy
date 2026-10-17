# Single Runtime Analogy Fallback Design

## Intent

There is one proof-search runtime. Conventional reasoning owns the frontier, forward and backward
planning, tactic history, failed-tactic memory, budgets, backtracking, and Lean interaction.
Analogy is an optional fallback that participates only after the conventional step is observably
stuck. The sole behavior switch is the boolean `analogy.enabled` value, with a CLI override for the
same value.

## Runtime flow

For every popped proof state, the shared controller:

1. asks the conventional tactic ranker for candidates;
2. verifies those candidates and updates the shared frontier;
3. evaluates stuckness from those conventional attempts;
4. if not stuck, continues normal search;
5. if stuck and `analogy.enabled` is false, performs no fallback and continues normal
   failure/backtracking;
6. if stuck and `analogy.enabled` is true, asks the analogy fallback for one analogy-guided tactic;
7. verifies that tactic through the same verifier, classifies it through the same progress policy,
   and returns any successor to the same frontier.

An analogy is scoped one-to-one to this fallback reasoning step. It does not own a route, cursor,
stack, frontier, recovery controller, or continuation loop. The next stuck proof state receives a
new analogy. A rejected analogy-guided tactic returns control to the conventional controller.

## One flag

`AppConfig.analogy.enabled` is the single persisted switch. The `solve` CLI exposes mutually
exclusive `--analogy` and `--no-analogy` overrides which replace only that value in the immutable
loaded config. There is no `mode`, condition string, direct-rescue branch, prompted arm, shuffled
arm, selected-route arm, or implicit optional-runner switch that can independently change runtime
behavior.

The runtime constructs the same controller in both modes. The analogy fallback dependency is
present but is invoked only when the boolean is true and conventional reasoning is stuck. Tests
must prove that a disabled run makes zero analogy model calls.

## Analogy fallback contract

The fallback receives the current proof state, fixed goal, conventional candidate pool, and Lean
results from the failed conventional step. It generates one structured analogy response whose
reasoning yields exactly one Lean tactic candidate. Schema repair may retry generation within the
configured model-call budget, but it may not submit multiple tactics or retain a route. Lean gets at
most one analogy-guided tactic attempt for that stuckness event.

The fallback returns the existing typed rescue outcome so search, trace rendering, training data,
and reward code continue to observe an analogy rollout without acquiring separate control flow.
The terminology in public records becomes `analogy_enabled: bool`; old condition names are not
produced by new runs.

`analogy.max_model_calls` is a per-stuck-state schema-generation/repair budget. A stuck state may
consume several invalid or repair generations, but at most one valid analogy-guided tactic is
submitted to Lean. Reaching a later stuck state starts a fresh bounded fallback event with a stable
state-derived seed namespace.

The per-problem `analogy_fallback_count` counts analogy-guided tactics actually submitted to Lean,
not fallback invocations or invalid generation attempts. Generation attempts and invalid outputs
remain visible in typed rollout traces and model-usage accounting.

## Evaluation

A comparison is two invocations of the same runtime with the same problems, seeds, conventional
budgets, and model configuration, changing only `analogy.enabled`. The comparison layer may pair
the resulting records, but it cannot implement proof search, generate alternative control tactics,
or replay a frozen analogy route.

Activation measurements remain observational diagnostics attached to an analogy fallback event.
They may describe displacement, relevance, or causal effect, but they do not introduce a separate
execution arm or batch selection barrier. Cross-problem collection/freeze/route machinery is
retired from the active runtime path and preserved only as immutable historical artifacts.

## Pruning

Remove active code and prompts for direct rescue, prompted analogy, shuffled analogy, frozen tactic
plans, route cursors, condition-specific slates, top-ups, and separate continuation/recovery
controllers. Remove paired direct-versus-analogy execution from `solve_problems`; a solve call runs
once with the resolved boolean. Replace paired runtime result/report types with one run type carrying
`analogy_enabled` and per-problem analogy fallback counts.

The standalone activation-evaluation command is retired rather than maintained as a second proof
runtime. Historical artifact readers and files remain readable only where doing so requires no
runtime branch; no new artifact may emit the old condition values.

Historical artifact files remain immutable on disk, but compatibility with the activation-specific
Python reader/config/report API is not required. That stack is removed with the retired command.
Results that still matter are Markdown/JSON evidence in their existing run directories.

Training keeps the shared `run_episode` execution path and the typed `RescueRollout` reward/data
interface. Alternate executable training paths that perform their own analogy loops, routes,
shuffled controls, continuation controllers, or condition dispatch are removed or disconnected.
Offline record transforms and reward utilities may remain when they consume the shared rollout
without controlling proof search.

## Safety and invariants

- Analogy is never called before conventional stuckness.
- Disabling analogy changes no conventional seed, width, budget, frontier ordering, or verifier path.
- An analogy event can submit at most one tactic to Lean.
- All accepted analogy successors pass the same novelty/progress checks as conventional successors.
- Cancellation and global expansion limits apply identically in both modes.
- There is no automatic fallback from invalid analogy output to a direct-rescue prompt.
- Existing historical run directories are never rewritten.

## Validation

Tests cover disabled zero-call behavior, enabled non-stuck zero-call behavior, enabled stuck single
analogy tactic behavior, rejection/backtracking, accepted-successor frontier reuse, completion,
cancellation, CLI override, deterministic on/off pairing, and absence of old runtime condition paths.
