from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import closing
from pathlib import Path

from tmcra_service.auth import (
    APIKeyAuth,
    AuthenticationError,
    AuthorizationError,
)
from tmcra_service.control_db import ControlDB
from tmcra_service.jobs import (
    CANCELLED,
    FAILED,
    PENDING,
    RUNNING,
    SUCCEEDED,
    IdempotencyConflict,
    JobQueueFull,
    JobStateError,
    JobStore,
)
from tmcra_service.rate_limit import PressureGate


class ControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "control.sqlite3"
        self.db = ControlDB(self.db_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_wal_and_transaction_rollback(self) -> None:
        self.assertEqual(self.db.journal_mode(), "wal")
        with self.assertRaises(RuntimeError):
            with self.db.transaction() as connection:
                connection.execute("CREATE TABLE rollback_probe(value TEXT)")
                raise RuntimeError("rollback")
        with closing(self.db.connect()) as connection:
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE name='rollback_probe'"
            ).fetchone())

    def test_api_key_hash_and_tenant_scope_enforcement(self) -> None:
        auth = APIKeyAuth(self.db, iterations=100_000)
        auth.set_tenant_scopes("tenant-a", {"memory:read", "memory:write"})
        issued = auth.create_key("tenant-a", {"memory:read"})
        self.assertTrue(auth.authenticate(issued.api_key).allows("memory:read"))
        auth.authorize(issued.api_key, "tenant-a", {"memory:read"})
        with self.assertRaises(AuthorizationError):
            auth.authorize(issued.api_key, "tenant-b", {"memory:read"})
        with self.assertRaises(AuthorizationError):
            auth.authorize(issued.api_key, "tenant-a", {"memory:write"})
        with self.assertRaises(AuthenticationError):
            auth.authenticate(issued.api_key + "x")
        with closing(self.db.connect()) as connection:
            row = connection.execute("SELECT secret_hash FROM api_keys WHERE key_id=?", (issued.key_id,)).fetchone()
            self.assertNotIn(issued.api_key.encode(), row[0].encode())
            self.assertNotIn(issued.api_key, row[0])

    def test_idempotent_job_state_machine(self) -> None:
        jobs = JobStore(self.db, lease_seconds=10)
        first = jobs.submit("tenant-a", "request-1", {"b": 2, "a": 1})
        replay = jobs.submit("tenant-a", "request-1", {"a": 1, "b": 2})
        self.assertEqual(first.job_id, replay.job_id)
        self.assertEqual(first.state, PENDING)
        with self.assertRaises(IdempotencyConflict):
            jobs.submit("tenant-a", "request-1", {"a": 9})
        running = jobs.claim(first.job_id, "worker-1")
        self.assertEqual(running.state, RUNNING)
        self.assertTrue(jobs.heartbeat(first.job_id, "worker-1", now=100.0))
        self.assertFalse(jobs.heartbeat(first.job_id, "worker-2", now=100.0))
        self.assertEqual(jobs.expired_running(now=109.0), [])
        self.assertEqual(
            [item.job_id for item in jobs.expired_running(now=111.0)],
            [first.job_id],
        )
        self.assertFalse(
            jobs.fail_expired(first.job_id, "worker-1", "expired", now=109.0)
        )
        with self.assertRaises(JobStateError):
            jobs.claim(first.job_id, "worker-2")
        done = jobs.succeed(first.job_id, {"ok": True}, worker_id="worker-1")
        self.assertEqual(done.state, SUCCEEDED)
        self.assertEqual(jobs.succeed(first.job_id, {"ok": True}, worker_id="worker-1").version, done.version)
        with self.assertRaises(JobStateError):
            jobs.transition(first.job_id, FAILED)
        failed = jobs.submit("tenant-a", "request-failed", {"job_type": "reindex"})
        failed = jobs.claim(failed.job_id, "worker-1")
        failed = jobs.fail(failed.job_id, "index failed", worker_id="worker-1")
        resumed = jobs.resume_failed(failed.job_id)
        self.assertEqual(resumed.state, PENDING)
        self.assertIsNone(resumed.error)
        self.assertIsNone(resumed.worker_id)
        self.assertIsNone(resumed.started_at)
        self.assertIsNone(resumed.finished_at)
        cancelled = jobs.submit("tenant-a", "request-2", {})
        self.assertEqual(jobs.cancel(cancelled.job_id).state, CANCELLED)

    def test_minute_limit_and_lease_expiry(self) -> None:
        gate = PressureGate(self.db, max_concurrency=1, per_minute=2, lease_seconds=5)
        first = gate.acquire("tenant-a", now=120.0)
        self.assertTrue(first)
        self.assertFalse(gate.acquire("tenant-a", now=121.0))
        self.assertTrue(gate.release(first.lease_id))
        second = gate.acquire("tenant-a", now=122.0)
        self.assertTrue(second)
        self.assertFalse(gate.acquire("tenant-a", now=123.0))
        self.assertEqual(gate.acquire("tenant-a", now=123.0).reason, "concurrency")
        self.assertTrue(gate.release(second.lease_id))
        self.assertEqual(gate.acquire("tenant-a", now=123.0).reason, "per_minute")
        self.assertTrue(gate.acquire("tenant-a", now=180.0))

    def test_pressure_gate_is_cross_process_atomic(self) -> None:
        script = textwrap.dedent(
            """
            import sys
            from tmcra_service.control_db import ControlDB
            from tmcra_service.rate_limit import PressureGate
            gate = PressureGate(ControlDB(sys.argv[1]), max_concurrency=1, per_minute=10, lease_seconds=30)
            result = gate.acquire("tenant-a", now=300.0)
            print("1" if result.granted else "0", flush=True)
            """
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent) + os.pathsep + env.get("PYTHONPATH", "")
        processes = [
            subprocess.Popen([sys.executable, "-c", script, str(self.db_path)], stdout=subprocess.PIPE, text=True, env=env)
            for _ in range(2)
        ]
        results = [process.communicate(timeout=20)[0].strip() for process in processes]
        self.assertEqual(results.count("1"), 1)
        self.assertEqual(results.count("0"), 1)

    def test_queue_admission_is_atomic_and_idempotent_replays_bypass_limit(self) -> None:
        jobs = JobStore(self.db)
        first = jobs.submit(
            "tenant-a",
            "queue-1",
            {"value": 1},
            tenant_queue_limit=1,
            global_queue_limit=2,
        )
        replay = jobs.submit(
            "tenant-a",
            "queue-1",
            {"value": 1},
            tenant_queue_limit=1,
            global_queue_limit=2,
        )
        self.assertEqual(first.job_id, replay.job_id)
        with self.assertRaises(JobQueueFull):
            jobs.submit(
                "tenant-a",
                "queue-2",
                {"value": 2},
                tenant_queue_limit=1,
                global_queue_limit=2,
            )


if __name__ == "__main__":
    unittest.main()
