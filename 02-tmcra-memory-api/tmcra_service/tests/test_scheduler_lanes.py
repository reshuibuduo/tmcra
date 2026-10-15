import threading
import unittest

from tmcra_service.runtime import ServiceWorker, _retry_claim


class _Worker:
    """Just what ServiceWorker._schedule_due_jobs uses."""

    def __init__(self, evolution_due, index_due):
        self.database = object()
        self.commercial = None
        self.scheduled = []
        self._due = {True: evolution_due, False: index_due}

    def _due_scopes(self, *, evolution, now):
        return self._due[evolution]

    def _schedule_auto_job(self, row, job_type):
        self.scheduled.append((row["scope_name"], job_type))
        return True


def row(scope, evolution_job=None, index_job=None):
    return {"tenant_id": "t", "scope_name": scope,
            "active_evolution_job_id": evolution_job, "active_index_job_id": index_job}


class ScheduleDueJobsTest(unittest.TestCase):
    def run_tick(self, evolution_due, index_due):
        worker = _Worker(evolution_due, index_due)
        ServiceWorker._schedule_due_jobs(worker, now=0.0)
        return worker.scheduled

    def test_no_reindex_next_to_a_new_consolidation(self):
        self.assertEqual(self.run_tick([row("a")], [row("a")]), [("a", "consolidate")])

    def test_no_reindex_while_a_consolidation_runs(self):
        self.assertEqual(self.run_tick([], [row("a", evolution_job="j1")]), [])

    def test_reindex_when_no_consolidation(self):
        self.assertEqual(self.run_tick([row("b")], [row("a"), row("b")]),
                         [("b", "consolidate"), ("a", "reindex")])


class RetryClaimTest(unittest.TestCase):
    def test_retries_until_the_claim_passes(self):
        answers = iter([False, False, True])
        self.assertTrue(_retry_claim(lambda: next(answers), threading.Event(), wait_seconds=5))

    def test_stops_with_the_service(self):
        stop = threading.Event()
        stop.set()
        self.assertFalse(_retry_claim(lambda: False, stop, wait_seconds=60))


class _Job:
    def __init__(self, job_id, state):
        self.job_id, self.state = job_id, state


class _Jobs:
    def __init__(self, states):
        self.states = dict(states)  # key -> state of an existing job
        self.submitted = []

    def submit(self, tenant_id, key, payload, **kwargs):
        self.submitted.append(key)
        return _Job(key, self.states.get(key, "pending"))

    def claim_scope_evolution_job(self, tenant_id, scope_name, job_id):
        return True

    claim_scope_index_job = claim_scope_evolution_job


class _AutoWorker:
    def __init__(self, states):
        self.jobs = _Jobs(states)
        self.settings = object()


ROW = {"tenant_id": "t", "scope_name": "s", "source_event_seq": 80, "conflict_generation": 0}
BASE = "auto:consolidate:s:80:0"


class ScheduleAutoJobTest(unittest.TestCase):
    def test_a_cancelled_attempt_does_not_block_the_watermark(self):
        worker = _AutoWorker({BASE: "cancelled"})
        self.assertTrue(ServiceWorker._schedule_auto_job(worker, ROW, "consolidate"))
        self.assertEqual(worker.jobs.submitted[0], BASE)
        self.assertTrue(worker.jobs.submitted[1].startswith(BASE + ":retry-"))

    def test_a_failed_job_still_needs_an_explicit_retry(self):
        worker = _AutoWorker({BASE: "failed"})
        self.assertFalse(ServiceWorker._schedule_auto_job(worker, ROW, "consolidate"))
        self.assertEqual(worker.jobs.submitted, [BASE])


if __name__ == "__main__":
    unittest.main()
