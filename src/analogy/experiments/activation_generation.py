"""Pre-Lean explicit analogy generation and matched tactic controls.

This module deliberately has no verifier, reference-proof, or successor-state
dependency. Every configured domain receives one attempt at each required stage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from typing import Any, Callable

from ..inference.parser import normalize_lean4_tactic
from ..inference.prompt_runner import PromptRunner
from ..inference.prompts import PromptCatalog, prompt_catalog, render_lean_goal_for_model
from ..records import Message
from ..solver.proof_views import ProofStructure
from ..solver.rescue import (
    ANALOGY_FAMILIES, GenerationAttempt, GenerationStatus, ProofRescueContext,
    RescueOutputError, RescueProposal, parse_analogy_rescue, parse_rescue_proposals,
)
from ..transfer.domain import (
    AnalogyAlignment, ProofNeed, ProofReasoningTransfer, SourceAnalogy,
    derive_proof_need, parse_alignment, parse_proof_reasoning_transfer,
    parse_source_analogy,
)
from .activation_records import ActivationEvalConfig, AnalogyCandidateRecord


@dataclass(frozen=True, slots=True)
class GeneratedAnalogyCandidate:
    record: AnalogyCandidateRecord
    source: SourceAnalogy
    alignment: AnalogyAlignment
    transfer: ProofReasoningTransfer
    tactics: tuple[RescueProposal, ...]
    messages: tuple[tuple[Message, ...], ...]
    raw_outputs: tuple[str, ...]
    processed_token_positions: int
    attempts: tuple[GenerationAttempt, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateGenerationFailure:
    candidate_id: str
    domain_cue: str
    stage: str
    diagnostic: str
    messages: tuple[tuple[Message, ...], ...]
    raw_outputs: tuple[str, ...]
    processed_token_positions: int
    attempts: tuple[GenerationAttempt, ...]


@dataclass(frozen=True, slots=True)
class GeneratedTacticSlate:
    tactics: tuple[RescueProposal, ...]
    messages: tuple[tuple[Message, ...], ...]
    raw_outputs: tuple[str, ...]
    processed_token_positions: int
    attempts: tuple[GenerationAttempt, ...]
    error: str | None = None
    analogy: str | None = None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 4:
        raise ValueError("tactics_per_slate must be between one and four")
    return value


def _run(provider: Any, messages: tuple[Message, ...], parser: Callable, seed: int):
    run = PromptRunner(provider).run(messages, parser=parser, seed=seed, max_attempts=1)
    # Runtime failures carry no completion; exact usage is unavailable rather
    # than an estimated word/character count. Keep the runtime diagnostic.
    positions = sum(
        provider.count_token_positions(messages, attempt.raw_text)
        for attempt in run.attempts if attempt.status is not GenerationStatus.RUNTIME_ERROR
    )
    return run, positions


class ActivationAnalogyGenerator:
    def __init__(self, provider: Any, config: ActivationEvalConfig, *, catalog: PromptCatalog | None = None):
        if config.analogy_candidates != len(config.domain_cues):
            raise ValueError("analogy_candidates must equal the number of domain cues")
        _limit(config.tactics_per_slate)
        self.provider = provider
        self.config = config
        self.catalog = catalog or prompt_catalog()

    def generate_candidates(
        self, context: ProofRescueContext, structure: ProofStructure,
        need: ProofNeed, *, seed: int,
    ) -> tuple[GeneratedAnalogyCandidate | CandidateGenerationFailure, ...]:
        derived = derive_proof_need(structure, context.state.expression, fixed_target=context.goal.expression)
        if need != derived:
            raise ValueError("need must match the current structure, state, and fixed target")
        neutral = {name: getattr(derived, name) for name in (
            "input_arity", "output_arity", "information_flow", "focus_before", "desired_focus_after", "summary",
        )}
        results = []
        for index, cue in enumerate(self.config.domain_cues):
            candidate_id = f"{context.goal.id}:{context.state.id}:{seed}:analogy:{index}"
            messages_seen, raw_outputs, attempts = [], [], []
            positions = 0
            source = alignment = transfer = None
            for stage_index, stage in enumerate(("source", "alignment", "transfer", "tactic")):
                if stage == "source":
                    messages = self.catalog.messages("activation_source_analogy", domain_cue=cue, need_json=_json(neutral))
                    parser = lambda raw: parse_source_analogy(raw, analogy_id=candidate_id, need=derived)
                elif stage == "alignment":
                    messages = self.catalog.messages("activation_alignment", source_json=_json(asdict(source)), need_json=_json(asdict(derived)))
                    parser = lambda raw: parse_alignment(raw, source=source, need=derived, alignment_id=f"{candidate_id}:alignment")
                elif stage == "transfer":
                    messages = self.catalog.messages("activation_transfer", source_json=_json(asdict(source)), alignment_json=_json(asdict(alignment)), need_json=_json(asdict(derived)))
                    parser = lambda raw: parse_proof_reasoning_transfer(raw, need=derived, alignment=alignment, transfer_id=f"{candidate_id}:transfer")
                else:
                    messages = _tactic_messages(context, source, alignment, transfer, catalog=self.catalog, tactics_per_slate=self.config.tactics_per_slate)
                    parser = lambda raw: parse_rescue_proposals(raw, self.config.tactics_per_slate)
                run, cost = _run(self.provider, messages, parser, seed + 4 * index + stage_index)
                messages_seen.append(messages)
                raw_outputs.extend(attempt.raw_text for attempt in run.attempts)
                attempts.extend(run.attempts)
                positions += cost
                if run.value is None:
                    results.append(CandidateGenerationFailure(
                        candidate_id, cue, stage, str(run.error), tuple(messages_seen), tuple(raw_outputs), positions, tuple(attempts),
                    ))
                    break
                if stage == "source":
                    source = run.value
                elif stage == "alignment":
                    alignment = run.value
                elif stage == "transfer":
                    transfer = run.value
                else:
                    record = AnalogyCandidateRecord(
                        candidate_id, source.domain, _json(asdict(source)), alignment.mapping,
                        alignment.limitation, tuple(item.candidate.source for item in run.value),
                    )
                    results.append(GeneratedAnalogyCandidate(
                        record, source, alignment, transfer, run.value, tuple(messages_seen), tuple(raw_outputs), positions, tuple(attempts),
                    ))
        return tuple(results)


def _tactic_messages(
    context: ProofRescueContext, source: SourceAnalogy, alignment: AnalogyAlignment,
    transfer: ProofReasoningTransfer, *, catalog: PromptCatalog,
    tactics_per_slate: int = 4, previous_state: str | None = None,
    last_tactic: str | None = None, include_previous: bool = True,
) -> tuple[Message, ...]:
    previous = "(omitted)"
    if include_previous and previous_state is not None:
        previous = previous_state
        if last_tactic is not None:
            previous += f"\nAccepted transition: {last_tactic}"
    return catalog.messages(
        "activation_tactic", overall_target=context.goal.expression,
        current_state=context.state.expression, accepted="\n".join(context.accepted_tactics) or "(none)",
        failed="\n".join(context.failed_tactics) or "(none)", previous_transition=previous,
        candidate_json=_json({"source": asdict(source), "alignment": asdict(alignment), "transfer": asdict(transfer)}),
        candidate_count=_limit(tactics_per_slate),
    )


def render_activation_tactic_messages(
    context: ProofRescueContext, candidate: GeneratedAnalogyCandidate, *,
    previous_state: str | None = None, last_tactic: str | None = None,
    include_previous: bool = True, tactics_per_slate: int = 4,
    catalog: PromptCatalog | None = None,
) -> tuple[Message, ...]:
    """Matched scoring contexts differ only in the predecessor field's value."""
    return _tactic_messages(
        context, candidate.source, candidate.alignment, candidate.transfer,
        catalog=catalog or prompt_catalog(), tactics_per_slate=tactics_per_slate,
        previous_state=previous_state, last_tactic=last_tactic, include_previous=include_previous,
    )


def _slate(provider: Any, messages: tuple[Message, ...], *, seed: int, parser: Callable, prompted: bool = False) -> GeneratedTacticSlate:
    run, positions = _run(provider, messages, parser, seed)
    tactics = () if run.value is None else run.value[1] if prompted else run.value
    return GeneratedTacticSlate(
        tactics, (messages,), tuple(attempt.raw_text for attempt in run.attempts), positions, run.attempts,
        None if run.error is None else str(run.error),
        run.value[0] if prompted and run.value is not None else None,
    )


def _rescue_values(context: ProofRescueContext) -> dict[str, str]:
    return dict(
        overall_target=render_lean_goal_for_model(context.goal.expression),
        current_goals=render_lean_goal_for_model(context.state.expression),
        accepted="\n".join(context.accepted_tactics) or "(none)",
        failed="\n".join(context.failed_tactics) or "(none)",
    )


def generate_neutral_slate(
    provider: Any, context: ProofRescueContext, *, seed: int,
    tactics_per_slate: int = 4, catalog: PromptCatalog | None = None,
) -> GeneratedTacticSlate:
    limit = _limit(tactics_per_slate)
    messages = (catalog or prompt_catalog()).messages("direct_rescue", **_rescue_values(context))
    return _slate(provider, messages, seed=seed, parser=lambda raw: parse_rescue_proposals(raw, limit))


def generate_prompted_slate(
    provider: Any, context: ProofRescueContext, *, seed: int,
    attempt_index: int = 0, tactics_per_slate: int = 4, catalog: PromptCatalog | None = None,
) -> GeneratedTacticSlate:
    limit = _limit(tactics_per_slate)
    offset = int(hashlib.sha256(context.state.expression.strip().encode("utf-8")).hexdigest(), 16) % len(ANALOGY_FAMILIES)
    family = ANALOGY_FAMILIES[(offset + attempt_index) % len(ANALOGY_FAMILIES)]
    messages = (catalog or prompt_catalog()).messages("analogy_rescue", **_rescue_values(context), analogy_family=family)

    def parse(raw):
        analogy, tactics = parse_analogy_rescue(raw, limit)
        prefix = f"[{family}]"
        if not analogy.casefold().startswith(prefix) or not analogy[len(prefix):].strip():
            raise RescueOutputError(f"analogy must begin with {prefix} and have a nonblank body")
        return analogy, tactics

    return _slate(provider, messages, seed=seed, parser=parse, prompted=True)


def generate_selected_slate(
    provider: Any, context: ProofRescueContext, candidate: GeneratedAnalogyCandidate, *, seed: int,
    previous_state: str | None = None, last_tactic: str | None = None,
    tactics_per_slate: int = 4, catalog: PromptCatalog | None = None,
) -> GeneratedTacticSlate:
    messages = render_activation_tactic_messages(
        context, candidate, previous_state=previous_state, last_tactic=last_tactic,
        tactics_per_slate=tactics_per_slate, catalog=catalog,
    )
    return _slate(provider, messages, seed=seed, parser=lambda raw: parse_rescue_proposals(raw, tactics_per_slate))


def shuffle_alignment(alignment: AnalogyAlignment, *, problem_id: str, seed: int) -> AnalogyAlignment:
    """Rotate only proof-role targets by a deterministic nonzero offset."""
    mapping = alignment.mapping
    targets = tuple(right for _, right in mapping)
    offsets = tuple(offset for offset in range(1, len(targets)) if targets[offset:] + targets[:offset] != targets)
    if not offsets:
        raise ValueError("alignment has no distinct proof-role rotation")
    digest = hashlib.sha256(f"{problem_id}:{seed}".encode()).digest()
    offset = offsets[int.from_bytes(digest, "big") % len(offsets)]
    rotated = targets[offset:] + targets[:offset]
    return replace(alignment, mapping=tuple((pair[0], right) for pair, right in zip(mapping, rotated)))


def generate_shuffled_slate(
    provider: Any, context: ProofRescueContext, candidate: GeneratedAnalogyCandidate, *, seed: int,
    previous_state: str | None = None, last_tactic: str | None = None,
    tactics_per_slate: int = 4, catalog: PromptCatalog | None = None,
) -> GeneratedTacticSlate:
    alignment = shuffle_alignment(candidate.alignment, problem_id=context.goal.id, seed=seed)
    shuffled = replace(candidate, alignment=alignment)
    return generate_selected_slate(
        provider, context, shuffled, seed=seed, previous_state=previous_state, last_tactic=last_tactic,
        tactics_per_slate=tactics_per_slate, catalog=catalog,
    )


def build_tactic_union(
    neutral: GeneratedTacticSlate,
    candidates: tuple[GeneratedAnalogyCandidate | CandidateGenerationFailure, ...], *, limit: int = 8,
) -> tuple[str, ...]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8:
        raise ValueError("tactic union limit must be between one and eight")
    ordered: dict[str, None] = {}
    for slate in (neutral, *candidates):
        if isinstance(slate, CandidateGenerationFailure):
            continue
        for proposal in slate.tactics:
            ordered.setdefault(normalize_lean4_tactic(proposal.candidate.source), None)
            if len(ordered) == limit:
                return tuple(ordered)
    return tuple(ordered)
