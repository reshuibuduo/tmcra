import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import migrate_tmcra_v4_challenge_lifecycle as migration


class ChallengeLifecycleMigrationTests(unittest.TestCase):
    def make_worker(
        self,
        *,
        unrelated_head=False,
        reindexed=False,
        later_replace_current=False,
    ):
        worker = Path(tempfile.mkdtemp()) / "worker_000"
        worker.mkdir()
        database = worker / "native_memory.sqlite3"
        scope = "tmcra_v4:q1"
        slot = "memory.user.travel.flight.preference.preferred.class"
        quote = "I am considering economy class instead."
        old_metadata = {
            "message_id": "s000_m000",
            "llm_write_proposal_index": 0,
            "reconciliation_decision": "insert",
            "superseded_by": "leaf.new",
            "superseded_reason": "same_state_revision",
        }
        new_metadata = {
            "message_id": "s000_m002",
            "llm_write_proposal_index": 1 if reindexed else 0,
            "reconciliation_decision": "challenge",
            "source_span": quote,
            "memory_type": "preference",
            "entity_key": "travel.flight",
            "attribute_key": "preferred.class",
            "conflict_action": "supersede",
            "conflict_reason": "same_state_revision",
        }
        if later_replace_current:
            old_metadata.update(
                {
                    "superseded_by": "leaf.final",
                    "superseded_reason": "v4_reconciliation_replace_current",
                }
            )
            new_metadata.update(
                {
                    "superseded_by": "leaf.final",
                    "superseded_reason": "slot_disallows_parallel",
                }
            )
        with sqlite3.connect(database) as connection:
            connection.executescript(
                """
                CREATE TABLE records(
                    scope_id TEXT,memory_id TEXT,slot_key TEXT,turn_index INTEGER,
                    state TEXT,supersedes_json TEXT,metadata_json TEXT,
                    PRIMARY KEY(scope_id,memory_id));
                CREATE TABLE slot_heads(
                    scope_id TEXT,slot_key TEXT,memory_id TEXT,
                    PRIMARY KEY(scope_id,slot_key));
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
                    47,
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
                    170,
                    "challenged",
                    json.dumps([] if later_replace_current else ["leaf.old"]),
                    json.dumps(new_metadata),
                ),
            )
            if later_replace_current:
                connection.execute(
                    "INSERT INTO records VALUES(?,?,?,?,?,?,?)",
                    (
                        scope,
                        "leaf.final",
                        slot,
                        170,
                        "active",
                        json.dumps(["leaf.new", "leaf.old"]),
                        json.dumps(
                            {
                                "message_id": "s000_m002",
                                "llm_write_proposal_index": 1,
                                "reconciliation_decision": "replace_current",
                                "source_span": "My actual title is still undecided.",
                                "memory_type": "preference",
                                "entity_key": "travel.flight",
                                "attribute_key": "preferred.class",
                                "conflict_action": "supersede",
                                "conflict_reason": "slot_disallows_parallel",
                            }
                        ),
                    ),
                )
            if unrelated_head:
                connection.execute(
                    "INSERT INTO records VALUES(?,?,?,?,?,?,?)",
                    (
                        scope,
                        "leaf.unrelated",
                        slot,
                        200,
                        "active",
                        "[]",
                        json.dumps({"message_id": "s000_m004"}),
                    ),
                )
            connection.execute(
                "INSERT INTO slot_heads VALUES(?,?,?)",
                (
                    scope,
                    slot,
                    (
                        "leaf.unrelated"
                        if unrelated_head
                        else "leaf.final" if later_replace_current else "leaf.old"
                    ),
                ),
            )
            request = {
                "canonical_slot_key": slot,
                "candidate_cited_leaves": [
                    {"memory_id": "leaf.old", "record_state": "active"}
                ],
                "new_cited_assertion": {
                    "evidence_quote": quote,
                    "memory_type": "preference",
                    "entity_key": "travel.flight",
                    "attribute_key": "preferred.class",
                },
            }
            response = {
                "slot_decision": "bind_existing",
                "selected_memory_id": "leaf.old",
                "decision": "challenge",
            }
            connection.execute(
                "INSERT INTO v4_reconciliation_jobs VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "job.0",
                    scope,
                    "s000_m002",
                    2 if reindexed else 0,
                    json.dumps(request),
                    json.dumps(response),
                    "completed",
                    "challenge",
                    "2026-07-12T00:00:00Z",
                ),
            )
            if later_replace_current:
                replacement_request = {
                    "canonical_slot_key": slot,
                    "new_cited_assertion": {
                        "evidence_quote": "My actual title is still undecided.",
                        "memory_type": "preference",
                        "entity_key": "travel.flight",
                        "attribute_key": "preferred.class",
                    },
                }
                replacement_response = {
                    "slot_decision": "bind_existing",
                    "selected_memory_id": "leaf.old",
                    "decision": "replace_current",
                }
                connection.execute(
                    "INSERT INTO v4_reconciliation_jobs VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        "job.1",
                        scope,
                        "s000_m002",
                        1,
                        json.dumps(replacement_request),
                        json.dumps(replacement_response),
                        "completed",
                        "replace_current",
                        "2026-07-12T00:00:01Z",
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

    def slot_head(self, database):
        with sqlite3.connect(database) as connection:
            return connection.execute(
                "SELECT memory_id FROM slot_heads"
            ).fetchone()[0]

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
        self.assertNotIn("superseded_reason", old_metadata)
        new_state, new_supersedes, new_metadata = self.record(database, "leaf.new")
        self.assertEqual(new_state, "challenged")
        self.assertEqual(new_supersedes, [])
        self.assertEqual(new_metadata["conflict_action"], "challenge")
        self.assertEqual(
            new_metadata["conflict_reason"], "v4_reconciliation_challenge"
        )
        self.assertEqual(self.slot_head(database), "leaf.old")

        artifact = json.loads(
            (worker / "product_writer_challenge_lifecycle_migrations.jsonl")
            .read_text(encoding="utf-8")
            .strip()
        )
        self.assertEqual(artifact["job_id"], "job.0")
        self.assertEqual(artifact["physical_api_calls"], 0)
        with sqlite3.connect(database) as connection:
            journal_count = connection.execute(
                "SELECT COUNT(*) FROM v4_challenge_lifecycle_migrations"
            ).fetchone()[0]
        self.assertEqual(journal_count, 1)

        resumed = migration.migrate_worker(worker, apply=True)
        self.assertEqual(resumed["invalid_same_job_overwrites"], 0)
        self.assertEqual(resumed["journaled_migrations"], 1)

    def test_exact_evidence_identity_allows_commit_index_reordering(self):
        worker, database = self.make_worker(reindexed=True)
        report = migration.migrate_worker(worker, apply=True)
        self.assertEqual(report["invalid_same_job_overwrites"], 1)
        artifact = json.loads(
            (worker / "product_writer_challenge_lifecycle_migrations.jsonl")
            .read_text(encoding="utf-8")
            .strip()
        )
        self.assertEqual(
            artifact["assertion_binding_mode"],
            "exact_evidence_identity_after_commit_reindex",
        )
        self.assertEqual(self.record(database, "leaf.old")[0], "active")

    def test_migration_fails_closed_for_unrelated_slot_head(self):
        worker, database = self.make_worker(unrelated_head=True)
        with self.assertRaisesRegex(
            migration.MigrationError, "slot head changed to unrelated record"
        ):
            migration.migrate_worker(worker, apply=True)
        self.assertEqual(self.record(database, "leaf.old")[0], "superseded")
        self.assertEqual(self.slot_head(database), "leaf.unrelated")
        with sqlite3.connect(database) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='v4_challenge_lifecycle_migrations'"
            ).fetchone()
            journal_count = (
                connection.execute(
                    "SELECT COUNT(*) FROM v4_challenge_lifecycle_migrations"
                ).fetchone()[0]
                if table
                else 0
            )
        self.assertEqual(journal_count, 0)

    def test_later_same_message_replace_current_is_not_reverted(self):
        worker, database = self.make_worker(later_replace_current=True)
        report = migration.migrate_worker(worker, apply=True)
        self.assertEqual(report["invalid_same_job_overwrites"], 0)
        self.assertEqual(self.record(database, "leaf.old")[0], "superseded")
        self.assertEqual(self.record(database, "leaf.new")[0], "challenged")
        self.assertEqual(self.record(database, "leaf.final")[0], "active")
        self.assertEqual(self.slot_head(database), "leaf.final")
        with sqlite3.connect(database) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' "
                "AND name='v4_challenge_lifecycle_migrations'"
            ).fetchone()
        self.assertIsNone(table)


if __name__ == "__main__":
    unittest.main()
