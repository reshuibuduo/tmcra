import os
import tempfile
import unittest
from unittest.mock import patch

from tmcra_mcp.config import MCPSettings
from tmcra_mcp.controls import control_key, control, policy, may_write, continuation, select_evidence, begin_turn, suppress_turn
from tmcra_mcp.durable import DurableIngestQueue
from tmcra_mcp.server import TMCRAToolset
from test_server import FakeClient
import httpx
from mcp.shared.memory import create_connected_server_and_client_session
from tmcra_mcp.client import TMCRAHttpClient
from tmcra_mcp.server import create_server


class ControlsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"TMCRA_MEMORY_STATE_DIR": self.directory.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.directory.cleanup)
        self.settings = MCPSettings("https://example.invalid", "test-controls-key", "project")
        self.key = control_key(self.settings, "project")

    async def test_off_prepare_and_reenable_never_backfill(self):
        client = FakeClient()
        queue = DurableIngestQueue(":memory:")
        self.addCleanup(queue.close)
        tools = TMCRAToolset(client, default_scope="project", queue=queue, control_settings=self.settings)
        first = policy(self.key, "session")
        control(self.key, "session", "mode", {"mode": "off"})
        result = await tools.prepare_turn(turn_id="private", session_id="session", user_message_id="private", user_content="PRIVATE", scope=None, evidence_mode="raw", agent_id=None)
        self.assertEqual(result["status"], "disabled")
        self.assertIsNone(client.recall_args)
        self.assertEqual(queue._connection.execute("SELECT count(*) FROM prepared_turns").fetchone()[0], 0)
        control(self.key, "session", "mode", {"mode": "normal"})
        self.assertFalse(may_write(first))

    async def test_pending_queue_obeys_generation_after_restart(self):
        client = FakeClient()
        queue = DurableIngestQueue(":memory:")
        self.addCleanup(queue.close)
        capture = policy(self.key, "session")
        queue.enqueue(scope_name="project", session_id="session", messages=[{"role": "user", "content": "old queued turn"}],
                      consistency="eventual", slow_policy="auto", idempotency_key="old-queue-one", metadata={}, agent_id=None,
                      recall_receipt={"_local_capture": capture})
        control(self.key, "session", "mode", {"mode": "off"})
        control(self.key, "session", "mode", {"mode": "normal"})
        result = await queue.drain(client)
        self.assertEqual(result[0]["status"], "discarded")
        self.assertIsNone(client.ingest_args)

    async def test_task_ambiguity_scope_isolation_and_budget(self):
        task = control(self.key, "one", "task", {"objective": "Complete login", "nextStep": "Test expiry"})
        self.assertIn("Complete login", continuation(self.key, "fresh", "继续")["query"])
        control(self.key, "two", "task", {"objective": "Billing"})
        self.assertEqual(len(continuation(self.key, "fresh", "继续")["candidates"]), 2)
        self.assertEqual(continuation(self.key, "one", "继续")["task"]["id"], task["id"])
        self.assertEqual(continuation(control_key(self.settings, "other"), "fresh", "继续")["candidates"], [])
        self.assertEqual(select_evidence("x" * 2000, 1000)["content"], "")
        self.assertEqual(select_evidence("known source", 1000, "known source")["content"], "")
        self.assertEqual(select_evidence("known source", 1000, "compact summary")["content"], "known source")

    async def test_feedback_tool_uses_idempotent_authenticated_wire_contract(self):
        import json
        calls = []
        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json={"scope_name":"project", "memory_id":"source-a", "items":[{"text":"old fact"}], "page":{"has_more":False}})
            calls.append(request)
            return httpx.Response(201, json={"feedback_id": "fb-test", "effective": True, "action": "correct", "correction_index_status": "pending"})
        client = TMCRAHttpClient(self.settings, transport=httpx.MockTransport(handler))
        queue = DurableIngestQueue(":memory:")
        self.addCleanup(queue.close)
        server = create_server(self.settings, client=client, queue=queue)
        try:
            async def approve(context, params):
                from mcp.types import ElicitResult
                self.assertEqual(calls, [])
                self.assertIn("old fact", params.message)
                self.assertIn("corrected fact", params.message)
                return ElicitResult(action="accept", content={"confirm":True})
            async with create_connected_server_and_client_session(server, elicitation_callback=approve) as session:
                result = await session.call_tool("tmcra_feedback", {"session_id": "s", "memory_ids": ["source-a"], "action": "correct", "replacement": "corrected fact", "idempotency_key": "logical-feedback-one"})
                self.assertFalse(result.isError, result)
                self.assertTrue(result.structuredContent["effective"])
            self.assertEqual(calls[0].headers["Idempotency-Key"], "logical-feedback-one")
            self.assertEqual(json.loads(calls[0].content)["replacement"], "corrected fact")
        finally:
            await client.aclose()

    async def test_correction_turn_veto_does_not_drop_unrelated_older_turn(self):
        first = begin_turn(self.key, "session", "first")
        correction = begin_turn(self.key, "session", "correction")
        suppress_turn(self.key, "session")
        self.assertTrue(may_write(first))
        self.assertFalse(may_write(correction))
        self.assertTrue(may_write(begin_turn(self.key, "session", "after")))
        self.assertFalse(may_write(correction))

    async def test_feedback_reject_cancel_missing_confirmation_do_not_post(self):
        from mcp.types import ElicitResult
        for action in ("decline", "cancel", "unchecked", "unsupported"):
            calls = []
            def handler(request):
                calls.append(request.method)
                return httpx.Response(200, json={"scope_name":"project", "memory_id":"source-a", "items":[{"text":"old fact"}], "page":{}})
            client = TMCRAHttpClient(self.settings, transport=httpx.MockTransport(handler))
            queue = DurableIngestQueue(":memory:")
            server = create_server(self.settings, client=client, queue=queue)
            async def reject(context, params):
                return ElicitResult(action="accept" if action == "unchecked" else action, content={"confirm":False})
            try:
                async with create_connected_server_and_client_session(server, elicitation_callback=None if action == "unsupported" else reject) as session:
                    result = await session.call_tool("tmcra_feedback", {"session_id":"s", "memory_ids":["source-a"], "action":"correct", "replacement":"new", "idempotency_key":"one-correction"})
                    self.assertFalse(result.isError, result)
                    self.assertFalse(result.structuredContent["applied"])
                self.assertNotIn("POST", calls)
            finally:
                await client.aclose()
                queue.close()
