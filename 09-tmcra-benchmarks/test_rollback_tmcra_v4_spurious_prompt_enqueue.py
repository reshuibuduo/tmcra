import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ops.rollback_tmcra_v4_spurious_prompt_enqueue import (
    _inspect_database,
    _rollback_database,
)
from run_tmcra_v4_build import BuildError
from tmcra_v4_cost_report import SLOW_INTERRUPTION_ERROR, sqlite_call_metadata


PROMPT = "tmcra-v4-slow-graph-test"


class RollbackSpuriousPromptEnqueueTests(unittest.TestCase):
    def _database(
        self,
        path: Path,
        *,
        physical_calls: int = 0,
        interrupted_unknown: bool = False,
    ) -> None:
        with closing(sqlite3.connect(path)) as con:
            con.execute(
                "CREATE TABLE slow_graph_jobs("
                "job_id TEXT,metadata_json TEXT,status TEXT,claim_token TEXT,"
                "claim_owner TEXT,lease_expires_at INTEGER)"
            )
            con.execute(
                "CREATE TABLE slow_graph_attempts("
                "attempt_id TEXT,job_id TEXT,scope_id TEXT,status TEXT,"
                "call_metadata_json TEXT,error TEXT,created_at INTEGER,"
                "completed_at INTEGER,claim_token TEXT,claim_owner TEXT)"
            )
            con.execute(
                "CREATE TABLE slow_graph_patches("
                "patch_id TEXT,job_id TEXT,patch_json TEXT)"
            )
            con.execute(
                "CREATE TABLE slow_graph_patch_operations("
                "patch_id TEXT,action TEXT)"
            )
            con.execute("CREATE TABLE slow_graph_provenance(patch_id TEXT)")
            con.execute(
                "CREATE TABLE slow_graph_batches(batch_id TEXT,job_ids_json TEXT)"
            )
            con.execute(
                "INSERT INTO slow_graph_jobs VALUES(?,?,?,?,?,?)",
                (
                    "j1",
                    json.dumps({"model_config": {"prompt_version": PROMPT}}),
                    "completed",
                    None,
                    None,
                    None,
                ),
            )
            call_metadata = (
                {}
                if interrupted_unknown
                else {
                    "physical_api_call": physical_calls > 0,
                    "physical_api_calls": physical_calls,
                }
            )
            con.execute(
                "INSERT INTO slow_graph_attempts VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "a1",
                    "j1",
                    "scope",
                    "expired" if interrupted_unknown else "completed",
                    json.dumps(call_metadata),
                    SLOW_INTERRUPTION_ERROR if interrupted_unknown else "",
                    1,
                    2,
                    "claim",
                    "owner",
                ),
            )
            con.execute(
                "INSERT INTO slow_graph_patches VALUES(?,?,?)",
                ("p1", "j1", json.dumps({"operations": [{"action": "noop"}]})),
            )
            con.execute(
                "INSERT INTO slow_graph_patch_operations VALUES(?,?)",
                ("p1", "noop"),
            )
            con.execute(
                "INSERT INTO slow_graph_batches VALUES(?,?)",
                ("b1", json.dumps(["j1"])),
            )
            con.commit()

    def test_verified_zero_call_noop_journal_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            self._database(database)
            inspection = _inspect_database(database, PROMPT)
            self.assertEqual(inspection["target_job_count"], 1)
            _rollback_database(database, inspection)
            self.assertEqual(_inspect_database(database, PROMPT)["target_job_count"], 0)
            with closing(sqlite3.connect(database)) as con:
                for table in (
                    "slow_graph_jobs",
                    "slow_graph_attempts",
                    "slow_graph_patches",
                    "slow_graph_patch_operations",
                    "slow_graph_batches",
                ):
                    self.assertEqual(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_physical_call_prevents_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            self._database(database, physical_calls=1)
            with self.assertRaisesRegex(BuildError, "physical API call"):
                _inspect_database(database, PROMPT)

    def test_interrupted_unknown_is_archived_and_still_counted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            self._database(database, interrupted_unknown=True)
            inspection = _inspect_database(database, PROMPT)
            self.assertEqual(inspection["unknown_attempt_ids"], ["a1"])
            _rollback_database(database, inspection)
            calls = sqlite_call_metadata(database)
            unknown = [
                metadata
                for _, metadata in calls
                if isinstance(metadata, dict)
                and metadata.get("external_call_outcome_unknown") is True
            ]
            self.assertEqual(len(unknown), 1)


if __name__ == "__main__":
    unittest.main()
