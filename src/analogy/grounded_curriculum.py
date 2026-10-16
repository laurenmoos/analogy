"""Offline, theorem-split, Lean-verified transitions with an answer-free online view.

The durable journal is authoritative. Each newline commits one verified prefix;
final artifacts are deterministic atomic projections of it.
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
from .grounding import GroundingOperator, enumerate_applications
from .lean_gym.corpus import load_problems, load_reference_proofs, validate_reference_coverage
from .lean_gym.models import VerificationStatus as Status
from .proof_inventory import LocalProofInventory, build_local_inventory
from .records import SymbolicState
from .replay import proof_state_fingerprint
from .prompts import PromptCatalog
from .tactic_compiler import TACTIC_COMPILER_VERSION, GroundedTacticCompiler


SCHEMA_VERSION = "grounded-curriculum-v2"
GROUNDING_SCHEMA_VERSION = "grounded-application-v1"
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
    unsupported_tactic: int
    compile_mismatch: int
    verifier_error: int
    repeated_state: int
    structure_failure: int


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
    toolchain = config.lean_project / "lean-toolchain"
    if not toolchain.is_file() or not re.fullmatch(
        r"leanprover/lean4:(?:v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?|nightly-\d{4}-\d{2}-\d{2}|[a-f0-9]{40})",
        toolchain.read_text().strip(),
    ):
        raise CurriculumError("lean_project must contain a pinned lean-toolchain")
    prompts = PromptCatalog.load(config.prompts.path)
    return {"schema_version": SCHEMA_VERSION, "config_hash": _hash(asdict(config)),
            "problems_hash": _file_hash(config.problems),
            "reference_proofs_hash": _file_hash(config.reference_proofs),
            "split_manifest_hash": _file_hash(config.split_manifest),
            "lean_project_hash": _tree_hash(config.lean_project, project=True),
            "lean_version": toolchain.read_text().strip(),
            "grounding_schema_version": GROUNDING_SCHEMA_VERSION,
            "compiler_version": TACTIC_COMPILER_VERSION,
            "operators": [item.value for item in GroundingOperator],
            "prompt_catalog_hash": prompts.content_hash,
            "prompt_versions": {"story_operator": prompts.version("story_operator")}}


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


def derive_grounding_operator(
    reference_tactic: str, inventory: LocalProofInventory, before, after,
) -> GroundingOperator:
    """Recognize the operator only; deterministic code owns every application."""
    _state_hash(before)
    _state_hash(after)
    tactic = " ".join(reference_tactic.split())
    local_names = {item.identifier for item in inventory.locals}
    if re.fullmatch(r"intro(?:\s+[^\s]+)?", tactic):
        return GroundingOperator.INTRODUCE
    if tactic == "constructor":
        return GroundingOperator.SPLIT
    if tactic == "left":
        return GroundingOperator.CHOOSE_LEFT
    if tactic == "right":
        return GroundingOperator.CHOOSE_RIGHT
    if match := re.fullmatch(r"rw\s*\[\s*(←\s*)?([^\[\],]+?)\s*\]", tactic):
        if match[2].strip() not in local_names:
            raise UnsupportedReferenceTactic("rewrite must cite a local equality")
        return (GroundingOperator.REWRITE_BACKWARD if match[1]
                else GroundingOperator.REWRITE_FORWARD)
    if tactic.startswith("cases ") and tactic[6:] in local_names:
        return GroundingOperator.CASES
    if tactic.startswith("apply ") and tactic[6:] in local_names:
        return GroundingOperator.APPLY_IMPLICATION
    if tactic.startswith("exact "):
        term = tactic[6:].strip()
        if term.startswith("False.elim"):
            return GroundingOperator.CONTRADICTION
        if re.fullmatch(r"[^\s]+\.left", term):
            return GroundingOperator.SELECT_LEFT
        if re.fullmatch(r"[^\s]+\.right", term):
            return GroundingOperator.SELECT_RIGHT
        if re.fullmatch(r"[^\s]+\.mp\s+.+", term):
            return GroundingOperator.IFF_FORWARD
        if re.fullmatch(r"[^\s]+\.mpr\s+.+", term):
            return GroundingOperator.IFF_BACKWARD
        if term in local_names:
            return GroundingOperator.EXACT_LOCAL
        if term.split(maxsplit=1)[0] in local_names:
            return GroundingOperator.APPLY_IMPLICATION
    raise UnsupportedReferenceTactic(f"unsupported reference tactic {reference_tactic!r}")


def _verify_candidates(verifier, problem, prefix, operator, inventory, expected_hash):
    applications = enumerate_applications(operator, inventory)
    candidates = GroundedTacticCompiler().compile_all(applications, inventory).candidates
    infrastructure = False
    for candidate in candidates:
        try:
            outcome = verifier.try_tactic(problem, prefix, candidate.text)
            if outcome.status in (Status.TIMEOUT, Status.INTERNAL_ERROR):
                infrastructure = True
                continue
            if outcome.accepted and _state_hash(outcome) == expected_hash:
                return candidate, applications, None
        except Exception:
            infrastructure = True
    return None, applications, "verifier_error" if infrastructure else "compile_mismatch"


def build_grounded_curriculum(config: CurriculumConfig, verifier) -> CurriculumSummary:
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
            raise CurriculumError("resume requires identical source/config/prompt/project hashes")
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
                 "seen_hashes": []}
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
                        operator = derive_grounding_operator(tactic, inventory, before, after)
                    except UnsupportedReferenceTactic:
                        event["failure"] = "unsupported_tactic"
                    except (ValueError, TypeError):
                        event["failure"] = "compile_mismatch"
                    else:
                        candidate, applications, failure = _verify_candidates(
                            verifier, problem, prefix, operator, inventory, after_hash,
                        )
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
                                "reference_tactic": tactic, "grounding_operator": operator.value,
                                "grounded_applications": [asdict(item) for item in applications],
                                "matching_candidate": asdict(candidate), "compiler_version": TACTIC_COMPILER_VERSION,
                                "grounding_schema_version": GROUNDING_SCHEMA_VERSION,
                                "lean_version": identity["lean_version"], "lean_project_hash": identity["lean_project_hash"],
                                "prompt_versions": identity["prompt_versions"], "seed": config.seed,
                                "outcome": "complete" if after.status is Status.COMPLETE else "novel_successor",
                                "sampling_weight": 1.0 if after.status is Status.COMPLETE else 2.0}
                            event["records"].append(record)
        _append_event(journal_path, event)
        # Use the serialized representation consistently across first run/resume.
        events.append(json.loads(_json(event)))
    if _identity(config) != identity:
        raise CurriculumError("source/config/prompt/project hashes changed during build")
    records = [record for event in events for record in event["records"]]
    base = [record for record in records if record["parent_transition_id"] is None]
    transitions_path = config.output_dir / "transitions.jsonl"
    manifest_path = config.output_dir / "manifest.json"
    summary = CurriculumSummary(transitions_path, manifest_path, len(events), len(base),
        sum(item["outcome"] == "novel_successor" for item in base),
        sum(item["outcome"] == "complete" for item in base),
        **{name: sum(event["failure"] == name for event in events) for name in FAILURES})
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


__all__ = ["CurriculumError", "CurriculumSummary", "OnlineCurriculumState", "derive_grounding_operator",
           "build_grounded_curriculum", "load_online_curriculum_states"]
