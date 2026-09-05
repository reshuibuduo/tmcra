from __future__ import annotations

import asyncio
import json
import unittest

import httpx

from tmcra_mcp.client import TMCRAError, TMCRAHttpClient, deterministic_idempotency_key
from tmcra_mcp.config import MCPSettings
from tmcra_mcp.receipts import ReceiptValidationError


def job(job_id: str = "job-1", status: str = "pending") -> dict[str, object]:
    return {
        "job_id": job_id,
        "tenant_id": "tenant-a",
        "scope_name": "user-a",
        "job_type": "ingest",
        "status": status,
        "attempts": 1,
        "created_at": 1.0,
        "updated_at": 1.0,
        "status_url": f"/v1/jobs/{job_id}",
    }


def ingest_response(job_id: str = "job-1", status: str = "pending") -> dict[str, object]:
    return {"scope_name": "user-a", "jobs": [job(job_id, status)]}


def recall_response() -> dict[str, object]:
    content = "memory"
    return {
        "query_id": "q1",
        "scope_name": "user-a",
        "index_job_id": "index-1",
        "evidence_route": {"requested": "auto", "selected": "raw", "reasons": []},
        "evidence": {"windows": []},
        "prompt_evidence": {
            "schema_version": "tmcra.prompt-evidence.v1",
            "format": "text/plain",
            "mode": "raw_hierarchical",
            "content": content,
            "content_sha256": __import__("hashlib").sha256(content.encode()).hexdigest(),
            "content_character_count": len(content),
            "source_text_verbatim": True,
            "trust_boundary": "untrusted_memory_data",
        },
    }


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_reuses_deterministic_idempotency_key(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(202, json=ingest_response())

        client = TMCRAHttpClient(
            MCPSettings("https://memory.example", "secret", max_attempts=1),
            transport=httpx.MockTransport(handler),
        )
        message = {
            "message_id": "m1",
            "role": "user",
            "content": "hello",
            "timestamp": "2026-07-15T00:00:00Z",
        }
        try:
            await client.ingest(scope="user-a", session_id="s1", messages=[message])
            await client.ingest(scope="user-a", session_id="s1", messages=[message])
        finally:
            await client.aclose()
        keys = [request.headers["idempotency-key"] for request in requests]
        self.assertEqual(keys[0], keys[1])
        self.assertTrue(keys[0].startswith("mcp-ingest-"))

    async def test_recall_validates_complete_response(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=recall_response())

        client = TMCRAHttpClient(
            MCPSettings("https://memory.example", "secret", max_attempts=1),
            transport=httpx.MockTransport(handler),
        )
        try:
            response = await client.recall(scope="user-a", query="pet")
        finally:
            await client.aclose()
        self.assertEqual(response["query_id"], "q1")
        self.assertEqual(response["schema_version"], "tmcra.mcp.recall-receipt.v1")
        self.assertEqual(response["contract_schema_version"], "tmcra.receipts.v1")
        self.assertEqual(response["submitted_status"], "completed")
        self.assertEqual(response["final_status"], "completed")
        self.assertTrue(response["final"])
        self.assertFalse(response["watermarks"]["available"])

    async def test_recall_rejects_non_production_window(self) -> None:
        client = TMCRAHttpClient(
            MCPSettings("https://memory.example", "secret", max_attempts=1),
            transport=httpx.MockTransport(lambda _: httpx.Response(500)),
        )
        try:
            with self.assertRaises(ValueError):
                await client.recall(scope="user-a", query="pet", max_windows=4)
        finally:
            await client.aclose()

    async def test_ingest_requires_202_and_complete_job_view(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=ingest_response())

        client = TMCRAHttpClient(
            MCPSettings("https://memory.example", "secret", max_attempts=1),
            transport=httpx.MockTransport(handler),
        )
        try:
            with self.assertRaises(TMCRAError):
                await client.ingest(
                    scope="user-a",
                    session_id="session-a",
                    messages=[{"message_id": "m1", "role": "assistant", "content": "done"}],
                )
        finally:
            await client.aclose()

    async def test_cancelled_bulk_ingest_stays_cancelled(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(202, json=ingest_response(status="cancelled"))

        client = TMCRAHttpClient(
            MCPSettings("https://memory.example", "secret", max_attempts=1),
            transport=httpx.MockTransport(handler),
        )
        try:
            response = await client.ingest(
                scope="user-a",
                session_id="session-a",
                messages=[{"message_id": "m1", "role": "user", "content": "hello"}],
            )
        finally:
            await client.aclose()
        self.assertEqual(response["status"], "cancelled")
        self.assertEqual(response["final_status"], "cancelled")

    async def test_bad_json_is_rejected(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not-json")

        client = TMCRAHttpClient(
            MCPSettings("https://memory.example", "secret", max_attempts=1),
            transport=httpx.MockTransport(handler),
        )
        try:
            with self.assertRaises(TMCRAError) as raised:
                await client.get_job("job-1")
        finally:
            await client.aclose()
        self.assertEqual(raised.exception.code, "invalid_json_response")

    async def test_wait_job_reaches_succeeded(self) -> None:
        calls = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=job("job-1", "pending" if calls == 1 else "succeeded"))

        client = TMCRAHttpClient(
            MCPSettings("https://memory.example", "secret", max_attempts=1),
            transport=httpx.MockTransport(handler),
        )
        try:
            result = await client.wait_job("job-1", timeout_seconds=1, poll_interval_seconds=0.01)
        finally:
            await client.aclose()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(calls, 2)

    async def test_wait_job_timeout(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=job("job-1", "running"))

        client = TMCRAHttpClient(
            MCPSettings("https://memory.example", "secret", max_attempts=1),
            transport=httpx.MockTransport(handler),
        )
        try:
            with self.assertRaises(TMCRAError) as raised:
                await client.wait_job("job-1", timeout_seconds=0.02, poll_interval_seconds=0.01)
        finally:
            await client.aclose()
        self.assertEqual(raised.exception.code, "job_wait_timeout")

    async def test_transport_failure_is_retryable_but_not_hidden(self) -> None:
        calls = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("offline")
            return httpx.Response(200, json=recall_response())

        client = TMCRAHttpClient(
            MCPSettings("https://memory.example", "secret", max_attempts=2),
            transport=httpx.MockTransport(handler),
        )
        try:
            result = await client.recall(scope="user-a", query="pet")
        finally:
            await client.aclose()
        self.assertEqual(result["query_id"], "q1")
        self.assertEqual(calls, 2)

    def test_idempotency_key_changes_with_payload(self) -> None:
        first = deterministic_idempotency_key("a", {"x": 1})
        second = deterministic_idempotency_key("a", {"x": 2})
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
