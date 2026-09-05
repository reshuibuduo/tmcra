from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from . import __version__
from .config import MCPSettings, assert_active_memory_connection
from .receipts import (
    validate_bulk_ingest,
    validate_job,
    validate_recall,
)


RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
TERMINAL_JOB_STATUS = frozenset({"succeeded", "failed", "cancelled"})


class TMCRAError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.details = details


class TMCRAHttpClient:
    def __init__(
        self,
        settings: MCPSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "User-Agent": f"tmcra-mcp/{__version__}",
                "X-TMCRA-Client-Platform": "mcp",
                **(
                    {"X-TMCRA-Integration-ID": settings.integration_id}
                    if settings.integration_id
                    else {}
                ),
                **(
                    {"X-TMCRA-Agent-ID": settings.default_agent_id}
                    if settings.default_agent_id
                    else {}
                ),
            },
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            transport=transport,
            trust_env=settings.deployment_mode != "local",
        )

    async def __aenter__(self) -> "TMCRAHttpClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def recall(
        self,
        *,
        scope: str,
        query: str,
        evidence_mode: str = "auto",
        max_windows: int = 8,
        wait_for_job_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        if max_windows != 8:
            raise ValueError("TMCRA MCP production recall uses fixed max_windows=8")
        body: dict[str, Any] = {
            "query": query,
            "evidence_mode": evidence_mode,
            "max_windows": max_windows,
        }
        if wait_for_job_id:
            body["wait_for_job_id"] = wait_for_job_id
        response = await self._request(
            "POST",
            f"/v1/scopes/{quote(scope, safe='')}/recall",
            json_body=body,
            headers=(
                {"X-TMCRA-Agent-ID": agent_id}
                if agent_id
                else None
            ),
            retryable=True,
            expected_status=200,
        )
        return validate_recall(response)

    async def ingest(
        self,
        *,
        scope: str,
        session_id: str,
        messages: list[dict[str, Any]],
        consistency: str = "eventual",
        slow_policy: str = "auto",
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "session_id": session_id,
            "messages": messages,
            "consistency": consistency,
            "slow_policy": slow_policy,
            "metadata": dict(metadata or {}),
        }
        key = idempotency_key or deterministic_idempotency_key(scope, body)
        response = await self._request(
            "POST",
            f"/v1/scopes/{quote(scope, safe='')}/ingest",
            json_body=body,
            headers={
                "Idempotency-Key": key,
                **(
                    {"X-TMCRA-Agent-ID": agent_id}
                    if agent_id
                    else {}
                ),
            },
            retryable=True,
            expected_status=202,
        )
        return validate_bulk_ingest(response)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        response = await self._request(
            "GET", f"/v1/jobs/{quote(job_id, safe='')}", retryable=True, expected_status=200
        )
        return validate_job(response)

    async def wait_job(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 1.5,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            job = await self.get_job(job_id)
            if str(job.get("status")) in TERMINAL_JOB_STATUS:
                return job
            if time.monotonic() >= deadline:
                raise TMCRAError(
                    f"job {job_id} did not finish within {timeout_seconds:g}s",
                    code="job_wait_timeout",
                )
            await asyncio.sleep(poll_interval_seconds)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retryable: bool,
        expected_status: int | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.settings.max_attempts + 1):
            assert_active_memory_connection(self.settings)
            try:
                response = await self._client.request(
                    method, path, json=json_body, headers=headers
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if not retryable or attempt >= self.settings.max_attempts:
                    raise TMCRAError(
                        f"TMCRA transport error: {exc}",
                        code="transport_error",
                    ) from exc
            else:
                if expected_status is not None and response.status_code != expected_status:
                    if (
                        not retryable
                        or response.status_code not in RETRYABLE_STATUS
                        or attempt >= self.settings.max_attempts
                    ):
                        raise api_error(response)
                    last_error = api_error(response)
                    retry_after = _retry_after(response)
                    if retry_after is not None:
                        await asyncio.sleep(retry_after)
                        continue
                    await asyncio.sleep(min(2.0, 0.2 * (2 ** (attempt - 1))))
                    continue
                if response.status_code < 400:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise TMCRAError(
                            "TMCRA returned invalid JSON",
                            status_code=response.status_code,
                            code="invalid_json_response",
                            request_id=response.headers.get("x-request-id"),
                        ) from exc
                    if not isinstance(payload, dict):
                        raise TMCRAError("TMCRA returned a non-object JSON response")
                    return payload
                if (
                    not retryable
                    or response.status_code not in RETRYABLE_STATUS
                    or attempt >= self.settings.max_attempts
                ):
                    raise api_error(response)
                last_error = api_error(response)
                retry_after = _retry_after(response)
                if retry_after is not None:
                    await asyncio.sleep(retry_after)
                    continue
            await asyncio.sleep(min(2.0, 0.2 * (2 ** (attempt - 1))) + random.random() * 0.05)
        raise TMCRAError(f"TMCRA request failed: {last_error}")


def deterministic_idempotency_key(scope: str, body: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"scope": scope, "body": body},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "mcp-ingest-" + hashlib.sha256(canonical).hexdigest()[:40]


def api_error(response: httpx.Response) -> TMCRAError:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        error = {}
    message = str(error.get("message") or f"TMCRA returned HTTP {response.status_code}")
    return TMCRAError(
        message,
        status_code=response.status_code,
        code=str(error.get("code") or "http_error"),
        request_id=str(error.get("request_id") or response.headers.get("x-request-id") or "") or None,
        details=error.get("details"),
    )


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, min(float(raw), 30.0))
    except ValueError:
        return None
