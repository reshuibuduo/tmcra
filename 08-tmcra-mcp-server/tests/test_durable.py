from __future__ import annotations

import asyncio
import unittest

from tmcra_mcp.durable import DurableIngestQueue
from tmcra_mcp.receipts import ReceiptValidationError


def job(job_id: str, status: str) -> dict[str, object]:
    return {
        "job_id": job_id,
        "tenant_id": "tenant-a",
        "scope_name": "scope-a",
        "job_type": "ingest",
        "status": status,
        "attempts": 1,
        "created_at": 1.0,
        "updated_at": 1.0,
        "status_url": f"/v1/jobs/{job_id}",
    }


class FakeClient:
    def __init__(self, *, final_status: str = "succeeded", fail_submit: bool = False) -> None:
        self.final_status = final_status
        self.fail_submit = fail_submit
        self.ingest_calls = 0
        self.wait_calls = 0

    async def ingest(self, **_: object) -> dict[str, object]:
        self.ingest_calls += 1
        if self.fail_submit:
            raise RuntimeError("offline")
        return {"scope_name": "scope-a", "jobs": [job("job-a", "pending")]}

    async def wait_job(self, job_id: str, **_: object) -> dict[str, object]:
        self.wait_calls += 1
        return job(job_id, self.final_status)


class TimeoutClient(FakeClient):
    async def wait_job(self, job_id: str, **_: object) -> dict[str, object]:
        from tmcra_mcp.client import TMCRAError

        raise TMCRAError("timed out", code="job_wait_timeout")


class DurableQueueTests(unittest.IsolatedAsyncioTestCase):
    def make_queue(self, max_attempts: int = 3) -> DurableIngestQueue:
        return DurableIngestQueue(":memory:", max_attempts=max_attempts)

    def enqueue(self, queue: DurableIngestQueue) -> str:
        return queue.enqueue(
            scope_name="scope-a",
            session_id="session-a",
            messages=[{"message_id": "m1", "role": "user", "content": "hello"}],
            consistency="read_your_writes",
            slow_policy="auto",
            idempotency_key="stable-key",
            metadata={"integration": "mcp"},
            agent_id=None,
        )

    async def test_202_to_succeeded_is_reconciled(self) -> None:
        queue = self.make_queue()
        item_id = self.enqueue(queue)
        try:
            result = (await queue.drain(FakeClient(), item_id=item_id))[0]
        finally:
            queue.close()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["ingest"]["jobs"][0]["status"], "succeeded")

    async def test_202_to_failed_becomes_dead_letter(self) -> None:
        queue = self.make_queue()
        item_id = self.enqueue(queue)
        try:
            result = (await queue.drain(FakeClient(final_status="failed"), item_id=item_id))[0]
            self.assertEqual(queue.counts()["dead_letter"], 1)
        finally:
            queue.close()
        self.assertEqual(result["status"], "dead_letter")

    async def test_submit_transport_failure_is_retained_for_reconciliation(self) -> None:
        queue = self.make_queue()
        item_id = self.enqueue(queue)
        try:
            result = (await queue.drain(FakeClient(fail_submit=True), item_id=item_id))[0]
            self.assertEqual(result["status"], "pending")
            self.assertEqual(queue.counts()["pending"], 1)
        finally:
            queue.close()

    async def test_restart_reuses_same_idempotency_key_and_can_reconcile(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.sqlite3"
            queue = DurableIngestQueue(path)
            item_id = self.enqueue(queue)
            await queue.drain(FakeClient(fail_submit=True), item_id=item_id)
            queue.close()
            recovered = DurableIngestQueue(path)
            try:
                self.assertEqual(recovered.recovery_items(), [item_id])
                client = FakeClient()
                result = (await recovered.drain(client, item_id=item_id))[0]
                self.assertEqual(result["status"], "succeeded")
                self.assertEqual(client.ingest_calls, 1)
            finally:
                recovered.close()

    async def test_reconciling_submitted_item_does_not_resubmit(self) -> None:
        queue = self.make_queue()
        item_id = self.enqueue(queue)
        try:
            first = FakeClient()
            await queue.drain(first, item_id=item_id, wait_for_terminal=False)
            second = FakeClient()
            result = (await queue.drain(second, item_id=item_id))[0]
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(first.ingest_calls, 1)
            self.assertEqual(second.ingest_calls, 0)
            self.assertEqual(second.wait_calls, 1)
        finally:
            queue.close()

    async def test_timeout_is_retained_for_later_reconciliation(self) -> None:
        queue = self.make_queue()
        item_id = self.enqueue(queue)
        try:
            result = (await queue.drain(TimeoutClient(), item_id=item_id))[0]
            self.assertEqual(result["status"], "submitted")
            self.assertEqual(queue.counts()["submitted"], 1)
        finally:
            queue.close()

    def test_file_queue_survives_reopen(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.sqlite3"
            first = DurableIngestQueue(path)
            item_id = self.enqueue(first)
            first.close()
            second = DurableIngestQueue(path)
            try:
                self.assertEqual(second.recovery_items(), [item_id])
                self.assertEqual(second.counts()["pending"], 1)
            finally:
                second.close()

    def test_enqueue_reuses_same_idempotency_key_but_rejects_payload_drift(self) -> None:
        queue = self.make_queue()
        try:
            first = self.enqueue(queue)
            second = queue.enqueue(
                scope_name="scope-a",
                session_id="session-a",
                messages=[{"message_id": "m1", "role": "user", "content": "hello"}],
                consistency="read_your_writes",
                slow_policy="auto",
                idempotency_key="stable-key",
                metadata={"integration": "mcp"},
                agent_id=None,
            )
            self.assertEqual(first, second)
            with self.assertRaises(ValueError):
                queue.enqueue(
                    scope_name="scope-a",
                    session_id="session-a",
                    messages=[{"message_id": "m1", "role": "user", "content": "changed"}],
                    consistency="read_your_writes",
                    slow_policy="auto",
                    idempotency_key="stable-key",
                    metadata={"integration": "mcp"},
                    agent_id=None,
                )
        finally:
            queue.close()


if __name__ == "__main__":
    unittest.main()
