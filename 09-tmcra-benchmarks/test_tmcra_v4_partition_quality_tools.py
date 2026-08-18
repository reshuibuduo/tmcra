import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ops.approve_tmcra_v4_partition_diff import approve
from ops.export_tmcra_v4_active_slow_review import export_active


class PartitionQualityToolsTest(unittest.TestCase):
    def test_active_export_uses_only_latest_retrievable_head(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root)
            db = run / "writer" / "worker_000" / "native_memory.sqlite3"
            db.parent.mkdir(parents=True)
            con = sqlite3.connect(db)
            con.execute(
                "CREATE TABLE records(memory_id TEXT,value TEXT,state TEXT,metadata_json TEXT)"
            )
            base = {
                "memory_layer": "slow",
                "content_variant": "slow_memory_capsule",
                "capsule_id": "cap",
                "region_key": "profile",
                "claims": [
                    {
                        "canonical_slot": "user.pet.name",
                        "support": ["leaf-1"],
                        "text": "The pet is Max.",
                    }
                ],
            }
            con.execute(
                "INSERT INTO records VALUES(?,?,?,?)",
                ("slow.cap.r1", "old", "superseded", json.dumps({**base, "revision": 1, "status": "active"})),
            )
            con.execute(
                "INSERT INTO records VALUES(?,?,?,?)",
                ("slow.cap.r2", "new", "active", json.dumps({**base, "revision": 2, "status": "challenged"})),
            )
            con.commit()
            con.close()
            report = export_active(run, ["worker_000"])
            self.assertEqual(report["entry_count"], 1)
            self.assertEqual(report["entries"][0]["memory_id"], "slow.cap.r2")

    def test_review_must_exactly_disposition_every_raw_issue(self):
        raw = {
            "status": "failed",
            "blocking_issue_count": 1,
            "missing_support_ids": ["old-leaf"],
            "added_support_ids": [],
            "duplicate_support_ids": [],
            "slot_changes": [],
            "partition_changed_support_count": 0,
            "partition_changed_region_count": 0,
        }
        manual = {
            "status": "passed_with_expected_retirement",
            "blocking_issue_count": 0,
            "approved_missing_support_ids": ["old-leaf"],
            "approved_added_support_ids": [],
            "approved_duplicate_support_ids": [],
            "approved_slot_change_support_ids": [],
            "decision": "The historical contradicted leaf must not remain current.",
        }
        result = approve(raw, manual)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["approved_issue_count"], 1)
        with self.assertRaisesRegex(ValueError, "exactly disposition"):
            approve(raw, {**manual, "approved_missing_support_ids": []})


if __name__ == "__main__":
    unittest.main()
