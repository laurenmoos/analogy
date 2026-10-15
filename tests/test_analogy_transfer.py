"""Strict boundaries between source situations, alignment, and proof reasoning."""

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from analogy.analogy_transfer import (
    Arity, InformationFlow, OperationClass, Permutation, TransferOutputError,
    derive_proof_need, parse_alignment, parse_proof_reasoning_transfer,
    parse_source_analogy, parse_transformation_record,
)
from analogy.proof_views import ProofStructure


def shape(root, *children):
    return {"root": root, "children": list(children)}


def structure(target="atom", *hypotheses):
    target = shape(target) if isinstance(target, str) else target
    return ProofStructure(1, json.dumps(target), tuple(json.dumps(h) for h in hypotheses))


def proof_need(active_subgoal="C"):
    return derive_proof_need(structure("atom", shape("target")), active_subgoal,
                             fixed_target="C → S")


def transformation_payload():
    return {
        "operation": "reuse", "input_arity": "one", "output_arity": "one",
        "permutation": "none", "information_flow": ["preserve"],
        "focus_before": "required item", "focus_after": "available item",
        "domain_neutral_summary": "Use an available item that meets the requirement.",
    }


def valid_source_payload():
    return {
        "domain": "warehouse logistics",
        "source_before": "A worker needs a spare wheel; a matching wheel is on the shelf.",
        "source_after": "The worker takes the matching wheel from the shelf.",
        "source_roles": ["requested wheel", "shelved wheel"],
        "relation_chain": ["The wheel on the shelf matches the request.",
                           "Taking the wheel fulfills the request."],
        "transformation": transformation_payload(),
        "rationale": "An existing matching resource fulfills the outstanding need.",
    }


def source_analogy():
    return parse_source_analogy(json.dumps(valid_source_payload()), analogy_id="analogy-1")


def valid_alignment_payload():
    return {
        "mapping": {"requested wheel": "active_subgoal", "shelved wheel": "matching_hypothesis"},
        "invariant": "The available resource meets the outstanding requirement.",
        "source_operation": "Take the matching wheel.",
        "proof_operation": "Use the matching premise.",
        "limitation": "The source includes physical movement.", "confidence": 0.75,
    }


def alignment():
    return parse_alignment(json.dumps(valid_alignment_payload()), source=source_analogy(),
                           need=proof_need(), alignment_id="alignment-1")


def valid_transfer_payload(active_subgoal="C"):
    return {
        "active_subgoal": active_subgoal, "premises": ["matching_hypothesis"],
        "reasoning_steps": ["The available premise establishes the active requirement."],
        "predicted_next_subgoal": "No remaining goals.", "completion_expected": True,
    }


def test_valid_records_preserve_caller_owned_links_and_immutable_tuples():
    source = source_analogy()
    aligned = alignment()
    transferred = parse_proof_reasoning_transfer(json.dumps(valid_transfer_payload()),
        need=proof_need(), alignment=aligned, transfer_id="transfer-1")
    assert source.transformation.operation is OperationClass.REUSE
    assert source.source_roles == ("requested wheel", "shelved wheel")
    assert aligned.source_analogy_id == "analogy-1"
    assert aligned.mapping == (("requested wheel", "active_subgoal"),
                               ("shelved wheel", "matching_hypothesis"))
    assert transferred.alignment_id == "alignment-1"
    assert transferred.reasoning_steps == ("The available premise establishes the active requirement.",)
    with pytest.raises(FrozenInstanceError):
        source.domain = "other"


@pytest.mark.parametrize("text", ["apply h1 to prove P", "⊢ P", "And.intro joins the pieces",
    "Reuse imp_intro_04", "The hypothesis establishes the proposition."])
def test_source_analogy_rejects_proof_paraphrases_and_lean_syntax(text):
    payload = valid_source_payload()
    payload["source_before"] = text
    with pytest.raises(TransferOutputError, match="source domain"):
        parse_source_analogy(json.dumps(payload), analogy_id="analogy-1")


@pytest.mark.parametrize("field", ["domain", "source_before", "source_after", "rationale",
                                  "source_roles", "relation_chain"])
def test_source_guard_covers_all_source_fields(field):
    payload = valid_source_payload()
    payload[field] = ["apply h1", "second"] if isinstance(payload[field], list) else "apply h1"
    with pytest.raises(TransferOutputError, match="source domain"):
        parse_source_analogy(json.dumps(payload), analogy_id="analogy-1")


@pytest.mark.parametrize("fragment", ["C", "C → S", "heated_water = steam"])
def test_source_analogy_rejects_supplied_proposition_fragments(fragment):
    payload = valid_source_payload()
    payload["source_before"] = f"The device operates when {fragment} is available."
    need = proof_need(fragment)
    with pytest.raises(TransferOutputError, match="source domain"):
        parse_source_analogy(json.dumps(payload), analogy_id="analogy-1", need=need)


def test_proposition_guard_respects_identifier_boundaries():
    payload = valid_source_payload()
    payload["source_before"] = "Cargo waits beside a matching wheel."
    parse_source_analogy(json.dumps(payload), analogy_id="analogy-1", need=proof_need("C"))


@pytest.mark.parametrize("count", [0, 1, 5])
def test_source_requires_two_to_four_relations(count):
    payload = valid_source_payload()
    payload["relation_chain"] = [f"Wheel relation {i}" for i in range(count)]
    with pytest.raises(TransferOutputError, match="relation_chain"):
        parse_source_analogy(json.dumps(payload), analogy_id="analogy-1")


@pytest.mark.parametrize("raw", ["{}", "[]", "null", "```json\n{}\n```", '{"domain":"one","domain":"two"}'])
def test_source_parser_rejects_invalid_json_schema(raw):
    with pytest.raises(TransferOutputError):
        parse_source_analogy(raw, analogy_id="analogy-1")


@pytest.mark.parametrize("flow", [[], ["preserve", "preserve"], ["add", "preserve"], ["invented"], "preserve"])
def test_transformation_requires_nonempty_unique_canonical_enum_order(flow):
    payload = transformation_payload()
    payload["information_flow"] = flow
    with pytest.raises(TransferOutputError):
        parse_transformation_record(json.dumps(payload))


def test_transformation_accepts_generic_logic_but_rejects_tactics():
    payload = transformation_payload()
    payload["domain_neutral_summary"] = "A conjunction retains both components."
    assert parse_transformation_record(json.dumps(payload)).information_flow == (InformationFlow.PRESERVE,)
    payload["focus_after"] = "exact h1"
    with pytest.raises(TransferOutputError, match="tactic"):
        parse_transformation_record(json.dumps(payload))


def test_alignment_requires_declared_roles_on_both_sides():
    payload = valid_alignment_payload()
    payload["mapping"] = {"unknown source": "unknown proof role", "shelved wheel": "matching_hypothesis"}
    with pytest.raises(TransferOutputError, match="declared roles"):
        parse_alignment(json.dumps(payload), source=source_analogy(), need=proof_need(), alignment_id="alignment-1")


@pytest.mark.parametrize("mapping", [
    {"requested wheel": "matching_hypothesis", "shelved wheel": "matching_hypothesis"},
    {"requested wheel": "active_subgoal", "shelved wheel": "active_subgoal"},
    {"requested wheel": "active_subgoal"},
])
def test_alignment_requires_active_subgoal_and_another_relevant_role(mapping):
    payload = valid_alignment_payload()
    payload["mapping"] = mapping
    with pytest.raises(TransferOutputError):
        parse_alignment(json.dumps(payload), source=source_analogy(), need=proof_need(), alignment_id="alignment-1")


@pytest.mark.parametrize("confidence", [True, "0.5", -0.1, 1.1, 10**400, float("nan"), float("inf")])
def test_alignment_confidence_is_a_finite_probability(confidence):
    payload = valid_alignment_payload()
    payload["confidence"] = confidence
    with pytest.raises(TransferOutputError):
        parse_alignment(json.dumps(payload), source=source_analogy(), need=proof_need(), alignment_id="alignment-1")


def test_transfer_must_target_active_subgoal_not_fixed_target():
    with pytest.raises(TransferOutputError, match="active subgoal"):
        parse_proof_reasoning_transfer(json.dumps(valid_transfer_payload("C → S")),
            need=proof_need(), alignment=alignment(), transfer_id="transfer-1")


@pytest.mark.parametrize("field,value", [("completion_expected", 1), ("reasoning_steps", []),
    ("reasoning_steps", ["exact h1"]), ("premises", "h1"), ("predicted_next_subgoal", " ")])
def test_transfer_rejects_malformed_or_tactic_bearing_reasoning(field, value):
    payload = valid_transfer_payload()
    payload[field] = value
    with pytest.raises(TransferOutputError):
        parse_proof_reasoning_transfer(json.dumps(payload), need=proof_need(), alignment=alignment(), transfer_id="transfer-1")


def test_transfer_rejects_alignment_for_another_need():
    need = replace(proof_need(), proof_roles=("active_subgoal", "different_hypothesis"))
    with pytest.raises(TransferOutputError, match="declared roles"):
        parse_proof_reasoning_transfer(json.dumps(valid_transfer_payload()), need=need,
                                      alignment=alignment(), transfer_id="transfer-1")


def test_all_parsers_reject_missing_and_unknown_keys():
    for parser, payload, kwargs in [
        (parse_transformation_record, transformation_payload(), {}),
        (parse_source_analogy, valid_source_payload(), {"analogy_id": "analogy-1"}),
        (parse_alignment, valid_alignment_payload(), {"source": source_analogy(), "need": proof_need(), "alignment_id": "alignment-1"}),
        (parse_proof_reasoning_transfer, valid_transfer_payload(), {"need": proof_need(), "alignment": alignment(), "transfer_id": "transfer-1"}),
    ]:
        for changed in [dict(payload, unknown=True), {k: v for k, v in payload.items() if k != next(iter(payload))}]:
            with pytest.raises(TransferOutputError):
                parser(json.dumps(changed), **kwargs)


def test_derive_proof_need_prefers_reuse_for_matching_hypothesis():
    need = derive_proof_need(structure("implication", shape("target")), "C")
    assert need.operation is OperationClass.REUSE
    assert need.active_subgoal == need.fixed_target == "C"
    assert need.proof_roles == ("active_subgoal", "matching_hypothesis")
    assert proof_need().fixed_target == "C → S"


@pytest.mark.parametrize("target,hypotheses,operation,role", [
    (shape("implication"), (), OperationClass.INTRODUCE, "introduced_assumption"),
    (shape("forall"), (), OperationClass.INTRODUCE, "introduced_assumption"),
    (shape("equality"), (), OperationClass.REWRITE, "equality_constructor"),
    (shape("atom"), (shape("equality"),), OperationClass.REWRITE, "equality_hypothesis"),
    (shape("and", shape("atom"), shape("atom")), (), OperationClass.COMBINE, "conjunction_constructor"),
    (shape("atom"), (shape("and", shape("atom"), shape("target")),), OperationClass.SELECT, "structured_hypothesis"),
    (shape("atom"), (shape("or", shape("atom"), shape("target")),), OperationClass.DECOMPOSE, "structured_hypothesis"),
    (shape("or"), (), OperationClass.SELECT_BRANCH, "disjunction_constructor"),
    (shape("atom"), (shape("implication", shape("atom"), shape("target")),), OperationClass.SPECIALIZE, "conditional_hypothesis"),
    (shape("atom"), (), OperationClass.UNKNOWN, None),
])
def test_derive_proof_need_uses_name_free_structure(target, hypotheses, operation, role):
    need = derive_proof_need(structure(target, *hypotheses), "private_proposition")
    assert need.operation is operation
    assert need.proof_roles[0] == "active_subgoal"
    if role:
        assert role in need.proof_roles
    assert "private_proposition" not in need.summary
    assert need.permutation is Permutation.NONE
    if operation is OperationClass.COMBINE:
        assert need.input_arity is Arity.TWO


def test_direct_record_construction_validates_and_freezes_nested_containers():
    record = parse_transformation_record(json.dumps(transformation_payload()))
    assert replace(record, information_flow=[InformationFlow.PRESERVE]).information_flow == (InformationFlow.PRESERVE,)
    with pytest.raises(TransferOutputError):
        replace(record, operation="invented")
    with pytest.raises(TransferOutputError):
        replace(proof_need(), proof_roles=("active_subgoal", "active_subgoal"))
    with pytest.raises(TransferOutputError):
        replace(source_analogy(), source_before="exact h1")


def test_source_allows_ordinary_words_that_are_also_lean_tactic_names():
    payload = valid_source_payload()
    payload["source_after"] = "A routing change lets the worker have the wheel."
    assert parse_source_analogy(json.dumps(payload), analogy_id="analogy-1").source_after.endswith("wheel.")


def test_reasoning_can_describe_a_contradiction_without_emitting_a_tactic():
    payload = valid_transfer_payload()
    payload["reasoning_steps"] = ["The derived condition and its negation yield a contradiction."]
    result = parse_proof_reasoning_transfer(json.dumps(payload), need=proof_need(),
                                           alignment=alignment(), transfer_id="transfer-1")
    assert result.completion_expected is True


def test_derive_proof_need_detects_a_structural_swap():
    atom, negated = shape("atom"), shape("not", shape("atom"))
    need = derive_proof_need(structure(shape("and", atom, negated),
                                       shape("and", negated, atom)), "C")
    assert need.operation is OperationClass.REORDER
    assert need.permutation is Permutation.SWAP
    assert need.proof_roles == ("active_subgoal", "structured_hypothesis")


@pytest.mark.parametrize("raw", ['{"root":[]}', '{"root":"unknown"}', '[]', '{"root":"atom","name":"h1"}'])
def test_derive_proof_need_rejects_non_name_free_shapes(raw):
    with pytest.raises(TransferOutputError, match="shape"):
        derive_proof_need(ProofStructure(1, raw, ()), "C")


def test_alignment_rejects_more_than_six_mappings():
    source = replace(source_analogy(), source_roles=tuple(f"wheel position {i}" for i in range(7)))
    payload = valid_alignment_payload()
    payload["mapping"] = {role: "active_subgoal" if i == 0 else "matching_hypothesis"
                          for i, role in enumerate(source.source_roles)}
    with pytest.raises(TransferOutputError, match="two to six"):
        parse_alignment(json.dumps(payload), source=source, need=proof_need(), alignment_id="alignment-1")


@pytest.mark.parametrize("text", ["h1", "And.left", "C"])
def test_domain_neutral_transformation_cannot_hide_proof_identifiers(text):
    payload = valid_source_payload()
    payload["transformation"]["focus_before"] = text
    with pytest.raises(TransferOutputError):
        parse_source_analogy(json.dumps(payload), analogy_id="analogy-1", need=proof_need())
