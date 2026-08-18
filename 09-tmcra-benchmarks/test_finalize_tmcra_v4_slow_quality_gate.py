import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ops.finalize_tmcra_v4_slow_quality_gate import (
    _json_object,
    _require_status,
    _validate_worker_database,
)
from run_tmcra_v4_build import BuildError


class FinalizeSlowQualityGateTests(unittest.TestCase):
    def test_worker_database_must_have_only_completed_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with closing(sqlite3.connect(database)) as con:
                con.execute("CREATE TABLE slow_graph_jobs(status TEXT)")
                con.execute("CREATE TABLE slow_graph_attempts(status TEXT)")
                con.execute("INSERT INTO slow_graph_jobs VALUES('completed')")
                con.commit()
            self.assertEqual(
                _validate_worker_database(database),
                {"completed_jobs": 1, "started_attempts": 0},
            )
            with closing(sqlite3.connect(database)) as con:
                con.execute("INSERT INTO slow_graph_jobs VALUES('pending')")
                con.commit()
            with self.assertRaisesRegex(BuildError, "unfinished"):
                _validate_worker_database(database)

    def test_status_and_json_artifact_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text(json.dumps({"status": "passed"}), encoding="utf-8")
            payload = _json_object(path)
            _require_status(payload, "passed", "artifact")
            with self.assertRaisesRegex(BuildError, "not complete"):
                _require_status(payload, "complete", "artifact")


if __name__ == "__main__":
    unittest.main()
