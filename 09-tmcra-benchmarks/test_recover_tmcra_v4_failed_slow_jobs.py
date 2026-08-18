import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ops.recover_tmcra_v4_failed_slow_jobs import _job_specs, _recovery_mode
from run_tmcra_v4_build import BuildError
from tmcra_v4_slow_graph import SLOW_PROMPT_MIGRATION_SOURCE_VERSION


class RecoverFailedSlowJobsTests(unittest.TestCase):
    def test_job_specs_are_explicit_and_unique(self) -> None:
        self.assertEqual(
            _job_specs(["worker_019=sgj_a", "worker_074=sgj_b"]),
            [("worker_019", "sgj_a"), ("worker_074", "sgj_b")],
        )
        with self.assertRaises(BuildError):
            _job_specs(["worker_019=sgj_a", "worker_019=sgj_b"])
        with self.assertRaises(BuildError):
            _job_specs(["worker_019:sgj_a"])

    def _failed_database(self, path: Path, metadata: dict, error: str) -> None:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "CREATE TABLE slow_graph_jobs(job_id TEXT,status TEXT,attempts INTEGER,"
                "claim_token TEXT,last_error TEXT)"
            )
            connection.execute(
                "CREATE TABLE slow_graph_attempts(job_id TEXT,status TEXT,error TEXT,"
                "call_metadata_json TEXT,created_at INTEGER,attempt_id TEXT)"
            )
            connection.execute("CREATE TABLE slow_graph_patches(job_id TEXT)")
            connection.execute(
                "INSERT INTO slow_graph_jobs VALUES(?,?,?,?,?)",
                ("sgj_a", "failed", 1, None, error),
            )
            connection.execute(
                "INSERT INTO slow_graph_attempts VALUES(?,?,?,?,?,?)",
                ("sgj_a", "failed", error, json.dumps(metadata), 1, "attempt_a"),
            )
            connection.commit()

    def test_recovery_mode_accepts_exhausted_bounded_pro_correction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "memory.sqlite3"
            self._failed_database(
                database,
                {
                    "physical_api_call": True,
                    "physical_api_calls": 2,
                    "route": "pro",
                    "status": "semantic_correction_rejected",
                    "http_status": 200,
                    "finish_reason": "stop",
                },
                "one semantic validation failure",
            )
            self.assertEqual(
                _recovery_mode(database, "sgj_a"),
                ("model_validation", "resume-failed-model-validation"),
            )

    def test_recovery_mode_accepts_only_the_reviewed_zero_call_bug(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "memory.sqlite3"
            self._failed_database(
                database,
                {
                    "physical_api_call": False,
                    "physical_api_calls": 0,
                    "route": "deterministic_noop",
                },
                'noop cannot consume uncited current durable Fast evidence: ["a"]',
            )
            self.assertEqual(
                _recovery_mode(database, "sgj_a"),
                (
                    "zero_call_promotion",
                    "resume-zero-call-promotion-failure",
                ),
            )

    def test_recovery_mode_accepts_one_reviewed_prompt_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "memory.sqlite3"
            error = (
                'atomic Fast evidence may belong to only one resulting claim: ["a"]'
            )
            metadata = {
                "physical_api_call": True,
                "physical_api_calls": 2,
                "route": "pro",
                "status": "semantic_correction_rejected",
                "http_status": 200,
                "finish_reason": "stop",
                "prompt_version": SLOW_PROMPT_MIGRATION_SOURCE_VERSION,
            }
            self._failed_database(database, metadata, error)
            with closing(sqlite3.connect(database)) as connection:
                first_metadata = {
                    **metadata,
                    "prompt_version": "tmcra-v4-slow-graph-2026-07-14.12",
                }
                connection.execute(
                    "UPDATE slow_graph_attempts SET call_metadata_json=? "
                    "WHERE attempt_id='attempt_a'",
                    (json.dumps(first_metadata),),
                )
                connection.execute(
                    "UPDATE slow_graph_jobs SET attempts=2 WHERE job_id='sgj_a'"
                )
                connection.execute(
                    "INSERT INTO slow_graph_attempts VALUES(?,?,?,?,?,?)",
                    ("sgj_a", "failed", error, json.dumps(metadata), 2, "attempt_b"),
                )
                connection.commit()
            self.assertEqual(
                _recovery_mode(database, "sgj_a"),
                (
                    "prompt_contract_migration",
                    "resume-failed-prompt-migration",
                ),
            )


if __name__ == "__main__":
    unittest.main()
