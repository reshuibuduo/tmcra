import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import migrate_tmcra_v4_provenance_offsets as migration


def make_database(*, source_text="prefix decisive quote suffix", quote="decisive quote"):
    root = Path(tempfile.mkdtemp())
    db = root / "native_memory.sqlite3"
    scope = "scope.1"
    source_id = "source.1"
    source_metadata = {
        "content_variant": "source_message",
        "node_kind": "immutable_source_message",
        "raw_content": source_text,
        "source_span": source_text,
        "source_turn_text": source_text,
        "message_id": "message.1",
    }
    fast_metadata = {
        "memory_layer": "fast",
        "content_variant": "product_semantic_memory",
        "node_kind": "atomic_user_assertion",
        "atomic_evidence_leaf": True,
        "authority": "user_assertion",
        "provenance": {
            "source_record_id": source_id,
            "message_id": "message.1",
            "source_turn_index": 1,
            "evidence_quote": quote,
        },
    }
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE records(scope_id TEXT,memory_id TEXT,value TEXT,state TEXT,"
            "turn_index INTEGER,metadata_json TEXT,PRIMARY KEY(scope_id,memory_id))"
        )
        connection.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?)",
            (scope, source_id, source_text, "evidence", 1, json.dumps(source_metadata)),
        )
        connection.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?)",
            (scope, "fast.1", "claim", "active", 2, json.dumps(fast_metadata)),
        )
        connection.commit()
    return root, db


class ProvenanceOffsetMigrationTests(unittest.TestCase):
    def test_dry_run_does_not_mutate(self):
        _, db = make_database()
        before = db.read_bytes()
        report = migration.migrate_database(db, apply=False)
        self.assertEqual(report["added_offset_count"], 1)
        self.assertFalse(report["applied"])
        self.assertEqual(db.read_bytes(), before)

    def test_apply_is_transactional_and_idempotent(self):
        _, db = make_database()
        report = migration.migrate_database(db, apply=True)
        self.assertEqual(report["added_offset_count"], 1)
        self.assertTrue(report["applied"])
        with sqlite3.connect(db) as connection:
            metadata = json.loads(
                connection.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='fast.1'"
                ).fetchone()[0]
            )
            provenance = metadata["provenance"]
            self.assertEqual(provenance["source_char_start"], 7)
            self.assertEqual(provenance["source_char_end"], 21)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM v4_graph_repair_journal"
                ).fetchone()[0],
                1,
            )
            journal_report = json.loads(
                connection.execute(
                    "SELECT report_json FROM v4_graph_repair_journal"
                ).fetchone()[0]
            )
            self.assertEqual(journal_report["rollback_record_count"], 1)
            self.assertEqual(len(journal_report["rollback_records"]), 1)
            rollback_metadata = json.loads(
                journal_report["rollback_records"][0]["before_metadata_json"]
            )
            self.assertNotIn("source_char_start", rollback_metadata["provenance"])
        repeated = migration.migrate_database(db, apply=True)
        self.assertEqual(repeated["added_offset_count"], 0)
        self.assertFalse(repeated["applied"])

    def test_ambiguous_quote_fails_without_mutation(self):
        _, db = make_database(source_text="same and same", quote="same")
        before = db.read_bytes()
        with self.assertRaisesRegex(migration.MigrationError, "not unique exact"):
            migration.migrate_database(db, apply=True)
        self.assertEqual(db.read_bytes(), before)

    def test_committed_batch_span_disambiguates_repeated_quote(self):
        _, db = make_database(source_text="same and same", quote="same")
        with sqlite3.connect(db) as connection:
            metadata = json.loads(
                connection.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='fast.1'"
                ).fetchone()[0]
            )
            metadata["provenance"].update(
                {
                    "batch_id": "batch.1",
                    "evidence_span_id": "e2",
                }
            )
            connection.execute(
                "UPDATE records SET metadata_json=? WHERE memory_id='fast.1'",
                (json.dumps(metadata),),
            )
            connection.execute(
                "CREATE TABLE v4_batch_journal("
                "batch_id TEXT PRIMARY KEY,status TEXT,response_json TEXT)"
            )
            response = {
                "messages": [
                    {
                        "message_id": "message.1",
                        "v3": {
                            "assertions": [
                                {
                                    "evidence_span_id": "e2",
                                    "evidence_quote": "same",
                                    "evidence_char_start": 9,
                                    "evidence_char_end": 13,
                                }
                            ]
                        },
                    }
                ]
            }
            connection.execute(
                "INSERT INTO v4_batch_journal VALUES(?,?,?)",
                ("batch.1", "committed", json.dumps(response)),
            )
            connection.commit()
        report = migration.migrate_database(db, apply=True)
        self.assertEqual(report["journal_disambiguated_count"], 1)
        with sqlite3.connect(db) as connection:
            metadata = json.loads(
                connection.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='fast.1'"
                ).fetchone()[0]
            )
        self.assertEqual(metadata["provenance"]["source_char_start"], 9)
        self.assertEqual(metadata["provenance"]["source_char_end"], 13)

    def test_existing_wrong_offsets_fail_closed(self):
        _, db = make_database()
        with sqlite3.connect(db) as connection:
            metadata = json.loads(
                connection.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='fast.1'"
                ).fetchone()[0]
            )
            metadata["provenance"]["source_char_start"] = 0
            metadata["provenance"]["source_char_end"] = 4
            connection.execute(
                "UPDATE records SET metadata_json=? WHERE memory_id='fast.1'",
                (json.dumps(metadata),),
            )
            connection.commit()
        with self.assertRaisesRegex(migration.MigrationError, "do not match quote"):
            migration.migrate_database(db, apply=True)


if __name__ == "__main__":
    unittest.main()
