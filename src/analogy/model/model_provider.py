"""Singleton boundary for loading and reusing frozen model providers."""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from typing import Any, Protocol

from ..config import ModelConfig
from ..utils.records import Embedding, Message


class ModelConfigurationError(ValueError):
    """Raised when the requested local model configuration cannot run."""


@dataclass(frozen=True, slots=True)
class CompletionScore:
    text: str
    total_log_likelihood: float
    mean_log_likelihood: float
    token_count: int


class TextGenerator(Protocol):
    def generate(self, messages: tuple[Message, ...], *, seed: int) -> str: ...


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Text plus measured decoder input positions, never retokenized output."""

    text: str
    processed_token_positions: int | None
    diagnostic: str | None = None

    def __post_init__(self):
        if not isinstance(self.text, str) or (self.processed_token_positions is not None and (isinstance(self.processed_token_positions, bool) or not isinstance(self.processed_token_positions, int) or self.processed_token_positions <= 0)):
            raise ModelConfigurationError("generation requires text and positive measured token positions")


class ModelProvider(TextGenerator, Protocol):
    hidden_size: int
    model_revision: str
    timeout_seconds: float

    def generate_with_usage(self, messages: tuple[Message, ...], *, seed: int) -> GenerationResult: ...

    def estimate_generation_positions(self, messages: tuple[Message, ...]) -> int: ...

    def count_token_positions(self, messages: tuple[Message, ...], completion: str) -> int: ...

    def embed_text(self, text: str, *, layer: int) -> Embedding: ...

    def embed_mean_text(self, text: str, *, layer: int) -> Embedding: ...

    def embed_text_with_usage(self, text: str, *, layer: int) -> tuple[Embedding, int]: ...

    def score_completions(
        self,
        messages: tuple[Message, ...],
        completions: tuple[str, ...],
    ) -> tuple[CompletionScore, ...]: ...


class QwenGenerator:
    """Frozen Transformers Qwen provider with lazily imported dependencies."""

    def __init__(
        self,
        *,
        tokenizer: Any,
        model: Any,
        torch_module: Any,
        device: str,
        max_new_tokens: int,
        timeout_seconds: float,
        do_sample: bool = False,
        temperature: float = 1.0,
        top_p: float = 1.0,
    ) -> None:
        self._tokenizer = tokenizer
        self._model = model
        self._torch = torch_module
        self._device = device
        self._max_new_tokens = max_new_tokens
        self.timeout_seconds = timeout_seconds
        self._do_sample = do_sample
        self._temperature = temperature
        self._top_p = top_p
        revision = getattr(getattr(model, "config", None), "_commit_hash", None)
        self.model_revision = str(revision) if revision else "unresolved:" + str(
            getattr(getattr(model, "config", None), "name_or_path", None) or "unknown")
        raw_hidden_size = getattr(getattr(model, "config", None), "hidden_size", None)
        self.hidden_size = int(raw_hidden_size) if raw_hidden_size is not None else 0
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        model.eval()

    @classmethod
    def from_config(
        cls,
        config: ModelConfig,
        *,
        torch_module: Any | None = None,
        transformers_module: Any | None = None,
    ) -> QwenGenerator:
        if torch_module is None:
            try:
                import torch as torch_module
            except ImportError as error:
                raise ModelConfigurationError(
                    "the model extra is not installed; install analogy[model]"
                ) from error

        device = cls._resolve_device(config.device, torch_module)
        if transformers_module is None:
            try:
                import transformers as transformers_module
            except ImportError as error:
                raise ModelConfigurationError(
                    "the model extra is not installed; install analogy[model]"
                ) from error

        revision = {"revision": config.revision} if config.revision else {}
        tokenizer = transformers_module.AutoTokenizer.from_pretrained(
            config.name, **revision
        )
        if device == "mps":
            model = transformers_module.AutoModelForCausalLM.from_pretrained(
                config.name,
                **revision,
                torch_dtype="auto",
                low_cpu_mem_usage=True,
            )
            model.to("mps")
        else:
            device_map: Any = "auto" if config.device == "auto" else {"": device}
            model = transformers_module.AutoModelForCausalLM.from_pretrained(
                config.name,
                **revision,
                device_map=device_map,
                torch_dtype="auto",
            )
        actual_device = str(getattr(model, "device", device))
        return cls(
            tokenizer=tokenizer,
            model=model,
            torch_module=torch_module,
            device=actual_device,
            max_new_tokens=config.max_new_tokens,
            timeout_seconds=config.timeout_seconds,
            do_sample=config.do_sample,
            temperature=config.temperature,
            top_p=config.top_p,
        )

    @staticmethod
    def _resolve_device(requested: str, torch_module: Any) -> str:
        normalized = requested.casefold()
        if normalized == "cuda":
            if not torch_module.cuda.is_available():
                raise ModelConfigurationError("configured cuda device is unavailable")
            return "cuda"
        if normalized == "mps":
            if not torch_module.backends.mps.is_available():
                raise ModelConfigurationError("configured mps device is unavailable")
            return "mps"
        if normalized == "cpu":
            return "cpu"
        if normalized != "auto":
            raise ModelConfigurationError(f"unknown model device: {requested}")
        if torch_module.cuda.is_available():
            return "cuda"
        if torch_module.backends.mps.is_available():
            return "mps"
        return "cpu"

    def generate(self, messages: tuple[Message, ...], *, seed: int) -> str:
        model_inputs = self._prepare_generation(messages)
        return self._run_generation(model_inputs, seed=seed)

    def generate_with_usage(self, messages: tuple[Message, ...], *, seed: int) -> GenerationResult:
        positions = 0
        def count(module, inputs, kwargs):
            nonlocal positions
            hidden = inputs[0] if inputs else kwargs["hidden_states"]
            positions += int(hidden.shape[0]) * int(hidden.shape[1])
        handle = self._decoder_layers()[0].register_forward_pre_hook(count, with_kwargs=True)
        try:
            text = self.generate(messages, seed=seed)
        finally:
            handle.remove()
        return GenerationResult(text, positions or None, None if positions else "no measured decoder steps")

    def _decoder_layers(self) -> Any:
        blocks = getattr(getattr(self._model, "model", None), "layers", None)
        if blocks is None or not len(blocks):
            raise ModelConfigurationError("model does not expose decoder layers")
        return blocks

    def count_token_positions(self, messages: tuple[Message, ...], completion: str) -> int:
        prompt_ids, completion_ids = _encode_completion(self._tokenizer, messages, completion, allow_empty=True)
        return len(prompt_ids) + len(completion_ids)

    def estimate_generation_positions(self, messages: tuple[Message, ...]) -> int:
        # Use generation's actual special-token policy, not the scoring encoder.
        inputs = self._prepare_generation(messages)["input_ids"]
        width = int(inputs.shape[-1]) if hasattr(inputs, "shape") else len(inputs[0])
        return width + self._max_new_tokens

    def score_completions(
        self, messages: tuple[Message, ...], completions: tuple[str, ...]
    ) -> tuple[CompletionScore, ...]:
        if not completions or any(not item.strip() for item in completions):
            raise ModelConfigurationError("scoring completions must be nonempty")
        rendered = _render_completion_prompt(self._tokenizer, messages)
        prompt_ids = tuple(
            int(item)
            for item in self._tokenizer.encode(rendered, add_special_tokens=False)
        )
        if not prompt_ids:
            raise ModelConfigurationError("scoring prompt tokenization must be nonempty")
        encoded_completions: list[tuple[str, tuple[int, ...]]] = []
        for completion in completions:
            completion_ids = tuple(
                int(item)
                for item in self._tokenizer.encode(
                    completion, add_special_tokens=False
                )
            )
            if not completion_ids:
                raise ModelConfigurationError(
                    "completion tokenization must be nonempty"
                )
            encoded_completions.append((completion, completion_ids))
        pad_token_id = getattr(self._tokenizer, "pad_token_id", None)
        if pad_token_id is None:
            pad_token_id = getattr(self._tokenizer, "eos_token_id", None)
        if pad_token_id is None:
            pad_token_id = 0
        width = max(
            len(prompt_ids) + len(completion_ids)
            for _, completion_ids in encoded_completions
        )
        rows = []
        masks = []
        for _, completion_ids in encoded_completions:
            sequence = prompt_ids + completion_ids
            padding = width - len(sequence)
            rows.append(sequence + (int(pad_token_id),) * padding)
            masks.append((1,) * len(sequence) + (0,) * padding)
        input_ids = self._torch.tensor(
            rows, dtype=self._torch.long, device=self._device
        )
        attention_mask = self._torch.tensor(
            masks, dtype=self._torch.long, device=self._device
        )
        started = time.monotonic()
        with self._torch.inference_mode():
            output = self._model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                use_cache=False,
            )
        if time.monotonic() - started >= self.timeout_seconds:
            raise TimeoutError(
                f"completion scoring exceeded {self.timeout_seconds:g} seconds"
            )
        logits = getattr(output, "logits", None)
        if logits is None or logits.ndim != 3:
            raise ModelConfigurationError(
                "completion scorer requires rank-3 logits"
            )
        log_probabilities = self._torch.log_softmax(logits.float(), dim=-1)
        scored: list[CompletionScore] = []
        for row, (completion, completion_ids) in enumerate(encoded_completions):
            token_scores = tuple(
                float(
                    log_probabilities[
                        row, len(prompt_ids) + offset - 1, token_id
                    ].item()
                )
                for offset, token_id in enumerate(completion_ids)
            )
            total = sum(token_scores)
            scored.append(
                CompletionScore(
                    completion,
                    total,
                    total / len(token_scores),
                    len(token_scores),
                )
            )
        return tuple(scored)

    def _prepare_generation(self, messages: tuple[Message, ...]) -> Any:
        rendered = [{"role": item.role, "content": item.content} for item in messages]
        prompt = self._tokenizer.apply_chat_template(
            rendered,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        return self._tokenizer([prompt], return_tensors="pt").to(self._device)

    def _run_generation(self, model_inputs: Any, *, seed: int) -> str:
        self._torch.manual_seed(seed)
        started = time.monotonic()
        decoding: dict[str, Any] = {"do_sample": self._do_sample}
        if self._do_sample:
            decoding.update(temperature=self._temperature, top_p=self._top_p)
        generated = self._model.generate(
            **model_inputs,
            max_new_tokens=self._max_new_tokens,
            max_time=self.timeout_seconds,
            **decoding,
        )
        elapsed = time.monotonic() - started
        if elapsed >= self.timeout_seconds:
            raise TimeoutError(
                f"generation exceeded {self.timeout_seconds:g} seconds"
            )
        input_ids = model_inputs["input_ids"]
        input_length = (
            int(input_ids.shape[-1])
            if hasattr(input_ids, "shape")
            else len(input_ids[0])
        )
        new_tokens = generated[0][input_length:]
        return self._tokenizer.decode(new_tokens, skip_special_tokens=True)

    def embed_text(self, text: str, *, layer: int) -> Embedding:
        return self._embed_text_pooled(text, layer=layer, pooling="last")

    def embed_mean_text(self, text: str, *, layer: int) -> Embedding:
        return self._embed_text_pooled(text, layer=layer, pooling="mean")

    def _embed_text_pooled(
        self, text: str, *, layer: int, pooling: str
    ) -> Embedding:
        if not isinstance(text, str) or not text.strip():
            raise ModelConfigurationError("embedding text must be nonblank")
        if isinstance(layer, bool) or not isinstance(layer, int):
            raise ModelConfigurationError("hidden layer must be an integer")
        encoded = self._tokenizer(
            [text], return_tensors="pt", padding=True
        ).to(self._device)
        with self._torch.no_grad():
            output = self._model(
                **encoded, output_hidden_states=True, use_cache=False
            )
        hidden_states = getattr(output, "hidden_states", None)
        if not isinstance(hidden_states, (tuple, list)) or not hidden_states:
            raise ModelConfigurationError("model did not return hidden states")
        resolved = _resolve_hidden_layer(layer, len(hidden_states))
        hidden = hidden_states[resolved]
        if not hasattr(hidden, "ndim") or hidden.ndim != 3:
            raise ModelConfigurationError("hidden state must have rank 3")
        attention_mask = encoded.get("attention_mask")
        if attention_mask is None or attention_mask.ndim != 2:
            raise ModelConfigurationError("tokenizer must return a rank-2 attention mask")
        if hidden.shape[0] != 1 or attention_mask.shape[0] != 1:
            raise ModelConfigurationError("embedding extraction requires batch size one")
        token_count = int(attention_mask[0].sum().item())
        if token_count <= 0 or token_count > hidden.shape[1]:
            raise ModelConfigurationError("attention mask selects no valid final token")
        if pooling == "mean":
            vector = hidden[0, :token_count].detach().float().mean(dim=0)
            token_index = None
        else:
            vector = hidden[0, token_count - 1].detach().float()
            token_index = token_count - 1
        norm = self._torch.linalg.vector_norm(vector)
        finite_vector = bool(self._torch.isfinite(vector).all().item())
        finite_norm = bool(self._torch.isfinite(norm).item())
        if not finite_vector or not finite_norm or norm.item() <= 0.0:
            raise ModelConfigurationError("embedding must be finite and nonzero")
        values = tuple(float(item) for item in (vector / norm).cpu().tolist())
        return Embedding(
            f"{self.model_revision}:layer={resolved}:{pooling}-nonpadding",
            values,
            model_revision=self.model_revision,
            layer=resolved,
            token_index=token_index,
            text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )

    def embed_text_with_usage(self, text: str, *, layer: int) -> tuple[Embedding, int]:
        embedding = self.embed_text(text, layer=layer)
        encoded = self._tokenizer([text], padding=True)
        mask = encoded.get("attention_mask")
        if mask is None:
            raise ModelConfigurationError("tokenizer must return an attention mask")
        row = mask[0]
        count = int(row.sum().item()) if hasattr(row, "sum") and hasattr(row.sum(), "item") else int(sum(row))
        if count <= 0:
            raise ModelConfigurationError("embedding tokenization must be nonempty")
        return embedding, count


def _resolve_hidden_layer(layer: int, count: int) -> int:
    if isinstance(layer, bool) or not isinstance(layer, int):
        raise ModelConfigurationError("hidden layer must be an integer")
    resolved = layer if layer >= 0 else count + layer
    if resolved < 0 or resolved >= count:
        raise ModelConfigurationError(
            f"hidden layer {layer} is out of range for {count} returned layers"
        )
    return resolved


def _encode_completion(tokenizer: Any, messages: tuple[Message, ...], completion: str, *, allow_empty: bool = False) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if not isinstance(completion, str) or (not allow_empty and not completion.strip()):
        raise ModelConfigurationError("scoring completion must be nonempty")
    rendered = _render_completion_prompt(tokenizer, messages)
    prompt_ids = tuple(int(item) for item in tokenizer.encode(rendered, add_special_tokens=False))
    completion_ids = tuple(int(item) for item in tokenizer.encode(completion, add_special_tokens=False))
    if not prompt_ids:
        raise ModelConfigurationError("scoring prompt tokenization must be nonempty")
    if not completion_ids and not allow_empty:
        raise ModelConfigurationError("completion tokenization must be nonempty")
    return prompt_ids, completion_ids


def _render_completion_prompt(tokenizer: Any, messages: tuple[Message, ...]) -> str:
    rendered = [{"role": item.role, "content": item.content} for item in messages]
    if messages and messages[-1].role == "assistant":
        return tokenizer.apply_chat_template(
            rendered, tokenize=False, add_generation_prompt=False,
            continue_final_message=True, enable_thinking=False,
        )
    return tokenizer.apply_chat_template(
        rendered, tokenize=False, add_generation_prompt=True, enable_thinking=False,
    )


def _create_model_provider(config: ModelConfig) -> ModelProvider:
    if config.backend == "transformers":
        return QwenGenerator.from_config(config)
    try:
        module = import_module("analogy.model.mlx_model")
    except ImportError as error:
        raise ModelConfigurationError(
            "the MLX extra is not installed; install analogy[mlx]"
        ) from error
    return module.MLXQwenGenerator.from_config(config)


@lru_cache(maxsize=1)
def get_model_provider(config: ModelConfig) -> ModelProvider:
    """Return the single cached provider for the active model configuration."""

    return _create_model_provider(config)


__all__ = [
    "GenerationResult",
    "CompletionScore",
    "ModelConfigurationError",
    "ModelProvider",
    "QwenGenerator",
    "TextGenerator",
    "get_model_provider",
]
