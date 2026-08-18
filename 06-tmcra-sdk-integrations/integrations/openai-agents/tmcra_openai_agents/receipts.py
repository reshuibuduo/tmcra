"""Redacted lifecycle receipts exposed by the OpenAI Agents adapter."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping


@dataclass(frozen=True)
class RecallReceipt:
    query_id: str | None
    status: str
    evidence_hash: str | None


@dataclass(frozen=True)
class IngestReceipt:
    turn_id: str
    idempotency_key: str
    status: str
    job_id: str | None = None
    status_url: str | None = None


def evidence_hash(content: str) -> str | None:
    if not content:
        return None
    return sha256(content.encode("utf-8")).hexdigest()


def job_fields(result: Any) -> tuple[str | None, str | None]:
    def get(name: str) -> Any:
        if isinstance(result, Mapping):
            return result.get(name)
        return getattr(result, name, None)
    return (
        str(get("job_id")) if get("job_id") is not None else None,
        str(get("status_url")) if get("status_url") is not None else None,
    )
