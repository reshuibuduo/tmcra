from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import migrate_tmcra_v4_fast_exact_evidence as migration


class FastExactEvidenceMigrationTests(unittest.TestCase):
    def make_database(
        self,
        *,
        stored_quote: str = "I prefer green.",
        committed_quote: str = "I  prefer green.",
    ) -> Path:
        directory = Path(tempfile.mkdtemp())
        database = directory / "native_memory.sqlite3"
        scope = "tmcra_v4:q1"
        source_id = "source.0"
        content = "I  prefer green."
        source_metadata = {
            "content_variant": "source_message",
            "node_kind": "immutable_source_message",
            "message_id": "s000_m000",
            "raw_content": content,
            "source_span": content,
            "source_turn_text": content,
        }
        fast_metadata = {
            "memory_layer": "fast",
            "content_variant": "product_semantic_memory",
            "node_kind": "atomic_user_assertion",
            "atomic_evidence_leaf": True,
            "authority": "user_assertion",
            "message_id": "s000_m000",
            "source_record_id": source_id,
            "llm_write_proposal_index": 0,
            "evidence_quote": stored_quote,
            "evidence_char_start": 0,
            "evidence_char_end": len(content),
            "raw_content": content,
            "source_span": content,
            "source_turn_text": content,
        }
        response = {
            "messages": [
                {
                    "message_id": "s000_m000",
                    "v3": {
                        "assertions": [
                            {
                                "evidence_quote": committed_quote,
                                "evidence_char_start": 0,
                                "evidence_char_end": len(content),
                            }
                        ]
                    },
                }
            ]
        }
        with sqlite3.connect(database) as connection:
            connection.executescript(
                """
                CREATE TABLE records(
                    scope_id TEXT,memory_id TEXT,value TEXT,state TEXT,
                    turn_index INTEGER,metadata_json TEXT,
                    PRIMARY KEY(scope_id,memory_id));
                CREATE TABLE v4_batch_journal(
                    batch_id TEXT,batch_index INTEGER,status TEXT,response_json TEXT);
                """
            )
            connection.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?)",
                (scope, source_id, content, "active", 0, json.dumps(source_metadata)),
            )
            connection.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?)",
                (
                    scope,
                    "leaf.0",
                    "The user prefers green.",
                    "active",
                    0,
                    json.dumps(fast_metadata),
                ),
            )
            connection.execute(
                "INSERT INTO v4_batch_journal VALUES(?,?,?,?)",
                ("batch.0", 0, "committed", json.dumps(response)),
            )
            connection.commit()
        return database

    def evidence_quote(self, database: Path) -> str:
        with sqlite3.connect(database) as connection:
            metadata = json.loads(
                connection.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='leaf.0'"
                ).fetchone()[0]
            )
        return metadata["evidence_quote"]

    def test_dry_run_apply_and_idempotence(self):
        database = self.make_database()
        dry = migration.migrate_database(database, apply=False)
        self.assertEqual(dry["changed_record_count"], 1)
        self.assertEqual(self.evidence_quote(database), "I prefer green.")

        applied = migration.migrate_database(database, apply=True)
        self.assertEqual(applied["changed_record_count"], 1)
        self.assertEqual(applied["physical_api_calls"], 0)
        self.assertEqual(self.evidence_quote(database), "I  prefer green.")
        with sqlite3.connect(database) as connection:
            journal = connection.execute(
                "SELECT report_json FROM v4_graph_repair_journal WHERE repair_id=?",
                (migration.MIGRATION_VERSION,),
            ).fetchone()
        self.assertIsNotNone(journal)
        self.assertEqual(len(json.loads(journal[0])["rollback_records"]), 1)

        resumed = migration.migrate_database(database, apply=True)
        self.assertEqual(resumed["changed_record_count"], 0)
        self.assertEqual(resumed["already_exact_record_count"], 1)

    def test_unproven_semantic_change_fails_closed(self):
        database = self.make_database(stored_quote="I dislike green.")
        with self.assertRaisesRegex(
            migration.MigrationError,
            "not a proven transport normalization",
        ):
            migration.migrate_database(database, apply=True)

    def test_committed_assertion_must_match_source_exactly(self):
        database = self.make_database(committed_quote="I prefer green.")
        with self.assertRaisesRegex(
            migration.MigrationError,
            "committed assertion does not prove",
        ):
            migration.migrate_database(database, apply=True)


if __name__ == "__main__":
    unittest.main()
