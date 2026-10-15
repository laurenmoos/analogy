# Surprise-driven analogy rescue smoke

Date: 2026-10-14

## Automated verification

- `PYTHONPATH=src .venv/bin/python -m pytest -q`: 507 passed, 1 skipped.
- `PYTHONPATH=src .venv/bin/python -m compileall -q src tests`: passed.
- `git diff --check`: passed.

## Deterministic model, real Lean

The smoke used `imp_intro_03`, a scripted deterministic model, the real Lean
4.29.1 subprocess verifier, the production structural extractor, the production
closed-loop coordinator, and the learned direction/radius policy class.

- The ordinary tactic failed, activating rescue.
- The first analogy card was invalid and succeeded on its one bounded repair.
- Lean accepted `intro h`, then accepted `constructor` from the returned proof
  state. This specifically exercises state-aware multi-iteration verification.
- Surprise was `(0.0, 0.0)` under the deterministic smoke encoder, so the loop
  stopped with `surprise_converged` after the configured patience of two.
- The rollout produced two gradient-bearing discounted samples.
- `config.json`, `generations.jsonl`, `rollouts.jsonl`,
  `analogy_iterations.jsonl`, `problems.jsonl`, `metrics.jsonl`, and
  `summary.json` were all nonempty under
  `/tmp/analogy-deterministic-smoke/run`.

A matched scripted comparison used the same theorem and configured budgets:

| condition | completion | Lean calls | rollouts | trainable samples | analogy iterations |
|---|---:|---:|---:|---:|---:|
| learned direct | no | 2 | 1 | 1 | 0 |
| analogy loop | no | 3 | 1 | 2 | 2 |

This is an integration result, not evidence that analogies improve solving.

## Frozen Qwen offline attempt

`Qwen/Qwen3-8B` loaded from the local cache with both Hugging Face and
Transformers in offline mode. On this host, Transformers placed parameters on
CPU/disk rather than an accelerator. Each of the three training problems and
the validation problem therefore hit the configured 60-second ordinary tactic
ranking timeout before rescue could begin. The bounded run still produced a
checkpoint and structured failure records under
`/tmp/analogy-smoke-runs/rescue/81275185ac4b4e969ca09fc1971c020d`.

No solve-rate or analogy-benefit claim can be drawn from that Qwen attempt. A
real matched quality benchmark needs an accelerator that can keep the model
resident and at least two compatible held-out stuck traces for the shuffled
condition.
