import threading
import unittest
from unittest import mock

from tmcra_service.runtime import ServiceWorker


class _Worker:
    """Just what ServiceWorker._claim_index_for_stage uses."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self._stop = threading.Event()

    def _claim_index(self, tenant_id, scope_name, job_id):
        self.calls += 1
        return self.results.pop(0) if self.results else False


class ClaimIndexForStageTest(unittest.TestCase):
    def claim(self, worker, **kwargs):
        return ServiceWorker._claim_index_for_stage(worker, "tenant", "scope", "job", **kwargs)

    def test_first_claim_wins_without_waiting(self):
        worker = _Worker([True])
        self.assertTrue(self.claim(worker))
        self.assertEqual(worker.calls, 1)

    def test_transient_refusals_are_retried(self):
        worker = _Worker([False, False, True])
        self.assertTrue(self.claim(worker, wait_seconds=5.0))
        self.assertEqual(worker.calls, 3)

    def test_gives_up_after_the_wait(self):
        worker = _Worker([])
        self.assertFalse(self.claim(worker, wait_seconds=0.3))
        self.assertGreater(worker.calls, 1)

    def test_stops_with_the_service(self):
        worker = _Worker([])
        worker._stop.set()
        self.assertFalse(self.claim(worker, wait_seconds=60.0))
        self.assertEqual(worker.calls, 1)


if __name__ == "__main__":
    unittest.main()
