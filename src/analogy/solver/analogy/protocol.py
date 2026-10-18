"""Model-facing text protocol for analogy state."""

from __future__ import annotations

import json
import re
from dataclasses import asdict

from ...model.prompts import prompt_catalog
from .state import (
    AnalogyScratchpad,
    AnalogyState,
    AnalogyStateError,
    MentalModel,
    MentalModelCorrespondence,
    WorkingMentalModel,
)


_BLOCK = re.compile(r"<analogy>\s*\n(?P<body>.*?)\n\s*</analogy>", re.DOTALL)
_MENTAL_MODEL_BLOCK = re.compile(
    r"<mental_model>\s*\n(?P<body>.*?)\n\s*</mental_model>", re.DOTALL
)
_STEP = re.compile(r"(?P<number>[1-9][0-9]*)\.\s+(?P<text>.+)")


def render_analogy_state(state: AnalogyState) -> str:
    mapping = "\n".join(f"- {item}" for item in state.mapping)
    steps = "\n".join(
        f"{index}. {item}" for index, item in enumerate(state.steps, 1)
    )
    abandoned = (
        "\n".join(f"- {item}" for item in state.abandoned_domains) or "- (none)"
    )
    return (
        "<analogy>\n"
        f"domain: {state.domain}\n"
        "mapping:\n"
        f"{mapping}\n"
        "steps:\n"
        f"{steps}\n"
        "abandoned domains:\n"
        f"{abandoned}\n"
        "</analogy>"
    )


def render_mental_model(model: MentalModel) -> str:
    payload = {
        "domain": model.domain,
        "correspondences": [asdict(item) for item in model.correspondences],
        "abandoned_domains": list(model.abandoned_domains),
    }
    return (
        "<mental_model>\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n</mental_model>"
    )


def render_working_model(model: WorkingMentalModel) -> str:
    if isinstance(model, MentalModel):
        return render_mental_model(model)
    return render_analogy_state(model)


def extract_analogy_block(text: str) -> tuple[AnalogyState, str]:
    if not isinstance(text, str):
        raise AnalogyStateError("analogy output must be text")
    matches = tuple(_BLOCK.finditer(text))
    if len(matches) != 1:
        raise AnalogyStateError("output must contain exactly one analogy block")
    match = matches[0]
    state = _parse_body(match.group("body"))
    remainder = text[: match.start()] + text[match.end() :]
    return state, remainder.strip("\n")


def extract_mental_model_block(text: str) -> tuple[MentalModel, str]:
    if not isinstance(text, str):
        raise AnalogyStateError("mental model output must be text")
    matches = tuple(_MENTAL_MODEL_BLOCK.finditer(text))
    if len(matches) != 1:
        raise AnalogyStateError("output must contain exactly one mental model block")
    match = matches[0]
    try:
        payload = json.loads(match.group("body"))
    except json.JSONDecodeError as error:
        raise AnalogyStateError(f"mental model must contain strict JSON: {error}") from error
    if not isinstance(payload, dict) or set(payload) != {
        "domain",
        "correspondences",
        "abandoned_domains",
    }:
        raise AnalogyStateError(
            "mental model must contain exactly domain, correspondences, and abandoned_domains"
        )
    raw_correspondences = payload["correspondences"]
    if not isinstance(raw_correspondences, list):
        raise AnalogyStateError("correspondences must be a nonempty sequence")
    fields = {
        "domain_before",
        "domain_transition",
        "domain_after",
        "proof_before",
        "proof_transition",
        "proof_after",
    }
    correspondences = []
    for item in raw_correspondences:
        if not isinstance(item, dict) or set(item) != fields:
            raise AnalogyStateError(
                "each correspondence must contain exactly the six transition fields"
            )
        correspondences.append(MentalModelCorrespondence(**item))
    abandoned = payload["abandoned_domains"]
    if not isinstance(abandoned, list):
        raise AnalogyStateError("abandoned_domains must be a list")
    model = MentalModel(
        payload["domain"], tuple(correspondences), tuple(abandoned)
    )
    remainder = text[: match.start()] + text[match.end() :]
    return model, remainder.strip("\n")


def extract_working_model_block(text: str) -> tuple[WorkingMentalModel, str]:
    if "<mental_model>" in text:
        return extract_mental_model_block(text)
    return extract_analogy_block(text)


def remove_analogy_blocks(text: str) -> str:
    """Remove complete analogy blocks before text enters prompt history."""

    if not isinstance(text, str):
        raise AnalogyStateError("analogy output must be text")
    return _MENTAL_MODEL_BLOCK.sub("", _BLOCK.sub("", text)).strip()


def render_scratchpad_context(scratchpad: AnalogyScratchpad) -> str:
    if scratchpad.current is not None:
        return render_working_model(scratchpad.current)
    abandoned = (
        "\n".join(f"- {item}" for item in scratchpad.abandoned_domains)
        or "- (none)"
    )
    return prompt_catalog().text(
        "analogy_pending_context",
        abandoned_domains=abandoned,
    )


def record_model_history(scratchpad: AnalogyScratchpad, text: str) -> None:
    cleaned = remove_analogy_blocks(text)
    if "<analogy>" in cleaned or "</analogy>" in cleaned:
        raise AnalogyStateError("history must not contain analogy block text")
    scratchpad.record_history(cleaned)


def _parse_body(body: str) -> AnalogyState:
    lines = body.splitlines()
    if not lines or not lines[0].startswith("domain:"):
        raise AnalogyStateError("analogy block requires domain")
    domain = lines[0].partition(":")[2].strip()
    try:
        mapping_index = lines.index("mapping:")
        steps_index = lines.index("steps:")
        abandoned_index = lines.index("abandoned domains:")
    except ValueError as error:
        missing = next(
            name
            for name in ("mapping", "steps", "abandoned domains")
            if f"{name}:" not in lines
        )
        raise AnalogyStateError(f"analogy block requires {missing}") from error
    if not (mapping_index == 1 < steps_index < abandoned_index):
        raise AnalogyStateError(
            "analogy sections must be ordered domain, mapping, steps, abandoned domains"
        )
    mapping_lines = lines[mapping_index + 1 : steps_index]
    if not mapping_lines or any(not line.startswith("- ") for line in mapping_lines):
        raise AnalogyStateError("mapping must be a nonempty bullet list")
    mapping = tuple(line[2:].strip() for line in mapping_lines)
    step_lines = lines[steps_index + 1 : abandoned_index]
    steps: list[str] = []
    for expected, line in enumerate(step_lines, 1):
        matched = _STEP.fullmatch(line)
        if matched is None or int(matched.group("number")) != expected:
            raise AnalogyStateError("steps must be consecutively numbered from 1")
        steps.append(matched.group("text").strip())
    if not steps:
        raise AnalogyStateError("steps must be a nonempty numbered list")
    abandoned_lines = lines[abandoned_index + 1 :]
    if not abandoned_lines or any(
        not line.startswith("- ") for line in abandoned_lines
    ):
        raise AnalogyStateError("abandoned domains must be a bullet list")
    abandoned = tuple(
        line[2:].strip()
        for line in abandoned_lines
        if line[2:].strip() != "(none)"
    )
    return AnalogyState(domain, mapping, tuple(steps), abandoned)


__all__ = [
    "extract_analogy_block",
    "extract_mental_model_block",
    "extract_working_model_block",
    "record_model_history",
    "remove_analogy_blocks",
    "render_analogy_state",
    "render_mental_model",
    "render_working_model",
    "render_scratchpad_context",
]
