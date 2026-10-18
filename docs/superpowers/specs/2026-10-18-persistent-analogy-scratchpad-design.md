# Persistent Analogy Scratchpad Design

## Intent

Give each proof-search episode one persistent analogy that behaves as external scratchpad state. The analogy is created when the proof begins, remains unchanged while ordinary search makes progress, is revised only when search is stuck, and is abandoned and rebuilt when semantic checking finds a countermodel.

This design supersedes the prior one-shot analogy lifecycle in `2026-10-17-single-runtime-analogy-fallback-design.md`. The shared Lean frontier and verifier remain authoritative; only analogy state ownership changes.

## Analogy state

An `AnalogyState` contains:

- a concrete source domain;
- an explicit mapping from proof propositions to objects or relations in that domain;
- a numbered story whose steps suggest Lean tactics;
- a bounded list of abandoned domain names.

The canonical rendering is one tagged block:

```text
<analogy>
domain: ...
mapping:
- ...
steps:
1. ...
abandoned domains:
- ...
</analogy>
```

The parser accepts exactly one block and validates that all four sections are present. Generated tactic text is parsed separately after the block is removed.

## Lifecycle and ownership

One mutable `AnalogyScratchpad` is created per problem and shared by the ordinary tactic ranker and stuck-state fallback.

At the first model call, the scratchpad has no current analogy. The prompt asks for a new domain, mapping, story, and tactic. The harness extracts and stores the block before parsing the tactic payload. Later ordinary model calls receive the current block between the system prompt and ordinary proof/search history, and their outputs may repeat the unchanged block; the harness removes it before parsing and rejects unrequested changes.

When search becomes stuck, the fallback asks the model to choose one update action:

1. `revise_steps`: preserve the domain and mapping exactly and replace only the numbered story;
2. `change_domain`: add the old domain to the bounded abandoned list, then replace domain, mapping, and story.

Semantic `COUNTERMODEL` recovery forces `change_domain`. Other stuck states permit either action. Accepted updates replace the scratchpad atomically. Ordinary progress never mutates it.

The scratchpad records a bounded tactic/Lean-result history independently of the analogy block. Every prompt is assembled in this order: system message, current analogy block as a dedicated system message when present, then the proof-state user prompt plus bounded history. The history never contains `<analogy>` text.

## Domain search

Creating or changing a domain is a global move. Within the existing `analogy.max_model_calls` budget, the fallback samples multiple domain/story proposals using distinct deterministic seeds. The first tactic from each valid story is submitted to Lean through the shared evaluator. The first proposal whose tactic produces usable progress is selected. If no tactic progresses but a proposal's predicted state matches Lean's observed result, that proposal is retained as the best calibrated analogy; otherwise the prior analogy remains unless a countermodel forced abandonment.

Revising steps is a local move and may retry within the same budget, but must preserve domain and mapping.

There is no learned or numeric analogy score. `search.top_k` continues to score ordinary Lean tactic candidates only. Analogy selection is based solely on Lean acceptance/progress, then prediction agreement.

## Prompt/output contracts

Ordinary generation returns the existing JSON tactic array plus one `<analogy>` block. On initial creation the block is required; once state exists, the model must repeat it unchanged so extraction is explicit while ordinary search cannot silently revise it.

Stuck-state generation returns one `<analogy>` block followed by strict JSON with exactly `action`, `predicted_state`, and singular `tactic`. The action must match the requested update mode. Parser or lifecycle violations consume generation budget but are never sent to Lean.

The harness strips analogy blocks before placing any assistant response into repair/history messages and before parsing JSON. Traces may persist the structured analogy state as explicit metadata, but tactic-message history contains no raw analogy block.

## Countermodels and abandoned domains

A semantic countermodel destroys the current domain/mapping/story before the next accepted update and forces a new domain. The prior domain is appended once to `abandoned_domains`. Prompts tell the model not to choose any listed domain. The list is bounded to the most recent eight domains and rendered one domain per line.

If forced rebuilding exhausts its budget without a valid new analogy, the scratchpad has no active analogy but retains the abandoned-domain list. Later stuck recovery continues to require `change_domain` until a replacement is accepted.

## Compatibility

- The analogy enable flag remains the sole feature gate.
- With analogy disabled, ordinary prompts and parsing remain unchanged.
- The same frontier, verifier, progress policy, semantic checker, cancellation, and expansion budget are used.
- Existing rollout records remain usable; their `analogy_trace` field stores the canonical block.
- `search.top_k` remains an ordinary tactic width and score normalization mechanism, not an analogy tournament.

## Validation

Tests cover block parsing/removal, history exclusion, initialization, ordinary immutability, step revision invariants, domain replacement and abandoned-domain tracking, countermodel-forced replacement, multi-domain Lean trials, prediction-match fallback, disabled behavior, and compatibility with existing tactic ranking and rescue traces.
