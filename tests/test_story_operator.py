import json

import pytest

from analogy.analogy_transfer import Arity, InformationFlow, NeutralProofNeed
from analogy.grounding import GroundingOperator
from analogy.proof_inventory import InventoryEntry, InventoryKind, LocalProofInventory, render_model_inventory
from analogy.prompts import PromptCatalog
from analogy.rescue import GenerationStatus
from analogy.story_operator import (
    StoryOperatorGenerationFailure,
    StoryOperatorGenerator,
    parse_story_operator_decision,
)


def _inventory():
    return LocalProofInventory(
        "fingerprint", "P", "P",
        (InventoryEntry("local:0", InventoryKind.LOCAL, "secret_name", "P", '{"root":"target"}'),),
        (InventoryEntry("active_subgoal", InventoryKind.ACTIVE_SUBGOAL, None, "P", '{"root":"target"}'),),
    )


def _need():
    return NeutralProofNeed(
        Arity.ONE, Arity.ONE, (InformationFlow.PRESERVE,),
        "a needed item and a matching available item", "the need fulfilled",
        ("active_subgoal", "matching_item"),
        "An available item matches what is needed.",
    )


def test_model_inventory_omits_state_hash_and_local_identifiers():
    rendered = render_model_inventory(_inventory())
    assert "fingerprint" not in rendered
    assert "secret_name" not in rendered
    assert json.loads(rendered)[0] == {
        "inventory_id": "local:0", "kind": "local", "proposition": "P",
        "structural_shape": {"root": "target"},
    }


def test_parse_story_operator_decision_accepts_only_enabled_exact_ids():
    decision = parse_story_operator_decision(json.dumps({
        "story": "A labeled parcel is already on the requested shelf.",
        "operator": "exact_local",
        "bindings": [{"role": "parcel", "inventory_id": "local:0"}],
    }), _inventory(), (GroundingOperator.EXACT_LOCAL,))
    assert decision.operator is GroundingOperator.EXACT_LOCAL
    assert decision.bindings[0].inventory_id == "local:0"


@pytest.mark.parametrize("raw,match", [
    ('{"story":"A","story":"B","operator":"exact_local","bindings":[]}', "duplicate"),
    ('{"story":"A","operator":"split","bindings":[]}', "disabled"),
    ('{"story":"A","operator":"exact_local","bindings":[{"role":"x","inventory_id":"missing"}]}', "unknown inventory"),
    ('{"story":"A","operator":"exact_local","bindings":[{"role":"x","inventory_id":"local:0"},{"role":"x","inventory_id":"active_subgoal"}]}', "role labels"),
    ('{"story":"A","operator":"exact_local","bindings":[{"role":"x","inventory_id":"local:0"},{"role":"y","inventory_id":"local:0"}]}', "inventory ids"),
    ('{"story":"exact h","operator":"exact_local","bindings":[]}', "everyday"),
    ('{"story":"A","operator":"exact_local","bindings":[],"completion":true}', "missing or unknown"),
])
def test_parse_story_operator_decision_rejects_invalid_output(raw, match):
    with pytest.raises(ValueError, match=match):
        parse_story_operator_decision(raw, _inventory(), (GroundingOperator.EXACT_LOCAL,))


class _Delta:
    def detach(self):
        return self


class _Action:
    delta = _Delta()


class _Generator:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def generate_with_delta(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def test_generator_makes_one_call_and_never_repairs(tmp_path):
    prompt_path = tmp_path / "prompts.yaml"
    prompt_path.write_text("""prompts:
  story_operator:
    version: v1
    system: Return strict JSON.
    user: 'Need={{need_json}} Items={{inventory_json}} Ops={{operators_json}}'
""")
    backend = _Generator(json.dumps({
        "story": "A parcel is already where it belongs.", "operator": "exact_local",
        "bindings": [{"role": "parcel", "inventory_id": "local:0"}],
    }))
    generated = StoryOperatorGenerator(
        backend, PromptCatalog.load(prompt_path), (GroundingOperator.EXACT_LOCAL,),
        hidden_layer=-1, base_seed=7,
    ).generate(_need(), _inventory(), _Action(), analogy_index=2)
    assert len(backend.calls) == 1
    assert backend.calls[0][1]["seed"] == 9
    assert generated.attempts[0].status is GenerationStatus.VALID


def test_generator_records_one_invalid_attempt_without_repair(tmp_path):
    prompt_path = tmp_path / "prompts.yaml"
    prompt_path.write_text("""prompts:
  story_operator:
    version: v1
    system: Return strict JSON.
    user: 'Need={{need_json}} Items={{inventory_json}} Ops={{operators_json}}'
""")
    backend = _Generator("not json")
    generator = StoryOperatorGenerator(
        backend, PromptCatalog.load(prompt_path), (GroundingOperator.EXACT_LOCAL,)
    )
    with pytest.raises(StoryOperatorGenerationFailure) as raised:
        generator.generate(_need(), _inventory(), _Action(), analogy_index=0)
    assert len(backend.calls) == 1
    assert len(raised.value.attempts) == 1
    assert raised.value.attempts[0].status is GenerationStatus.INVALID
