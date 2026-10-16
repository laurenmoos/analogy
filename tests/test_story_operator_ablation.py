import json

import torch

from analogy.analogy_transfer import Arity, InformationFlow, NeutralProofNeed
from analogy.grounding import GroundingOperator
from analogy.proof_inventory import InventoryEntry, InventoryKind, LocalProofInventory
from analogy.prompts import PromptCatalog
from scripts.benchmark_story_operator_ablation import benchmark


class _Generator:
    hidden_size = 2

    def __init__(self):
        self.calls = []

    def count_tokens(self, value):
        if isinstance(value, tuple):
            return sum(len(item.content.split()) for item in value)
        return len(value.split())

    def generate_with_delta(self, messages, *, delta, layer, seed):
        arm = "plain" if messages[0].content == "plain" else "story"
        self.calls.append({
            "arm": arm, "seed": seed, "delta": tuple(delta.tolist()),
            "user": messages[1].content,
        })
        if arm == "plain":
            return '{"operator":"exact_local"}'
        return json.dumps({
            "story": "A spare key already fits the lock.",
            "operator": "exact_local",
            "bindings": [{"role": "key", "inventory_id": "local:0"}],
        })


def _reconstruct(fixture):
    inventory = LocalProofInventory(
        f"hash-{fixture['id']}", "P", "P",
        (InventoryEntry("local:0", InventoryKind.LOCAL, "h", "P",
                        '{"root":"target","children":[]}'),),
        (InventoryEntry("active_subgoal", InventoryKind.ACTIVE_SUBGOAL, None, "P",
                        '{"root":"target","children":[]}'),),
    )
    description = NeutralProofNeed(
        Arity.ONE, Arity.ONE, (InformationFlow.PRESERVE,), "a match", "fulfilled",
        ("active_subgoal", "matching_item"), "An available item matches the goal.",
    )
    return description, inventory, GroundingOperator.EXACT_LOCAL


def test_ablation_pairs_calls_crosses_order_and_keeps_plain_inventory_free(tmp_path):
    prompt_path = tmp_path / "prompts.yaml"
    prompt_path.write_text("""prompts:
  ablation_plain:
    version: p1
    system: plain
    user: 'Need={{need_json}} Ops={{operators_json}}'
  ablation_story:
    version: s1
    system: story
    user: 'Need={{need_json}} Items={{inventory_json}} Ops={{operators_json}}'
""")
    fixtures = ({"id": "a"}, {"id": "b"})
    generator = _Generator()
    output = tmp_path / "out"
    summary = benchmark(
        fixtures, PromptCatalog.load(prompt_path), generator,
        (GroundingOperator.EXACT_LOCAL,), (torch.tensor([0.25, -0.5]),),
        _reconstruct, lambda _fixture, candidates: tuple(True for _ in candidates), output,
        hidden_layer=-1, base_seed=11,
    )

    assert [(call["arm"], call["seed"], call["delta"]) for call in generator.calls] == [
        ("plain", 11, (0.25, -0.5)), ("story", 11, (0.25, -0.5)),
        ("story", 12, (0.25, -0.5)), ("plain", 12, (0.25, -0.5)),
    ]
    plain_calls = [call for call in generator.calls if call["arm"] == "plain"]
    assert all("Items=" not in call["user"] and "local:0" not in call["user"] for call in plain_calls)
    assert summary["arms"]["plain"]["calls"] == 2
    assert summary["arms"]["story"]["calls"] == 2
    assert summary["arms"]["plain"]["first_pick_successes"] == 2
    assert summary["arms"]["plain"]["prompt_token_samples"] == 2
    assert summary["arms"]["story"]["output_token_samples"] == 2
    assert summary["story_diversity"]["unique_by_fixture"] == {"a": 1, "b": 1}
    assert summary["paired"][0]["first_pick_delta"] == 0
    assert {path.name for path in output.iterdir()} == {"attempts.jsonl", "summary.json", "report.md"}
