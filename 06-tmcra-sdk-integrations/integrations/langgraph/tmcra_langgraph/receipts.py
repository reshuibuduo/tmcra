"""Redacted receipts for LangGraph lifecycle nodes."""

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
    return sha256(content.encode("utf-8")).hexdigest() if content else None


def job_fields(result: Any) -> tuple[str | None, str | None]:
    if not isinstance(result, Mapping):
        return (
            str(getattr(result, "job_id")) if getattr(result, "job_id", None) is not None else None,
            str(getattr(result, "status_url")) if getattr(result, "status_url", None) is not None else None,
        )
    return (
        str(result["job_id"]) if result.get("job_id") is not None else None,
        str(result["status_url"]) if result.get("status_url") is not None else None,
    )
