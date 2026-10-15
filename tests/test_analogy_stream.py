import pytest

from analogy.analogy_stream import (
    AnalogyOutputError, AnalogyStream, parse_analogy_hop,
    parse_analogy_tactic_plan,
)


VALID = ('{"domain":"river","situation":"a blocked channel",'
         '"relation_chain":["flow is blocked","pressure finds another channel"],'
         '"mapping":{"channel":"proof branch"},"continuation":"open a second channel",'
         '"predicted_consequence":"split the remaining goal",'
         '"rationale":"the obstruction suggests branching","stop":true}')


def test_parse_hop_preserves_readable_relation_chain_and_mapping():
    hop = parse_analogy_hop(VALID)
    assert hop.relation_chain == ("flow is blocked", "pressure finds another channel")
    assert hop.mapping == (("channel", "proof branch"),)


@pytest.mark.parametrize("bad", ["{}", '{"stop":true}', "not json"])
def test_parse_hop_rejects_incomplete_or_opaque_cards(bad):
    with pytest.raises(AnalogyOutputError):
        parse_analogy_hop(bad)


def test_tactic_plan_must_cite_supplied_hop():
    stream = AnalogyStream((parse_analogy_hop(VALID),))
    plan = parse_analogy_tactic_plan(
        '{"predicted_state":"done","tactics":["exact h"],"motivating_hop_id":"hop-0"}', stream
    )
    assert plan.tactics == ("exact h",)
    with pytest.raises(AnalogyOutputError, match="cite"):
        parse_analogy_tactic_plan(
            '{"predicted_state":"done","tactics":["exact h"],"motivating_hop_id":"other"}', stream
        )
