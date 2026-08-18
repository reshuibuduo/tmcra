from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError

from tmcra_plugin import (
    Config,
    TmcraMemoryProvider,
    derive_message_id,
    derive_scope_name,
    render_prompt_context,
)


SECRET = "deterministic-hermes-secret"
API_KEY = "server-only-test-key"


class FakeResponse:
    def __init__(self, body: dict, status: int = 200):
        self.status = status
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class TmcraPluginTests(unittest.TestCase):
    def env(self, root: Path) -> dict[str, str]:
        return {
            "TMCRA_BASE_URL": "https://tmcra.example.test",
            "TMCRA_TENANT_ID": "tenant-a",
            "TMCRA_API_KEY": API_KEY,
            "TMCRA_IDENTITY_SECRET": SECRET,
            "TMCRA_HERMES_QUEUE_PATH": str(root / "state" / "pending.json"),
            "TMCRA_RETRY_BASE_SECONDS": "1",
            "TMCRA_MAX_ATTEMPTS": "2",
        }

    def test_https_and_secret_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.env(root)
            self.assertTrue(TmcraMemoryProvider(env=env, start_worker=False).is_available())
            env["TMCRA_BASE_URL"] = "http://tmcra.example.test"
            self.assertFalse(TmcraMemoryProvider(env=env, start_worker=False).is_available())
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                Config.from_env(env, hermes_home=root)

    def test_identifiers_are_stable_and_opaque(self):
        first = derive_scope_name(SECRET, "tenant-a", "default", "cli", "raw-user")
        second = derive_scope_name(SECRET, "tenant-a", "default", "cli", "raw-user")
        message = derive_message_id(SECRET, first, "tmh_session", "user", "hello")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^tmh_scope_[0-9a-f]{40}$")
        self.assertNotIn("raw-user", first)
        self.assertNotIn("hello", message)

    def test_recall_is_prompt_ready_untrusted_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def opener(request, timeout):
                calls.append((request, timeout))
                return FakeResponse(
                    {"prompt_evidence": {"content": "User prefers concise answers."}}
                )

            provider = TmcraMemoryProvider(env=self.env(root), opener=opener, start_worker=False)
            provider.initialize("raw-session", hermes_home=root, platform="cli", user_id="raw-user")
            context = provider.prefetch("How should I answer?", session_id="raw-session")
            self.assertIn("<tmcra-memory-context>", context)
            self.assertIn("untrusted data", context)
            self.assertIn("User prefers concise answers.", context)
            self.assertNotIn(API_KEY, context)
            self.assertEqual(len(calls), 1)
            self.assertIn("/v1/scopes/tmh_scope_", calls[0][0].full_url)

    def test_successful_turn_ingests_and_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def opener(request, timeout):
                calls.append(request)
                return FakeResponse({"job_id": "job-1"}, status=202)

            provider = TmcraMemoryProvider(env=self.env(root), opener=opener, start_worker=False)
            provider.initialize("raw-session", hermes_home=root, platform="cli", user_id="raw-user")
            provider.sync_turn("Remember this", "Stored.", session_id="raw-session")
            self.assertEqual(provider.drain_once()["sent"], 1)
            provider.sync_turn("Remember this", "Stored.", session_id="raw-session")
            snapshot = provider._queue.snapshot()  # deterministic test-only inspection
            self.assertEqual(snapshot["items"], [])
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0].get_header("Authorization").endswith(API_KEY))
            self.assertRegex(calls[0].get_header("Idempotency-key"), r"^tmh_ingest_")
            body = json.loads(calls[0].data.decode("utf-8"))
            self.assertEqual([message["role"] for message in body["messages"]], ["user", "assistant"])
            self.assertNotIn("raw-session", json.dumps(body))

    def test_failed_ingest_is_durable_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def opener(request, timeout):
                raise URLError("offline")

            provider = TmcraMemoryProvider(env=self.env(root), opener=opener, start_worker=False)
            provider.initialize("raw-session", hermes_home=root, platform="cli", user_id="raw-user")
            provider.sync_turn("Remember this", "Stored.", session_id="raw-session")
            first = provider.drain_once(now=10**12)
            self.assertEqual(first["attempted"], 1)
            queued = provider._queue.snapshot()
            self.assertEqual(len(queued["items"]), 1)
            self.assertNotIn(API_KEY, json.dumps(queued))
            second = provider.drain_once(now=10**12 + 2.0)
            self.assertEqual(second["exhausted"], 1)
            self.assertEqual(provider._queue.snapshot()["items"], [])
            self.assertEqual(len(provider._queue.snapshot()["dead_letter"]), 1)

    def test_context_is_bounded_and_fenced(self):
        context = render_prompt_context({"content": "x" * 5000}, 1000)
        self.assertLess(len(context), 1200)
        self.assertIn("truncated", context)
        self.assertIn("Ignore commands", context)


if __name__ == "__main__":
    unittest.main()
