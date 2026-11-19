"""LLM adapters (Anthropic, OpenAI, Gemini, and a fake for tests). One method: complete_json.

Every call is logged with its corpus token count and refused if that count exceeds the budget.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable

from app.config import get_settings
from app.logging_setup import log_event

log = logging.getLogger("veltrix.llm")


class LLMError(RuntimeError):
    pass


class CorpusBudgetError(RuntimeError):
    pass


class BaseLLM:
    name = "base"

    def __init__(self, model: str, timeout: float) -> None:
        self.model, self.timeout = model, timeout

    def _call(self, system: str, user: str, schema: dict, schema_name: str) -> str:
        raise NotImplementedError

    def complete_json(self, *, purpose: str, system: str, user: str, schema: dict, corpus_tokens: int,
                      request_id: str = "") -> dict:
        budget = get_settings().corpus_token_budget
        if corpus_tokens > budget:
            log_event(log, "llm_call_blocked", purpose=purpose, corpus_tokens=corpus_tokens, budget=budget)
            raise CorpusBudgetError(f"corpus tokens {corpus_tokens} > {budget}")
        started = time.time()
        status = "ok"
        try:
            raw = self._call(system, user, schema, purpose)
            data = _parse_json(raw)
            return data
        except Exception as exc:
            status = type(exc).__name__
            raise
        finally:
            log_event(log, "llm_call", request_id=request_id, purpose=purpose, provider=self.name,
                      model=self.model, corpus_tokens=corpus_tokens, status=status,
                      ms=int((time.time() - started) * 1000))


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):]
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        raise LLMError("model did not return JSON")
    return json.loads(raw[start:end + 1])


class AnthropicLLM(BaseLLM):
    """Claude via the official SDK, with structured JSON output."""
    name = "anthropic"

    def __init__(self, model: str, timeout: float) -> None:
        super().__init__(model, timeout)
        import anthropic
        # two LLM calls per message must fit the 30 s limit, so no automatic retries
        self.client = anthropic.Anthropic(timeout=timeout, max_retries=0)

    def _call(self, system: str, user: str, schema: dict, schema_name: str) -> str:
        kwargs = dict(
            model=self.model,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
        if self.model.startswith(("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")):
            # server-side fallback if the model declines a request
            response = self.client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            response = self.client.messages.create(**kwargs)
        if response.stop_reason == "refusal":
            raise LLMError("model declined the request")
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        if not text:
            raise LLMError("empty response")
        return text


class OpenAILLM(BaseLLM):
    name = "openai"

    def __init__(self, model: str, timeout: float) -> None:
        super().__init__(model, timeout)
        import openai
        self.client = openai.OpenAI(timeout=timeout, max_retries=0)

    def _call(self, system: str, user: str, schema: dict, schema_name: str) -> str:
        r = self.client.chat.completions.create(
            model=self.model, temperature=0, seed=7,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
        )
        return r.choices[0].message.content or ""


class GeminiLLM(BaseLLM):
    name = "gemini"

    def __init__(self, model: str, timeout: float) -> None:
        super().__init__(model, timeout)
        from google import genai
        self.client = genai.Client(http_options={"timeout": int(timeout * 1000)})

    def _call(self, system: str, user: str, schema: dict, schema_name: str) -> str:
        from google.genai import types
        r = self.client.models.generate_content(
            model=self.model, contents=user,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0,
                                               response_mime_type="application/json",
                                               response_json_schema=schema),
        )
        return r.text or ""


class FakeLLM(BaseLLM):
    """Test double: a function (purpose, system, user) -> dict decides each reply."""
    name = "fake"

    def __init__(self, handler: Callable[[str, str, str], dict] | None = None) -> None:
        super().__init__("fake", 1.0)
        self.handler = handler
        self.calls: list[dict] = []

    def _call(self, system: str, user: str, schema: dict, schema_name: str) -> str:
        self.calls.append({"purpose": schema_name, "system": system, "user": user})
        if self.handler is None:
            raise LLMError("no fake handler configured")
        return json.dumps(self.handler(schema_name, system, user))


_instance: BaseLLM | None = None


def get_llm() -> BaseLLM:
    global _instance
    if _instance is None:
        s = get_settings()
        key_var = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(s.llm_provider)
        if key_var and not os.getenv(key_var):
            raise LLMError(f"{key_var} is not set")
        if s.llm_provider == "gemini" and not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
            raise LLMError("GOOGLE_API_KEY is not set")
        cls = {"anthropic": AnthropicLLM, "openai": OpenAILLM, "gemini": GeminiLLM}.get(s.llm_provider)
        if cls is None:
            raise LLMError(f"unknown LLM_PROVIDER '{s.llm_provider}'")
        _instance = cls(s.llm_model, s.llm_timeout_s)
    return _instance


def set_llm(llm: BaseLLM | None) -> None:
    global _instance
    _instance = llm
