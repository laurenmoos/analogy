"""Offline, theorem-split, Lean-verified transitions with an answer-free online view.

The durable journal is authoritative. Each newline commits one prefix and all its
verified variants; final artifacts are deterministic atomic projections of it.
No retrieval or model execution is reachable from the online loader.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import CurriculumConfig
from .grounded_plan import (GROUNDED_PLAN_SCHEMA_VERSION, GroundedInferenceStep,
                            GroundedRoleBinding, GroundedTacticPlan, GroundingOperator as Op)
from .lean_gym.corpus import load_problems, load_reference_proofs, validate_reference_coverage
from .lean_gym.models import VerificationStatus as Status
from .proof_inventory import LocalProofInventory, _declarations_and_target, build_local_inventory
from .records import SymbolicState
from .replay import proof_state_fingerprint
from .grounding import enumerate_applications
from .proof_inventory import implication_parts as _implication, proposition_binary as _binary
from .tactic_compiler import TACTIC_COMPILER_VERSION, GroundedTacticCompiler


SCHEMA_VERSION = "grounded-curriculum-v1"
SPLITS = ("train", "validation", "test")
FAILURES = ("unsupported_tactic", "compile_mismatch", "verifier_error", "repeated_state", "structure_failure")


class CurriculumError(ValueError):
    pass


class UnsupportedReferenceTactic(CurriculumError):
    pass


@dataclass(frozen=True, slots=True)
class CurriculumSummary:
    transitions_path: Path
    manifest_path: Path
    attempted: int
    retained: int
    nonterminal: int
    completions: int
    variants: int
    unsupported_tactic: int
    compile_mismatch: int
    verifier_error: int
    repeated_state: int
    structure_failure: int
    augmentation_failure: int


@dataclass(frozen=True, slots=True)
class OnlineCurriculumState:
    problem_id: str
    accepted_prefix: tuple[str, ...]
    current_state_hash: str
    split: str
    transition_id: str

    def __post_init__(self) -> None:
        if self.split not in SPLITS:
            raise CurriculumError("split must be train, validation, or test")
        for name in ("problem_id", "current_state_hash", "transition_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise CurriculumError(f"{name} must be nonblank")
        if not isinstance(self.accepted_prefix, (list, tuple)) or any(
            not isinstance(tactic, str) or not tactic.strip() for tactic in self.accepted_prefix
        ):
            raise CurriculumError("accepted_prefix must be a tactic sequence")
        object.__setattr__(self, "accepted_prefix", tuple(self.accepted_prefix))


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=lambda item: str(item) if isinstance(item, Path) else item.value)


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_hash(path: Path, *, project=False) -> str:
    if not path.is_dir():
        raise CurriculumError(f"pinned directory does not exist: {path}")
    ignored = {".git", ".lake", "__pycache__"} if project else {".git", "__pycache__"}
    files = [item for item in sorted(path.rglob("*")) if item.is_file()
             and not ignored.intersection(item.relative_to(path).parts)]
    return _hash([(str(item.relative_to(path)), _file_hash(item)) for item in files])


def _identity(config: CurriculumConfig) -> dict:
    from .analogy_transfer_generation import (SOURCE_PROMPT_VERSION, ALIGNMENT_PROMPT_VERSION,
                                              TRANSFER_PROMPT_VERSION, GROUNDING_PROMPT_VERSION)
    toolchain = config.lean_project / "lean-toolchain"
    if not toolchain.is_file() or not re.fullmatch(
        r"leanprover/lean4:(?:v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?|nightly-\d{4}-\d{2}-\d{2}|[a-f0-9]{40})",
        toolchain.read_text().strip(),
    ):
        raise CurriculumError("lean_project must contain a pinned lean-toolchain")
    model_hash = None
    if config.model is not None:
        model_path = Path(config.model.name)
        if config.analogies_per_transition and not model_path.is_dir():
            raise CurriculumError("offline augmentation requires a local pinned model directory")
        if model_path.is_dir():
            model_hash = _tree_hash(model_path)
    return {"schema_version": SCHEMA_VERSION, "config_hash": _hash(asdict(config)),
            "problems_hash": _file_hash(config.problems),
            "reference_proofs_hash": _file_hash(config.reference_proofs),
            "split_manifest_hash": _file_hash(config.split_manifest),
            "lean_project_hash": _tree_hash(config.lean_project, project=True),
            "lean_version": toolchain.read_text().strip(),
            "model_hash": _hash({"config": asdict(config.model) if config.model else None,
                                  "revision": config.model_revision, "files": model_hash}),
            "model_revision": config.model_revision,
            "grounded_plan_schema_version": GROUNDED_PLAN_SCHEMA_VERSION,
            "compiler_version": TACTIC_COMPILER_VERSION,
            "prompt_versions": {"source": SOURCE_PROMPT_VERSION, "alignment": ALIGNMENT_PROMPT_VERSION,
                                "transfer": TRANSFER_PROMPT_VERSION, "grounding": GROUNDING_PROMPT_VERSION}}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise CurriculumError(f"invalid artifact {path}: {error}") from error


def _load_splits(path: Path, problem_ids: set[str]) -> dict[str, list[str]]:
    raw = _read_json(path)
    if not isinstance(raw, dict) or set(raw) != {"schema_version", *SPLITS} or raw["schema_version"] != 1:
        raise CurriculumError("split manifest requires schema_version 1 and train/validation/test lists")
    seen: set[str] = set()
    for split in SPLITS:
        values = raw[split]
        if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
            raise CurriculumError("split groups must be theorem ID lists")
        if len(values) != len(set(values)) or seen.intersection(values):
            raise CurriculumError("split theorem IDs overlap")
        seen.update(values)
    if seen != problem_ids:
        raise CurriculumError(f"split coverage mismatch: missing={sorted(problem_ids - seen)}, unknown={sorted(seen - problem_ids)}")
    return {split: raw[split] for split in SPLITS}


def _atomic(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = path.read_bytes()
    # An absent newline means the commit was interrupted, even if JSON parses.
    complete_end = data.rfind(b"\n") + 1
    if complete_end < len(data):
        with path.open("r+b") as handle:
            handle.truncate(complete_end)
            handle.flush()
            os.fsync(handle.fileno())
    result = []
    for line in data[:complete_end].splitlines():
        try:
            event = json.loads(line)
            checksum = event.pop("event_hash")
            if checksum != _hash(event):
                raise ValueError("event hash mismatch")
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise CurriculumError(f"corrupt completed journal row: {error}") from error
        result.append(event)
    return result


def _append_event(path: Path, event: dict) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_json({**event, "event_hash": _hash(event)}) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _state_hash(result) -> str:
    if result.accepted and result.status is Status.COMPLETE:
        return _hash({"complete": True})
    if result.accepted and result.status is Status.OPEN and result.proof_state is not None:
        return proof_state_fingerprint(result.proof_state.normalized)
    raise CurriculumError("verifier did not return an accepted state or completion")


def derive_grounded_plan(reference_tactic: str, inventory: LocalProofInventory, before, after) -> GroundedTacticPlan:
    """Recognize a conservative tactic subset; raw syntax is never a compiler input.

    Unsupported terms are counted rather than guessed. Every resulting plan still
    has to compile and reproduce the full verified successor, including all goals.
    """
    _state_hash(before)
    _state_hash(after)
    completion = after.status is Status.COMPLETE
    successor = "No remaining goals." if completion else _declarations_and_target(after.proof_state.normalized)[1]
    output = inventory.active_subgoal if completion else successor
    bindings: list[GroundedRoleBinding] = []
    steps: list[GroundedInferenceStep] = []
    by_name = {entry.identifier: entry for entry in inventory.locals}

    def bind(name):
        if name not in by_name:
            raise UnsupportedReferenceTactic(f"unknown local term {name!r}")
        entry = by_name[name]
        existing = next((item for item in bindings if item.inventory_id == entry.inventory_id), None)
        if existing:
            return existing.binding_id, existing.proposition
        key = f"binding-{len(bindings)}"
        bindings.append(GroundedRoleBinding(key, key, entry.inventory_id, entry.proposition, None,
                                            "Exact local declaration used by the verified reference."))
        return key, entry.proposition

    def step(op, args, proposition):
        key = f"step-{len(steps)}"
        steps.append(GroundedInferenceStep(key, op, tuple(arg[0] for arg in args),
            tuple(arg[1] for arg in args), proposition, len(steps), False))
        return key, proposition

    def term(text, depth=0):
        if depth > 32 or len(steps) > 48:
            raise UnsupportedReferenceTactic("reference term exceeds supported bounds")
        text = text.strip()
        if text in by_name:
            return bind(text)
        if text.startswith("(") and text.endswith(")"):
            # Only strip a wrapper enclosing the entire term.
            balance = 0
            for index, char in enumerate(text):
                balance += (char == "(") - (char == ")")
                if balance == 0:
                    if index == len(text) - 1:
                        return term(text[1:-1], depth + 1)
                    break
        # Split a single application at a depth-zero space; recursive parsing
        # handles nested parenthesized arguments and local proof projections.
        balance = 0
        for index, char in enumerate(text):
            balance += (char == "(") - (char == ")")
            if char == " " and balance == 0:
                fn, arg = text[:index], text[index + 1:].strip()
                if fn.endswith((".mp", ".mpr")):
                    projection = "mpr" if fn.endswith(".mpr") else "mp"
                    relation = term(fn[:-(len(projection) + 1)], depth + 1)
                    sides = _binary(relation[1], "↔")
                    if sides is None:
                        raise UnsupportedReferenceTactic("iff projection requires local iff")
                    return step(Op.IFF_FORWARD if projection == "mp" else Op.IFF_BACKWARD,
                                [relation, term(arg, depth + 1)], sides[1 if projection == "mp" else 0])
                fn_term = term(fn, depth + 1)
                relation = _implication(fn_term[1])
                if relation is None:
                    raise UnsupportedReferenceTactic("application requires implication")
                return step(Op.APPLY_IMPLICATION, [fn_term, term(arg, depth + 1)], relation[1])
        for suffix, operator, side in ((".left", Op.SELECT_LEFT, 0), (".right", Op.SELECT_RIGHT, 1)):
            if text.endswith(suffix):
                local = term(text[:-len(suffix)], depth + 1)
                relation = _binary(local[1], "∧")
                if relation:
                    return step(operator, [local], relation[side])
        raise UnsupportedReferenceTactic(f"unsupported reference term {text!r}")

    tactic = reference_tactic.strip()
    if tactic.startswith("exact "):
        value = term(tactic[6:])
        if not steps:
            step(Op.EXACT_LOCAL, [value], output)
    elif tactic.startswith("apply ") and tactic[6:] in by_name:
        step(Op.APPLY_IMPLICATION, [bind(tactic[6:])], output)
    elif re.fullmatch(r"intro(?:\s+[^\s]+)?", tactic):
        step(Op.INTRODUCE, [], output)
    elif tactic in {"constructor", "left", "right"}:
        step({"constructor": Op.SPLIT, "left": Op.CHOOSE_LEFT, "right": Op.CHOOSE_RIGHT}[tactic], [], output)
    elif (match := re.fullmatch(r"rw\s*\[\s*(←\s*)?([^\[\],]+?)\s*\]", tactic)):
        step(Op.REWRITE_BACKWARD if match[1] else Op.REWRITE_FORWARD, [bind(match[2].strip())], output)
    elif tactic.startswith("cases ") and tactic[6:] in by_name:
        step(Op.CASES, [bind(tactic[6:])], output)
    else:
        raise UnsupportedReferenceTactic(f"unsupported reference tactic {tactic!r}")
    final = steps[-1]
    steps[-1] = GroundedInferenceStep(final.step_id, final.operator, final.inputs,
        final.input_propositions, final.output_proposition, final.transfer_step_index, completion)
    identity = _hash([reference_tactic, inventory.state_fingerprint, _state_hash(after)])
    return GroundedTacticPlan(f"plan-{identity}", f"reference-{identity}", inventory.state_fingerprint,
                              tuple(bindings), tuple(steps), successor, completion, ())


def _verify_candidates(verifier, problem, prefix, plan, inventory, expected_hash):
    applications = enumerate_applications(plan.steps[-1].operator, inventory)
    candidates = GroundedTacticCompiler().compile_all(applications, inventory).candidates
    infrastructure = False
    for candidate in candidates:
        try:
            outcome = verifier.try_tactic(problem, prefix, candidate.text)
            if outcome.status in (Status.TIMEOUT, Status.INTERNAL_ERROR):
                infrastructure = True
                continue
            if outcome.accepted and _state_hash(outcome) == expected_hash:
                return candidate, None
        except Exception:
            infrastructure = True
    return None, "verifier_error" if infrastructure else "compile_mismatch"


def _offline_variant(config, generator, inventory, structure, state, index):
    """Call the existing stages with an explicit zero source-lens action."""
    import torch
    from .rl import PolicyAction
    from .analogy_transfer import AnalogyObservation, derive_proof_need
    from .analogy_transfer_generation import (SourceAnalogyGenerator, AlignmentGenerator,
                                              ProofTransferGenerator, GroundedPlanGenerator)
    observation = AnalogyObservation(inventory.fixed_target, state.expression, None, None,
        state.context, (), None, None, None, None, None, None, None, False, 0, 8, 8)
    need = derive_proof_need(structure, inventory.active_subgoal, fixed_target=inventory.fixed_target)
    options = {"base_seed": config.seed, "hidden_layer": config.model.hidden_layer}
    zero = torch.tensor(0.0)
    action = PolicyAction(torch.zeros(generator.hidden_size), zero, zero)
    source = SourceAnalogyGenerator(generator, **options).generate(observation, need, action, analogy_index=index).source
    alignment = AlignmentGenerator(generator, **options).generate(observation, need, source, analogy_index=index).alignment
    transfer = ProofTransferGenerator(generator, **options).generate(observation, need, source, alignment,
                                                                    analogy_index=index).transfer
    plan = GroundedPlanGenerator(generator, **options).generate(inventory, transfer,
        plan_id=f"variant-plan-{index}", analogy_index=index).plan
    return plan, {"source": asdict(source), "alignment": asdict(alignment), "transfer": asdict(transfer),
                  "latent_action": {"kind": "unsteered_zero", "delta": [0.0] * generator.hidden_size}}


def build_grounded_curriculum(config: CurriculumConfig, verifier, *, generator=None) -> CurriculumSummary:
    """Replay every prefix, then publish only reproducible accepted transitions."""
    problems = {item.id: item for item in load_problems(config.problems)}
    proofs = load_reference_proofs(config.reference_proofs)
    validate_reference_coverage(tuple(problems.values()), proofs)
    splits = _load_splits(config.split_manifest, set(problems))
    if hasattr(verifier, "project_root") and Path(verifier.project_root).resolve() != config.lean_project.resolve():
        raise CurriculumError("verifier must use the configured pinned Lean project")
    identity = _identity(config)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    identity_path = config.output_dir / "build-identity.json"
    if identity_path.exists():
        if _read_json(identity_path) != identity:
            raise CurriculumError("resume requires identical source/config/model/project hashes")
    else:
        if any(config.output_dir.iterdir()):
            raise CurriculumError("cannot resume artifacts without identical build identity")
        _atomic(identity_path, _json(identity) + "\n")
    journal_path = config.output_dir / "journal.jsonl"
    events = _journal(journal_path)
    scheduled = [(split, problem_id, index) for split in SPLITS for problem_id in splits[split]
                 for index in range(len(proofs[problem_id].tactics))]
    if len(events) > len(scheduled):
        raise CurriculumError("journal exceeds scheduled prefixes")
    build_id = _hash(identity)
    seen_by_problem: dict[str, set[str]] = {}
    for position, (split, problem_id, index) in enumerate(scheduled):
        problem = problems[problem_id]
        prefix = proofs[problem_id].tactics[:index]
        tactic = proofs[problem_id].tactics[index]
        transition_id = _hash([build_id, problem_id, split, index, prefix, tactic])
        seen = seen_by_problem.setdefault(problem_id, set())
        if position < len(events):
            event = events[position]
            if event["transition_id"] != transition_id:
                raise CurriculumError("journal identity does not match scheduled prefix")
            seen.update(event["seen_hashes"])
            continue
        event = {"transition_id": transition_id, "records": [], "failure": None,
                 "augmentation_failure": 0, "seen_hashes": []}
        try:
            before = verifier.state_after(problem, prefix)
            after = verifier.try_tactic(problem, prefix, tactic)
            before_hash, after_hash = _state_hash(before), _state_hash(after)
            if before.status is not Status.OPEN:
                raise CurriculumError("prefix must leave an open state")
        except Exception:
            event["failure"] = "verifier_error"
        else:
            seen.add(before_hash)
            event["seen_hashes"].append(before_hash)
            repeated = after.status is Status.OPEN and after_hash in seen
            seen.add(after_hash)
            event["seen_hashes"].append(after_hash)
            if repeated:
                event["failure"] = "repeated_state"
            else:
                try:
                    structure = verifier.proof_structure_after(problem, prefix)
                    state = SymbolicState(transition_id, before.proof_state.normalized, "lean4", prefix)
                    inventory = build_local_inventory(state, structure, fixed_target=problem.target)
                except Exception:
                    event["failure"] = "structure_failure"
                else:
                    try:
                        plan = derive_grounded_plan(tactic, inventory, before, after)
                    except UnsupportedReferenceTactic:
                        event["failure"] = "unsupported_tactic"
                    except (ValueError, TypeError):
                        event["failure"] = "compile_mismatch"
                    else:
                        candidate, failure = _verify_candidates(verifier, problem, prefix, plan, inventory, after_hash)
                        event["failure"] = failure
                        if candidate:
                            record = {"schema_version": SCHEMA_VERSION, "transition_id": transition_id,
                                "parent_transition_id": None, "problem_id": problem_id, "split": split,
                                "theorem_hash": _hash(asdict(problem)), "source_hash": identity["problems_hash"],
                                "reference_proofs_hash": identity["reference_proofs_hash"],
                                "prefix_index": index, "prefix_hash": _hash(prefix), "accepted_prefix": prefix,
                                "before_state_hash": before_hash, "after_state_hash": after_hash,
                                "before_state": before.proof_state.normalized,
                                "after_state": after.proof_state.normalized if after.proof_state else None,
                                "reference_tactic": tactic, "grounded_plan": asdict(plan),
                                "matching_candidate": asdict(candidate), "compiler_version": TACTIC_COMPILER_VERSION,
                                "grounded_plan_schema_version": GROUNDED_PLAN_SCHEMA_VERSION,
                                "lean_version": identity["lean_version"], "lean_project_hash": identity["lean_project_hash"],
                                "model_revision": config.model_revision, "model_hash": identity["model_hash"],
                                "prompt_versions": identity["prompt_versions"], "seed": config.seed,
                                "outcome": "complete" if after.status is Status.COMPLETE else "novel_successor",
                                "sampling_weight": 1.0 if after.status is Status.COMPLETE else 2.0}
                            event["records"].append(record)
                            for variant_index in range(config.analogies_per_transition):
                                if generator is None:
                                    from .model_backend import load_model_backend
                                    generator = load_model_backend(config.model)
                                try:
                                    variant_plan, artifacts = _offline_variant(config, generator, inventory, structure,
                                        state, position * config.analogies_per_transition + variant_index)
                                    match, failure = _verify_candidates(verifier, problem, prefix, variant_plan,
                                                                       inventory, after_hash)
                                    if match is None:
                                        raise CurriculumError(failure)
                                    event["records"].append({**record, **artifacts,
                                        "transition_id": _hash([transition_id, "variant", variant_index]),
                                        "parent_transition_id": transition_id, "grounded_plan": asdict(variant_plan),
                                        "matching_candidate": asdict(match)})
                                except Exception:
                                    event["augmentation_failure"] += 1
        _append_event(journal_path, event)
        # Use the serialized representation consistently across first run/resume.
        events.append(json.loads(_json(event)))
    if _identity(config) != identity:
        raise CurriculumError("source/config/model/project hashes changed during build")
    records = [record for event in events for record in event["records"]]
    base = [record for record in records if record["parent_transition_id"] is None]
    transitions_path = config.output_dir / "transitions.jsonl"
    manifest_path = config.output_dir / "manifest.json"
    summary = CurriculumSummary(transitions_path, manifest_path, len(events), len(base),
        sum(item["outcome"] == "novel_successor" for item in base),
        sum(item["outcome"] == "complete" for item in base), len(records) - len(base),
        **{name: sum(event["failure"] == name for event in events) for name in FAILURES},
        augmentation_failure=sum(event["augmentation_failure"] for event in events))
    _atomic(transitions_path, "".join(_json(record) + "\n" for record in records))
    summary_path = config.output_dir / "summary.json"
    _atomic(summary_path, _json(asdict(summary)) + "\n")
    # The manifest is the final commit marker and authenticates both projections.
    _atomic(manifest_path, _json({**identity, "build_id": build_id, "splits": splits,
        "transitions": transitions_path.name, "transitions_hash": _file_hash(transitions_path),
        "summary": summary_path.name, "summary_hash": _file_hash(summary_path)}) + "\n")
    return summary


def load_online_curriculum_states(manifest: Path, split: str) -> tuple[OnlineCurriculumState, ...]:
    """Load scheduled states by split; no tactic answers escape this boundary."""
    if split not in SPLITS:
        raise ValueError("split must be train, validation, or test")
    manifest = Path(manifest)
    data = _read_json(manifest)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise CurriculumError("unsupported curriculum schema")
    artifacts = {}
    for name, label in (("transitions", "transition"), ("summary", "summary")):
        try:
            path = manifest.parent / data[name]
            valid = (path.resolve().parent == manifest.parent.resolve()
                     and _file_hash(path) == data[f"{name}_hash"])
        except (OSError, KeyError, TypeError) as error:
            raise CurriculumError(f"{label} artifact hash could not be verified") from error
        if not valid:
            raise CurriculumError(f"{label} artifact hash mismatch")
        artifacts[name] = path
    path = artifacts["transitions"]
    result = []
    seen = set()
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row["schema_version"] != SCHEMA_VERSION or row["problem_id"] not in data["splits"][row["split"]]:
            raise CurriculumError("transition split or schema mismatch")
        if row["parent_transition_id"] is None and row["split"] == split:
            if row["transition_id"] in seen:
                raise CurriculumError("duplicate transition identity")
            seen.add(row["transition_id"])
            result.append(OnlineCurriculumState(row["problem_id"], tuple(row["accepted_prefix"]),
                row["before_state_hash"], split, row["transition_id"]))
    return tuple(result)


__all__ = ["CurriculumError", "CurriculumSummary", "OnlineCurriculumState", "derive_grounded_plan",
           "build_grounded_curriculum", "load_online_curriculum_states"]
