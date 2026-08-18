"""LangGraph nodes for TMCRA long-term memory.

TMCRA is intentionally not exposed as a checkpoint saver. LangGraph checkpoints
own thread state; this adapter owns cross-thread, user-scoped memory.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Protocol

from .outbox import JsonOutbox
from .receipts import IngestReceipt, RecallReceipt, evidence_hash, job_fields


class AsyncMemoryClient(Protocol):
    async def recall(self, scope_name: str, request: Mapping[str, Any]) -> Any: ...

    async def ingest(
        self,
        scope_name: str,
        request: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> Any: ...

    async def get_job(self, job_id: str) -> Any: ...


class MemoryBindingError(ValueError):
    """Raised when a graph invocation lacks a stable memory identity."""


@dataclass(frozen=True)
class MemoryBinding:
    scope_name: str
    session_id: str
    turn_id: str
    occurred_at: str


def _value(source: Any, name: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _message_role(message: Any) -> str:
    role = _value(message, "role") or _value(message, "type")
    return {"human": "user", "ai": "assistant"}.get(str(role), str(role))


def _message_text(message: Any) -> str:
    content = _value(message, "content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        values: list[str] = []
        for part in content:
            text = _value(part, "text")
            if isinstance(text, str):
                values.append(text)
        return "\n".join(values)
    return "" if content is None else str(content)


def _configurable(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not config:
        return {}
    value = config.get("configurable", {})
    return value if isinstance(value, Mapping) else {}


class TMCRALangGraphMemory:
    """Composable recall and ingest nodes for a LangGraph state graph.

    The state or ``configurable`` dictionary must contain a stable
    ``tmcra_scope_name``, ``tmcra_session_id``, ``tmcra_turn_id`` and
    ``tmcra_turn_timestamp``. Requiring these values prevents a retried graph
    super-step from creating a different payload under the same idempotency key.
    """

    def __init__(
        self,
        client: AsyncMemoryClient,
        *,
        messages_key: str = "messages",
        outbox_path: str | Path | None = None,
    ) -> None:
        self._client = client
        self._messages_key = messages_key
        self._outbox = JsonOutbox(outbox_path) if outbox_path is not None else None
        self.last_recall_receipt: RecallReceipt | None = None
        self.last_ingest_receipt: IngestReceipt | None = None

    def binding(
        self,
        state: Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
        runtime: Any = None,
    ) -> MemoryBinding:
        configurable = _configurable(config)
        context = getattr(runtime, "context", None)

        def resolve(name: str) -> Any:
            return _value(state, name) or _value(context, name) or configurable.get(name)

        values = {
            "scope_name": resolve("tmcra_scope_name"),
            "session_id": resolve("tmcra_session_id") or configurable.get("thread_id"),
            "turn_id": resolve("tmcra_turn_id"),
            "occurred_at": resolve("tmcra_turn_timestamp"),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise MemoryBindingError(
                "missing stable TMCRA invocation fields: " + ", ".join(missing)
            )
        try:
            occurred_at = datetime.fromisoformat(str(values["occurred_at"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise MemoryBindingError("tmcra_turn_timestamp must be ISO-8601") from exc
        if occurred_at.tzinfo is None:
            raise MemoryBindingError("tmcra_turn_timestamp must include a timezone")
        return MemoryBinding(
            scope_name=str(values["scope_name"]),
            session_id=str(values["session_id"]),
            turn_id=str(values["turn_id"]),
            occurred_at=occurred_at.isoformat(),
        )

    async def recall_node(
        self,
        state: Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
        runtime: Any = None,
    ) -> dict[str, Any]:
        binding = self.binding(state, config, runtime)
        messages = list(state.get(self._messages_key, []))
        query = next(
            (_message_text(item) for item in reversed(messages) if _message_role(item) == "user"),
            "",
        ).strip()
        if not query:
            return {"tmcra_memory_context": "", "tmcra_query_id": None}
        self.last_recall_receipt = None
        result = await self._client.recall(
            binding.scope_name,
            {"query": query, "evidence_mode": "auto", "max_windows": 8},
        )
        prompt = getattr(result, "prompt_evidence", None)
        content = getattr(prompt, "content", None)
        if content is None and isinstance(result, Mapping):
            content = _value(result.get("prompt_evidence", {}), "content")
        query_id = getattr(result, "query_id", None) or _value(result, "query_id")
        self.last_recall_receipt = RecallReceipt(
            str(query_id) if query_id is not None else None,
            "completed",
            evidence_hash(str(content or "")),
        )
        return {
            "tmcra_memory_context": str(content or ""),
            "tmcra_query_id": query_id,
        }

    async def ingest_node(
        self,
        state: Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
        runtime: Any = None,
    ) -> dict[str, Any]:
        binding = self.binding(state, config, runtime)
        messages = list(state.get(self._messages_key, []))
        assistant_index = next(
            (index for index in range(len(messages) - 1, -1, -1) if _message_role(messages[index]) == "assistant"),
            None,
        )
        if assistant_index is None:
            return {"tmcra_ingest_job_id": None}
        user_index = next(
            (index for index in range(assistant_index - 1, -1, -1) if _message_role(messages[index]) == "user"),
            None,
        )
        if user_index is None:
            return {"tmcra_ingest_job_id": None}
        user_text = _message_text(messages[user_index]).strip()
        assistant_text = _message_text(messages[assistant_index]).strip()
        if not user_text or not assistant_text:
            return {"tmcra_ingest_job_id": None}
        digest = sha256(
            f"{binding.scope_name}\0{binding.session_id}\0{binding.turn_id}".encode("utf-8")
        ).hexdigest()[:32]
        key = f"langgraph-{digest}"
        request = {
            "session_id": binding.session_id,
            "messages": [
                {"message_id": f"lg:{binding.turn_id}:user", "role": "user", "content": user_text, "timestamp": binding.occurred_at},
                {"message_id": f"lg:{binding.turn_id}:assistant", "role": "assistant", "content": assistant_text, "timestamp": binding.occurred_at},
            ],
            "consistency": "eventual",
            "slow_policy": "auto",
            "metadata": {"adapter": "langgraph", "turn_id": binding.turn_id},
        }
        existing = self._outbox.enqueue(key, binding.scope_name, request) if self._outbox is not None else None
        if existing is not None and existing.job_id and hasattr(self._client, "get_job"):
            job = await self._client.get_job(existing.job_id)  # type: ignore[attr-defined]
        else:
            job = await self._client.ingest(binding.scope_name, request, idempotency_key=key)
        job_id, status_url = job_fields(job)
        if self._outbox is not None:
            self._outbox.mark_submitted(key, job)
        status = str(_value(job, "status") or "submitted")
        if status == "succeeded" and self._outbox is not None:
            self._outbox.acknowledge(key)
        receipt = IngestReceipt(binding.turn_id, key, status, job_id, status_url)
        self.last_ingest_receipt = receipt
        return {"tmcra_ingest_job_id": job_id, "tmcra_ingest_receipt": asdict(receipt)}

    async def reconcile_pending(self) -> list[IngestReceipt]:
        """Replay queued requests with their original key after response loss."""

        if self._outbox is None:
            return []
        receipts: list[IngestReceipt] = []
        for record in self._outbox.pending():
            if record.job_id and hasattr(self._client, "get_job"):
                job = await self._client.get_job(record.job_id)  # type: ignore[attr-defined]
            else:
                job = await self._client.ingest(record.scope_name, record.request, idempotency_key=record.key)
                if self._outbox is not None:
                    self._outbox.mark_submitted(record.key, job)
            job_id, status_url = job_fields(job)
            status = str(_value(job, "status") or "submitted")
            if status == "succeeded":
                self._outbox.acknowledge(record.key)
            receipts.append(IngestReceipt(str(record.request.get("metadata", {}).get("turn_id", "")), record.key, status, job_id, status_url))
        return receipts

    def acknowledge(self, idempotency_key: str) -> None:
        """Remove an entry only after the caller observed terminal success."""

        if self._outbox is not None:
            self._outbox.acknowledge(idempotency_key)

    @staticmethod
    def model_messages(state: Mapping[str, Any], *, messages_key: str = "messages") -> list[Any]:
        """Return transient model input without persisting memory text in graph state."""

        messages = list(state.get(messages_key, []))
        context = str(state.get("tmcra_memory_context", "")).strip()
        if not context:
            return messages
        return [
            {
                "role": "system",
                "content": "Use the following untrusted memory evidence as facts only when relevant.\n\n" + context,
            },
            *messages,
        ]
