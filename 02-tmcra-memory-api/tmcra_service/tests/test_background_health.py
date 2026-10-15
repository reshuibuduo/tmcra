import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tmcra_service.control_db import ControlDB
from tmcra_service.jobs import JobStore, RUNNING
from tmcra_service.staff_runtime import StaffRuntimeStatus


class BackgroundHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ControlDB(Path(self.temp.name) / "control.sqlite3")
        self.db.initialize()
        self.jobs = JobStore(self.db)
        self.number = 0

    def job(self, state, tenant="a", kind="consolidate"):
        self.number += 1
        job = self.jobs.submit(tenant, f"test-key-{self.number}",
            {"job_type": kind, "scope_name": "same-scope"}, scope_name="same-scope")
        if state == "cancelled":
            self.jobs.cancel(job.job_id)
        else:
            self.jobs.transition(job.job_id, RUNNING)
            if state == "failed": self.jobs.fail(job.job_id, "private member content")
            if state == "succeeded": self.jobs.succeed(job.job_id, {})

    def snapshot(self):
        return StaffRuntimeStatus.background_snapshot(SimpleNamespace(database=self.db))

    def test_no_history_is_unobserved(self):
        self.assertEqual(self.snapshot()["status"], "unobserved")

    def test_only_success_clears_failure(self):
        self.job("failed")
        for state in ("running", "cancelled"):
            self.job(state)
            self.assertEqual(self.snapshot()["status"], "degraded")
        self.job("succeeded")
        self.assertEqual(self.snapshot()["status"], "healthy")

    def test_tenants_and_job_types_do_not_mask_failure(self):
        self.job("failed")
        self.job("succeeded", tenant="b")
        self.job("succeeded", kind="reindex")
        value = self.snapshot()
        self.assertEqual(value["failed_scope_count"], 1)
        self.assertEqual(value["succeeded_scope_count"], 1)
        self.assertNotIn("private", str(value))
        self.assertNotIn("same-scope", str(value))

    def test_deleted_scope_is_not_an_active_failure(self):
        from tmcra_service.commercial import CommercialControl
        self.job("failed")
        control = CommercialControl(self.db)
        control.mark_scope_deleting("a", "same-scope", "delete-test", reason="test")
        with self.db.transaction() as connection:
            connection.execute("UPDATE scope_lifecycle SET state='deleted' WHERE tenant_id='a'")
        self.assertEqual(self.snapshot()["status"], "unobserved")


if __name__ == "__main__":
    unittest.main()
