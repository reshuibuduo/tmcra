"""Shared HTTP retry and response handling for sync and async clients."""

from __future__ import annotations

import asyncio
import email.utils
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

from .errors import (
    ConfigurationError,
    RequestTimeoutError,
    TransportError,
    error_from_response,
)


RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class RetryConfig:
    max_retries: int = 2
    initial_delay: float = 0.25
    max_delay: float = 5.0
    backoff_factor: float = 2.0

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ConfigurationError("max_retries must be non-negative")
        if self.initial_delay < 0 or self.max_delay < 0:
            raise ConfigurationError("retry delays must be non-negative")
        if self.backoff_factor < 1:
            raise ConfigurationError("backoff_factor must be at least 1")


def validate_base_url(base_url: str) -> str:
    if not isinstance(base_url, str) or not base_url.strip():
        raise ConfigurationError("base_url must be a non-empty URL")
    value = base_url.strip().rstrip("/")
    parsed = httpx.URL(value)
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise ConfigurationError("base_url must be an absolute http or https URL")
    return value


def validate_api_key(api_key: str | None) -> str | None:
    if api_key is None:
        return None
    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigurationError("api_key must be a non-empty string when provided")
    return api_key.strip()


def validate_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or not 8 <= len(value) <= 200:
        raise ValueError("idempotency_key must contain between 8 and 200 characters")
    return value


def retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, parsed.timestamp() - time.time())


def retry_delay(response: httpx.Response | None, attempt: int, config: RetryConfig) -> float:
    server_delay = retry_after_seconds(response) if response is not None else None
    if server_delay is not None:
        return min(config.max_delay, server_delay)
    return min(config.max_delay, config.initial_delay * (config.backoff_factor**attempt))


def decode_response(response: httpx.Response, *, allow_statuses: frozenset[int] = frozenset()) -> Any:
    if response.status_code >= 400 and response.status_code not in allow_statuses:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        raise error_from_response(
            response.status_code,
            body,
            request_id=response.headers.get("x-request-id"),
            headers=dict(response.headers),
        )
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise TransportError("TMCRA returned a non-JSON success response") from exc


class SyncTransport:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout: float | httpx.Timeout,
        retry: RetryConfig,
        headers: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = validate_base_url(base_url)
        self.api_key = validate_api_key(api_key)
        self.retry = retry
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._headers = {"accept": "application/json", "user-agent": "tmcra-client/0.1.0"}
        if headers:
            self._headers.update(headers)
        if self.api_key:
            self._headers["authorization"] = f"Bearer {self.api_key}"

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        allow_statuses: frozenset[int] = frozenset(),
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        request_headers = dict(self._headers)
        if headers:
            request_headers.update(headers)
        if idempotency_key is not None:
            request_headers["idempotency-key"] = validate_idempotency_key(idempotency_key)
        normalized_method = method.upper()
        retryable = normalized_method in SAFE_METHODS or (
            normalized_method == "POST" and idempotency_key is not None
        )
        attempts = self.retry.max_retries if retryable else 0
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(attempts + 1):
            try:
                empty_body = b"" if normalized_method in {"POST", "PUT", "PATCH"} and json is None else None
                response = self._client.request(
                    normalized_method,
                    url,
                    json=json,
                    content=empty_body,
                    params=params,
                    headers=request_headers,
                )
            except httpx.TimeoutException as exc:
                if attempt < attempts:
                    time.sleep(retry_delay(None, attempt, self.retry))
                    continue
                raise RequestTimeoutError(f"TMCRA request timed out: {normalized_method} {path}") from exc
            except httpx.RequestError as exc:
                if attempt < attempts:
                    time.sleep(retry_delay(None, attempt, self.retry))
                    continue
                raise TransportError(f"TMCRA request failed: {normalized_method} {path}") from exc
            if response.status_code in RETRYABLE_STATUSES and attempt < attempts:
                time.sleep(retry_delay(response, attempt, self.retry))
                continue
            return decode_response(response, allow_statuses=allow_statuses)
        raise AssertionError("unreachable retry loop")

    def request_bytes(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> bytes:
        """Download a binary response with the normal safe-GET retry policy."""

        request_headers = dict(self._headers)
        request_headers["accept"] = "application/octet-stream, application/zip"
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(self.retry.max_retries + 1):
            try:
                response = self._client.get(
                    url,
                    params=params,
                    headers=request_headers,
                )
            except httpx.TimeoutException as exc:
                if attempt < self.retry.max_retries:
                    time.sleep(retry_delay(None, attempt, self.retry))
                    continue
                raise RequestTimeoutError(
                    f"TMCRA request timed out: GET {path}"
                ) from exc
            except httpx.RequestError as exc:
                if attempt < self.retry.max_retries:
                    time.sleep(retry_delay(None, attempt, self.retry))
                    continue
                raise TransportError(f"TMCRA request failed: GET {path}") from exc
            if (
                response.status_code in RETRYABLE_STATUSES
                and attempt < self.retry.max_retries
            ):
                time.sleep(retry_delay(response, attempt, self.retry))
                continue
            if response.status_code >= 400:
                decode_response(response)
            return bytes(response.content)
        raise AssertionError("unreachable retry loop")

    def close(self) -> None:
        self._client.close()


class AsyncTransport:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout: float | httpx.Timeout,
        retry: RetryConfig,
        headers: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = validate_base_url(base_url)
        self.api_key = validate_api_key(api_key)
        self.retry = retry
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout, transport=transport)
        self._headers = {"accept": "application/json", "user-agent": "tmcra-client/0.1.0"}
        if headers:
            self._headers.update(headers)
        if self.api_key:
            self._headers["authorization"] = f"Bearer {self.api_key}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        allow_statuses: frozenset[int] = frozenset(),
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        request_headers = dict(self._headers)
        if headers:
            request_headers.update(headers)
        if idempotency_key is not None:
            request_headers["idempotency-key"] = validate_idempotency_key(idempotency_key)
        normalized_method = method.upper()
        retryable = normalized_method in SAFE_METHODS or (
            normalized_method == "POST" and idempotency_key is not None
        )
        attempts = self.retry.max_retries if retryable else 0
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(attempts + 1):
            try:
                empty_body = b"" if normalized_method in {"POST", "PUT", "PATCH"} and json is None else None
                response = await self._client.request(
                    normalized_method,
                    url,
                    json=json,
                    content=empty_body,
                    params=params,
                    headers=request_headers,
                )
            except httpx.TimeoutException as exc:
                if attempt < attempts:
                    await asyncio.sleep(retry_delay(None, attempt, self.retry))
                    continue
                raise RequestTimeoutError(f"TMCRA request timed out: {normalized_method} {path}") from exc
            except httpx.RequestError as exc:
                if attempt < attempts:
                    await asyncio.sleep(retry_delay(None, attempt, self.retry))
                    continue
                raise TransportError(f"TMCRA request failed: {normalized_method} {path}") from exc
            if response.status_code in RETRYABLE_STATUSES and attempt < attempts:
                await asyncio.sleep(retry_delay(response, attempt, self.retry))
                continue
            return decode_response(response, allow_statuses=allow_statuses)
        raise AssertionError("unreachable retry loop")

    async def request_bytes(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> bytes:
        """Download a binary response with the normal safe-GET retry policy."""

        request_headers = dict(self._headers)
        request_headers["accept"] = "application/octet-stream, application/zip"
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(self.retry.max_retries + 1):
            try:
                response = await self._client.get(
                    url,
                    params=params,
                    headers=request_headers,
                )
            except httpx.TimeoutException as exc:
                if attempt < self.retry.max_retries:
                    await asyncio.sleep(retry_delay(None, attempt, self.retry))
                    continue
                raise RequestTimeoutError(
                    f"TMCRA request timed out: GET {path}"
                ) from exc
            except httpx.RequestError as exc:
                if attempt < self.retry.max_retries:
                    await asyncio.sleep(retry_delay(None, attempt, self.retry))
                    continue
                raise TransportError(f"TMCRA request failed: GET {path}") from exc
            if (
                response.status_code in RETRYABLE_STATUSES
                and attempt < self.retry.max_retries
            ):
                await asyncio.sleep(retry_delay(response, attempt, self.retry))
                continue
            if response.status_code >= 400:
                decode_response(response)
            return bytes(response.content)
        raise AssertionError("unreachable retry loop")

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
