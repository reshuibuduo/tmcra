import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import migrate_tmcra_v4_keep_parallel as migration


class KeepParallelMigrationTests(unittest.TestCase):
    def make_worker(self, *, downstream=False, mismatched_signature=False):
        worker = Path(tempfile.mkdtemp()) / "worker_000"
        worker.mkdir()
        database = worker / "native_memory.sqlite3"
        scope = "tmcra_v4:q1"
        slot = "memory.user.preference.color"
        signature = "preference|color"
        old_metadata = {
            "message_id": "s000_m000",
            "llm_write_proposal_index": 0,
            "reconciliation_decision": "insert",
            "state_signature": signature,
            "superseded_by": "leaf.new",
            "superseded_reason": "same_state_revision",
        }
        new_metadata = {
            "message_id": "s000_m002",
            "llm_write_proposal_index": 0,
            "reconciliation_decision": "keep_parallel",
            "state_signature": "different" if mismatched_signature else signature,
            "conflict_action": "supersede",
            "conflict_reason": "same_state_revision",
        }
        if downstream:
            new_metadata.update(
                {
                    "superseded_by": "leaf.final",
                    "superseded_reason": "same_state_revision",
                }
            )
        with sqlite3.connect(database) as connection:
            connection.executescript(
                """
                CREATE TABLE records(
                    scope_id TEXT,memory_id TEXT,slot_key TEXT,turn_index INTEGER,
                    state TEXT,supersedes_json TEXT,metadata_json TEXT,
                    PRIMARY KEY(scope_id,memory_id));
                CREATE TABLE v4_reconciliation_jobs(
                    job_id TEXT,scope_id TEXT,message_id TEXT,assertion_index INTEGER,
                    request_json TEXT,response_json TEXT,status TEXT,decision TEXT,
                    created_at TEXT);
                """
            )
            connection.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?)",
                (
                    scope,
                    "leaf.old",
                    slot,
                    1,
                    "superseded",
                    "[]",
                    json.dumps(old_metadata),
                ),
            )
            connection.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?,?)",
                (
                    scope,
                    "leaf.new",
                    slot,
                    3,
                    "superseded" if downstream else "parallel_active",
                    json.dumps(["leaf.old"]),
                    json.dumps(new_metadata),
                ),
            )
            if downstream:
                connection.execute(
                    "INSERT INTO records VALUES(?,?,?,?,?,?,?)",
                    (
                        scope,
                        "leaf.final",
                        slot,
                        5,
                        "active",
                        json.dumps(["leaf.new"]),
                        json.dumps(
                            {
                                "message_id": "s000_m004",
                                "state_signature": signature,
                            }
                        ),
                    ),
                )
            request = {
                "candidate_cited_leaves": [
                    {"memory_id": "leaf.old", "record_state": "active"}
                ]
            }
            response = {
                "slot_decision": "bind_existing",
                "selected_memory_id": "leaf.old",
                "decision": "keep_parallel",
            }
            connection.execute(
                "INSERT INTO v4_reconciliation_jobs VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "job.0",
                    scope,
                    "s000_m002",
                    0,
                    json.dumps(request),
                    json.dumps(response),
                    "completed",
                    "keep_parallel",
                    "2026-07-12T00:00:00Z",
                ),
            )
            connection.commit()
        return worker, database

    def record(self, database, memory_id):
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT state,supersedes_json,metadata_json FROM records WHERE memory_id=?",
                (memory_id,),
            ).fetchone()
        return row[0], json.loads(row[1]), json.loads(row[2])

    def test_dry_run_then_apply_is_audited_and_idempotent(self):
        worker, database = self.make_worker()
        dry = migration.migrate_worker(worker, apply=False)
        self.assertEqual(dry["invalid_same_job_overwrites"], 1)
        self.assertEqual(self.record(database, "leaf.old")[0], "superseded")

        applied = migration.migrate_worker(worker, apply=True)
        self.assertEqual(applied["invalid_same_job_overwrites"], 1)
        self.assertEqual(applied["physical_api_calls"], 0)
        old_state, _, old_metadata = self.record(database, "leaf.old")
        self.assertEqual(old_state, "active")
        self.assertNotIn("superseded_by", old_metadata)
        new_state, new_supersedes, new_metadata = self.record(database, "leaf.new")
        self.assertEqual(new_state, "parallel_active")
        self.assertEqual(new_supersedes, [])
        self.assertEqual(new_metadata["conflict_action"], "keep_parallel")
        artifact = json.loads(
            (worker / "product_writer_keep_parallel_migrations.jsonl")
            .read_text(encoding="utf-8")
            .strip()
        )
        self.assertEqual(artifact["job_id"], "job.0")
        self.assertEqual(artifact["physical_api_calls"], 0)

        resumed = migration.migrate_worker(worker, apply=True)
        self.assertEqual(resumed["invalid_same_job_overwrites"], 0)
        self.assertEqual(resumed["journaled_migrations"], 1)

    def test_later_valid_supersession_is_propagated(self):
        worker, database = self.make_worker(downstream=True)
        migration.migrate_worker(worker, apply=True)
        old_state, _, old_metadata = self.record(database, "leaf.old")
        self.assertEqual(old_state, "superseded")
        self.assertEqual(old_metadata["superseded_by"], "leaf.final")
        self.assertEqual(old_metadata["superseded_reason"], "same_state_revision")
        _, final_supersedes, _ = self.record(database, "leaf.final")
        self.assertEqual(final_supersedes, ["leaf.new", "leaf.old"])

    def test_exact_evidence_identity_allows_commit_index_reordering(self):
        worker, database = self.make_worker()
        quote = "I prefer green."
        with sqlite3.connect(database) as connection:
            request = json.loads(
                connection.execute(
                    "SELECT request_json FROM v4_reconciliation_jobs WHERE job_id='job.0'"
                ).fetchone()[0]
            )
            request.update(
                {
                    "canonical_slot_key": "memory.user.preference.color",
                    "new_cited_assertion": {
                        "evidence_quote": quote,
                        "memory_type": "preference",
                        "entity_key": "preference",
                        "attribute_key": "color",
                    },
                }
            )
            metadata = json.loads(
                connection.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='leaf.new'"
                ).fetchone()[0]
            )
            metadata.update(
                {
                    "llm_write_proposal_index": 1,
                    "source_span": quote,
                    "memory_type": "preference",
                    "entity_key": "preference",
                    "attribute_key": "color",
                }
            )
            connection.execute(
                "UPDATE v4_reconciliation_jobs SET assertion_index=2,request_json=? WHERE job_id='job.0'",
                (json.dumps(request),),
            )
            connection.execute(
                "UPDATE records SET metadata_json=? WHERE memory_id='leaf.new'",
                (json.dumps(metadata),),
            )
            connection.commit()

        report = migration.migrate_worker(worker, apply=True)
        self.assertEqual(report["invalid_same_job_overwrites"], 1)
        artifact = json.loads(
            (worker / "product_writer_keep_parallel_migrations.jsonl")
            .read_text(encoding="utf-8")
            .strip()
        )
        self.assertEqual(
            artifact["assertion_binding_mode"],
            "exact_evidence_identity_after_commit_reindex",
        )

    def test_keep_parallel_restores_same_job_overwrite_across_relation_change(self):
        worker, database = self.make_worker(mismatched_signature=True)
        report = migration.migrate_worker(worker, apply=True)
        self.assertEqual(report["invalid_same_job_overwrites"], 1)
        self.assertEqual(self.record(database, "leaf.old")[0], "active")

    def test_migration_fails_closed_when_downstream_identity_differs(self):
        worker, _ = self.make_worker(
            downstream=True,
            mismatched_signature=True,
        )
        with self.assertRaisesRegex(
            migration.MigrationError, "downstream lifecycle"
        ):
            migration.migrate_worker(worker, apply=True)

    def test_noop_apply_does_not_create_a_migration_table(self):
        worker, database = self.make_worker()
        with sqlite3.connect(database) as connection:
            metadata = json.loads(
                connection.execute(
                    "SELECT metadata_json FROM records WHERE memory_id='leaf.old'"
                ).fetchone()[0]
            )
            metadata.pop("superseded_by", None)
            metadata.pop("superseded_reason", None)
            connection.execute(
                "UPDATE records SET state='active',metadata_json=? "
                "WHERE memory_id='leaf.old'",
                (json.dumps(metadata),),
            )
            connection.commit()
        report = migration.migrate_worker(worker, apply=True)
        self.assertEqual(report["invalid_same_job_overwrites"], 0)
        with sqlite3.connect(database) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='v4_keep_parallel_migrations'"
            ).fetchone()
        self.assertIsNone(table)


if __name__ == "__main__":
    unittest.main()
