from __future__ import annotations

import json
import unittest

import httpx

from tmcra_mcp.client import TMCRAHttpClient, deterministic_idempotency_key
from tmcra_mcp.config import MCPSettings


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_reuses_deterministic_idempotency_key(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(202, json={"job_id": "job-1", "status": "pending"})

        settings = MCPSettings("https://memory.example", "secret", max_attempts=1)
        client = TMCRAHttpClient(settings, transport=httpx.MockTransport(handler))
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

    async def test_recall_returns_api_payload(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/scopes/user-a/recall")
            self.assertEqual(json.loads(request.content)["max_windows"], 4)
            return httpx.Response(200, json={"query_id": "q1", "prompt_evidence": {}})

        client = TMCRAHttpClient(
            MCPSettings("https://memory.example", "secret", max_attempts=1),
            transport=httpx.MockTransport(handler),
        )
        try:
            response = await client.recall(scope="user-a", query="pet", max_windows=4)
        finally:
            await client.aclose()
        self.assertEqual(response["query_id"], "q1")

    def test_idempotency_key_changes_with_payload(self) -> None:
        first = deterministic_idempotency_key("a", {"x": 1})
        second = deterministic_idempotency_key("a", {"x": 2})
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
