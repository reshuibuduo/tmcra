"""Typed request and response models for the TMCRA Memory API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class MemoryMessage(RequestModel):
    message_id: str = Field(min_length=1, max_length=200)
    role: Literal["user", "assistant", "system", "tool"]
    content: str = Field(min_length=1, max_length=200_000)
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestRequest(RequestModel):
    session_id: str = Field(min_length=1, max_length=200)
    messages: list[MemoryMessage] = Field(min_length=1, max_length=1000)
    consistency: Literal["eventual", "read_your_writes"] = "eventual"
    slow_policy: Literal["auto", "deferred", "force"] = "auto"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("messages")
    @classmethod
    def unique_message_ids(cls, value: list[MemoryMessage]) -> list[MemoryMessage]:
        identifiers = [item.message_id for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("message_id values must be unique within one request")
        return value


class BulkIngestItem(IngestRequest):
    idempotency_key: str = Field(min_length=8, max_length=200)


class BulkIngestRequest(RequestModel):
    items: list[BulkIngestItem] = Field(min_length=1, max_length=100)

    @field_validator("items")
    @classmethod
    def validate_batch(cls, value: list[BulkIngestItem]) -> list[BulkIngestItem]:
        keys = [item.idempotency_key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("idempotency_key values must be unique within one batch")
        if sum(len(item.messages) for item in value) > 5000:
            raise ValueError("one batch may contain at most 5000 messages")
        return value


class RecallRequest(RequestModel):
    query: str = Field(min_length=1, max_length=100_000)
    query_time: datetime | None = None
    evidence_mode: Literal["raw", "auto", "compiled"] = "auto"
    recall_profile: Literal["quality", "interactive"] = "quality"
    response_projection: Literal["full", "prompt_only"] = "full"
    max_windows: Literal[8] = 8
    wait_for_job_id: str | None = Field(default=None, max_length=100)
    debug: bool = False


class TurnRequest(RequestModel):
    """Schema retained for parity with the service module's public models."""

    session_id: str = Field(min_length=1, max_length=200)
    user_message: MemoryMessage
    query: str | None = Field(default=None, max_length=100_000)
    evidence_mode: Literal["raw", "auto", "compiled"] = "auto"
    consistency: Literal["eventual", "read_your_writes"] = "eventual"
    max_windows: Literal[8] = 8


Timestamp = float | datetime


class JobResponse(ResponseModel):
    job_id: str
    tenant_id: str
    scope_name: str
    job_type: str
    status: str
    attempts: int
    created_at: Timestamp
    updated_at: Timestamp
    started_at: Timestamp | None = None
    finished_at: Timestamp | None = None
    heartbeat_at: Timestamp | None = None
    lease_expires_at: Timestamp | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    status_url: str | None = None
    idempotent_replay: bool | None = None
    idempotent_retry: bool | None = None
    consistency_contract: dict[str, Any] | None = None
    resume_mode: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {"succeeded", "failed", "cancelled"}

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


class JobAccepted(JobResponse):
    """Response returned by job-creating endpoints."""


class JobView(JobResponse):
    """Response returned by the job status endpoint."""


class BulkIngestResponse(ResponseModel):
    scope_name: str
    jobs: list[JobView]


class ScopeTokenCreateRequest(RequestModel):
    label: str = Field(min_length=1, max_length=120)
    subject: str | None = Field(default=None, min_length=1, max_length=200)
    permissions: list[str] = Field(min_length=1, max_length=10)
    scope_names: list[str] = Field(default_factory=list, max_length=100)
    scope_prefixes: list[str] = Field(default_factory=list, max_length=100)
    expires_in_seconds: int = Field(ge=300, le=31_622_400)
    provisional_delivery_seconds: int | None = Field(default=None, ge=60, le=900)


class ScopeTokenView(ResponseModel):
    token_id: str
    tenant_id: str
    permissions: list[str]
    scope_names: list[str]
    scope_prefixes: list[str] = Field(default_factory=list)
    label: str
    subject: str | None = None
    created_by_key_id: str | None = None
    created_at: float
    expires_at: float
    revoked_at: float | None = None
    last_used_at: float | None = None


class IssuedScopeToken(ScopeTokenView):
    access_token: str


class RetentionPolicyRequest(RequestModel):
    enabled: bool
    inactive_days: int = Field(ge=1, le=3650)


class RetentionPolicy(ResponseModel):
    scope_name: str
    enabled: bool
    inactive_days: int
    created_at: float | None = None
    updated_at: float | None = None


class FeedbackRequest(RequestModel):
    query_id: str | None = Field(default=None, max_length=200)
    rating: Literal["helpful", "incorrect", "stale", "unsafe", "missing"]
    memory_ids: list[str] = Field(default_factory=list, max_length=100)
    comment: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FeedbackResponse(ResponseModel):
    feedback_id: str
    scope_name: str
    rating: str
    created_at: float


WebhookEvent = Literal[
    "job.succeeded",
    "job.failed",
    "job.cancelled",
    "ingest.completed",
    "consolidation.completed",
    "index.completed",
    "export.ready",
    "scope.deleted",
]


class WebhookCreateRequest(RequestModel):
    label: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=2048)
    events: list[WebhookEvent] = Field(min_length=1, max_length=8)


class WebhookView(ResponseModel):
    endpoint_id: str
    label: str
    url: str
    events: list[str]
    enabled: bool
    created_at: float
    updated_at: float | None = None


class IssuedWebhook(WebhookView):
    signing_secret: str


class ScopeLifecycle(ResponseModel):
    scope_name: str
    state: Literal["active", "deleting", "deleted"]


class ScopeCatalogView(ResponseModel):
    scope_name: str
    created_at: float
    last_seen_at: float
    session_count: int
    ingest_request_count: int
    recall_request_count: int
    message_count: int
    last_ingest_at: float | None = None
    last_recall_at: float | None = None


class ScopeSessionView(ResponseModel):
    session_id: str
    created_at: float
    last_ingest_at: float
    ingest_request_count: int
    message_count: int


class ScopeSummaryView(ResponseModel):
    scope: ScopeCatalogView
    sessions: list[ScopeSessionView]


class SessionScopeRestrictionsView(ResponseModel):
    unrestricted: bool
    names: list[str]
    prefixes: list[str]


class SessionCredentialView(ResponseModel):
    type: Literal["api_key", "scope_token"]
    tenant_id: str
    principal: str
    permissions: list[str]
    scope_restrictions: SessionScopeRestrictionsView
    subject: str | None = None
    expires_at: float | None = None


class SessionServiceView(ResponseModel):
    name: str = "tmcra-memory"
    version: str
    capabilities: list[str]


class AuthenticatedSessionView(ResponseModel):
    ok: bool = True
    authenticated: bool = True
    service: SessionServiceView
    credential: SessionCredentialView


class QuotaMetricView(ResponseModel):
    used: int
    limit: int | None = None
    remaining: int | None = None


class BillingQuotaGroupView(ResponseModel):
    group_id: str
    display_name: str
    status: Literal["active", "suspended", "cancelled"]
    period_id: str
    period_status: Literal["scheduled", "active", "expired", "cancelled"]
    billing_interval: Literal["monthly", "yearly", "custom"]
    starts_at: float
    ends_at: float
    max_members: int
    currency: str
    price_minor_units: int | None = None


class QuotaView(ResponseModel):
    tenant_id: str
    principal: str
    plan: str = "pilot"
    plan_version: str | None = None
    billing_group: BillingQuotaGroupView | None = None
    ingest_raw_tokens: QuotaMetricView
    recall_requests: QuotaMetricView
    member_usage: dict[str, dict[str, int]] = Field(default_factory=dict)


class BillingProfileView(ResponseModel):
    tenant_id: str
    subject: str | None = None
    consumer_principal: str
    quota_principal: str
    membership: dict[str, Any] | None = None
    quota: QuotaView


class EntitlementUpdateRequest(RequestModel):
    ingest_raw_tokens: int | None
    recall_requests: int | None

    @field_validator("ingest_raw_tokens", "recall_requests")
    @classmethod
    def non_negative_or_unlimited(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("entitlement values must be non-negative or null")
        return value


class EvidenceRoute(ResponseModel):
    requested: str
    selected: str
    reasons: tuple[str, ...]


class PromptEvidence(ResponseModel):
    schema_version: str
    format: str
    mode: str
    content: str
    content_sha256: str
    content_character_count: int
    source_text_verbatim: bool
    trust_boundary: str
    window_count: int | None = None
    source_block_count: int | None = None
    neighbor_block_count: int | None = None
    memory_context_block_count: int | None = None


class RecallResponse(ResponseModel):
    query_id: str
    scope_name: str
    index_job_id: str
    evidence_route: EvidenceRoute
    evidence: dict[str, Any]
    prompt_evidence: PromptEvidence
    debug: dict[str, Any] | None = None


GraphLayer = Literal["slow", "fast", "source"]


class MemoryGraphNode(ResponseModel):
    id: str
    layer: GraphLayer
    kind: str
    category: str
    label: str
    summary: str
    relation: str
    state: str
    status: str
    confidence: float
    salience: float
    turn_index: int
    occurred_at: str | None = None
    subject_id: str | None = None
    cluster_id: str | None = None
    source_kind: str | None = None
    evidence_count: int
    visible_neighbor_count: int
    expandable: bool
    attributes: dict[str, Any] = Field(default_factory=dict)


class MemoryGraphEdge(ResponseModel):
    id: str
    source: str
    target: str
    type: str
    weight: float
    origin: Literal["stored", "derived"]


class MemoryGraphCounts(ResponseModel):
    nodes: int
    edges: int
    slow: int
    fast: int
    source: int


class MemoryGraphPage(ResponseModel):
    limit: int
    offset: int
    truncated: bool
    next_cursor: str | None = None
    returned_neighbors: int | None = None


class MemoryGraphResponse(ResponseModel):
    schema_version: str
    scope_name: str
    snapshot_id: str
    view: Literal["overview", "neighbors", "recall_trace"]
    requested_layers: list[GraphLayer]
    resolved_layers: list[GraphLayer]
    fallback_layer: GraphLayer | None = None
    nodes: list[MemoryGraphNode]
    edges: list[MemoryGraphEdge]
    counts: MemoryGraphCounts
    page: MemoryGraphPage
    root_id: str | None = None
    depth: int | None = None
    selected_memory_ids: list[str] = Field(default_factory=list)
    missing_memory_ids: list[str] = Field(default_factory=list)


class MemoryGraphEvidenceItem(ResponseModel):
    source_record_id: str
    relationship: str
    session_id: str | None = None
    message_id: str | None = None
    role: str | None = None
    occurred_at: str | None = None
    text: str
    text_sha256: str
    source_text_verbatim: bool
    evidence_char_start: int | None = None
    evidence_char_end: int | None = None


class MemoryGraphEvidenceResponse(ResponseModel):
    schema_version: str
    scope_name: str
    snapshot_id: str
    memory_id: str
    items: list[MemoryGraphEvidenceItem]
    page: MemoryGraphPage


class MemoryGraphTraceRequest(RequestModel):
    query: str = Field(min_length=1, max_length=100_000)
    query_time: datetime | None = None
    max_windows: Literal[8] = 8
    debug: bool = False


class MemoryGraphTraceResponse(MemoryGraphResponse):
    query_id: str
    index_job_id: str
    retrieval_summary: dict[str, Any]
    debug: dict[str, Any] | None = None


class HealthResponse(ResponseModel):
    status: str
    service: str
    version: str


class ReadinessResponse(ResponseModel):
    status: str
    service: str
    version: str
    checks: dict[str, bool] = Field(default_factory=dict)
    worker_alive: bool | None = None
    adapter_compatibility: dict[str, bool] = Field(default_factory=dict)
    online_engine_loaded: bool | None = None


class UsageCallTotals(ResponseModel):
    registered_call_count: int = 0
    completed_call_count: int = 0
    failed_call_count: int = 0
    unknown_call_count: int = 0
    in_flight_call_count: int = 0
    unpriced_completed_call_count: int = 0
    input_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    output_tokens: int = 0
    known_cost_micro_cny: int = 0


class UsageSource(ResponseModel):
    scope_count: int = 0
    ingested_raw_token_estimate: int = 0
    ingested_user_turns: int = 0
    source_event_count: int = 0


class UsageStage(ResponseModel):
    registered_call_count: int = 0
    completed_call_count: int = 0
    unknown_or_unpriced_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    known_cost_micro_cny: int = 0


class UsageCosts(ResponseModel):
    tenant_id: str
    scope_name: str | None = None
    scope_prefix: str | None = None
    from_timestamp: float | None = None
    to_timestamp: float | None = None
    currency: str
    ledger_coverage: str
    source_ledger_coverage: str | None = None
    complete_for_registered_calls: bool
    source: UsageSource
    calls: UsageCallTotals
    known_cost_cny: float
    known_model_api_cny_per_million_ingested_raw_tokens: float | None = None
    uncertain_cost_call_count: int
    by_stage: dict[str, UsageStage] = Field(default_factory=dict)
    quota_events: dict[str, int] = Field(default_factory=dict)
    quota_event_scope_coverage: dict[str, str] = Field(default_factory=dict)
    attribution_coverage: dict[str, dict[str, int]] = Field(default_factory=dict)
    group_by: str | None = None
    buckets: list[dict[str, Any]] = Field(default_factory=list)


# Compatibility names used by earlier SDK consumers.
ReadyResponse = ReadinessResponse
UsageCostResponse = UsageCosts
