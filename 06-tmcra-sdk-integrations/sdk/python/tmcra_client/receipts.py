"""Structured receipts returned by the automatic lifecycle wrapper."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ReceiptStatus = Literal[
    "submitted",
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
]
TerminalReceiptStatus = Literal["succeeded", "failed", "cancelled"]


@dataclass(frozen=True)
class WatermarkView:
    """Searchability watermarks shared by all lifecycle receipt projections."""

    source_event_seq: int | None = None
    promoted_event_seq: int | None = None
    indexed_event_seq: int | None = None
    source_raw_token_estimate: int | None = None
    available: bool = False

    def __post_init__(self) -> None:
        if any(
            value is not None
            for value in (
                self.source_event_seq,
                self.promoted_event_seq,
                self.indexed_event_seq,
                self.source_raw_token_estimate,
            )
        ) and not self.available:
            object.__setattr__(self, "available", True)


@dataclass(frozen=True)
class RecallReceipt:
    """The auditable result of one recall request."""

    query_id: str
    scope_name: str
    evidence_hash: str
    submitted_status: Literal["completed"] = "completed"
    final_status: Literal["completed"] = "completed"
    submitted: bool = True
    final: bool = True
    status_url: str | None = None
    index_job_id: str | None = None
    source_event_seq: int | None = None
    promoted_event_seq: int | None = None
    indexed_event_seq: int | None = None
    watermarks: WatermarkView = field(default_factory=WatermarkView)

    @property
    def scope(self) -> str:
        return self.scope_name

    @property
    def evidence_sha256(self) -> str:
        return self.evidence_hash

    @property
    def status(self) -> str:
        return self.final_status or self.submitted_status


@dataclass(frozen=True)
class IngestReceipt:
    """The submitted and, when requested, terminal result of one ingest job."""

    scope_name: str
    message_ids: tuple[str, ...]
    idempotency_key: str
    job_id: str | None
    submitted_status: Literal["submitted"] = "submitted"
    observed_status: str = "submitted"
    final_status: TerminalReceiptStatus | None = None
    submitted: bool = True
    final: bool = False
    status_url: str | None = None
    source_event_seq: int | None = None
    promoted_event_seq: int | None = None
    indexed_event_seq: int | None = None
    error: dict[str, Any] | None = None
    watermarks: WatermarkView = field(default_factory=WatermarkView)

    @property
    def scope(self) -> str:
        return self.scope_name

    @property
    def status(self) -> str:
        """Return terminal status when known, otherwise the submitted status."""

        return self.final_status or self.submitted_status

    @property
    def completed(self) -> bool:
        return self.final_status == "succeeded"


@dataclass(frozen=True)
class LifecycleTurnReceipt:
    """Unified receipt for recall, answer, and ingest of one Agent turn."""

    session_id: str
    idempotency_key: str
    recall_receipts: tuple[RecallReceipt, ...]
    ingest_receipt: IngestReceipt
    message_ids: tuple[str, ...]
    query_ids: tuple[str, ...]
    evidence_hashes: tuple[str, ...]
    submitted_status: Literal["submitted"] = "submitted"
    final_status: TerminalReceiptStatus | None = None
    job_id: str | None = None
    status_url: str | None = None
    submitted: bool = True
    final: bool = False
    source_event_seq: int | None = None
    promoted_event_seq: int | None = None
    indexed_event_seq: int | None = None
    watermarks: WatermarkView = field(default_factory=WatermarkView)

    @property
    def recalls(self) -> tuple[RecallReceipt, ...]:
        """TypeScript-compatible alias for the recall sub-receipts."""

        return self.recall_receipts

    @property
    def ingest(self) -> IngestReceipt:
        """TypeScript-compatible alias for the ingest sub-receipt."""

        return self.ingest_receipt

    @property
    def status(self) -> str:
        return self.final_status or self.submitted_status

    @property
    def completed(self) -> bool:
        return self.final_status == "succeeded"
