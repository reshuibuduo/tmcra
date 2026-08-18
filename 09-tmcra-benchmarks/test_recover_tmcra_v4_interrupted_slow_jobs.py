import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from ops.recover_tmcra_v4_interrupted_slow_jobs import (
    INTERRUPTION_ERROR,
    _claim_owner_pid,
    _load_stale_lock,
    _snapshot_database,
    _verify_reopened,
)
from run_tmcra_v4_build import BuildError
from tmcra_v4_slow_graph import SLOW_PROMPT_VERSION


class RecoverInterruptedSlowJobsTests(unittest.TestCase):
    def test_verify_reopened_ignores_unrelated_reviewed_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with closing(sqlite3.connect(database)) as con:
                con.execute(
                    "CREATE TABLE slow_graph_jobs("
                    "job_id TEXT,status TEXT,attempts INTEGER,last_error TEXT,"
                    "claim_token TEXT,claim_owner TEXT,lease_expires_at INTEGER)"
                )
                con.execute(
                    "CREATE TABLE slow_graph_attempts("
                    "attempt_id TEXT,job_id TEXT,status TEXT,error TEXT,completed_at INTEGER)"
                )
                con.execute(
                    "INSERT INTO slow_graph_jobs VALUES(?,?,?,?,?,?,?)",
                    ("reopened", "pending", 1, "", None, None, None),
                )
                con.execute(
                    "INSERT INTO slow_graph_jobs VALUES(?,?,?,?,?,?,?)",
                    ("unrelated", "failed", 1, "review required", None, None, None),
                )
                con.execute(
                    "INSERT INTO slow_graph_attempts VALUES(?,?,?,?,?)",
                    ("attempt", "reopened", "expired", INTERRUPTION_ERROR, 1),
                )
                con.commit()
            _verify_reopened(
                database,
                [
                    {
                        "job_id": "reopened",
                        "attempt_id": "attempt",
                        "job_attempts_before": 0,
                    }
                ],
            )

    def test_snapshot_accepts_only_expired_empty_started_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with closing(sqlite3.connect(database)) as con:
                con.execute(
                    "CREATE TABLE slow_graph_jobs("
                    "job_id TEXT,scope_id TEXT,region_key TEXT,status TEXT,attempts INTEGER,"
                    "last_error TEXT,claim_token TEXT,claim_owner TEXT,"
                    "lease_expires_at INTEGER,created_at INTEGER)"
                )
                con.execute(
                    "CREATE TABLE slow_graph_attempts("
                    "attempt_id TEXT,job_id TEXT,status TEXT,call_metadata_json TEXT,"
                    "error TEXT,created_at INTEGER,completed_at INTEGER,"
                    "claim_token TEXT,claim_owner TEXT)"
                )
                con.execute(
                    "INSERT INTO slow_graph_jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        "sgj_1",
                        "scope",
                        "business",
                        "pending",
                        0,
                        "",
                        "claim",
                        "pid:999999:owner",
                        int(time.time()) - 10,
                        1,
                    ),
                )
                con.execute(
                    "INSERT INTO slow_graph_attempts VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        "sga_1",
                        "sgj_1",
                        "started",
                        "{}",
                        "",
                        1,
                        None,
                        "claim",
                        "pid:999999:owner",
                    ),
                )
                con.commit()
            with mock.patch(
                "ops.recover_tmcra_v4_interrupted_slow_jobs._pid_is_alive",
                return_value=False,
            ):
                snapshot = _snapshot_database(database)
            self.assertEqual(snapshot["unfinished_jobs"], 1)
            self.assertEqual(
                snapshot["interrupted_attempts"][0]["attempt_id"], "sga_1"
            )

    def test_snapshot_rejects_durable_attempt_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with closing(sqlite3.connect(database)) as con:
                con.execute(
                    "CREATE TABLE slow_graph_jobs("
                    "job_id TEXT,scope_id TEXT,region_key TEXT,status TEXT,attempts INTEGER,"
                    "last_error TEXT,claim_token TEXT,claim_owner TEXT,"
                    "lease_expires_at INTEGER,created_at INTEGER)"
                )
                con.execute(
                    "CREATE TABLE slow_graph_attempts("
                    "attempt_id TEXT,job_id TEXT,status TEXT,call_metadata_json TEXT,"
                    "error TEXT,created_at INTEGER,completed_at INTEGER,"
                    "claim_token TEXT,claim_owner TEXT)"
                )
                con.execute(
                    "INSERT INTO slow_graph_jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        "sgj_1", "scope", "business", "pending", 0, "", "claim",
                        "pid:999999:owner", int(time.time()) - 10, 1,
                    ),
                )
                con.execute(
                    "INSERT INTO slow_graph_attempts VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        "sga_1", "sgj_1", "started", '{"route":"pro"}', "", 1,
                        None, "claim", "pid:999999:owner",
                    ),
                )
                con.commit()
            with mock.patch(
                "ops.recover_tmcra_v4_interrupted_slow_jobs._pid_is_alive",
                return_value=False,
            ):
                with self.assertRaisesRegex(BuildError, "durable outcome"):
                    _snapshot_database(database)

    def test_stale_lock_must_match_workers_and_dead_pid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / "SLOW_REPAIR_LOCK").write_text(
                json.dumps(
                    {
                        "pid": 999999,
                        "selected_workers": ["worker_000"],
                        "prompt_version": SLOW_PROMPT_VERSION,
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch(
                "ops.recover_tmcra_v4_interrupted_slow_jobs._pid_is_alive",
                return_value=False,
            ):
                self.assertEqual(
                    _load_stale_lock(run_dir, ["worker_000"])["pid"], 999999
                )
                with self.assertRaisesRegex(BuildError, "exactly match"):
                    _load_stale_lock(run_dir, ["worker_001"])

    def test_claim_owner_must_encode_pid(self) -> None:
        self.assertEqual(_claim_owner_pid("pid:123:token"), 123)
        with self.assertRaises(BuildError):
            _claim_owner_pid("worker:123")


if __name__ == "__main__":
    unittest.main()
