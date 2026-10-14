from __future__ import annotations

import time

import pytest

from analogy.config import ModelConfig
from analogy.model import (
    ModelConfigurationError,
    ModelOutputError,
    QwenGenerator,
    capability_prompt,
    parse_tactic_candidates,
    parse_tactic_sequence,
)
from analogy.records import CandidateOrigin, CapabilityResult, Message


def test_parse_tactic_sequence_accepts_nonempty_json_string_array() -> None:
    assert parse_tactic_sequence('["intro h", "exact h"]') == (
        "intro h",
        "exact h",
    )


def test_parse_tactic_candidates_deduplicates_truncates_and_normalizes() -> None:
    parsed = parse_tactic_candidates(
        '[{"tactic":"  exact   h  ","score":2},'
        '{"tactic":"simp","score":1},'
        '{"tactic":"exact h","score":4},'
        '{"tactic":"aesop","score":1}]',
        top_k=2,
    )

    assert [(item.source, item.score, item.origin) for item in parsed] == [
        ("exact h", 0.8, CandidateOrigin.ORDINARY),
        ("simp", 0.2, CandidateOrigin.ORDINARY),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "{}",
        "[]",
        "[3]",
        '[{"tactic":"exact h"}]',
        '[{"score":1}]',
        '[{"tactic":"exact h","score":1,"extra":true}]',
        '[{"tactic":" ","score":1}]',
        '[{"tactic":"exact h","score":true}]',
        '[{"tactic":"exact h","score":"1"}]',
        '[{"tactic":"exact h","score":-1}]',
        '[{"tactic":"exact h","score":NaN}]',
        '[{"tactic":"exact h","score":Infinity}]',
        '[{"tactic":"exact h","score":0},{"tactic":"simp","score":0}]',
    ],
)
def test_parse_tactic_candidates_rejects_invalid_rankings(text: str) -> None:
    with pytest.raises(ModelOutputError):
        parse_tactic_candidates(text, top_k=8)


@pytest.mark.parametrize("top_k", [0, -1, True, 1.5])
def test_parse_tactic_candidates_rejects_invalid_top_k(top_k: object) -> None:
    with pytest.raises(ValueError, match="top_k"):
        parse_tactic_candidates(
            '[{"tactic":"exact h","score":1}]',
            top_k=top_k,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '["intro h", 3]',
        '["intro h", "  "]',
        '{"tactics": ["intro h"]}',
    ],
)
def test_parse_tactic_sequence_rejects_invalid_model_output(text: str) -> None:
    with pytest.raises(ModelOutputError):
        parse_tactic_sequence(text)


def test_capability_records_are_immutable_and_normalize_sequences() -> None:
    message = Message("user", "Prove P → P")
    result = CapabilityResult("identity", ["intro h", "exact h"], True, None, 0.25)

    assert message.role == "user"
    assert result.tactics == ("intro h", "exact h")
    with pytest.raises(AttributeError):
        result.solved = False


class FakeBatch(dict):
    def __init__(self, values):
        super().__init__(input_ids=values, attention_mask=[[1] * len(values[0])])
        self.input_ids = values
        self.moved_to = None

    def to(self, device):
        self.moved_to = device
        return self


class FakeTokenizer:
    def __init__(self) -> None:
        self.template_call = None
        self.tokenize_call = None
        self.decoded = None

    def apply_chat_template(self, messages, **kwargs):
        self.template_call = (messages, kwargs)
        return "rendered chat"

    def __call__(self, texts, **kwargs):
        self.tokenize_call = (texts, kwargs)
        return FakeBatch([[10, 11]])

    def decode(self, tokens, **kwargs):
        self.decoded = (tokens, kwargs)
        return '["intro h", "exact h"]'


class FakeParameter:
    def __init__(self) -> None:
        self.required = None

    def requires_grad_(self, required):
        self.required = required
        return self


class FakeModel:
    def __init__(self) -> None:
        self.device = "mps"
        self.parameter = FakeParameter()
        self.eval_called = False
        self.generate_call = None
        self.moved_to = None

    def parameters(self):
        return (self.parameter,)

    def eval(self):
        self.eval_called = True
        return self

    def to(self, device):
        self.moved_to = device
        self.device = device
        return self

    def generate(self, **kwargs):
        self.generate_call = kwargs
        return [[10, 11, 99, 100]]


class FakeTorch:
    def __init__(self, *, cuda: bool = False, mps: bool = True) -> None:
        self.seeds = []
        self.cuda = type("Cuda", (), {"is_available": staticmethod(lambda: cuda)})()
        self.backends = type(
            "Backends",
            (),
            {"mps": type("Mps", (), {"is_available": staticmethod(lambda: mps)})()},
        )()

    def manual_seed(self, seed):
        self.seeds.append(seed)


def test_qwen_generator_uses_chat_template_seed_and_only_decodes_new_tokens() -> None:
    tokenizer = FakeTokenizer()
    model = FakeModel()
    torch = FakeTorch()
    generator = QwenGenerator(
        tokenizer=tokenizer,
        model=model,
        torch_module=torch,
        device="mps",
        max_new_tokens=64,
        timeout_seconds=30.0,
        do_sample=True,
        temperature=0.3,
        top_p=0.9,
    )

    result = generator.generate((Message("user", "Prove P → P"),), seed=17)

    assert result == '["intro h", "exact h"]'
    assert tokenizer.template_call == (
        [{"role": "user", "content": "Prove P → P"}],
        {
            "tokenize": False,
            "add_generation_prompt": True,
            "enable_thinking": False,
        },
    )
    assert tokenizer.tokenize_call == (
        ["rendered chat"],
        {"return_tensors": "pt"},
    )
    assert model.generate_call == {
        "input_ids": [[10, 11]],
        "attention_mask": [[1, 1]],
        "max_new_tokens": 64,
        "do_sample": True,
        "temperature": 0.3,
        "top_p": 0.9,
        "max_time": 30.0,
    }
    assert torch.seeds == [17]
    assert tokenizer.decoded == ([99, 100], {"skip_special_tokens": True})
    assert model.parameter.required is False
    assert model.eval_called is True


def test_capability_prompt_teaches_structure_without_problem_metadata() -> None:
    from analogy.lean_gym.models import Problem

    problem = Problem(
        id="identity",
        title="secret title",
        binders="(P : Prop)",
        target="P → P",
        source_path="secret/path.lean",
        source_level="secret-level",
        connective="implication",
        difficulty=1,
    )

    messages = capability_prompt(problem)
    prompt = "\n".join(message.content for message in messages)

    assert "Binders are already in the local context" in prompt
    assert '(A : Prop) ⊢ A → A => ["intro ha", "exact ha"]' in prompt
    assert '(h : A ∨ B) ⊢ B ∨ A =>' in prompt
    assert '(f : A → B) (nb : ¬B) ⊢ ¬A =>' in prompt
    assert "Do not invent identifiers" in prompt
    assert "secret title" not in prompt
    assert "secret/path.lean" not in prompt
    assert messages[-1].role == "user"
    assert messages[-1].content == (
        "Binders: (P : Prop)\nTarget: P → P\nReturn only the JSON tactic array."
    )


def test_qwen_generator_raises_timeout_when_generation_exceeds_limit() -> None:
    class SlowModel(FakeModel):
        def generate(self, **kwargs):
            time.sleep(0.01)
            return super().generate(**kwargs)

    generator = QwenGenerator(
        tokenizer=FakeTokenizer(),
        model=SlowModel(),
        torch_module=FakeTorch(),
        device="mps",
        max_new_tokens=64,
        timeout_seconds=0.001,
    )

    with pytest.raises(TimeoutError, match="generation exceeded"):
        generator.generate((Message("user", "Prove P → P"),), seed=17)


@pytest.mark.parametrize("device", ["cuda", "mps"])
def test_qwen_generator_rejects_an_explicit_unavailable_device(device: str) -> None:
    config = ModelConfig("Qwen/Qwen3-8B", device, -1, 64, 30.0, True, 0.3, 0.9)

    with pytest.raises(ModelConfigurationError, match=device):
        QwenGenerator.from_config(
            config,
            torch_module=FakeTorch(cuda=False, mps=False),
            transformers_module=object(),
        )


def test_qwen_generator_keeps_auto_selected_mps_model_off_disk() -> None:
    tokenizer = FakeTokenizer()
    model = FakeModel()

    class TokenizerLoader:
        @staticmethod
        def from_pretrained(name):
            return tokenizer

    class ModelLoader:
        call = None

        @classmethod
        def from_pretrained(cls, name, **kwargs):
            cls.call = (name, kwargs)
            return model

    transformers = type(
        "Transformers",
        (),
        {"AutoTokenizer": TokenizerLoader, "AutoModelForCausalLM": ModelLoader},
    )()
    config = ModelConfig("Qwen/Qwen3-8B", "auto", -1, 64, 30.0, True, 0.3, 0.9)

    QwenGenerator.from_config(
        config,
        torch_module=FakeTorch(cuda=False, mps=True),
        transformers_module=transformers,
    )

    assert ModelLoader.call == (
        "Qwen/Qwen3-8B",
        {"torch_dtype": "auto", "low_cpu_mem_usage": True},
    )
    assert model.moved_to == "mps"
