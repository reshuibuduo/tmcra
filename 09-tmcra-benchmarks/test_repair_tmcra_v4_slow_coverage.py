import json
import sqlite3
import tempfile
import unittest
from unittest import mock
from contextlib import closing
from pathlib import Path

from ops.repair_tmcra_v4_slow_coverage import (
    STATE_NAME,
    _attempt_summary,
    _attempts,
    _mark_build_incomplete,
    _requested_workers,
    _select_workers,
    _validate_resumable_jobs,
    _validated_repo,
)
from run_tmcra_v4_build import BuildError
import run_tmcra_v4_build as build


class RepairSlowCoverageTests(unittest.TestCase):
    def test_resume_existing_does_not_enqueue_new_prompt_version_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            worker = {
                "worker_dir": directory,
                "scope_id": "scope",
            }
            with (
                mock.patch.object(build, "_run") as run,
                mock.patch.object(build, "_failed_slow_jobs", return_value=[]),
            ):
                build._slow_worker_resume(
                    worker,
                    repo=Path("/repo"),
                    environment={},
                    enqueue=False,
                )
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(len(commands), 2)
            self.assertTrue(any("drain" in command for command in commands))
            self.assertTrue(any("audit" in command for command in commands))
            self.assertFalse(any("enqueue" in command for command in commands))

    def test_fresh_slow_copy_marker_can_start_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "FRESH_SLOW_COPY_COMPLETE.json").write_text(
                json.dumps(
                    {
                        "schema_version": "tmcra.v4.fresh-slow-run.1",
                        "status": "complete",
                    }
                ),
                encoding="utf-8",
            )
            _mark_build_incomplete(run_dir, ["worker_000"])
            state = json.loads((run_dir / STATE_NAME).read_text(encoding="utf-8"))
            self.assertEqual(
                state["fresh_slow_copy_marker"],
                "FRESH_SLOW_COPY_COMPLETE.json",
            )

    def test_repo_is_validated_before_worker_fanout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(BuildError, "invalid --repo for Slow graph schema"):
                _validated_repo(Path(directory))

    def test_requested_workers_are_exact_and_unique(self) -> None:
        self.assertEqual(
            _requested_workers(["worker_000,worker_014", "worker_019"]),
            ["worker_000", "worker_014", "worker_019"],
        )
        with self.assertRaises(BuildError):
            _requested_workers(["worker_000,worker_000"])
        with self.assertRaises(BuildError):
            _requested_workers(["qid_000"])

    def test_select_workers_preserves_requested_order(self) -> None:
        manifest = {
            "workers": [
                {"worker_dir": "/run/writer/worker_001", "worker_index": 1},
                {"worker_dir": "/run/writer/worker_000", "worker_index": 0},
            ]
        }
        selected = _select_workers(manifest, ["worker_000", "worker_001"])
        self.assertEqual([item["worker_index"] for item in selected], [0, 1])
        with self.assertRaises(BuildError):
            _select_workers(manifest, ["worker_999"])

    def test_attempt_summary_counts_only_supplied_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with closing(sqlite3.connect(database)) as con:
                con.execute(
                    "CREATE TABLE slow_graph_attempts("
                    "attempt_id TEXT,job_id TEXT,status TEXT,call_metadata_json TEXT,"
                    "error TEXT,created_at TEXT)"
                )
                con.execute(
                    "INSERT INTO slow_graph_attempts VALUES(?,?,?,?,?,?)",
                    (
                        "a1",
                        "j1",
                        "completed",
                        json.dumps(
                            {
                                "route": "flash",
                                "physical_api_calls": 1,
                                "usage": {
                                    "prompt_tokens": 10,
                                    "completion_tokens": 2,
                                    "total_tokens": 12,
                                },
                                "cost_audit": {"estimated_cost": 0.25},
                            }
                        ),
                        "",
                        "2026-01-01T00:00:00Z",
                    ),
                )
                con.commit()
            attempts = list(_attempts(database).values())
            summary = _attempt_summary(attempts)
            self.assertEqual(summary["physical_api_calls"], 1)
            self.assertEqual(summary["route_counts"], {"flash": 1})
            self.assertEqual(summary["usage"]["total_tokens"], 12)
            self.assertEqual(summary["estimated_cost_cny"], 0.25)

    def test_resume_existing_requires_clean_pending_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with closing(sqlite3.connect(database)) as con:
                con.execute(
                    "CREATE TABLE slow_graph_jobs("
                    "job_id TEXT,status TEXT,last_error TEXT,claim_token TEXT,"
                    "claim_owner TEXT,lease_expires_at INTEGER,created_at INTEGER)"
                )
                con.execute(
                    "CREATE TABLE slow_graph_attempts("
                    "attempt_id TEXT,job_id TEXT,status TEXT,created_at INTEGER)"
                )
                con.execute(
                    "INSERT INTO slow_graph_jobs VALUES(?,?,?,?,?,?,?)",
                    ("j1", "pending", "", None, None, None, 1),
                )
                con.commit()
            self.assertEqual(_validate_resumable_jobs(database)[0]["job_id"], "j1")

            with closing(sqlite3.connect(database)) as con:
                con.execute(
                    "UPDATE slow_graph_jobs SET claim_token='claim',claim_owner='owner',"
                    "lease_expires_at=1 WHERE job_id='j1'"
                )
                con.execute(
                    "INSERT INTO slow_graph_attempts VALUES(?,?,?,?)",
                    ("a1", "j1", "started", 1),
                )
                con.commit()
            with self.assertRaisesRegex(BuildError, "clean resumable boundary"):
                _validate_resumable_jobs(database)


if __name__ == "__main__":
    unittest.main()
