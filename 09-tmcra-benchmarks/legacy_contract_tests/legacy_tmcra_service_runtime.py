from __future__ import annotations

import tempfile
import threading
import unittest
from queue import Empty, Queue
from pathlib import Path
from types import SimpleNamespace

from tmcra_service.control_db import ControlDB
from tmcra_service.jobs import FAILED, PENDING, JobStateError, JobStore
from tmcra_service.runtime import ServiceWorker


class StubStorage:
    def __init__(self, committed: bool) -> None:
        self.committed = committed

    def can_resume_ingest(self, **kwargs: object) -> bool:
        return self.committed


class RecordingStorage:
    def __init__(self, *, active: bool = False, fail_index: bool = False) -> None:
        self.active = active
        self.fail_index = fail_index
        self.builds: list[str] = []
        self.slows: list[str] = []

    def can_resume_ingest(self, **kwargs: object) -> bool:
        return False

    def ingest(self, **kwargs: object) -> dict[str, object]:
        return {"new_message_count": 1, "replayed_message_count": 0}

    def active_snapshot(self, tenant_id: str, scope_name: str) -> dict[str, object]:
        if not self.active:
            raise RuntimeError("no active generation")
        return {"generation_id": "generation-1"}

    def build_index(self, **kwargs: object) -> dict[str, object]:
        if self.fail_index:
            raise RuntimeError("index failed")
        self.builds.append(str(kwargs["job_id"]))
        return {"active_index": {"generation_id": kwargs["job_id"]}}

    def consolidate_slow(self, **kwargs: object) -> dict[str, object]:
        self.slows.append(str(kwargs["job_id"]))
        return {"slow": "committed"}


class MetricDueJobs:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[dict[str, object]] = []

    def list_due_evolution_scopes(self, **kwargs: object) -> list[dict[str, object]]:
        self.calls.append(kwargs)
        due: list[dict[str, object]] = []
        for row in self.rows:
            raw_delta = int(row["source_raw_token_estimate"]) - int(
                row["promoted_raw_token_estimate"]
            )
            turn_delta = int(row["source_user_turns"]) - int(
                row["promoted_user_turns"]
            )
            batch_due = raw_delta >= int(kwargs["dirty_token_threshold"]) or turn_delta >= int(
                kwargs["dirty_user_turn_threshold"]
            )
            age_due = (
                float(row["age_seconds"]) >= float(kwargs["max_age_seconds"])
                and (
                    raw_delta >= int(kwargs["min_token_threshold"])
                    or turn_delta >= int(kwargs["min_user_turn_threshold"])
                )
            )
            if batch_due or age_due:
                due.append(row)
        return due


class ConflictingJobStore:
    lease_seconds = 1.0

    def __init__(self) -> None:
        self.worker: ServiceWorker | None = None
        self.claimed = False

    def expired_running(self) -> list[object]:
        return []

    def claim_next(self, worker_id: str) -> object | None:
        if self.claimed:
            return None
        self.claimed = True
        return SimpleNamespace(job_id="lost-job")

    def heartbeat(self, job_id: str, worker_id: str) -> bool:
        return False

    def fail(self, job_id: str, error: str, *, worker_id: str) -> None:
        assert self.worker is not None
        self.worker._stop.set()
        raise JobStateError("lease ownership changed")


class FailingExecutionWorker(ServiceWorker):
    def _execute(self, job: object) -> object:
        raise RuntimeError("operation failed after lease ownership changed")


class BlockingExecutionWorker(ServiceWorker):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.started: Queue[tuple[str, str]] = Queue()
        self.release = threading.Event()
        self._executing: set[tuple[str, str]] = set()
        self._executing_lock = threading.Lock()
        self.overlap = False

    def _execute(self, job: object) -> object:
        scope_key = self._scope_key(job)  # type: ignore[arg-type]
        with self._executing_lock:
            if scope_key in self._executing:
                self.overlap = True
            self._executing.add(scope_key)
        self.started.put(scope_key)
        try:
            if not self.release.wait(5):
                raise TimeoutError("test execution was not released")
            return {"scope": scope_key[1]}
        finally:
            with self._executing_lock:
                self._executing.remove(scope_key)


class RuntimeRecoveryTests(unittest.TestCase):
    def _expired_ingest(self, root: Path) -> tuple[ControlDB, JobStore, str]:
        database = ControlDB(root / "control.sqlite3")
        jobs = JobStore(database, lease_seconds=1)
        job = jobs.submit(
            "tenant-a",
            "request-1",
            {
                "job_type": "ingest",
                "scope_name": "default",
                "session_id": "session-a",
                "messages": [],
            },
        )
        jobs.claim(job.job_id, "dead-worker")
        jobs.heartbeat(job.job_id, "dead-worker", now=0)
        return database, jobs, job.job_id

    def test_expired_committed_ingest_is_requeued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, jobs, job_id = self._expired_ingest(Path(directory))
            worker = ServiceWorker(
                settings=None,  # type: ignore[arg-type]
                database=database,
                jobs=jobs,
                storage=StubStorage(True),  # type: ignore[arg-type]
            )
            self.assertEqual(worker.recover_abandoned_jobs(), 1)
            self.assertEqual(jobs.get(job_id).state, PENDING)  # type: ignore[union-attr]

    def test_expired_uncommitted_ingest_requires_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, jobs, job_id = self._expired_ingest(Path(directory))
            worker = ServiceWorker(
                settings=None,  # type: ignore[arg-type]
                database=database,
                jobs=jobs,
                storage=StubStorage(False),  # type: ignore[arg-type]
            )
            self.assertEqual(worker.recover_abandoned_jobs(), 1)
            recovered = jobs.get(job_id)
            self.assertEqual(recovered.state, FAILED)  # type: ignore[union-attr]
            self.assertEqual(
                recovered.error,  # type: ignore[union-attr]
                "process_lost_requires_explicit_artifact_audit",
            )

    def test_worker_survives_terminal_state_race(self) -> None:
        jobs = ConflictingJobStore()
        worker = FailingExecutionWorker(
            settings=None,  # type: ignore[arg-type]
            database=None,  # type: ignore[arg-type]
            jobs=jobs,  # type: ignore[arg-type]
            storage=StubStorage(False),  # type: ignore[arg-type]
        )
        jobs.worker = worker
        worker._run()
        self.assertIsNone(worker.active_job_id)

    def test_dispatcher_parallelizes_scopes_behind_global_fifo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = ControlDB(Path(directory) / "control.sqlite3")
            jobs = JobStore(database, lease_seconds=5)
            jobs.submit("tenant-a", "request-a", {"job_type": "reindex", "scope_name": "a"})
            jobs.submit("tenant-a", "request-b", {"job_type": "reindex", "scope_name": "b"})
            worker = BlockingExecutionWorker(
                settings=SimpleNamespace(worker_concurrency=2),
                database=database,
                jobs=jobs,
                storage=StubStorage(False),
                poll_seconds=0.01,
            )
            worker.start()
            started = {worker.started.get(timeout=2), worker.started.get(timeout=2)}
            self.assertEqual(started, {("tenant-a", "a"), ("tenant-a", "b")})
            self.assertFalse(worker.overlap)
            worker.release.set()
            worker.stop(timeout=5)
            self.assertFalse(worker.status().alive)

    def test_dispatcher_serializes_same_scope_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = ControlDB(Path(directory) / "control.sqlite3")
            jobs = JobStore(database, lease_seconds=5)
            jobs.submit("tenant-a", "request-a", {"job_type": "reindex", "scope_name": "a"})
            jobs.submit("tenant-a", "request-a2", {"job_type": "reindex", "scope_name": "a"})
            worker = BlockingExecutionWorker(
                settings=SimpleNamespace(worker_concurrency=2),
                database=database,
                jobs=jobs,
                storage=StubStorage(False),
                poll_seconds=0.01,
            )
            worker.start()
            self.assertEqual(worker.started.get(timeout=2), ("tenant-a", "a"))
            with self.assertRaises(Empty):
                worker.started.get(timeout=0.1)
            worker.release.set()
            self.assertEqual(worker.started.get(timeout=2), ("tenant-a", "a"))
            worker.stop(timeout=5)
            self.assertFalse(worker.overlap)


class AutomaticEvolutionRuntimeTests(unittest.TestCase):
    def settings(self) -> SimpleNamespace:
        return SimpleNamespace(
            worker_concurrency=2,
            tenant_queue_limit=100,
            global_queue_limit=1000,
            slow_dirty_token_threshold=32_000,
            slow_dirty_user_turn_threshold=64,
            slow_max_age_seconds=86_400.0,
            slow_min_token_threshold=4_000,
            slow_min_user_turn_threshold=8,
            slow_min_interval_seconds=1_800.0,
            index_dirty_threshold=16,
            index_max_age_seconds=2.0,
            scheduler_interval_seconds=1.0,
        )

    @staticmethod
    def message(message_id: str = "m1", *, role: str = "user") -> dict[str, object]:
        return {
            "message_id": message_id,
            "role": role,
            "content": "hello world",
            "timestamp": "2026-07-15T00:00:00Z",
        }

    def ingest_job(
        self,
        jobs: JobStore,
        *,
        key: str,
        consistency: str = "eventual",
        slow_policy: str = "auto",
    ) -> object:
        return jobs.submit(
            "tenant-a",
            key,
            {
                "job_type": "ingest",
                "scope_name": "alpha",
                "session_id": "session-a",
                "messages": [self.message(key)],
                "consistency": consistency,
                "slow_policy": slow_policy,
            },
            scope_name="alpha",
        )

    def worker(
        self,
        root: Path,
        storage: RecordingStorage,
    ) -> tuple[ServiceWorker, ControlDB, JobStore]:
        database = ControlDB(root / "control.sqlite3")
        jobs = JobStore(database)
        return (
            ServiceWorker(
                settings=self.settings(),
                database=database,
                jobs=jobs,
                storage=storage,  # type: ignore[arg-type]
            ),
            database,
            jobs,
        )

    def test_first_index_and_read_your_writes_are_synchronous(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = RecordingStorage(active=False)
            worker, database, jobs = self.worker(Path(directory), storage)
            first = self.ingest_job(jobs, key="first-0001")
            worker._execute(first)  # type: ignore[arg-type]
            self.assertEqual(len(storage.builds), 1)
            state = database.get_scope_evolution_state("tenant-a", "alpha")
            self.assertEqual(state["source_event_seq"], 1)  # type: ignore[index]
            self.assertEqual(state["indexed_event_seq"], 1)  # type: ignore[index]

            storage.active = True
            ryw = self.ingest_job(
                jobs,
                key="ryw-0001",
                consistency="read_your_writes",
            )
            worker._execute(ryw)  # type: ignore[arg-type]
            self.assertEqual(len(storage.builds), 2)
            state = database.get_scope_evolution_state("tenant-a", "alpha")
            self.assertEqual(state["source_event_seq"], 2)  # type: ignore[index]
            self.assertEqual(state["indexed_event_seq"], 2)  # type: ignore[index]

    def test_eventual_ingest_keeps_active_generation_and_coalesces_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = RecordingStorage(active=True)
            worker, database, jobs = self.worker(Path(directory), storage)
            database.record_committed_source_events("tenant-a", "alpha", 1)
            first = self.ingest_job(jobs, key="eventual-0001")
            result = worker._execute(first)  # type: ignore[arg-type]
            self.assertIsNone(result["index"])
            self.assertEqual(storage.builds, [])
            self.assertEqual(
                database.get_scope_evolution_state("tenant-a", "alpha")["indexed_event_seq"],  # type: ignore[index]
                0,
            )

            dirty_since = float(
                database.get_scope_evolution_state("tenant-a", "alpha")["index_dirty_since_at"]  # type: ignore[index]
            )
            self.assertEqual(worker._schedule_due_jobs(now=dirty_since + 2), 1)
            self.assertEqual(worker._schedule_due_jobs(now=dirty_since + 2), 0)
            with database.transaction(immediate=False) as connection:
                job_ids = [row["job_id"] for row in connection.execute("SELECT job_id FROM jobs")]
            pending = [
                job
                for job in (jobs.get(job_id) for job_id in job_ids)
                if job is not None and job.payload.get("auto")
            ]
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].payload["job_type"], "reindex")

    def test_crash_replay_does_not_double_count_ingest_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = RecordingStorage(active=True)
            worker, database, jobs = self.worker(Path(directory), storage)
            job = self.ingest_job(jobs, key="replay-0001")
            worker._execute(job)  # type: ignore[arg-type]
            worker._execute(job)  # type: ignore[arg-type]
            state = database.get_scope_evolution_state("tenant-a", "alpha")
            self.assertEqual(state["source_event_seq"], 1)  # type: ignore[index]
            self.assertEqual(state["source_user_turns"], 1)  # type: ignore[index]
            self.assertEqual(state["source_raw_token_estimate"], 3)  # type: ignore[index]
            stages = jobs.list_stages(job_id=job.job_id)  # type: ignore[attr-defined]
            self.assertEqual([(stage.stage_name, stage.attempt) for stage in stages], [("writer", 1)])

    def test_slow_policy_has_no_conflict_only_trigger_and_age_minimum(self) -> None:
        rows = [
            {
                "tenant_id": "tenant-a",
                "scope_name": "conflict-only",
                "source_raw_token_estimate": 100,
                "promoted_raw_token_estimate": 0,
                "source_user_turns": 1,
                "promoted_user_turns": 0,
                "age_seconds": 200_000,
                "conflict_generation": 1,
            },
            {
                "tenant_id": "tenant-a",
                "scope_name": "token-batch",
                "source_raw_token_estimate": 32_000,
                "promoted_raw_token_estimate": 0,
                "source_user_turns": 1,
                "promoted_user_turns": 0,
                "age_seconds": 1,
            },
            {
                "tenant_id": "tenant-a",
                "scope_name": "turn-batch",
                "source_raw_token_estimate": 100,
                "promoted_raw_token_estimate": 0,
                "source_user_turns": 64,
                "promoted_user_turns": 0,
                "age_seconds": 1,
            },
            {
                "tenant_id": "tenant-a",
                "scope_name": "age-too-small",
                "source_raw_token_estimate": 3_999,
                "promoted_raw_token_estimate": 0,
                "source_user_turns": 7,
                "promoted_user_turns": 0,
                "age_seconds": 90_000,
            },
            {
                "tenant_id": "tenant-a",
                "scope_name": "age-minimum-batch",
                "source_raw_token_estimate": 4_000,
                "promoted_raw_token_estimate": 0,
                "source_user_turns": 1,
                "promoted_user_turns": 0,
                "age_seconds": 86_400,
            },
        ]
        jobs = MetricDueJobs(rows)
        worker = ServiceWorker(
            settings=self.settings(),
            database=object(),  # type: ignore[arg-type]
            jobs=jobs,  # type: ignore[arg-type]
            storage=StubStorage(False),  # type: ignore[arg-type]
        )
        due = worker._due_scopes(evolution=True, now=100)
        self.assertEqual(
            [row["scope_name"] for row in due],
            ["token-batch", "turn-batch", "age-minimum-batch"],
        )
        self.assertEqual(jobs.calls[0]["include_conflicts"], False)
        self.assertEqual(jobs.calls[0]["min_success_interval_seconds"], 1_800.0)

    def test_consolidation_advances_both_watermarks_only_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = RecordingStorage(active=True)
            worker, database, jobs = self.worker(Path(directory), storage)
            database.record_committed_source_events(
                "tenant-a", "alpha", 3, conflict_generation=2
            )
            job = jobs.submit(
                "tenant-a",
                "force-consolidate-1",
                {"job_type": "consolidate", "scope_name": "alpha"},
                scope_name="alpha",
            )
            result = worker._execute(job)
            self.assertEqual(result["watermarks"]["promoted_event_seq"], 3)
            state = database.get_scope_evolution_state("tenant-a", "alpha")
            self.assertEqual(state["promoted_conflict_generation"], 2)  # type: ignore[index]
            self.assertIsNone(state["active_evolution_job_id"])  # type: ignore[index]
            self.assertIsNone(state["active_index_job_id"])  # type: ignore[index]

            failing_storage = RecordingStorage(active=True, fail_index=True)
            failing_worker, failing_db, failing_jobs = self.worker(
                Path(directory) / "failure", failing_storage
            )
            failing_db.record_committed_source_events("tenant-a", "alpha", 3)
            failed = failing_jobs.submit(
                "tenant-a",
                "force-consolidate-2",
                {"job_type": "consolidate", "scope_name": "alpha"},
                scope_name="alpha",
            )
            with self.assertRaises(RuntimeError):
                failing_worker._execute(failed)
            state = failing_db.get_scope_evolution_state("tenant-a", "alpha")
            self.assertEqual(state["promoted_event_seq"], 0)  # type: ignore[index]
            self.assertIsNone(state["active_evolution_job_id"])  # type: ignore[index]
            self.assertIsNone(state["active_index_job_id"])  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
