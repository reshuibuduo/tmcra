from __future__ import annotations

from typing import Any, Dict

from .base import AdapterResponse
from .reasoning_adapters import (
    OpenAICompatCoTReasoner,
    OpenAICompatDirectReasoner,
    OpenAICompatFullContextReasoner,
)


def _completion_usage_dict(completion: Any) -> Dict[str, int]:
    usage = getattr(completion, "usage", None)
    if usage is None and isinstance(completion, dict):
        usage = completion.get("usage")
    if usage is None:
        return {}
    if isinstance(usage, dict):
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or (prompt_tokens + completion_tokens))
    else:
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or (prompt_tokens + completion_tokens))
    usage_dict = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    return usage_dict if any(usage_dict.values()) else {}


class _UsageCaptureMixin:
    async def _answer_with_usage_capture(self, parent_cls: type, query: str, *, answer_mode: str, memory_adapter) -> AdapterResponse:
        if getattr(self, "client", None) is None:
            return await parent_cls.answer(self, query, answer_mode=answer_mode, memory_adapter=memory_adapter)
        completions_api = getattr(getattr(self.client, "chat", None), "completions", None)
        original_create = getattr(completions_api, "create", None)
        if completions_api is None or original_create is None:
            return await parent_cls.answer(self, query, answer_mode=answer_mode, memory_adapter=memory_adapter)
        captured_usage: Dict[str, int] = {}

        def wrapped_create(*args, **kwargs):
            completion = original_create(*args, **kwargs)
            usage_dict = _completion_usage_dict(completion)
            if usage_dict:
                captured_usage.update(usage_dict)
            return completion

        setattr(completions_api, "create", wrapped_create)
        try:
            response = await parent_cls.answer(self, query, answer_mode=answer_mode, memory_adapter=memory_adapter)
        finally:
            setattr(completions_api, "create", original_create)
        if captured_usage:
            response.metadata["llm_usage"] = dict(captured_usage)
        return response


class UsageAwareOpenAICompatDirectReasoner(_UsageCaptureMixin, OpenAICompatDirectReasoner):
    async def answer(self, query: str, *, answer_mode: str, memory_adapter) -> AdapterResponse:
        return await self._answer_with_usage_capture(OpenAICompatDirectReasoner, query, answer_mode=answer_mode, memory_adapter=memory_adapter)


class UsageAwareOpenAICompatCoTReasoner(_UsageCaptureMixin, OpenAICompatCoTReasoner):
    async def answer(self, query: str, *, answer_mode: str, memory_adapter) -> AdapterResponse:
        return await self._answer_with_usage_capture(OpenAICompatCoTReasoner, query, answer_mode=answer_mode, memory_adapter=memory_adapter)


class UsageAwareOpenAICompatFullContextReasoner(_UsageCaptureMixin, OpenAICompatFullContextReasoner):
    async def answer(self, query: str, *, answer_mode: str, memory_adapter) -> AdapterResponse:
        return await self._answer_with_usage_capture(OpenAICompatFullContextReasoner, query, answer_mode=answer_mode, memory_adapter=memory_adapter)
