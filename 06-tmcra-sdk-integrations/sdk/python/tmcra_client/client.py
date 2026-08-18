"""Synchronous and asynchronous TMCRA API clients."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any, TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError as PydanticValidationError

from ._transport import AsyncTransport, RetryConfig, SyncTransport
from .errors import ConfigurationError, PollingTimeoutError, ResponseValidationError
from .models import (
    AuthenticatedSessionView,
    BillingProfileView,
    BulkIngestRequest,
    BulkIngestResponse,
    EntitlementUpdateRequest,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    IngestRequest,
    IssuedScopeToken,
    IssuedWebhook,
    JobAccepted,
    JobResponse,
    JobView,
    MemoryGraphEvidenceResponse,
    MemoryGraphResponse,
    MemoryGraphTraceRequest,
    MemoryGraphTraceResponse,
    QuotaView,
    RecallRequest,
    RecallResponse,
    RetentionPolicy,
    RetentionPolicyRequest,
    ReadinessResponse,
    ScopeLifecycle,
    ScopeCatalogView,
    ScopeSummaryView,
    ScopeTokenCreateRequest,
    ScopeTokenView,
    UsageCosts,
    WebhookCreateRequest,
    WebhookView,
)


T = TypeVar("T", bound=BaseModel)
SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
GRAPH_LAYERS = frozenset({"slow", "fast", "source"})


def _scope_path(scope_name: str) -> str:
    if not isinstance(scope_name, str) or not SCOPE_RE.fullmatch(scope_name):
        raise ValueError("scope_name must match the TMCRA scope name format")
    return quote(scope_name, safe="")


def _job_path(job_id: str) -> str:
    if not isinstance(job_id, str) or not job_id.strip() or "/" in job_id:
        raise ValueError("job_id must be a non-empty path segment")
    return quote(job_id, safe="")


def _memory_path(memory_id: str) -> str:
    if not isinstance(memory_id, str) or not memory_id.strip() or "/" in memory_id:
        raise ValueError("memory_id must be a non-empty path segment")
    return quote(memory_id, safe="")


def _graph_layers(layers: tuple[str, ...]) -> str:
    if not layers or any(layer not in GRAPH_LAYERS for layer in layers):
        raise ValueError("layers must contain slow, fast, or source")
    return ",".join(layers)


def _parse(model: type[T], body: Any) -> T:
    try:
        return model.model_validate(body)
    except PydanticValidationError as exc:
        raise ResponseValidationError(
            f"TMCRA response did not match {model.__name__}", body=body
        ) from exc


def _request_body(
    request: BaseModel | Mapping[str, Any], model: type[BaseModel]
) -> dict[str, Any]:
    try:
        validated = request if isinstance(request, model) else model.model_validate(request)
    except PydanticValidationError as exc:
        raise ConfigurationError(f"invalid {model.__name__}: {exc}") from exc
    return validated.model_dump(mode="json", exclude_none=True)


class SyncClient:
    """A synchronous client for the TMCRA Memory API."""

    def __init__(
        self,
        base_url: str = "https://api.tmcra.com",
        api_key: str | None = None,
        *,
        timeout: float | httpx.Timeout = 30.0,
        max_retries: int = 2,
        retry_initial_delay: float = 0.25,
        retry_max_delay: float = 5.0,
        headers: Mapping[str, str] | None = None,
        client_platform: str = "python",
        integration_id: str | None = None,
        agent_id: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if isinstance(timeout, (int, float)) and timeout <= 0:
            raise ConfigurationError("timeout must be positive")
        attribution_headers = {
            "X-TMCRA-Client-Platform": client_platform,
            **({"X-TMCRA-Integration-ID": integration_id} if integration_id else {}),
            **({"X-TMCRA-Agent-ID": agent_id} if agent_id else {}),
            **dict(headers or {}),
        }
        self._transport = SyncTransport(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            retry=RetryConfig(
                max_retries=max_retries,
                initial_delay=retry_initial_delay,
                max_delay=retry_max_delay,
            ),
            headers=attribution_headers,
            transport=transport,
        )

    def __enter__(self) -> "SyncClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        self._transport.close()

    def healthz(self) -> HealthResponse:
        return _parse(HealthResponse, self._transport.request("GET", "/healthz"))

    def readyz(self) -> ReadinessResponse:
        return _parse(
            ReadinessResponse,
            self._transport.request("GET", "/readyz", allow_statuses=frozenset({503})),
        )

    def authenticated_session(self) -> AuthenticatedSessionView:
        """Return the authenticated credential and service capability contract."""
        return _parse(
            AuthenticatedSessionView,
            self._transport.request("GET", "/v1/session"),
        )

    def list_scopes(
        self, *, prefix: str | None = None, limit: int = 100
    ) -> list[ScopeCatalogView]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        params: dict[str, Any] = {"limit": limit}
        if prefix is not None:
            if not prefix or len(prefix) > 128:
                raise ValueError("prefix must contain between 1 and 128 characters")
            params["prefix"] = prefix
        body = self._transport.request("GET", "/v1/scopes", params=params)
        if not isinstance(body, list):
            raise ResponseValidationError("TMCRA response did not match scope list", body=body)
        return [_parse(ScopeCatalogView, item) for item in body]

    def scope_summary(self, scope_name: str) -> ScopeSummaryView:
        return _parse(
            ScopeSummaryView,
            self._transport.request(
                "GET", f"/v1/scopes/{_scope_path(scope_name)}/summary"
            ),
        )

    def quota(self, *, subject: str | None = None) -> QuotaView:
        params = {"subject": subject} if subject is not None else None
        return _parse(
            QuotaView,
            self._transport.request("GET", "/v1/usage/quota", params=params),
        )

    def billing_profile(self) -> BillingProfileView:
        return _parse(
            BillingProfileView,
            self._transport.request("GET", "/v1/billing/profile"),
        )

    def set_entitlement(
        self,
        subject: str,
        request: EntitlementUpdateRequest | Mapping[str, Any],
    ) -> QuotaView:
        subject_path = _job_path(subject)
        return _parse(
            QuotaView,
            self._transport.request(
                "PUT",
                f"/v1/usage/entitlements/{subject_path}",
                json=_request_body(request, EntitlementUpdateRequest),
            ),
        )

    def set_quota_entitlement(
        self,
        subject: str,
        request: EntitlementUpdateRequest | Mapping[str, Any],
    ) -> QuotaView:
        if not subject.strip():
            raise ValueError("subject must not be empty")
        return _parse(
            QuotaView,
            self._transport.request(
                "PUT",
                "/v1/usage/quota",
                params={"subject": subject},
                json=_request_body(request, EntitlementUpdateRequest),
            ),
        )

    def health(self) -> HealthResponse:
        return self.healthz()

    def ready(self) -> ReadinessResponse:
        return self.readyz()

    def ingest(
        self,
        scope_name: str,
        request: IngestRequest | Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> JobAccepted:
        body = self._transport.request(
            "POST",
            f"/v1/scopes/{_scope_path(scope_name)}/ingest",
            json=_request_body(request, IngestRequest),
            idempotency_key=idempotency_key,
        )
        return _parse(JobAccepted, body)

    def bulk_ingest(
        self,
        scope_name: str,
        request: BulkIngestRequest | Mapping[str, Any],
    ) -> BulkIngestResponse:
        payload = _request_body(request, BulkIngestRequest)
        body = self._transport.request(
            "POST",
            f"/v1/scopes/{_scope_path(scope_name)}/ingest/batch",
            json=payload,
            idempotency_key=payload["items"][0]["idempotency_key"],
        )
        return _parse(BulkIngestResponse, body)

    def consolidate(self, scope_name: str, *, idempotency_key: str) -> JobAccepted:
        body = self._transport.request(
            "POST",
            f"/v1/scopes/{_scope_path(scope_name)}/consolidate",
            idempotency_key=idempotency_key,
        )
        return _parse(JobAccepted, body)

    def recall(
        self, scope_name: str, request: RecallRequest | Mapping[str, Any]
    ) -> RecallResponse:
        body = self._transport.request(
            "POST",
            f"/v1/scopes/{_scope_path(scope_name)}/recall",
            json=_request_body(request, RecallRequest),
        )
        return _parse(RecallResponse, body)

    def memory_graph(
        self,
        scope_name: str,
        *,
        layers: tuple[str, ...] = ("slow",),
        limit: int = 180,
        cursor: str | None = None,
        query: str | None = None,
    ) -> MemoryGraphResponse:
        params: dict[str, Any] = {"layers": _graph_layers(layers), "limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if query is not None:
            params["query"] = query
        body = self._transport.request(
            "GET",
            f"/v1/scopes/{_scope_path(scope_name)}/memory-graph",
            params=params,
        )
        return _parse(MemoryGraphResponse, body)

    def memory_graph_neighbors(
        self,
        scope_name: str,
        memory_id: str,
        *,
        depth: int = 1,
        layers: tuple[str, ...] = ("slow", "fast", "source"),
        limit: int = 80,
        cursor: str | None = None,
    ) -> MemoryGraphResponse:
        params: dict[str, Any] = {
            "depth": depth,
            "layers": _graph_layers(layers),
            "limit": limit,
        }
        if cursor is not None:
            params["cursor"] = cursor
        body = self._transport.request(
            "GET",
            f"/v1/scopes/{_scope_path(scope_name)}/memory-graph/nodes/{_memory_path(memory_id)}/neighbors",
            params=params,
        )
        return _parse(MemoryGraphResponse, body)

    def memory_graph_evidence(
        self,
        scope_name: str,
        memory_id: str,
        *,
        limit: int = 10,
        cursor: str | None = None,
    ) -> MemoryGraphEvidenceResponse:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        body = self._transport.request(
            "GET",
            f"/v1/scopes/{_scope_path(scope_name)}/memory-graph/nodes/{_memory_path(memory_id)}/evidence",
            params=params,
        )
        return _parse(MemoryGraphEvidenceResponse, body)

    def trace_memory_recall(
        self,
        scope_name: str,
        request: MemoryGraphTraceRequest | Mapping[str, Any],
    ) -> MemoryGraphTraceResponse:
        body = self._transport.request(
            "POST",
            f"/v1/scopes/{_scope_path(scope_name)}/memory-graph/trace",
            json=_request_body(request, MemoryGraphTraceRequest),
        )
        return _parse(MemoryGraphTraceResponse, body)

    def get_job(self, job_id: str) -> JobView:
        return _parse(JobView, self._transport.request("GET", f"/v1/jobs/{_job_path(job_id)}"))

    def usage_costs(
        self,
        *,
        scope_name: str | None = None,
        scope_prefix: str | None = None,
        from_timestamp: float | None = None,
        to_timestamp: float | None = None,
        group_by: str | None = None,
    ) -> UsageCosts:
        params = {
            **({"scope_name": scope_name} if scope_name is not None else {}),
            **({"scope_prefix": scope_prefix} if scope_prefix is not None else {}),
            **(
                {"from_timestamp": from_timestamp}
                if from_timestamp is not None
                else {}
            ),
            **({"to_timestamp": to_timestamp} if to_timestamp is not None else {}),
            **({"group_by": group_by} if group_by is not None else {}),
        }
        return _parse(UsageCosts, self._transport.request("GET", "/v1/usage/costs", params=params))

    def issue_access_token(
        self,
        request: ScopeTokenCreateRequest | Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> IssuedScopeToken:
        retry_key = idempotency_key or f"scope-token-{uuid.uuid4()}"
        return _parse(
            IssuedScopeToken,
            self._transport.request(
                "POST",
                "/v1/access-tokens",
                json=_request_body(request, ScopeTokenCreateRequest),
                idempotency_key=retry_key,
            ),
        )

    def confirm_access_token(self, token_id: str) -> ScopeTokenView:
        return _parse(
            ScopeTokenView,
            self._transport.request(
                "POST", f"/v1/access-tokens/{_job_path(token_id)}/confirm"
            ),
        )

    def list_access_tokens(self) -> list[ScopeTokenView]:
        body = self._transport.request("GET", "/v1/access-tokens")
        return [_parse(ScopeTokenView, item) for item in body]

    def revoke_access_token(self, token_id: str) -> dict[str, Any]:
        return self._transport.request("DELETE", f"/v1/access-tokens/{_job_path(token_id)}")

    def create_webhook(
        self, request: WebhookCreateRequest | Mapping[str, Any]
    ) -> IssuedWebhook:
        return _parse(
            IssuedWebhook,
            self._transport.request(
                "POST", "/v1/webhooks", json=_request_body(request, WebhookCreateRequest)
            ),
        )

    def list_webhooks(self) -> list[WebhookView]:
        body = self._transport.request("GET", "/v1/webhooks")
        return [_parse(WebhookView, item) for item in body]

    def disable_webhook(self, endpoint_id: str) -> dict[str, Any]:
        return self._transport.request("DELETE", f"/v1/webhooks/{_job_path(endpoint_id)}")

    def export_scope(self, scope_name: str, *, idempotency_key: str) -> JobAccepted:
        return _parse(
            JobAccepted,
            self._transport.request(
                "POST",
                f"/v1/scopes/{_scope_path(scope_name)}/exports",
                idempotency_key=idempotency_key,
            ),
        )

    def download_scope_export(self, scope_name: str, export_id: str) -> bytes:
        return self._transport.request_bytes(
            f"/v1/scopes/{_scope_path(scope_name)}/exports/{_job_path(export_id)}"
        )

    def delete_scope(self, scope_name: str, *, idempotency_key: str) -> JobAccepted:
        body = self._transport.request(
            "DELETE",
            f"/v1/scopes/{_scope_path(scope_name)}",
            idempotency_key=idempotency_key,
            headers={"X-TMCRA-Confirm-Scope": scope_name},
        )
        return _parse(JobAccepted, body)

    def reopen_scope(self, scope_name: str) -> ScopeLifecycle:
        return _parse(
            ScopeLifecycle,
            self._transport.request("POST", f"/v1/scopes/{_scope_path(scope_name)}/reopen"),
        )

    def set_retention_policy(
        self,
        scope_name: str,
        request: RetentionPolicyRequest | Mapping[str, Any],
    ) -> RetentionPolicy:
        return _parse(
            RetentionPolicy,
            self._transport.request(
                "PUT",
                f"/v1/scopes/{_scope_path(scope_name)}/retention",
                json=_request_body(request, RetentionPolicyRequest),
            ),
        )

    def get_retention_policy(self, scope_name: str) -> RetentionPolicy:
        return _parse(
            RetentionPolicy,
            self._transport.request("GET", f"/v1/scopes/{_scope_path(scope_name)}/retention"),
        )

    def submit_feedback(
        self,
        scope_name: str,
        request: FeedbackRequest | Mapping[str, Any],
    ) -> FeedbackResponse:
        return _parse(
            FeedbackResponse,
            self._transport.request(
                "POST",
                f"/v1/scopes/{_scope_path(scope_name)}/feedback",
                json=_request_body(request, FeedbackRequest),
            ),
        )

    def cancel_job(self, job_id: str) -> JobResponse:
        return _parse(JobResponse, self._transport.request("POST", f"/v1/jobs/{_job_path(job_id)}/cancel"))

    def retry_job(self, job_id: str, *, idempotency_key: str) -> JobAccepted:
        body = self._transport.request(
            "POST",
            f"/v1/jobs/{_job_path(job_id)}/retry",
            idempotency_key=idempotency_key,
        )
        return _parse(JobAccepted, body)

    def wait_for_job(
        self,
        job_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 1.0,
        max_poll_interval: float = 10.0,
    ) -> JobView:
        _validate_polling(timeout, poll_interval, max_poll_interval)
        deadline = time.monotonic() + timeout
        interval = poll_interval
        last: JobView | None = None
        while True:
            last = self.get_job(job_id)
            if last.is_terminal:
                return last
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PollingTimeoutError(job_id, timeout)
            time.sleep(min(interval, remaining))
            interval = min(max_poll_interval, interval * 1.5)

    poll_job = wait_for_job


class AsyncClient:
    """An asynchronous client for the TMCRA Memory API."""

    def __init__(
        self,
        base_url: str = "https://api.tmcra.com",
        api_key: str | None = None,
        *,
        timeout: float | httpx.Timeout = 30.0,
        max_retries: int = 2,
        retry_initial_delay: float = 0.25,
        retry_max_delay: float = 5.0,
        headers: Mapping[str, str] | None = None,
        client_platform: str = "python",
        integration_id: str | None = None,
        agent_id: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if isinstance(timeout, (int, float)) and timeout <= 0:
            raise ConfigurationError("timeout must be positive")
        attribution_headers = {
            "X-TMCRA-Client-Platform": client_platform,
            **({"X-TMCRA-Integration-ID": integration_id} if integration_id else {}),
            **({"X-TMCRA-Agent-ID": agent_id} if agent_id else {}),
            **dict(headers or {}),
        }
        self._transport = AsyncTransport(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            retry=RetryConfig(
                max_retries=max_retries,
                initial_delay=retry_initial_delay,
                max_delay=retry_max_delay,
            ),
            headers=attribution_headers,
            transport=transport,
            client=http_client,
        )

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._transport.close()

    async def healthz(self) -> HealthResponse:
        return _parse(HealthResponse, await self._transport.request("GET", "/healthz"))

    async def readyz(self) -> ReadinessResponse:
        return _parse(
            ReadinessResponse,
            await self._transport.request("GET", "/readyz", allow_statuses=frozenset({503})),
        )

    async def authenticated_session(self) -> AuthenticatedSessionView:
        """Return the authenticated credential and service capability contract."""
        return _parse(
            AuthenticatedSessionView,
            await self._transport.request("GET", "/v1/session"),
        )

    async def list_scopes(
        self, *, prefix: str | None = None, limit: int = 100
    ) -> list[ScopeCatalogView]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        params: dict[str, Any] = {"limit": limit}
        if prefix is not None:
            if not prefix or len(prefix) > 128:
                raise ValueError("prefix must contain between 1 and 128 characters")
            params["prefix"] = prefix
        body = await self._transport.request("GET", "/v1/scopes", params=params)
        if not isinstance(body, list):
            raise ResponseValidationError("TMCRA response did not match scope list", body=body)
        return [_parse(ScopeCatalogView, item) for item in body]

    async def scope_summary(self, scope_name: str) -> ScopeSummaryView:
        return _parse(
            ScopeSummaryView,
            await self._transport.request(
                "GET", f"/v1/scopes/{_scope_path(scope_name)}/summary"
            ),
        )

    async def quota(self, *, subject: str | None = None) -> QuotaView:
        params = {"subject": subject} if subject is not None else None
        return _parse(
            QuotaView,
            await self._transport.request("GET", "/v1/usage/quota", params=params),
        )

    async def billing_profile(self) -> BillingProfileView:
        return _parse(
            BillingProfileView,
            await self._transport.request("GET", "/v1/billing/profile"),
        )

    async def set_entitlement(
        self,
        subject: str,
        request: EntitlementUpdateRequest | Mapping[str, Any],
    ) -> QuotaView:
        subject_path = _job_path(subject)
        return _parse(
            QuotaView,
            await self._transport.request(
                "PUT",
                f"/v1/usage/entitlements/{subject_path}",
                json=_request_body(request, EntitlementUpdateRequest),
            ),
        )

    async def set_quota_entitlement(
        self,
        subject: str,
        request: EntitlementUpdateRequest | Mapping[str, Any],
    ) -> QuotaView:
        if not subject.strip():
            raise ValueError("subject must not be empty")
        return _parse(
            QuotaView,
            await self._transport.request(
                "PUT",
                "/v1/usage/quota",
                params={"subject": subject},
                json=_request_body(request, EntitlementUpdateRequest),
            ),
        )

    async def health(self) -> HealthResponse:
        return await self.healthz()

    async def ready(self) -> ReadinessResponse:
        return await self.readyz()

    async def ingest(
        self,
        scope_name: str,
        request: IngestRequest | Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> JobAccepted:
        body = await self._transport.request(
            "POST",
            f"/v1/scopes/{_scope_path(scope_name)}/ingest",
            json=_request_body(request, IngestRequest),
            idempotency_key=idempotency_key,
        )
        return _parse(JobAccepted, body)

    async def bulk_ingest(
        self,
        scope_name: str,
        request: BulkIngestRequest | Mapping[str, Any],
    ) -> BulkIngestResponse:
        payload = _request_body(request, BulkIngestRequest)
        body = await self._transport.request(
            "POST",
            f"/v1/scopes/{_scope_path(scope_name)}/ingest/batch",
            json=payload,
            idempotency_key=payload["items"][0]["idempotency_key"],
        )
        return _parse(BulkIngestResponse, body)

    async def consolidate(self, scope_name: str, *, idempotency_key: str) -> JobAccepted:
        body = await self._transport.request(
            "POST",
            f"/v1/scopes/{_scope_path(scope_name)}/consolidate",
            idempotency_key=idempotency_key,
        )
        return _parse(JobAccepted, body)

    async def recall(
        self, scope_name: str, request: RecallRequest | Mapping[str, Any]
    ) -> RecallResponse:
        body = await self._transport.request(
            "POST",
            f"/v1/scopes/{_scope_path(scope_name)}/recall",
            json=_request_body(request, RecallRequest),
        )
        return _parse(RecallResponse, body)

    async def memory_graph(
        self,
        scope_name: str,
        *,
        layers: tuple[str, ...] = ("slow",),
        limit: int = 180,
        cursor: str | None = None,
        query: str | None = None,
    ) -> MemoryGraphResponse:
        params: dict[str, Any] = {"layers": _graph_layers(layers), "limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if query is not None:
            params["query"] = query
        body = await self._transport.request(
            "GET",
            f"/v1/scopes/{_scope_path(scope_name)}/memory-graph",
            params=params,
        )
        return _parse(MemoryGraphResponse, body)

    async def memory_graph_neighbors(
        self,
        scope_name: str,
        memory_id: str,
        *,
        depth: int = 1,
        layers: tuple[str, ...] = ("slow", "fast", "source"),
        limit: int = 80,
        cursor: str | None = None,
    ) -> MemoryGraphResponse:
        params: dict[str, Any] = {
            "depth": depth,
            "layers": _graph_layers(layers),
            "limit": limit,
        }
        if cursor is not None:
            params["cursor"] = cursor
        body = await self._transport.request(
            "GET",
            f"/v1/scopes/{_scope_path(scope_name)}/memory-graph/nodes/{_memory_path(memory_id)}/neighbors",
            params=params,
        )
        return _parse(MemoryGraphResponse, body)

    async def memory_graph_evidence(
        self,
        scope_name: str,
        memory_id: str,
        *,
        limit: int = 10,
        cursor: str | None = None,
    ) -> MemoryGraphEvidenceResponse:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        body = await self._transport.request(
            "GET",
            f"/v1/scopes/{_scope_path(scope_name)}/memory-graph/nodes/{_memory_path(memory_id)}/evidence",
            params=params,
        )
        return _parse(MemoryGraphEvidenceResponse, body)

    async def trace_memory_recall(
        self,
        scope_name: str,
        request: MemoryGraphTraceRequest | Mapping[str, Any],
    ) -> MemoryGraphTraceResponse:
        body = await self._transport.request(
            "POST",
            f"/v1/scopes/{_scope_path(scope_name)}/memory-graph/trace",
            json=_request_body(request, MemoryGraphTraceRequest),
        )
        return _parse(MemoryGraphTraceResponse, body)

    async def get_job(self, job_id: str) -> JobView:
        return _parse(JobView, await self._transport.request("GET", f"/v1/jobs/{_job_path(job_id)}"))

    async def usage_costs(
        self,
        *,
        scope_name: str | None = None,
        scope_prefix: str | None = None,
        from_timestamp: float | None = None,
        to_timestamp: float | None = None,
        group_by: str | None = None,
    ) -> UsageCosts:
        params = {
            **({"scope_name": scope_name} if scope_name is not None else {}),
            **({"scope_prefix": scope_prefix} if scope_prefix is not None else {}),
            **(
                {"from_timestamp": from_timestamp}
                if from_timestamp is not None
                else {}
            ),
            **({"to_timestamp": to_timestamp} if to_timestamp is not None else {}),
            **({"group_by": group_by} if group_by is not None else {}),
        }
        return _parse(UsageCosts, await self._transport.request("GET", "/v1/usage/costs", params=params))

    async def issue_access_token(
        self,
        request: ScopeTokenCreateRequest | Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> IssuedScopeToken:
        retry_key = idempotency_key or f"scope-token-{uuid.uuid4()}"
        return _parse(
            IssuedScopeToken,
            await self._transport.request(
                "POST",
                "/v1/access-tokens",
                json=_request_body(request, ScopeTokenCreateRequest),
                idempotency_key=retry_key,
            ),
        )

    async def confirm_access_token(self, token_id: str) -> ScopeTokenView:
        return _parse(
            ScopeTokenView,
            await self._transport.request(
                "POST", f"/v1/access-tokens/{_job_path(token_id)}/confirm"
            ),
        )

    async def list_access_tokens(self) -> list[ScopeTokenView]:
        body = await self._transport.request("GET", "/v1/access-tokens")
        return [_parse(ScopeTokenView, item) for item in body]

    async def revoke_access_token(self, token_id: str) -> dict[str, Any]:
        return await self._transport.request("DELETE", f"/v1/access-tokens/{_job_path(token_id)}")

    async def create_webhook(
        self, request: WebhookCreateRequest | Mapping[str, Any]
    ) -> IssuedWebhook:
        return _parse(
            IssuedWebhook,
            await self._transport.request(
                "POST", "/v1/webhooks", json=_request_body(request, WebhookCreateRequest)
            ),
        )

    async def list_webhooks(self) -> list[WebhookView]:
        body = await self._transport.request("GET", "/v1/webhooks")
        return [_parse(WebhookView, item) for item in body]

    async def disable_webhook(self, endpoint_id: str) -> dict[str, Any]:
        return await self._transport.request("DELETE", f"/v1/webhooks/{_job_path(endpoint_id)}")

    async def export_scope(self, scope_name: str, *, idempotency_key: str) -> JobAccepted:
        return _parse(
            JobAccepted,
            await self._transport.request(
                "POST",
                f"/v1/scopes/{_scope_path(scope_name)}/exports",
                idempotency_key=idempotency_key,
            ),
        )

    async def download_scope_export(self, scope_name: str, export_id: str) -> bytes:
        return await self._transport.request_bytes(
            f"/v1/scopes/{_scope_path(scope_name)}/exports/{_job_path(export_id)}"
        )

    async def delete_scope(self, scope_name: str, *, idempotency_key: str) -> JobAccepted:
        body = await self._transport.request(
            "DELETE",
            f"/v1/scopes/{_scope_path(scope_name)}",
            idempotency_key=idempotency_key,
            headers={"X-TMCRA-Confirm-Scope": scope_name},
        )
        return _parse(JobAccepted, body)

    async def reopen_scope(self, scope_name: str) -> ScopeLifecycle:
        return _parse(
            ScopeLifecycle,
            await self._transport.request("POST", f"/v1/scopes/{_scope_path(scope_name)}/reopen"),
        )

    async def set_retention_policy(
        self,
        scope_name: str,
        request: RetentionPolicyRequest | Mapping[str, Any],
    ) -> RetentionPolicy:
        return _parse(
            RetentionPolicy,
            await self._transport.request(
                "PUT",
                f"/v1/scopes/{_scope_path(scope_name)}/retention",
                json=_request_body(request, RetentionPolicyRequest),
            ),
        )

    async def get_retention_policy(self, scope_name: str) -> RetentionPolicy:
        return _parse(
            RetentionPolicy,
            await self._transport.request("GET", f"/v1/scopes/{_scope_path(scope_name)}/retention"),
        )

    async def submit_feedback(
        self,
        scope_name: str,
        request: FeedbackRequest | Mapping[str, Any],
    ) -> FeedbackResponse:
        return _parse(
            FeedbackResponse,
            await self._transport.request(
                "POST",
                f"/v1/scopes/{_scope_path(scope_name)}/feedback",
                json=_request_body(request, FeedbackRequest),
            ),
        )

    async def cancel_job(self, job_id: str) -> JobResponse:
        return _parse(JobResponse, await self._transport.request("POST", f"/v1/jobs/{_job_path(job_id)}/cancel"))

    async def retry_job(self, job_id: str, *, idempotency_key: str) -> JobAccepted:
        body = await self._transport.request(
            "POST",
            f"/v1/jobs/{_job_path(job_id)}/retry",
            idempotency_key=idempotency_key,
        )
        return _parse(JobAccepted, body)

    async def wait_for_job(
        self,
        job_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 1.0,
        max_poll_interval: float = 10.0,
    ) -> JobView:
        _validate_polling(timeout, poll_interval, max_poll_interval)
        deadline = time.monotonic() + timeout
        interval = poll_interval
        while True:
            last = await self.get_job(job_id)
            if last.is_terminal:
                return last
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PollingTimeoutError(job_id, timeout)
            await asyncio.sleep(min(interval, remaining))
            interval = min(max_poll_interval, interval * 1.5)

    poll_job = wait_for_job


def _validate_polling(timeout: float, poll_interval: float, max_poll_interval: float) -> None:
    if timeout <= 0 or poll_interval <= 0 or max_poll_interval <= 0:
        raise ValueError("polling timeout and intervals must be positive")
    if max_poll_interval < poll_interval:
        raise ValueError("max_poll_interval must be at least poll_interval")


# Compatibility names used by earlier SDK consumers.
TMCRAClient = SyncClient
AsyncTMCRAClient = AsyncClient
