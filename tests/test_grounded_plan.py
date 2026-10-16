"""Strict state-bound plans: model text never becomes executable syntax."""

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from analogy.analogy_transfer import ProofReasoningTransfer
from analogy.grounded_plan import GroundedPlanError, GroundingOperator, parse_grounded_tactic_plan
from analogy.proof_inventory import build_local_inventory
from analogy.proof_views import ProofStructure
from analogy.records import SymbolicState


def inventory(expression="h : C\n⊢ C"):
    return build_local_inventory(
        SymbolicState("state", expression, "lean4"),
        ProofStructure(1, '{"root":"atom"}', ('{"root":"atom"}',)), fixed_target="C")


def transfer():
    return ProofReasoningTransfer("transfer-2", "alignment-2", "C", ("matching_hypothesis",),
                                  ("Use the matching premise.",), "No remaining goals.", True)


def valid_plan_payload(inv=None):
    inv = inv or inventory()
    return {
        "motivating_transfer_id": "transfer-2", "state_fingerprint": inv.state_fingerprint,
        "bindings": [{"binding_id": "binding-0", "proof_role": "matching_hypothesis",
                      "inventory_id": "local:0", "proposition": "C", "source_role": None,
                      "justification": "This is the required premise."}],
        "steps": [{"step_id": "step-0", "operator": "exact_local", "inputs": ["binding-0"],
                   "input_propositions": ["C"], "output_proposition": "C",
                   "transfer_step_index": 0, "closes_subgoal": True}],
        "expected_next_subgoal": "No remaining goals.", "completion_expected": True,
        "surface_hints": [],
    }


def parse(payload, inv=None, reasoning=None):
    return parse_grounded_tactic_plan(json.dumps(payload), inv or inventory(), reasoning or transfer(),
                                     plan_id="plan-1")


def test_plan_binds_consumed_premise_to_exact_inventory_entry_and_is_immutable():
    plan = parse(valid_plan_payload())
    assert plan.plan_id == "plan-1"
    assert plan.bindings[0].inventory_id == "local:0"
    assert plan.bindings[0].proposition == "C"
    assert plan.steps[0].operator is GroundingOperator.EXACT_LOCAL
    assert plan.steps[0].inputs == ("binding-0",)
    with pytest.raises(FrozenInstanceError):
        plan.plan_id = "different"


@pytest.mark.parametrize("field,value,diagnostic", [
    ("state_fingerprint", "old-state", "current verified state"),
    ("motivating_transfer_id", "other", "supplied transfer"),
    ("bindings", [], "consumed premise"),
    ("steps", [], "steps"),
    ("completion_expected", False, "completion"),
    ("expected_next_subgoal", "another goal", "next subgoal"),
])
def test_plan_rejects_stale_or_incompatible_claims(field, value, diagnostic):
    payload = valid_plan_payload()
    payload[field] = value
    with pytest.raises(GroundedPlanError, match=diagnostic):
        parse(payload)


def test_same_printed_identifier_does_not_allow_a_plan_from_another_state():
    with pytest.raises(GroundedPlanError, match="current verified state"):
        parse(valid_plan_payload(), inventory("h : D\n⊢ C"))


@pytest.mark.parametrize("collection,field,value", [
    ("bindings", "inventory_id", "local:999"),
    ("bindings", "inventory_id", " local:0 "),
    ("bindings", "proposition", " C "),
    ("bindings", "binding_id", " binding-0 "),
    ("bindings", "source_role", 1),
    ("bindings", "justification", ""),
    ("steps", "inputs", ["step-0"]),
    ("steps", "inputs", ["step-1"]),
    ("steps", "inputs", ["binding-0", "binding-0"]),
    ("steps", "input_propositions", []),
    ("steps", "input_propositions", ["D"]),
    ("steps", "output_proposition", "D"),
    ("steps", "operator", "raw_lean"),
    ("steps", "transfer_step_index", True),
    ("steps", "transfer_step_index", 1),
    ("steps", "closes_subgoal", False),
    ("steps", "closes_subgoal", "true"),
])
def test_plan_rejects_unknown_noncanonical_or_inconsistent_references(collection, field, value):
    payload = valid_plan_payload()
    payload[collection][0][field] = value
    with pytest.raises(GroundedPlanError):
        parse(payload)


@pytest.mark.parametrize("location", ["root", "bindings", "steps"])
def test_plan_requires_exact_keys_at_every_level(location):
    payload = valid_plan_payload()
    target = payload if location == "root" else payload[location][0]
    target["unknown"] = "extra"
    with pytest.raises(GroundedPlanError, match="keys"):
        parse(payload)


@pytest.mark.parametrize("raw", ["[]", "not json", '{"state_fingerprint": NaN}',
                                '{"steps": [], "steps": []}'])
def test_plan_rejects_non_strict_json(raw):
    with pytest.raises(GroundedPlanError):
        parse_grounded_tactic_plan(raw, inventory(), transfer(), plan_id="plan-1")


def test_model_cannot_choose_caller_owned_plan_id():
    payload = valid_plan_payload()
    payload["plan_id"] = "model-owned"
    with pytest.raises(GroundedPlanError, match="keys"):
        parse(payload)


@pytest.mark.parametrize("collection", ["bindings", "steps"])
def test_plan_rejects_duplicate_ids(collection):
    payload = valid_plan_payload()
    payload[collection].append(dict(payload[collection][0]))
    with pytest.raises(GroundedPlanError, match="unique"):
        parse(payload)


def test_binding_a_consumed_premise_without_using_it_is_not_coverage():
    payload = valid_plan_payload()
    payload["steps"][0]["inputs"] = []
    payload["steps"][0]["input_propositions"] = []
    with pytest.raises(GroundedPlanError, match="consumed premise"):
        parse(payload)


def test_unicode_and_shadowed_names_are_grounded_only_by_inventory_id():
    inv = inventory("h✝ : D\nh : C\n«λ local» : C\n⊢ C")
    payload = valid_plan_payload(inv)
    payload["bindings"][0]["inventory_id"] = "local:2"
    assert parse(payload, inv).bindings[0].inventory_id == "local:2"
    payload["bindings"][0]["inventory_id"] = "local:0"
    with pytest.raises(GroundedPlanError, match="proposition"):
        parse(payload, inv)


def test_steps_may_reference_only_prior_steps_with_exact_propositions():
    payload = valid_plan_payload()
    payload["steps"].append({**payload["steps"][0], "step_id": "step-1", "operator": "compose",
                            "inputs": ["step-0"]})
    plan = parse(payload)
    assert plan.steps[1].inputs == ("step-0",)


def test_open_successor_must_match_both_transfer_and_final_step():
    inv = inventory("h : C\n⊢ C → D")
    reasoning = replace(transfer(), active_subgoal="C → D", premises=(),
                        predicted_next_subgoal="D", completion_expected=False)
    payload = valid_plan_payload(inv)
    payload.update(bindings=[], completion_expected=False, expected_next_subgoal="D")
    payload["steps"][0].update(operator="introduce", inputs=[], input_propositions=[],
                               output_proposition="D", closes_subgoal=False)
    assert parse(payload, inv, reasoning).expected_next_subgoal == "D"
    payload["steps"][0]["output_proposition"] = "C"
    with pytest.raises(GroundedPlanError, match="output proposition"):
        parse(payload, inv, reasoning)


def test_transfer_must_describe_current_active_subgoal():
    with pytest.raises(GroundedPlanError, match="active subgoal"):
        parse(valid_plan_payload(), reasoning=replace(transfer(), active_subgoal="D"))


def test_consumed_premise_in_a_disconnected_step_is_not_coverage():
    payload = valid_plan_payload()
    payload["steps"].append({**payload["steps"][0], "step_id": "step-1", "operator": "introduce",
                            "inputs": [], "input_propositions": []})
    with pytest.raises(GroundedPlanError, match="consumed premise"):
        parse(payload)


def test_exact_local_cannot_change_an_intermediate_proposition():
    payload = valid_plan_payload()
    payload["steps"][0]["output_proposition"] = "D"
    payload["steps"].append({**payload["steps"][0], "step_id": "step-1", "operator": "compose",
                            "inputs": ["step-0"], "input_propositions": ["D"],
                            "output_proposition": "C"})
    with pytest.raises(GroundedPlanError, match="exact_local"):
        parse(payload)


@pytest.mark.parametrize("field,value", [("surface_hints", ["hint"] * 17),
                                        ("surface_hints", ["x" * 16385])])
def test_plan_bounds_model_generated_diagnostics(field, value):
    payload = valid_plan_payload()
    payload[field] = value
    with pytest.raises(GroundedPlanError, match="bounded"):
        parse(payload)
