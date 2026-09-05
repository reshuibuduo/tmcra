from __future__ import annotations

import unittest
import hashlib
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session

from tmcra_mcp.config import MCPSettings
from tmcra_mcp.durable import DurableIngestQueue
from tmcra_mcp.server import MCPMessage, TMCRAToolset, create_server


class FakeClient:
    def __init__(self) -> None:
        self.ingest_args: dict[str, Any] | None = None
        self.recall_args: dict[str, Any] | None = None

    async def recall(self, **kwargs: Any) -> dict[str, Any]:
        self.recall_args = kwargs
        content = "memory"
        return {
            "query_id": "q1",
            "scope_name": kwargs["scope"],
            "index_job_id": "index-1",
            "evidence_route": {"requested": "auto", "selected": "raw", "reasons": []},
            "prompt_evidence": {
                "schema_version": "tmcra.prompt-evidence.v1",
                "format": "text/plain",
                "mode": "raw_hierarchical",
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "content_character_count": len(content),
                "source_text_verbatim": True,
                "trust_boundary": "untrusted_memory_data",
            },
            "evidence": {"private": "large"},
        }

    async def ingest(self, **kwargs: Any) -> dict[str, Any]:
        self.ingest_args = kwargs
        return {
            "scope_name": kwargs["scope"],
            "jobs": [{
                "job_id": "job-1", "tenant_id": "tenant-a", "scope_name": kwargs["scope"],
                "job_type": "ingest", "status": "pending", "attempts": 1,
                "created_at": 1.0, "updated_at": 1.0, "status_url": "/v1/jobs/job-1",
            }],
        }

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return {
            "job_id": job_id, "tenant_id": "tenant-a", "scope_name": "user-a",
            "job_type": "ingest", "status": "succeeded", "attempts": 1,
            "created_at": 1.0, "updated_at": 1.0, "status_url": f"/v1/jobs/{job_id}",
        }

    async def wait_job(self, job_id: str, **_: Any) -> dict[str, Any]:
        return await self.get_job(job_id)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_recall_is_bounded_by_default(self) -> None:
        tools = TMCRAToolset(FakeClient(), default_scope="user-a")
        result = await tools.recall(
            query="pet",
            scope=None,
            evidence_mode="auto",
            wait_for_job_id=None,
            include_structured_evidence=False,
        )
        self.assertEqual(result["scope_name"], "user-a")
        self.assertNotIn("evidence", result)
        self.assertEqual(result["prompt_evidence"]["content"], "memory")

    async def test_recall_forwards_explicit_agent_to_usage_ledger(self) -> None:
        client = FakeClient()
        tools = TMCRAToolset(client, default_scope="project", default_agent_id="planner")
        await tools.recall(
            query="handoff",
            scope=None,
            evidence_mode="auto",
            wait_for_job_id=None,
            include_structured_evidence=False,
            agent_id="reviewer",
        )
        assert client.recall_args is not None
        self.assertEqual(client.recall_args["agent_id"], "reviewer")

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
            agent_id=None,
        )
        assert client.ingest_args is not None
        self.assertEqual(client.ingest_args["metadata"]["integration"], "mcp")
        self.assertEqual(client.ingest_args["metadata"]["scope_sharing"], "shared")
        self.assertNotIn("agent_id", client.ingest_args["metadata"])

    async def test_shared_scope_preserves_roles_and_explicit_agent_attribution(self) -> None:
        client = FakeClient()
        tools = TMCRAToolset(
            client,
            default_scope="project-shared",
            default_agent_id="planner",
        )
        messages = [
            MCPMessage(message_id="m-user", role="user", content="Ship it"),
            MCPMessage(message_id="m-assistant", role="assistant", content="Done"),
        ]
        await tools.ingest(
            session_id="agent-session",
            messages=messages,
            scope=None,
            consistency="eventual",
            slow_policy="auto",
            idempotency_key=None,
            agent_id="implementer",
        )
        assert client.ingest_args is not None
        self.assertEqual(client.ingest_args["scope"], "project-shared")
        self.assertEqual(
            [message["role"] for message in client.ingest_args["messages"]],
            ["user", "assistant"],
        )
        self.assertEqual(
            client.ingest_args["messages"][0]["metadata"],
            {"actor_role": "user", "target_agent_id": "implementer"},
        )
        self.assertEqual(
            client.ingest_args["messages"][1]["metadata"],
            {"actor_role": "assistant", "agent_id": "implementer"},
        )
        self.assertEqual(client.ingest_args["metadata"]["agent_id"], "implementer")
        self.assertEqual(client.ingest_args["metadata"]["agent_id_source"], "tool_argument")
        self.assertEqual(client.ingest_args["agent_id"], "implementer")

    async def test_configured_agent_id_is_optional_and_not_inferred(self) -> None:
        configured_client = FakeClient()
        configured = TMCRAToolset(
            configured_client,
            default_scope="project-shared",
            default_agent_id="reviewer",
        )
        await configured.ingest(
            session_id="review-session",
            messages=[MCPMessage(message_id="review", role="assistant", content="Reviewed")],
            scope=None,
            consistency="eventual",
            slow_policy="auto",
            idempotency_key=None,
            agent_id=None,
        )
        assert configured_client.ingest_args is not None
        self.assertEqual(configured_client.ingest_args["metadata"]["agent_id"], "reviewer")
        self.assertEqual(
            configured_client.ingest_args["metadata"]["agent_id_source"],
            "configured_default",
        )
        self.assertEqual(
            configured_client.ingest_args["messages"][0]["metadata"]["agent_id"],
            "reviewer",
        )

    async def test_per_message_agent_identity_supports_mixed_agent_batch(self) -> None:
        client = FakeClient()
        tools = TMCRAToolset(client, default_scope="project-shared")
        await tools.ingest(
            session_id="handoff-session",
            messages=[
                MCPMessage(
                    message_id="u1",
                    role="user",
                    content="Investigate the queue",
                    target_agent_id="researcher",
                ),
                MCPMessage(
                    message_id="a1",
                    role="assistant",
                    content="Root cause found",
                    agent_id="researcher",
                ),
                MCPMessage(
                    message_id="a2",
                    role="assistant",
                    content="Patch applied",
                    agent_id="implementer",
                ),
            ],
            scope=None,
            consistency="eventual",
            slow_policy="auto",
            idempotency_key=None,
            agent_id=None,
        )
        assert client.ingest_args is not None
        self.assertEqual(
            [message["metadata"] for message in client.ingest_args["messages"]],
            [
                {"actor_role": "user", "target_agent_id": "researcher"},
                {"actor_role": "assistant", "agent_id": "researcher"},
                {"actor_role": "assistant", "agent_id": "implementer"},
            ],
        )

    def test_server_can_register_all_tools(self) -> None:
        server = create_server(
            MCPSettings("https://memory.example", "secret", "user-a"),
            client=FakeClient(),
            queue=DurableIngestQueue(":memory:"),
        )
        self.assertIsNotNone(server)

    async def test_mcp_protocol_lists_and_calls_recall(self) -> None:
        server = create_server(
            MCPSettings("https://memory.example", "secret", "user-a"),
            client=FakeClient(),
            queue=DurableIngestQueue(":memory:"),
        )
        async with create_connected_server_and_client_session(server) as session:
            listed = await session.list_tools()
            self.assertEqual(
                {tool.name for tool in listed.tools},
                {
                    "tmcra_recall", "tmcra_ingest", "tmcra_turn_prepare",
                    "tmcra_turn_commit", "tmcra_reconcile", "tmcra_get_job", "tmcra_wait_job",
                    "tmcra_memory_control", "tmcra_feedback",
                },
            )
            ingest_tool = next(tool for tool in listed.tools if tool.name == "tmcra_ingest")
            self.assertIn("agent_id", ingest_tool.inputSchema["properties"])
            result = await session.call_tool("tmcra_recall", {"query": "pet"})
            self.assertFalse(result.isError)
            self.assertEqual(result.structuredContent["query_id"], "q1")

    async def test_turn_prepare_commit_is_closed_loop(self) -> None:
        queue = DurableIngestQueue(":memory:")
        client = FakeClient()
        tools = TMCRAToolset(client, default_scope="user-a", queue=queue)
        prepared = await tools.prepare_turn(
            turn_id="turn-1",
            session_id="session-1",
            user_message_id="user-1",
            user_content="What did we decide?",
            scope=None,
            evidence_mode="auto",
            agent_id=None,
        )
        self.assertEqual(prepared["status"], "prepared")
        self.assertEqual(prepared["injectable_context"]["trust_boundary"], "untrusted_memory_data")
        committed = await tools.commit_turn(
            turn_id="turn-1",
            assistant_message_id="assistant-1",
            assistant_content="We decided to ship it.",
            assistant_timestamp=None,
            consistency="read_your_writes",
            slow_policy="auto",
            idempotency_key="turn-1-key",
            agent_id=None,
        )
        self.assertEqual(committed["status"], "succeeded")
        self.assertEqual(committed["recall"]["query_id"], "q1")
        self.assertEqual(queue.counts()["succeeded"], 1)
        queue.close()

    async def test_turn_prepare_retry_reuses_durable_prepare_record(self) -> None:
        queue = DurableIngestQueue(":memory:")
        client = FakeClient()
        tools = TMCRAToolset(client, default_scope="user-a", queue=queue)
        first = await tools.prepare_turn(
            turn_id="turn-retry",
            session_id="session-1",
            user_message_id="user-1",
            user_content="same question",
            scope=None,
            evidence_mode="auto",
            agent_id=None,
        )
        second = await tools.prepare_turn(
            turn_id="turn-retry",
            session_id="session-1",
            user_message_id="user-1",
            user_content="same question",
            scope=None,
            evidence_mode="auto",
            agent_id=None,
        )
        self.assertEqual(first["recall"]["query_id"], second["recall"]["query_id"])
        queue.close()


if __name__ == "__main__":
    unittest.main()
