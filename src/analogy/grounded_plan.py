"""Typed, exact-state grounding records, never a certificate of Lean correctness."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .analogy_transfer import (
    ProofReasoningTransfer, TransferOutputError, _exact_object, _parse_object,
)
from .grounding import GroundingOperator
from .proof_inventory import InventoryError, LocalProofInventory


GROUNDED_PLAN_SCHEMA_VERSION = "grounded-plan-v1"


class GroundedPlanError(TransferOutputError):
    """A plan does not match the strict schema or its supplied verified state."""


def _text(value: object, name: str) -> str:
    # Proposition whitespace belongs to the verified inventory; never normalize it.
    if not isinstance(value, str) or not value.strip() or len(value) > 16384:
        raise GroundedPlanError(f"{name} must be nonblank bounded text")
    return value


def _id(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) > 128 or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_.:-]*", value
    ):
        raise GroundedPlanError(f"{name} must be a canonical identifier")
    return value


def _sequence(value: object, name: str, *, maximum: int = 64, minimum: int = 0) -> tuple:
    if not isinstance(value, (tuple, list)) or not minimum <= len(value) <= maximum:
        raise GroundedPlanError(f"{name} must be a bounded sequence with {minimum} to {maximum} entries")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class GroundedRoleBinding:
    binding_id: str
    proof_role: str
    inventory_id: str
    proposition: str
    source_role: str | None
    justification: str

    def __post_init__(self) -> None:
        for name in ("binding_id", "inventory_id"):
            _id(getattr(self, name), name)
        for name in ("proof_role", "proposition", "justification"):
            _text(getattr(self, name), name)
        if self.source_role is not None:
            _text(self.source_role, "source_role")


@dataclass(frozen=True, slots=True)
class GroundedInferenceStep:
    step_id: str
    operator: GroundingOperator
    inputs: tuple[str, ...]
    input_propositions: tuple[str, ...]
    output_proposition: str
    transfer_step_index: int
    closes_subgoal: bool

    def __post_init__(self) -> None:
        _id(self.step_id, "step_id")
        if not isinstance(self.operator, GroundingOperator):
            raise GroundedPlanError("operator must be a GroundingOperator")
        inputs = _sequence(self.inputs, "inputs", maximum=16)
        propositions = _sequence(self.input_propositions, "input_propositions", maximum=16)
        for item in inputs:
            _id(item, "input reference")
        if len(set(inputs)) != len(inputs):
            raise GroundedPlanError("input references must be unique")
        for item in propositions:
            _text(item, "input proposition")
        if len(inputs) != len(propositions):
            raise GroundedPlanError("each input must have an exact input proposition")
        _text(self.output_proposition, "output proposition")
        if (isinstance(self.transfer_step_index, bool)
                or not isinstance(self.transfer_step_index, int) or self.transfer_step_index < 0):
            raise GroundedPlanError("transfer_step_index must be a non-negative integer")
        if not isinstance(self.closes_subgoal, bool):
            raise GroundedPlanError("closes_subgoal must be a boolean")
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "input_propositions", propositions)


@dataclass(frozen=True, slots=True)
class GroundedTacticPlan:
    plan_id: str
    motivating_transfer_id: str
    state_fingerprint: str
    bindings: tuple[GroundedRoleBinding, ...]
    steps: tuple[GroundedInferenceStep, ...]
    expected_next_subgoal: str
    completion_expected: bool
    surface_hints: tuple[str, ...]

    def __post_init__(self) -> None:
        _id(self.plan_id, "plan_id")
        _id(self.motivating_transfer_id, "motivating_transfer_id")
        _text(self.state_fingerprint, "state_fingerprint")
        _text(self.expected_next_subgoal, "expected next subgoal")
        if not isinstance(self.completion_expected, bool):
            raise GroundedPlanError("completion_expected must be a boolean")
        for name, kind, minimum in (("bindings", GroundedRoleBinding, 0),
                                    ("steps", GroundedInferenceStep, 1)):
            values = _sequence(getattr(self, name), name, minimum=minimum)
            if any(not isinstance(value, kind) for value in values):
                raise GroundedPlanError(f"{name} must contain {kind.__name__} records")
            object.__setattr__(self, name, values)
        hints = _sequence(self.surface_hints, "surface_hints", maximum=16)
        for hint in hints:
            _text(hint, "surface hint")
        object.__setattr__(self, "surface_hints", hints)


def _array(value: object, name: str) -> list:
    if not isinstance(value, list):
        raise GroundedPlanError(f"{name} must be a JSON array")
    return value


def _validate_grounding(
    plan: GroundedTacticPlan, inventory: LocalProofInventory, transfer: ProofReasoningTransfer,
) -> None:
    if plan.state_fingerprint != inventory.state_fingerprint:
        raise GroundedPlanError("state_fingerprint must cite the current verified state")
    if transfer.active_subgoal != " ".join(inventory.active_subgoal.split()):
        # Transfer's historical schema canonicalizes prose whitespace; inventory does not.
        raise GroundedPlanError("transfer must describe the current active subgoal")
    if plan.motivating_transfer_id != transfer.transfer_id:
        raise GroundedPlanError("motivating_transfer_id must cite the supplied transfer")
    if plan.completion_expected != transfer.completion_expected:
        raise GroundedPlanError("completion expectation must agree with the supplied transfer")
    if plan.expected_next_subgoal != transfer.predicted_next_subgoal:
        raise GroundedPlanError("expected next subgoal must exactly match the supplied transfer")

    references: dict[str, str] = {}
    roles: set[str] = set()
    for binding in plan.bindings:
        if binding.binding_id in references or binding.proof_role in roles:
            raise GroundedPlanError("binding IDs and exclusive proof roles must be unique")
        try:
            entry = inventory.resolve(binding.inventory_id)
        except InventoryError as error:
            raise GroundedPlanError(str(error)) from error
        if binding.proposition != entry.proposition:
            raise GroundedPlanError("binding proposition must exactly equal its inventory proposition")
        references[binding.binding_id] = binding.proposition
        roles.add(binding.proof_role)
    if not set(transfer.premises) <= roles:
        raise GroundedPlanError("every consumed premise must have an exact proof-role binding")

    for step in plan.steps:
        if step.step_id in references:
            raise GroundedPlanError("binding and step IDs must be globally unique")
        if step.transfer_step_index >= len(transfer.reasoning_steps):
            raise GroundedPlanError("transfer_step_index must cite an existing transfer reasoning step")
        for reference, proposition in zip(step.inputs, step.input_propositions):
            if reference not in references:
                raise GroundedPlanError("inputs must cite a binding or a prior step, never a future step or cycle")
            if proposition != references[reference]:
                raise GroundedPlanError("input proposition must exactly equal its referenced proposition")
        references[step.step_id] = step.output_proposition

    # Coverage must contribute to the final result, not merely an unused side branch.
    needed = {plan.steps[-1].step_id}
    for step in reversed(plan.steps):
        if step.step_id in needed:
            needed.update(step.inputs)
    used_roles = {binding.proof_role for binding in plan.bindings if binding.binding_id in needed}
    if not set(transfer.premises) <= used_roles:
        raise GroundedPlanError("every consumed premise must contribute to the final inference chain")
    for step in plan.steps:
        if step.operator is GroundingOperator.EXACT_LOCAL and (
            len(step.inputs) != 1 or step.output_proposition != step.input_propositions[0]
        ):
            raise GroundedPlanError("exact_local must preserve its single input proposition")
    final = plan.steps[-1]
    if final.closes_subgoal != plan.completion_expected:
        raise GroundedPlanError("final closes_subgoal must agree with completion expectation")
    output = inventory.active_subgoal if plan.completion_expected else plan.expected_next_subgoal
    if final.output_proposition != output:
        raise GroundedPlanError("final output proposition must equal the active goal or claimed next subgoal")


def parse_grounded_tactic_plan(
    raw: str, inventory: LocalProofInventory, transfer: ProofReasoningTransfer, plan_id: str,
) -> GroundedTacticPlan:
    """Reject invalid grounding before compilation; Lean alone verifies the reasoning.

    Transfer premises are exact proof-role labels. Step indices are zero-based.
    Completion uses the transfer's next-subgoal description and the active goal as
    the final step's output; open transitions use the exact successor proposition.
    """
    try:
        payload = dict(_parse_object(raw, GroundedTacticPlan, ("plan_id",)))
        payload["bindings"] = tuple(GroundedRoleBinding(**_exact_object(item, GroundedRoleBinding))
                                    for item in _array(payload["bindings"], "bindings"))
        steps = []
        for item in _array(payload["steps"], "steps"):
            data = dict(_exact_object(item, GroundedInferenceStep))
            data["operator"] = GroundingOperator(data["operator"])
            data["inputs"] = _array(data["inputs"], "inputs")
            data["input_propositions"] = _array(data["input_propositions"], "input_propositions")
            steps.append(GroundedInferenceStep(**data))
        payload["steps"] = tuple(steps)
        payload["surface_hints"] = _array(payload["surface_hints"], "surface_hints")
        plan = GroundedTacticPlan(plan_id=plan_id, **payload)
        _validate_grounding(plan, inventory, transfer)
        return plan
    except GroundedPlanError:
        raise
    except (TransferOutputError, TypeError, ValueError) as error:
        raise GroundedPlanError(str(error)) from error


__all__ = ["GROUNDED_PLAN_SCHEMA_VERSION", "GroundedPlanError", "GroundingOperator",
           "GroundedRoleBinding", "GroundedInferenceStep", "GroundedTacticPlan",
           "parse_grounded_tactic_plan"]
