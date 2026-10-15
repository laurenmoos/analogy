from __future__ import annotations

import gzip
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "standalone_analogy_recovery.py"
LAUNCHER_PATH = Path(__file__).resolve().parents[1] / "run_experiment.sh"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("standalone_analogy_recovery", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_masked_problem_payload_has_no_reference_channel() -> None:
    module = load_script()
    problem = module.Problem("secret", "(P : Prop)", "P → P")

    payload = module.masked_problem_payload(problem)

    rendered = json.dumps(payload, sort_keys=True)
    assert rendered == '{"binders": "(P : Prop)", "problem_id": "secret", "target": "P \\u2192 P"}'
    assert "intro hidden_name" not in rendered


def test_poetry_pairs_only_adjacent_lines_from_same_work(tmp_path: Path) -> None:
    module = load_script()
    path = tmp_path / "poetry.ndjson.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in (
            {"gid": "a", "s": "first"},
            {"gid": "a", "s": "second"},
            {"gid": "b", "s": "third"},
        ):
            handle.write(json.dumps(row) + "\n")

    transitions = tuple(module.iter_poetry_transitions(path))

    assert [(item.before, item.after) for item in transitions] == [("first", "second")]


def test_balanced_sampling_is_deterministic_and_deduplicated() -> None:
    module = load_script()
    poetry = tuple(
        module.Transition(f"p{i}", "poetry", "a", f"b{i}", {})
        for i in range(4)
    )
    physics = tuple(
        module.Transition(f"f{i}", "physics", "a", f"b{i}", {})
        for i in range(4)
    )

    first = module.sample_transitions(poetry, physics, per_domain=2, seed=7)
    second = module.sample_transitions(poetry, physics, per_domain=2, seed=7)

    assert first == second
    assert [item.domain for item in first].count("poetry") == 2
    assert [item.domain for item in first].count("physics") == 2
    assert len({item.id for item in first}) == 4


def test_prompts_cannot_receive_reference_proof() -> None:
    module = load_script()
    problem = module.Problem("identity", "(P : Prop)", "P → P")

    prompt = module.candidate_prompt(problem, "⊢ P → P", (), (), 4)

    rendered = "\n".join(message.content for message in prompt)
    assert "P → P" in rendered
    assert "reference" not in rendered.casefold()
    assert "exact hidden" not in rendered


def valid_transformation_payload() -> dict[str, object]:
    return {
        "operation": "select",
        "input_arity": "two",
        "output_arity": "one",
        "permutation": "none",
        "information_flow": ["select", "discard"],
        "focus_before": "a composite object",
        "focus_after": "its second component",
        "domain_neutral_summary": "Select one component from a two-part object.",
    }


def test_transformation_record_parser_accepts_only_exact_schema() -> None:
    module = load_script()
    parsed = module.parse_transformation_record(json.dumps(valid_transformation_payload()))

    assert parsed.operation is module.OperationClass.SELECT
    assert parsed.information_flow == (
        module.InformationFlow.SELECT,
        module.InformationFlow.DISCARD,
    )

    invalid = valid_transformation_payload()
    invalid["domain_neutral_summary"] = "Use exact h.right."
    invalid["extra"] = True
    with pytest.raises(module.ModelOutputError):
        module.parse_transformation_record(json.dumps(invalid))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.pop("operation"),
        lambda value: value.update(operation="invented"),
        lambda value: value.update(information_flow=["select", "select"]),
        lambda value: value.update(information_flow=["discard", "select"]),
        lambda value: value.update(focus_before="  "),
        lambda value: value.update(domain_neutral_summary="Apply cases to split the object."),
        lambda value: value.update(domain_neutral_summary="x" * 241),
    ],
)
def test_transformation_record_parser_rejects_invalid_fields(mutation) -> None:
    module = load_script()
    payload = valid_transformation_payload()
    mutation(payload)

    with pytest.raises(module.ModelOutputError):
        module.parse_transformation_record(json.dumps(payload))


def test_transformation_prompt_declares_closed_schema() -> None:
    module = load_script()
    transition = module.Transition("p1", "poetry", "whole", "part", {})

    rendered = "\n".join(
        message.content for message in module.render_transformation_prompt(transition)
    )

    assert '"operation"' in rendered
    assert "select_branch" in rendered
    assert "Lean tactic" in rendered
    assert "exactly one JSON object" in rendered


@pytest.mark.parametrize(
    "text",
    ["{}", "[]", '["intro h", "intro h"]', '["bad\\nline"]'],
)
def test_candidate_parser_rejects_malformed_or_duplicate_tactics(text: str) -> None:
    module = load_script()
    with pytest.raises(module.ModelOutputError):
        module.parse_candidates(text, top_k=4)


def test_candidate_parser_normalizes_qwen_bare_tactic_array() -> None:
    module = load_script()

    assert module.parse_candidates(
        "[split, cases h, exact h.right, assumption]", top_k=4
    ) == ("split", "cases h", "exact h.right", "assumption")


def test_candidate_parser_preserves_commas_nested_inside_tactic_syntax() -> None:
    module = load_script()

    assert module.parse_candidates(
        "[simp [h₁, h₂], exact ⟨left, right⟩]", top_k=4
    ) == ("simp [h₁, h₂]", "exact ⟨left, right⟩")


def test_ranking_parser_appends_omitted_indices_in_original_order() -> None:
    module = load_script()
    assert module.parse_ranking("[2, 0]", candidate_count=4) == (2, 0, 1, 3)


def test_ranking_parser_accepts_qwen_json_code_fence() -> None:
    module = load_script()
    assert module.parse_ranking(
        "```json\n[0, 1, 2, 3]\n```", candidate_count=4
    ) == (0, 1, 2, 3)


class FakeBatch(dict):
    def __init__(self, torch_module) -> None:
        super().__init__(
            input_ids=torch_module.tensor([[10, 11, 0]]),
            attention_mask=torch_module.tensor([[1, 1, 0]]),
        )

    def to(self, device: str):
        return self


class FakeTokenizer:
    def __init__(self, torch_module) -> None:
        self.torch = torch_module
        self.template_kwargs = None
        self.template_history = []

    def apply_chat_template(self, messages, **kwargs):
        self.template_kwargs = kwargs
        self.template_history.append(kwargs)
        return "rendered chat"

    def __call__(self, texts, **kwargs):
        return FakeBatch(self.torch)

    def decode(self, tokens, **kwargs):
        values = tokens.tolist() if hasattr(tokens, "tolist") else list(tokens)
        if values and values[-1] == 50:
            return "<think>consider the available evidence"
        return '["intro h"]'


class FakeModel:
    def __init__(self, torch_module, final_vector=(3.0, 4.0)) -> None:
        self.torch = torch_module
        self.device = "cpu"
        self.config = SimpleNamespace(_commit_hash="revision-1", num_hidden_layers=2)
        self.parameter = torch_module.nn.Parameter(torch_module.tensor([1.0]))
        self.final_vector = final_vector
        self.eval_called = False
        self.generate_kwargs = None
        self.generate_history = []

    def parameters(self):
        return (self.parameter,)

    def eval(self):
        self.eval_called = True
        return self

    def __call__(self, **kwargs):
        torch = self.torch
        final = torch.tensor(
            [[[9.0, 9.0], list(self.final_vector), [8.0, 8.0]]],
            dtype=torch.float16,
        )
        return SimpleNamespace(
            hidden_states=(
                torch.zeros((1, 3, 2)),
                torch.ones((1, 3, 2)),
                final,
            )
        )

    def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        self.generate_history.append(kwargs)
        final_token = 50 if len(self.generate_history) == 1 and kwargs.get("max_new_tokens") == 16 else 99
        return self.torch.tensor([[10, 11, 0, final_token]])


def make_qwen(module, final_vector=(3.0, 4.0)):
    import torch

    return module.QwenModel(
        tokenizer=FakeTokenizer(torch),
        model=FakeModel(torch, final_vector),
        torch_module=torch,
        device="cpu",
        max_new_tokens=16,
        timeout_seconds=5.0,
    )


def test_embedding_uses_final_nonpadding_token_and_normalizes_float32() -> None:
    module = load_script()
    model = make_qwen(module)

    embedding = model.embed("structural need", layer=-1)

    assert embedding.values == pytest.approx((0.6, 0.8))
    assert embedding.layer == 2
    assert embedding.token_index == 1
    assert embedding.dimension == 2
    assert embedding.model_revision == "revision-1"
    assert embedding.text_hash == module.sha256_text("structural need")
    assert model._model.parameter.requires_grad is False
    assert model._model.eval_called is True


def test_adaptive_generation_policy_scales_reasoning_for_difficulty() -> None:
    module = load_script()

    easy = module.generation_policy(1, base_max_new_tokens=256, base_timeout_seconds=60)
    medium = module.generation_policy(3, base_max_new_tokens=256, base_timeout_seconds=60)
    hard = module.generation_policy(5, base_max_new_tokens=256, base_timeout_seconds=60)

    assert (easy.thinking, easy.reasoning_max_tokens, easy.answer_max_tokens) == (False, 0, 256)
    assert (medium.thinking, medium.reasoning_max_tokens, medium.reasoning_timeout_seconds) == (True, 128, 120)
    assert (hard.thinking, hard.reasoning_max_tokens, hard.reasoning_timeout_seconds) == (True, 256, 180)
    assert hard.answer_max_tokens == 256
    assert hard.answer_timeout_seconds == 120


def test_complete_json_is_extracted_only_after_thinking() -> None:
    module = load_script()
    text = '<think>compare [3, 2]</think>\n```json\n[1, 0, 2, 3]\n```'

    assert module.extract_complete_json(text, "array", thinking=True) == "[1, 0, 2, 3]"
    assert module.extract_complete_json("<think>still reasoning [0]", "array", thinking=True) is None


def test_qwen_hard_generation_separates_reasoning_and_structured_answer() -> None:
    module = load_script()
    model = make_qwen(module)
    events = []
    model.set_generation_sink(events.append)

    output = model.generate(
        (module.Message("user", "rank"),),
        seed=7,
        operation="ranking",
        difficulty=5,
        expected_json="array",
    )

    assert output == '["intro h"]'
    assert [item["enable_thinking"] for item in model._tokenizer.template_history] == [True, False]
    assert len(model._model.generate_history) == 2
    assert model._model.generate_history[0]["max_new_tokens"] == 16
    assert model._model.generate_history[0]["max_time"] == 15.0
    assert model._model.generate_history[1]["max_new_tokens"] == 16
    assert model._model.generate_history[1]["max_time"] == 10.0
    assert [event["phase"] for event in events] == ["reasoning", "answer"]
    assert events[0]["reasoning_tokens"] == 1
    assert events[1]["stop_reason"] == "complete_json"
    assert events[1]["answer_tokens"] == 1
    assert events[1]["output_text"] == '["intro h"]'
    assert "output_text" not in events[0]


@pytest.mark.parametrize("layer", [-4, 3])
def test_embedding_rejects_out_of_range_layers(layer: int) -> None:
    module = load_script()
    with pytest.raises(module.ModelConfigurationError, match="layer"):
        make_qwen(module).embed("need", layer=layer)


def test_embedding_rejects_zero_vector() -> None:
    module = load_script()
    with pytest.raises(module.ModelOutputError, match="zero|finite"):
        make_qwen(module, final_vector=(0.0, 0.0)).embed("need", layer=-1)


def indexed_fixture(module):
    items = []
    for identifier, vector in (("x", (1.0, 0.0)), ("y", (0.8, 0.6)), ("z", (0.0, 1.0))):
        transition = module.Transition(identifier, "poetry", "before", f"after {identifier}", {})
        embedding = module.Embedding(vector, 0, 0, "rev", module.sha256_text(identifier))
        items.append(module.IndexedTransition(transition, f"change {identifier}", embedding, f"key-{identifier}"))
    return tuple(items)


def test_cosine_and_random_retrieval_use_same_eligible_index() -> None:
    module = load_script()
    index = indexed_fixture(module)

    nearest = module.cosine_retrieve((1.0, 0.0), index, count=2)
    random_hits = module.random_retrieve(index, count=2, seed=11)

    assert [hit.item.transition.id for hit in nearest] == ["x", "y"]
    assert len(random_hits) == len(nearest) == 2
    assert {hit.item.transition.id for hit in random_hits} <= {
        item.transition.id for item in index
    }
    assert all(hit.similarity is None for hit in random_hits)


def test_cache_key_changes_with_prompt_model_layer_or_source() -> None:
    module = load_script()
    transition = module.Transition("x", "poetry", "before", "after", {})
    changed = module.Transition("y", "poetry", "before", "different", {})
    base = module.transformation_cache_key(transition, "rev-a", -1, "transformation-v1")

    assert base != module.transformation_cache_key(transition, "rev-b", -1, "transformation-v1")
    assert base != module.transformation_cache_key(transition, "rev-a", -2, "transformation-v1")
    assert base != module.transformation_cache_key(transition, "rev-a", -1, "transformation-v2")
    assert base != module.transformation_cache_key(changed, "rev-a", -1, "transformation-v1")


def test_build_index_rejects_non_normalized_cached_vector(tmp_path: Path) -> None:
    module = load_script()
    model = SpyModel()
    transition = module.Transition("x", "poetry", "before", "after", {})
    key = module.transformation_cache_key(
        transition,
        model.model_revision,
        -1,
        module.TRANSFORMATION_PROMPT_VERSION,
    )
    cache = tmp_path / f"{key}.json"
    cache.write_text(
        json.dumps(
            {
                "cache_key": key,
                "transition_id": "x",
                "transformation": "one element becomes available",
                "embedding": {
                    "values": [3.0, 4.0],
                    "layer": -1,
                    "token_index": 0,
                    "model_revision": model.model_revision,
                    "text_hash": module.sha256_text("one element becomes available"),
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.ExperimentError, match="normalized"):
        module.build_index((transition,), model, tmp_path, -1)


class SpyModel:
    model_revision = "spy-revision"

    def __init__(self) -> None:
        self.generation_calls = []
        self.embedding_calls = []

    def generate(self, messages, *, seed: int):
        self.generation_calls.append((messages, seed))
        system = messages[0].content
        if "candidate-v1" in system:
            return '["intro h", "exact h"]'
        if "proof-need-v1" in system:
            return "make a hidden element available"
        if "transformation-v1" in system:
            return json.dumps({
                "operation": "select",
                "input_arity": "two",
                "output_arity": "one",
                "permutation": "none",
                "information_flow": ["select", "discard"],
                "focus_before": "a composite object",
                "focus_after": "one component",
                "domain_neutral_summary": "One component becomes available from a composite object.",
            })
        return "[0]"

    def embed(self, text: str, layer: int):
        self.embedding_calls.append((text, layer))
        module = load_script()
        return module.Embedding((1.0, 0.0), layer, 0, self.model_revision, module.sha256_text(text))


class PolicySpyModel(SpyModel):
    def __init__(self, module) -> None:
        super().__init__()
        self.module = module
        self.generation_options = []

    def generate(self, messages, *, seed: int, **kwargs):
        self.generation_options.append(kwargs)
        return super().generate(messages, seed=seed)

    def embed(self, text: str, layer: int):
        self.embedding_calls.append((text, layer))
        return self.module.Embedding(
            (1.0, 0.0), layer, 0, self.model_revision, self.module.sha256_text(text)
        )


def test_no_retrieval_never_calls_embedding_or_retriever() -> None:
    module = load_script()
    model = SpyModel()
    problem = module.Problem("identity", "(P : Prop)", "P → P")

    decision = module.rank_for_condition(
        module.Condition.NONE,
        problem,
        "goal",
        ("exact h",),
        None,
        (),
        model,
        2,
        0,
    )

    assert decision.indices == (0,)
    assert decision.hits == ()
    assert model.embedding_calls == []


def test_candidate_pool_store_shares_pool_by_state_fingerprint() -> None:
    module = load_script()
    model = SpyModel()
    store = module.CandidatePoolStore(model)
    problem = module.Problem("identity", "(P : Prop)", "P → P")

    first = store.get(problem, "⊢ P → P", (), (), seed=3, top_k=4)
    second = store.get(problem, "⊢ P → P", (), (), seed=3, top_k=4)
    other = store.get(problem, "h : P\n⊢ P", ("intro h",), (), seed=3, top_k=4)

    assert first == second == ("intro h", "exact h")
    assert other == ("intro h", "exact h")
    candidate_calls = [call for call in model.generation_calls if "candidate-v1" in call[0][0].content]
    assert len(candidate_calls) == 2


def test_generation_operations_receive_difficulty_and_json_contract(tmp_path: Path) -> None:
    module = load_script()
    model = PolicySpyModel(module)
    problem = module.Problem("hard", "(P : Prop)", "P → P", difficulty=5)
    store = module.CandidatePoolStore(model)

    store.get(problem, "⊢ P → P", (), (), seed=0, top_k=2)
    index = module.build_index(
        (module.Transition("p", "poetry", "closed", "open", {}),),
        model,
        tmp_path / "cache",
        -1,
    )
    module.rank_for_condition(
        module.Condition.ANALOGY,
        problem,
        "⊢ P → P",
        ("intro h", "exact h"),
        None,
        index,
        model,
        1,
        0,
    )

    assert model.generation_options == [
        {"operation": "candidate", "difficulty": 5, "expected_json": "array"},
        {"operation": "transformation", "difficulty": 1, "expected_json": "object"},
        {"operation": "proof_need", "difficulty": 5, "expected_json": None},
        {"operation": "ranking", "difficulty": 5, "expected_json": "array"},
    ]


def open_result(module, state: str):
    return module.LeanResult(module.LeanStatus.OPEN, True, state, (), 0, 1)


def complete_result(module):
    return module.LeanResult(module.LeanStatus.COMPLETE, True, None, (), 0, 1)


class ScriptedVerifier:
    def __init__(self, module, attempts):
        self.module = module
        self.attempts = attempts

    def initial_state(self, problem):
        return open_result(self.module, f"⊢ {problem.target}")

    def try_tactic(self, problem, prefix, candidate):
        return self.attempts.get(
            (prefix, candidate),
            self.module.LeanResult(
                self.module.LeanStatus.REJECTED,
                False,
                None,
                ("rejected",),
                1,
                1,
            ),
        )


class FixedPoolStore:
    def __init__(self, candidates):
        self.candidates = candidates

    def get(self, problem, state, accepted, failed, *, seed, top_k):
        return tuple(self.candidates[:top_k])


class IdentityRanker:
    def rank(self, condition, problem, state, accepted, failed, candidates, seed, deadline=None):
        module = load_script()
        return module.RankingDecision(candidates, tuple(range(len(candidates))))


def test_search_counts_only_completion_as_solved() -> None:
    module = load_script()
    problem = module.Problem("identity", "(P : Prop)", "P → P")
    verifier = ScriptedVerifier(
        module,
        {
            ((), "idle"): open_result(module, "⊢ P → P"),
            ((), "intro h"): open_result(module, "h : P\n⊢ P"),
            (("intro h",), "exact h"): complete_result(module),
        },
    )
    attempts = []

    result = module.search_problem(
        problem,
        module.Condition.NONE,
        0,
        FixedPoolStore(("idle", "intro h", "exact h")),
        IdentityRanker(),
        verifier,
        module.SearchLimits(8, 16, 5.0, top_k=3),
        attempts.append,
    )

    assert result.solved is True
    assert result.tactics == ("intro h", "exact h")
    assert result.repeated_state_count == 1
    assert any(item.disposition == "repeated" for item in attempts)


def test_open_novel_state_at_budget_is_not_solved() -> None:
    module = load_script()
    problem = module.Problem("identity", "(P : Prop)", "P → P")
    verifier = ScriptedVerifier(
        module,
        {((), "intro h"): open_result(module, "h : P\n⊢ P")},
    )

    result = module.search_problem(
        problem,
        module.Condition.NONE,
        0,
        FixedPoolStore(("intro h",)),
        IdentityRanker(),
        verifier,
        module.SearchLimits(
            max_expansions=1,
            max_verifier_calls=1,
            theorem_timeout_seconds=5.0,
            top_k=1,
        ),
        list().append,
    )

    assert result.solved is False
    assert result.status == "budget_exhausted"


def test_standalone_real_lean_reports_rejected_open_and_complete() -> None:
    module = load_script()
    project = Path(__file__).resolve().parents[1] / "src" / "analogy" / "lean_gym" / "lean_project"
    verifier = module.LeanVerifier(project, timeout_seconds=10.0)
    problem = module.Problem("identity", "(P : Prop)", "P → P")

    initial = verifier.initial_state(problem)
    rejected = verifier.try_tactic(problem, (), "exact missing")
    opened = verifier.try_tactic(problem, (), "intro h")
    complete = verifier.try_tactic(problem, ("intro h",), "exact h")

    assert initial.status is module.LeanStatus.OPEN
    assert rejected.status is module.LeanStatus.REJECTED
    assert opened.status is module.LeanStatus.OPEN
    assert opened.state is not None and "h : P" in opened.state
    assert complete.status is module.LeanStatus.COMPLETE
    assert complete.accepted is True
    assert all("lean-gym-" not in diagnostic for diagnostic in rejected.diagnostics)


def trial(module, problem_id: str, condition: str, solved: bool):
    return module.TrialResult(
        problem_id=problem_id,
        condition=condition,
        seed=0,
        status="complete" if solved else "exhausted",
        solved=solved,
        tactics=("exact h",) if solved else (),
        expansion_count=1,
        verifier_call_count=1,
        rejected_count=0,
        repeated_state_count=0,
        novel_state_count=0,
        failure=None if solved else "exhausted",
        elapsed_seconds=0.1,
    )


def test_summary_uses_no_retrieval_to_define_solved_subgroups() -> None:
    module = load_script()
    trials = (
        trial(module, "a", "none", True),
        trial(module, "a", "random", False),
        trial(module, "a", "analogy", True),
        trial(module, "b", "none", False),
        trial(module, "b", "random", False),
        trial(module, "b", "analogy", True),
    )

    summary = module.summarize_trials(trials)

    assert summary["subgroups"]["baseline_solved"] == ["a"]
    assert summary["subgroups"]["baseline_unsolved"] == ["b"]
    assert summary["comparisons"]["analogy_vs_none"]["newly_solved"] == ["b"]
    assert summary["comparisons"]["random_vs_none"]["regressed"] == ["a"]


class CompletingVerifier:
    def __init__(self, module):
        self.module = module

    def initial_state(self, problem):
        return open_result(self.module, f"⊢ {problem.target}")

    def try_tactic(self, problem, prefix, candidate):
        return complete_result(self.module)


class ExperimentModel(SpyModel):
    def __init__(self, module):
        super().__init__()
        self.module = module
        self.generation_sink = None

    def set_generation_sink(self, sink):
        self.generation_sink = sink

    def generate(self, messages, *, seed: int):
        self.generation_calls.append((messages, seed))
        if self.generation_sink is not None:
            self.generation_sink(
                {
                    "operation": "test",
                    "generated_tokens": 1,
                    "reasoning_tokens": 0,
                    "answer_tokens": 1,
                    "stop_reason": "eos",
                }
            )
        system = messages[0].content
        if "candidate-v1" in system:
            return '["exact h"]'
        if "proof-need-v1" in system:
            return "make the available element satisfy the goal"
        if "transformation-v1" in system:
            return json.dumps(valid_transformation_payload())
        return "[0]"

    def embed(self, text: str, layer: int):
        self.embedding_calls.append((text, layer))
        return self.module.Embedding(
            (1.0, 0.0),
            layer,
            0,
            self.model_revision,
            self.module.sha256_text(text),
        )


def test_run_experiment_writes_parseable_outputs_without_reference_leakage(tmp_path: Path) -> None:
    module = load_script()
    model = ExperimentModel(module)
    problems = (
        module.Problem("sentinel", "(P : Prop) (h : P)", "P"),
        module.Problem("ordinary", "(P : Prop) (h : P)", "P"),
    )
    references = {
        "sentinel": module.ReferenceProof("sentinel", ("exact DO_NOT_LEAK",)),
        "ordinary": module.ReferenceProof("ordinary", ("exact h",)),
    }
    transitions = (
        module.Transition("p", "poetry", "closed", "open", {}),
        module.Transition("f", "physics", "off", "on", {}),
    )
    output = tmp_path / "run"
    config = module.ExperimentConfig(
        problems=problems,
        reference_proofs=references,
        transitions=transitions,
        conditions=(module.Condition.NONE, module.Condition.RANDOM, module.Condition.ANALOGY),
        seeds=(0,),
        output_dir=output,
        cache_dir=tmp_path / "cache",
        hidden_layer=-1,
        retrieval_count=1,
        search_limits=module.SearchLimits(2, 4, 5.0, top_k=1),
        model_name="fake",
    )

    result_path = module.run_experiment(
        config,
        model_factory=lambda _: model,
        verifier_factory=lambda _: CompletingVerifier(module),
    )

    assert result_path == output
    for name in ("transitions.jsonl", "attempts.jsonl", "trials.jsonl", "generations.jsonl"):
        rows = [json.loads(line) for line in (output / name).read_text(encoding="utf-8").splitlines()]
        assert rows
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["run_complete"] is True
    trial_rows = [json.loads(line) for line in (output / "trials.jsonl").read_text(encoding="utf-8").splitlines()]
    sentinel = next(row for row in trial_rows if row["problem_id"] == "sentinel" and row["condition"] == "none")
    ordinary = next(row for row in trial_rows if row["problem_id"] == "ordinary" and row["condition"] == "none")
    assert sentinel["exact_reference_overlap"] == 0
    assert ordinary["exact_reference_overlap"] == 1
    captured_prompts = "\n".join(
        message.content
        for messages, _ in model.generation_calls
        for message in messages
    )
    assert "DO_NOT_LEAK" not in captured_prompts
    assert "DO_NOT_LEAK" not in (output / "attempts.jsonl").read_text(encoding="utf-8")
    assert "DO_NOT_LEAK" not in (output / "transitions.jsonl").read_text(encoding="utf-8")

    with pytest.raises(module.ExperimentError, match="output"):
        module.run_experiment(
            config,
            model_factory=lambda _: model,
            verifier_factory=lambda _: CompletingVerifier(module),
        )


def test_dry_run_validates_real_inputs_without_loading_model(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_script()
    monkeypatch.setattr(
        module.QwenModel,
        "load",
        classmethod(lambda cls, *args, **kwargs: pytest.fail("model must not load")),
    )

    status = module.main(
        [
            "--dry-run",
            "--poetry-limit",
            "2",
            "--physics-limit",
            "2",
            "--problem",
            "imp_intro_01",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert status == 0
    assert output["problem_count"] == 1
    assert output["transition_counts"] == {"physics": 2, "poetry": 2}
    assert output["condition_count"] == 3
    assert output["lean_available"] is True


@pytest.mark.parametrize(
    "arguments",
    [
        ["--dry-run", "--poetry-limit", "0"],
        ["--dry-run", "--physics-limit", "0"],
        ["--dry-run", "--poetry-limit", "1", "--physics-limit", "1", "--retrieval-count", "3"],
        ["--dry-run", "--condition", "none", "--condition", "none"],
        ["--dry-run", "--problem", "unknown_problem"],
    ],
)
def test_cli_rejects_invalid_experiment_arguments(arguments, capsys) -> None:
    module = load_script()
    assert module.main(arguments) == 2
    assert capsys.readouterr().err


def test_cli_rejects_missing_input_and_existing_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_script()
    missing = tmp_path / "missing.json"
    assert module.main(["--dry-run", "--problems", str(missing)]) == 2
    assert capsys.readouterr().err

    output = tmp_path / "occupied"
    output.mkdir()
    (output / "keep").write_text("user data", encoding="utf-8")
    assert module.main(["--output-dir", str(output), "--problem", "imp_intro_01"]) == 2
    assert capsys.readouterr().err


def test_cli_rejects_unavailable_lean(monkeypatch, capsys) -> None:
    module = load_script()
    monkeypatch.setattr(module.shutil, "which", lambda executable: None)
    assert module.main(["--dry-run", "--problem", "imp_intro_01"]) == 2
    assert "lake" in capsys.readouterr().err


def test_three_condition_smoke_uses_real_lean_and_shared_pool(tmp_path: Path) -> None:
    module = load_script()
    model = ExperimentModel(module)
    problem = module.Problem("identity", "(P : Prop) (h : P)", "P")
    transitions = (
        module.Transition("p", "poetry", "closed", "open", {}),
        module.Transition("f", "physics", "off", "on", {}),
    )
    project = Path(__file__).resolve().parents[1] / "src" / "analogy" / "lean_gym" / "lean_project"
    config = module.ExperimentConfig(
        problems=(problem,),
        reference_proofs={"identity": module.ReferenceProof("identity", ("exact h",))},
        transitions=transitions,
        conditions=(module.Condition.NONE, module.Condition.RANDOM, module.Condition.ANALOGY),
        seeds=(0,),
        output_dir=tmp_path / "real-lean-run",
        cache_dir=tmp_path / "cache",
        hidden_layer=-1,
        retrieval_count=1,
        search_limits=module.SearchLimits(2, 4, 10.0, top_k=1),
        model_name="fake",
    )

    output = module.run_experiment(
        config,
        model_factory=lambda _: model,
        verifier_factory=lambda _: module.LeanVerifier(project, timeout_seconds=10.0),
    )

    trials = [json.loads(line) for line in (output / "trials.jsonl").read_text(encoding="utf-8").splitlines()]
    attempts = [json.loads(line) for line in (output / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(trials) == 3
    assert {row["condition"] for row in trials} == {"none", "random", "analogy"}
    assert all(row["solved"] for row in trials)
    assert {tuple(row["candidates"]) for row in attempts} == {("exact h",)}
    candidate_calls = [call for call in model.generation_calls if "candidate-v1" in call[0][0].content]
    assert len(candidate_calls) == 1


def test_direct_script_execution_defines_all_helpers_before_main(tmp_path: Path) -> None:
    problems = tmp_path / "problems.json"
    proofs = tmp_path / "proofs.json"
    poetry = tmp_path / "poetry.ndjson.gz"
    physics = tmp_path / "physics.jsonl"
    problems.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"repository": "test", "license": "MIT"},
                "problems": [
                    {
                        "id": "identity",
                        "title": "Identity",
                        "binders": "(P : Prop)",
                        "target": "P → P",
                        "source_path": "test",
                        "source_level": "test",
                        "connective": "implication",
                        "difficulty": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    proofs.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "proofs": [{"problem_id": "identity", "tactics": ["intro h", "exact h"]}],
            }
        ),
        encoding="utf-8",
    )
    with gzip.open(poetry, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"gid": "a", "s": "closed"}) + "\n")
        handle.write(json.dumps({"gid": "a", "s": "open"}) + "\n")
    physics.write_text(
        json.dumps(
            {
                "example_id": "e",
                "stories": [{"story_id": 0, "sentences": ["off", "on"], "plausible": True}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    lean_project = Path(__file__).resolve().parents[1] / "src" / "analogy" / "lean_gym" / "lean_project"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--dry-run",
            "--problems",
            str(problems),
            "--reference-proofs",
            str(proofs),
            "--poetry",
            str(poetry),
            "--physics",
            str(physics),
            "--lean-project",
            str(lean_project),
            "--poetry-limit",
            "1",
            "--physics-limit",
            "1",
            "--retrieval-count",
            "1",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["problem_count"] == 1


def test_root_launcher_runs_dry_run_from_any_directory(tmp_path: Path) -> None:
    completed = subprocess.run(
        [str(LAUNCHER_PATH), "--dry-run", "--condition", "none"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["problem_count"] == 3
    assert payload["condition_count"] == 1


def test_shared_candidate_generation_is_excluded_from_each_condition_budget(monkeypatch) -> None:
    module = load_script()
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    class SlowFirstPool(FixedPoolStore):
        used = False

        def get(self, *args, **kwargs):
            if not self.used:
                clock[0] += 2.0
                self.used = True
            return super().get(*args, **kwargs)

    result = module.search_problem(
        module.Problem("identity", "(P : Prop) (h : P)", "P"),
        module.Condition.NONE,
        0,
        SlowFirstPool(("exact h",)),
        IdentityRanker(),
        CompletingVerifier(module),
        module.SearchLimits(1, 1, 1.0, top_k=1),
        list().append,
    )

    assert result.solved is True


def test_late_verifier_completion_is_rejected(monkeypatch) -> None:
    module = load_script()
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    class LateVerifier(CompletingVerifier):
        def try_tactic(self, problem, prefix, candidate, timeout_seconds=None):
            clock[0] += 100.0
            return complete_result(self.module)

    result = module.search_problem(
        module.Problem("identity", "(P : Prop) (h : P)", "P"),
        module.Condition.NONE,
        0,
        FixedPoolStore(("exact h",)),
        IdentityRanker(),
        LateVerifier(module),
        module.SearchLimits(1, 1, 1.0, top_k=1),
        list().append,
    )

    assert result.solved is False
    assert result.status == "timeout"


def test_operational_failure_writes_incomplete_artifacts(tmp_path: Path) -> None:
    module = load_script()

    class FailingModel(ExperimentModel):
        def generate(self, messages, *, seed: int, **kwargs):
            if "candidate-v1" in messages[0].content:
                raise TimeoutError("model stalled")
            return super().generate(messages, seed=seed)

    problem = module.Problem("identity", "(P : Prop) (h : P)", "P")
    output = tmp_path / "failed-run"
    config = module.ExperimentConfig(
        problems=(problem,),
        reference_proofs={"identity": module.ReferenceProof("identity", ("exact h",))},
        transitions=(),
        conditions=(module.Condition.NONE,),
        seeds=(0,),
        output_dir=output,
        cache_dir=None,
        hidden_layer=-1,
        retrieval_count=1,
        search_limits=module.SearchLimits(1, 1, 5.0, top_k=1),
        model_name="fake",
    )

    with pytest.raises(module.ExperimentRunIncomplete):
        module.run_experiment(
            config,
            model_factory=lambda _: FailingModel(module),
            verifier_factory=lambda _: CompletingVerifier(module),
        )

    assert json.loads((output / "config.json").read_text())["run_complete"] is False
    summary = json.loads((output / "summary.json").read_text())
    assert summary["run_complete"] is False
    assert summary["operational_failures"][0]["status"] == "model_failure"


def test_retrieval_query_uses_prefix_failures_and_is_persisted(tmp_path: Path) -> None:
    module = load_script()
    model = ExperimentModel(module)
    problem = module.Problem("identity", "(P : Prop) (h : P)", "P")
    index = module.build_index(
        (module.Transition("p", "poetry", "closed", "open", {}),),
        model,
        tmp_path / "cache",
        -1,
    )

    decision = module.rank_for_condition(
        module.Condition.ANALOGY,
        problem,
        "h : P\n⊢ P",
        ("exact h",),
        None,
        index,
        model,
        1,
        0,
        accepted=("intro h",),
        failed=("assumption",),
    )

    rendered = "\n".join(
        message.content for messages, _ in model.generation_calls for message in messages
    )
    assert "intro h" in rendered
    assert "assumption" in rendered
    assert decision.proof_need.description in rendered


def test_manifest_records_inputs_model_lean_environment_and_problem_statements(tmp_path: Path) -> None:
    module = load_script()
    problem = module.Problem("identity", "(P : Prop) (h : P)", "P")
    config = module.ExperimentConfig(
        problems=(problem,),
        reference_proofs={"identity": module.ReferenceProof("identity", ("exact h",))},
        transitions=(),
        conditions=(module.Condition.NONE,),
        seeds=(7,),
        output_dir=tmp_path / "run",
        cache_dir=None,
        hidden_layer=-1,
        retrieval_count=1,
        search_limits=module.SearchLimits(1, 1, 5.0, top_k=1),
        model_name="fake",
        input_manifest={"problems": {"path": "/data/problems.json", "sha256": "abc"}},
        lean_configuration={"project": "/lean", "version": "Lean 4.x"},
    )

    module.run_experiment(
        config,
        model_factory=lambda _: ExperimentModel(module),
        verifier_factory=lambda _: CompletingVerifier(module),
    )
    manifest = json.loads((config.output_dir / "config.json").read_text())
    attempt = json.loads((config.output_dir / "attempts.jsonl").read_text().splitlines()[0])

    assert manifest["inputs"]["problems"]["sha256"] == "abc"
    assert manifest["problems"][0]["target"] == "P"
    assert manifest["model"]["resolved_revision"] == "spy-revision"
    assert manifest["lean"]["version"] == "Lean 4.x"
    assert manifest["environment"]["python"]
    assert attempt["operation_seeds"]["candidate"]


def test_none_only_cli_accepts_missing_and_short_retrieval_corpora(tmp_path: Path) -> None:
    module = load_script()
    parser = module.build_parser()
    args = parser.parse_args(
        [
            "--dry-run",
            "--condition", "none",
            "--poetry", str(tmp_path / "missing-poetry.gz"),
            "--physics", str(tmp_path / "missing-physics.jsonl"),
            "--problem", "imp_intro_01",
        ]
    )

    config, _, _, counts = module._resolve_cli(args)

    assert config.transitions == ()
    assert counts == {"poetry": 0, "physics": 0}


def test_launcher_defaults_to_three_mixed_problems_across_all_conditions() -> None:
    completed = subprocess.run(
        [str(LAUNCHER_PATH), "--dry-run"],
        cwd=LAUNCHER_PATH.parent,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "condition_count": 3,
        "lean_available": True,
        "model_loaded": False,
        "problem_count": 3,
        "seed_count": 1,
        "transition_counts": {"physics": 8, "poetry": 8},
    }


def test_dry_run_probes_lean_instead_of_trusting_executable_path(tmp_path: Path, capsys) -> None:
    module = load_script()
    status = module.main(
        [
            "--dry-run",
            "--condition", "none",
            "--lean-project", str(tmp_path),
            "--lake-executable", "/usr/bin/false",
            "--problem", "imp_intro_01",
        ]
    )

    assert status == 2
    assert "Lean" in capsys.readouterr().err
