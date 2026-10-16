"""Render verified proof records as connected, human-readable state edges.

Accepts dataclasses or their JSONL dictionaries. No causal edges are inferred
from model prose, predicted transitions, or tactic text.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .progress_judge import persisted_stage_evidence


_replace = os.replace


def _record(value: Any) -> Any:
    if is_dataclass(value):
        return _record(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _record(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_record(item) for item in value]
    return value


def _goals(expression: str) -> list[str]:
    # Lean's goal marker is authoritative; case labels and hypotheses are not goals.
    goals = []
    for block in expression.split("⊢")[1:]:
        goals.append(block.split("\n\n", 1)[0].strip())
    return goals or [expression.strip()]


class _Trace:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.lines: list[str] = []
        self.states: dict[str, int] = {}
        self.shown: dict[str, str] = {}
        self.analogy_number = 0

    def number(self, state: dict) -> int:
        key = state["id"]
        if key not in self.states:
            self.states[key] = len(self.states)
        return self.states[key]

    def state(self, state: dict) -> None:
        number = self.number(state)
        if self.shown.get(state["id"]) == state["expression"]:
            self.lines.append(f"[State {number}] (continued)")
            return
        self.shown[state["id"]] = state["expression"]
        goals = _goals(state["expression"])
        if len(goals) == 1:
            self.lines.append(f"[State {number}] ACTIVE SUBGOAL: {goals[0]}")
        else:
            self.lines.append(f"[State {number}] ACTIVE SUBGOALS:")
            self.lines.extend(f"    {index}. {goal}" for index, goal in enumerate(goals, 1))
        self.lines.append("    LEAN STATE:")
        self.lines.extend(f"        {line}" for line in state["expression"].splitlines())

    def attempt(self, attempt: dict, before: dict, prefix: str, stage: str) -> None:
        result = attempt.get("result", attempt)
        number = self.number(before)
        self.lines.append(f"{prefix}TACTIC [State {number}]: {result['candidate']['source']} [{stage}]")
        stem = prefix.removesuffix("└─ ")
        self.lines.append(f"{stem}   └─ LEAN RESULT: {result['status']}")
        edge = stem + "      "
        status = result["status"]
        verification_status = result.get("verification_status")
        if verification_status is not None:
            self.lines.append(f"{edge}verification_status={verification_status}")
        if verification_status in {"timeout", "internal_error"}:
            self.lines.append(f"{edge}[INFRASTRUCTURE FAILURE: {verification_status}]")
        if status == "error":
            for diagnostic in result.get("diagnostics", ()):
                self.lines.extend(f"{edge}{line}" for line in diagnostic.splitlines())
            self.lines.append(f"{edge}↩ [State {number} unchanged]")
        elif status == "complete":
            self.lines.append(f"{edge}▼ [COMPLETE: theorem target proved] (no remaining subgoals)")
        elif attempt.get("discarded"):
            self.lines.append(f"{edge}▼ [Candidate state; not admitted]")
            for goal in _goals(result["state"]["expression"]):
                self.lines.append(f"{edge}  ACTIVE SUBGOAL: {goal}")
            self.lines.append(f"{edge}↩ [DISCARDED: {attempt['discarded']['reason']}; State {number} unchanged]")
        else:
            self.lines.append(f"{edge}▼")
            self.state(result["state"])

    def iteration(self, iteration: dict) -> None:
        record_kind, stages = persisted_stage_evidence(iteration)
        before = iteration["state_before"]
        self.state(before)
        self.analogy_number += 1
        self.lines.append(f"    ├─ Analogy {self.analogy_number} at State {self.number(before)}")
        source = iteration.get("source")
        hop = iteration.get("hop")
        if source:
            self.lines.append(f"    │  └─ SOURCE ANALOGY: {source['domain']}")
            for label, field in (("Before", "source_before"), ("After", "source_after")):
                self.lines.append(f"    │     {label}: {source[field]}")
            self.lines.extend(f"    │     Relation: {relation}" for relation in source.get("relation_chain", ()))
            self.lines.append(f"    │     Transformation: {source['transformation']['domain_neutral_summary']}")
            self.lines.append(f"    │     Rationale: {source['rationale']}")
            alignment = iteration.get("alignment")
            self.lines.append("    │     └─ ALIGNMENT")
            if alignment:
                self.lines.extend(f"    │        {left} ↔ {right}" for left, right in alignment["mapping"])
                for label, field in (("Invariant", "invariant"), ("Proof operation", "proof_operation"), ("Limitation", "limitation")):
                    self.lines.append(f"    │        {label}: {alignment[field]}")
            else:
                self.lines.append("    │        [not produced]")
            transfer = iteration.get("transfer")
            self.lines.append("    │        └─ TRANSFERRED PROOF REASONING")
            if transfer:
                self.lines.append(f"    │           Active subgoal: {transfer['active_subgoal']}")
                self.lines.extend(f"    │           {index}. {step}" for index, step in enumerate(transfer["reasoning_steps"], 1))
                self.lines.append(f"    │           Expected: {transfer['predicted_next_subgoal']}")
            else:
                self.lines.append("    │           [not produced]")
            prefix = "    │           └─ "
        elif hop:
            self.lines.append(f"    │  └─ LEGACY ANALOGY: {hop['domain']}")
            self.lines.append(f"    │     {hop['situation']}")
            self.lines.extend(f"    │     {left} ↔ {right}" for left, right in hop.get("mapping", ()))
            self.lines.append(f"    │     Reasoning: {hop['continuation']}")
            prefix = "    │     └─ "
        else:
            self.lines.extend([
                "    │  └─ SOURCE ANALOGY: not produced",
                "    │     └─ ALIGNMENT: not produced",
                "    │        └─ TRANSFERRED PROOF REASONING: not produced",
            ])
            prefix = "    │           └─ "
        if record_kind == "grounded_generated":
            self.grounding(iteration, stages)
            prefix = "    │                          └─ "
        if not iteration.get("attempts") and record_kind != "grounded_generated":
            self.lines.extend([f"{prefix}TACTIC: not verified",
                               "    │              └─ LEAN RESULT: not attempted"])
        for attempt in iteration.get("attempts", ()):
            stage = attempt.get("stage", "analogy")
            origin = iteration.get("finisher_state") if stage == "conventional_finisher" else before
            self.attempt(attempt, origin or before, prefix, stage)
        if iteration.get("failed_stage"):
            self.lines.append(f"    [FAILED STAGE: {iteration['failed_stage']}] {iteration.get('failure_diagnostic') or ''}".rstrip())
        decision = iteration.get("decision")
        if decision:
            self.lines.append(f"    [CONTROLLER: {decision['reason']}]")

    def grounding(self, iteration: dict, stages: dict) -> None:
        """Render persisted premises and proposals; only attempt() creates edges."""
        prefix = "    │           "
        inventory = iteration.get("inventory")
        self.lines.append(f"{prefix}└─ LOCAL PROOF INVENTORY [{stages['inventory']['status']}]")
        if inventory is not None:
            for name in ("state_fingerprint", "fixed_target", "active_subgoal"):
                self.lines.extend(f"{prefix}   {line}" for line in f"{name}: {inventory[name]}".splitlines())
            for entry in (*inventory.get("locals", ()), *inventory.get("target_entries", ())):
                self.lines.append(f"{prefix}   {entry['inventory_id']} → {entry.get('identifier') or entry['kind']} : {entry['proposition']}")
                self.lines.append(f"{prefix}     kind={entry['kind']}; shape={entry['structural_shape']}")

        def plan_sections(plan: dict | None, status: str) -> None:
            self.lines.append(f"{prefix}   └─ GROUNDED ROLE BINDINGS [{status}]")
            if plan is not None:
                self.lines.append(f"{prefix}      Plan: {plan['plan_id']}; transfer: {plan['motivating_transfer_id']}; state: {plan['state_fingerprint']}")
                for binding in plan.get("bindings", ()):
                    self.lines.append(f"{prefix}      {binding['binding_id']}: {binding['proof_role']} → {binding['inventory_id']} : {binding['proposition']}")
                    self.lines.append(f"{prefix}        Source role: {binding.get('source_role')}; {binding['justification']}")
            self.lines.append(f"{prefix}      └─ TYPED INFERENCE STEPS [{status}]")
            if plan is not None:
                for step in plan.get("steps", ()):
                    self.lines.append(f"{prefix}         {step['step_id']}: {step['operator']}({', '.join(step['inputs'])})")
                    self.lines.append(f"{prefix}           {step['input_propositions']} → {step['output_proposition']}; transfer step {step['transfer_step_index']}; closes={step['closes_subgoal']}")
                self.lines.append(f"{prefix}         Expected: {plan['expected_next_subgoal']}; completion={plan['completion_expected']}")

        def compilation(candidates: list, diagnostics: list, status: str) -> None:
            self.lines.append(f"{prefix}         └─ COMPILED TACTICS [{status}] (proposals, not verified edges)")
            for candidate in candidates:
                self.lines.append(f"{prefix}            {candidate['text']}")
                self.lines.append(f"{prefix}              steps={candidate['step_ids']}; bindings={candidate['binding_ids']}; expected={candidate['predicted_next_subgoal']}")
            for diagnostic in diagnostics:
                self.lines.append(f"{prefix}            {diagnostic['code']} [{diagnostic.get('step_id')}]: {diagnostic['message']}")

        self.lines.append(f"{prefix}   Versions: {iteration.get('grounded_plan_schema_version')}; {iteration.get('compiler_version')}")
        plan_sections(iteration.get("grounded_plan"), stages["grounding"]["status"])
        compilation(iteration.get("compiled_tactics", ()), iteration.get("compilation_diagnostics", ()), stages["compilation"]["status"])
        if iteration.get("grounding_generation_attempts") and iteration.get("grounded_plan") is None:
            self.lines.append(f"{prefix}   Grounding generation: {json.dumps(iteration['grounding_generation_attempts'], ensure_ascii=False)}")
        if iteration.get("grounding_repair_attempts") or iteration.get("repaired_grounded_plan"):
            self.lines.append(f"{prefix}   └─ GROUNDED REPAIR [{stages['repair']['status']}]")
            for attempt in iteration.get("attempts", ()):
                if attempt.get("stage") == "grounded_compiler":
                    result = attempt["result"]
                    self.lines.append(f"{prefix}      Original candidate: {result['candidate']['source']}; Lean: {result['status']}")
                    self.lines.extend(f"{prefix}        {message}" for message in result.get("diagnostics", ()))
            for generation in iteration.get("grounding_repair_attempts", ()):
                self.lines.append(f"{prefix}      Generation: {generation['status']}; seed={generation['seed']}; diagnostic={generation.get('diagnostic')}")
            plan_sections(iteration.get("repaired_grounded_plan"), stages["repair"]["status"])
            compilation(iteration.get("repaired_compiled_tactics", ()), iteration.get("repair_compilation_diagnostics", ()), stages["repair"]["status"])
        self.lines.append(f"{prefix}            └─ LEAN RESULTS" + ("" if iteration.get("attempts") else ": not attempted"))

    def render(self) -> str:
        problem = self.record["problem"]
        self.lines = [f"## {problem['id']}", "", f"THEOREM TARGET (fixed): {problem['target']}", "", "```text"]
        initial = self.record.get("initial_state")
        expansions = self.record.get("ordinary_expansions", ())
        rollouts = self.record.get("rollouts", ())
        if initial is None and expansions:
            initial = expansions[0]["node"]["state"]
        if initial is None:
            initial = next((iteration["state_before"] for rollout in rollouts
                            for iteration in (rollout.get("analogy_trace") or {}).get("iterations", ())), None)
        if initial:
            self.state(initial)
            if initial.get("context"):
                self.lines.append("    ACCEPTED PREFIX (intermediate states not recorded):")
                self.lines.extend(f"        {tactic}" for tactic in initial["context"])
        rendered_rollouts = set()

        def render_rollout(index: int, rollout: dict) -> None:
            rendered_rollouts.add(index)
            trace = rollout.get("analogy_trace")
            if trace:
                for iteration in trace["iterations"]:
                    self.iteration(iteration)
                self.lines.append(f"    [ANALOGY STOP: {trace['stop_reason']}]")
            else:
                origin = next((item["node"]["state"] for item in expansions
                               if item["node"]["state"]["id"] == rollout["state_id"]), initial)
                if origin is None or origin["id"] != rollout["state_id"]:
                    self.lines.append(f"    [Direct rescue at {rollout['state_id']}: source state not recorded]")
                    return
                self.state(origin)
                for attempt in rollout.get("attempts", ()):
                    self.attempt(attempt, origin, "    └─ ", "direct rescue")

        for expansion in expansions:
            before = expansion["node"]["state"]
            self.state(before)
            for result in expansion.get("attempts", ()):
                discarded = next((item for item in expansion.get("discarded", ()) if item["result"] == result), None)
                self.attempt({"result": result, "discarded": discarded}, before, "    └─ ", "ordinary search")
            for index, rollout in enumerate(rollouts):
                if rollout["state_id"] == before["id"]:
                    render_rollout(index, rollout)
        for index, rollout in enumerate(rollouts):
            if index not in rendered_rollouts:
                render_rollout(index, rollout)
        if self.record.get("termination"):
            self.lines.append(f"[SEARCH STOP: {self.record['termination']}]")
        self.lines.extend(["```", ""])
        # Use a fence longer than any source content, so diagnostic/model text
        # containing Markdown fences cannot escape the immutable target block.
        fence = "`" * max(3, max((len(run) for line in self.lines[5:-2]
                                  for run in re.findall(r'`+', line)), default=0) + 1)
        self.lines[4] = fence + "text"
        self.lines[-2] = fence
        return "\n".join(line.rstrip() for line in self.lines)


def render_theorem_trace(trace: Any) -> str:
    """Render a structured theorem trace (or its serialized dictionary)."""
    return _Trace(_record(trace)).render()


def write_readable_traces(run_directory: Path) -> None:
    """Replace only Markdown, after reading finalized machine artifacts.

    Rendering happens before opening a temporary file. A same-directory file
    is flushed/fsynced and atomically replaced; any interruption leaves the old
    Markdown and every machine artifact intact.
    """
    directory = Path(run_directory)
    rows = [json.loads(line) for line in (directory / "problems.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    rollouts = [json.loads(line) for line in (directory / "rollouts.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    sections = ["# Readable proof traces\n"]
    for row in sorted(rows, key=lambda item: (item["problem_id"], item.get("epoch", 0), item.get("phase", ""))):
        sections.append(f"Phase: {row.get('phase', 'unknown')}; epoch: {row.get('epoch', 0)}\n")
        if row.get("proof_trace"):
            trace = dict(row["proof_trace"])
            if "rollouts" not in trace:
                trace["rollouts"] = [rollout for rollout in rollouts
                    if (rollout.get("problem_id"), rollout.get("epoch"), rollout.get("phase")) ==
                       (row["problem_id"], row.get("epoch"), row.get("phase"))
                    and ("proof_path_id" not in trace or
                         rollout.get("proof_path_id") == trace["proof_path_id"])]
            sections.append(render_theorem_trace(trace))
        else:
            sections.append(f"## {row['problem_id']}\n\nStructured proof trace unavailable in this record.\n")
    content = "\n".join(sections)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=directory,
                                         prefix=".readable_traces-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _replace(temporary, directory / "readable_traces.md")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
