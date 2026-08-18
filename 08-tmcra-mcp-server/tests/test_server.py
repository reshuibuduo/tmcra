from __future__ import annotations

import unittest
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session

from tmcra_mcp.config import MCPSettings
from tmcra_mcp.server import MCPMessage, TMCRAToolset, create_server


class FakeClient:
    def __init__(self) -> None:
        self.ingest_args: dict[str, Any] | None = None

    async def recall(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "query_id": "q1",
            "scope_name": kwargs["scope"],
            "evidence_route": {"selected": "raw"},
            "prompt_evidence": {"content": "memory", "trust_boundary": "data"},
            "evidence": {"private": "large"},
        }

    async def ingest(self, **kwargs: Any) -> dict[str, Any]:
        self.ingest_args = kwargs
        return {"job_id": "job-1", "status": "pending"}

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "status": "succeeded"}

    async def wait_job(self, job_id: str, **_: Any) -> dict[str, Any]:
        return {"job_id": job_id, "status": "succeeded"}


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_recall_is_bounded_by_default(self) -> None:
        tools = TMCRAToolset(FakeClient(), default_scope="user-a")
        result = await tools.recall(
            query="pet",
            scope=None,
            evidence_mode="auto",
            max_windows=8,
            wait_for_job_id=None,
            include_structured_evidence=False,
        )
        self.assertEqual(result["scope_name"], "user-a")
        self.assertNotIn("evidence", result)
        self.assertEqual(result["prompt_evidence"]["content"], "memory")

    async def test_ingest_marks_integration_metadata(self) -> None:
        client = FakeClient()
        tools = TMCRAToolset(client, default_scope="user-a")
        await tools.ingest(
            session_id="session-a",
            messages=[MCPMessage(message_id="m1", role="user", content="hello")],
            scope=None,
            consistency="eventual",
            slow_policy="auto",
            idempotency_key=None,
        )
        assert client.ingest_args is not None
        self.assertEqual(client.ingest_args["metadata"]["integration"], "mcp")

    def test_server_can_register_all_tools(self) -> None:
        server = create_server(
            MCPSettings("https://memory.example", "secret", "user-a"),
            client=FakeClient(),
        )
        self.assertIsNotNone(server)

    async def test_mcp_protocol_lists_and_calls_recall(self) -> None:
        server = create_server(
            MCPSettings("https://memory.example", "secret", "user-a"),
            client=FakeClient(),
        )
        async with create_connected_server_and_client_session(server) as session:
            listed = await session.list_tools()
            self.assertEqual(
                {tool.name for tool in listed.tools},
                {"tmcra_recall", "tmcra_ingest", "tmcra_get_job", "tmcra_wait_job"},
            )
            result = await session.call_tool("tmcra_recall", {"query": "pet"})
            self.assertFalse(result.isError)
            self.assertEqual(result.structuredContent["query_id"], "q1")


if __name__ == "__main__":
    unittest.main()
