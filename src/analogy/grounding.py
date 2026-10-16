"""Deterministic grounding vocabulary shared by config and compilation."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from functools import partial

from .proof_inventory import (
    LocalProofInventory, implication_parts, proposition_binary, same_proposition,
)


class GroundingOperator(str, Enum):
    EXACT_LOCAL = "exact_local"
    INTRODUCE = "introduce"
    APPLY_IMPLICATION = "apply_implication"
    IFF_FORWARD = "iff_forward"
    IFF_BACKWARD = "iff_backward"
    REWRITE_FORWARD = "rewrite_forward"
    REWRITE_BACKWARD = "rewrite_backward"
    SPLIT = "split"
    SELECT_LEFT = "select_left"
    SELECT_RIGHT = "select_right"
    CHOOSE_LEFT = "choose_left"
    CHOOSE_RIGHT = "choose_right"
    CASES = "cases"
    CONTRADICTION = "contradiction"
    COMPOSE = "compose"


@dataclass(frozen=True, slots=True)
class GroundedApplication:
    application_id: str
    operator: GroundingOperator
    state_fingerprint: str
    inventory_ids: tuple[str, ...]
    input_propositions: tuple[str, ...]
    output_proposition: str
    expected_next_subgoal: str
    completion_expected: bool

    def __post_init__(self) -> None:
        if not isinstance(self.operator, GroundingOperator):
            raise ValueError("operator must be a GroundingOperator")
        if not isinstance(self.application_id, str) or not self.application_id:
            raise ValueError("application_id must be nonblank")
        if not isinstance(self.state_fingerprint, str) or not self.state_fingerprint:
            raise ValueError("state_fingerprint must be nonblank")
        if len(self.inventory_ids) != len(self.input_propositions):
            raise ValueError("each inventory id must have an exact input proposition")
        if len(set(self.inventory_ids)) != len(self.inventory_ids):
            raise ValueError("inventory ids must be unique")
        if any(not isinstance(item, str) or not item for item in (
            *self.inventory_ids, *self.input_propositions, self.output_proposition,
            self.expected_next_subgoal,
        )):
            raise ValueError("application text must be nonblank")
        if not isinstance(self.completion_expected, bool):
            raise ValueError("completion_expected must be boolean")


def _application(inventory, operator, entries=(), *, output, complete):
    return GroundedApplication(
        "pending", operator, inventory.state_fingerprint,
        tuple(item.inventory_id for item in entries),
        tuple(item.proposition for item in entries), output, output, complete,
    )


def _locals(inventory):
    return tuple(sorted(inventory.locals, key=lambda item: item.inventory_id))


def _exact_local(inventory):
    return tuple(_application(
        inventory, GroundingOperator.EXACT_LOCAL, (item,),
        output=inventory.active_subgoal, complete=True,
    ) for item in _locals(inventory)
      if same_proposition(item.proposition, inventory.active_subgoal))


def _introduce(inventory):
    relation = implication_parts(inventory.active_subgoal)
    return () if relation is None else (_application(
        inventory, GroundingOperator.INTRODUCE, output=relation[1], complete=False,
    ),)


def _apply_implication(inventory):
    result = []
    locals_ = _locals(inventory)
    for function in locals_:
        relation = implication_parts(function.proposition)
        if relation is None or not same_proposition(relation[1], inventory.active_subgoal):
            continue
        antecedent, _ = relation
        result.append(_application(inventory, GroundingOperator.APPLY_IMPLICATION,
                                   (function,), output=antecedent, complete=False))
        result.extend(_application(
            inventory, GroundingOperator.APPLY_IMPLICATION, (function, argument),
            output=inventory.active_subgoal, complete=True,
        ) for argument in locals_
          if argument is not function and same_proposition(argument.proposition, antecedent))
    return tuple(result)


def _iff(inventory, *, forward):
    result = []
    operator = GroundingOperator.IFF_FORWARD if forward else GroundingOperator.IFF_BACKWARD
    locals_ = _locals(inventory)
    for relation_entry in locals_:
        relation = proposition_binary(relation_entry.proposition, "↔")
        if relation is None:
            continue
        source, destination = relation if forward else relation[::-1]
        if same_proposition(destination, inventory.active_subgoal):
            result.extend(_application(
                inventory, operator, (relation_entry, argument),
                output=inventory.active_subgoal, complete=True,
            ) for argument in locals_
              if argument is not relation_entry and same_proposition(argument.proposition, source))
    return tuple(result)


def _replace_proposition(text, source, destination):
    if same_proposition(text, source):
        return destination
    positions = [match.start() for match in re.finditer(re.escape(source), text)]
    if len(positions) != 1:
        return None
    start = positions[0]
    return text[:start] + destination + text[start + len(source):]


def _rewrite(inventory, *, forward):
    operator = GroundingOperator.REWRITE_FORWARD if forward else GroundingOperator.REWRITE_BACKWARD
    result = []
    for relation_entry in _locals(inventory):
        relation = (proposition_binary(relation_entry.proposition, "=")
                    or proposition_binary(relation_entry.proposition, "↔"))
        if relation is None:
            continue
        source, destination = relation if forward else relation[::-1]
        rewritten = _replace_proposition(inventory.active_subgoal, source, destination)
        if rewritten is not None and not same_proposition(rewritten, inventory.active_subgoal):
            result.append(_application(inventory, operator, (relation_entry,),
                                       output=rewritten, complete=False))
    return tuple(result)


def _split(inventory):
    relation = proposition_binary(inventory.active_subgoal, "∧")
    output = relation[0] if relation else None
    if output is None:
        relation = proposition_binary(inventory.active_subgoal, "↔")
        output = f"{relation[0]} → {relation[1]}" if relation else None
    return () if output is None else (_application(
        inventory, GroundingOperator.SPLIT, output=output, complete=False,
    ),)


def _select(inventory, *, index):
    operator = GroundingOperator.SELECT_LEFT if index == 0 else GroundingOperator.SELECT_RIGHT
    result = []
    for item in _locals(inventory):
        relation = proposition_binary(item.proposition, "∧")
        if relation is not None and same_proposition(relation[index], inventory.active_subgoal):
            result.append(_application(inventory, operator, (item,),
                                       output=inventory.active_subgoal, complete=True))
    return tuple(result)


def _choose(inventory, *, index):
    operator = GroundingOperator.CHOOSE_LEFT if index == 0 else GroundingOperator.CHOOSE_RIGHT
    relation = proposition_binary(inventory.active_subgoal, "∨")
    return () if relation is None else (_application(
        inventory, operator, output=relation[index], complete=False,
    ),)


def _cases(inventory):
    return tuple(_application(
        inventory, GroundingOperator.CASES, (item,),
        output=inventory.active_subgoal, complete=False,
    ) for item in _locals(inventory)
      if proposition_binary(item.proposition, "∨") is not None)


def _contradiction(inventory):
    locals_ = _locals(inventory)
    result = [_application(
        inventory, GroundingOperator.CONTRADICTION, (item,),
        output=inventory.active_subgoal, complete=True,
    ) for item in locals_ if same_proposition(item.proposition, "False")]
    for proof in locals_:
        for negation in locals_:
            relation = implication_parts(negation.proposition)
            if (proof is not negation and relation is not None
                    and same_proposition(relation[1], "False")
                    and same_proposition(proof.proposition, relation[0])):
                result.append(_application(
                    inventory, GroundingOperator.CONTRADICTION, (proof, negation),
                    output=inventory.active_subgoal, complete=True,
                ))
    return tuple(result)


def enumerate_applications(operator, inventory):
    if not isinstance(operator, GroundingOperator) or not isinstance(inventory, LocalProofInventory):
        raise ValueError("enumeration requires a grounding operator and local inventory")
    builders = {
        GroundingOperator.EXACT_LOCAL: _exact_local,
        GroundingOperator.INTRODUCE: _introduce,
        GroundingOperator.APPLY_IMPLICATION: _apply_implication,
        GroundingOperator.IFF_FORWARD: partial(_iff, forward=True),
        GroundingOperator.IFF_BACKWARD: partial(_iff, forward=False),
        GroundingOperator.REWRITE_FORWARD: partial(_rewrite, forward=True),
        GroundingOperator.REWRITE_BACKWARD: partial(_rewrite, forward=False),
        GroundingOperator.SPLIT: _split,
        GroundingOperator.SELECT_LEFT: partial(_select, index=0),
        GroundingOperator.SELECT_RIGHT: partial(_select, index=1),
        GroundingOperator.CHOOSE_LEFT: partial(_choose, index=0),
        GroundingOperator.CHOOSE_RIGHT: partial(_choose, index=1),
        GroundingOperator.CASES: _cases,
        GroundingOperator.CONTRADICTION: _contradiction,
        GroundingOperator.COMPOSE: lambda _inventory: (),
    }
    return tuple(replace(item, application_id=f"application:{index}")
                 for index, item in enumerate(builders[operator](inventory)))


__all__ = ["GroundedApplication", "GroundingOperator", "enumerate_applications"]
