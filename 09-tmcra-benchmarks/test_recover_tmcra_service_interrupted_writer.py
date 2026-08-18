import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import tmcra_v4_batch_writer as v4
from ops import recover_tmcra_service_interrupted_writer as recovery


class InterruptedWriterRecoveryTest(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, str, str]:
        database = root / "native_memory.sqlite3"
        operation_dir = root / "operation"
        operation_dir.mkdir()
        operation_id = "job-process-loss"
        scope_id = "tmcra_v4:scope"
        session_id = "session-1"
        message = v4.SourceMessage(
            scope_id=scope_id,
            session_id=session_id,
            session_index=0,
            message_index=0,
            message_id="message-1",
            role="user",
            timestamp="2026-08-09T01:02:00Z",
            content="Exact immutable source text.",
        )
        batch = v4.SourceBatch(scope_id, session_id, 0, 0, (message,))
        store = v4.V4BatchStore(database)
        store.prepare(batch, v4.build_batch_request(batch))
        store.mark_api_started(batch.batch_id)
        metadata = {
            "raw_content": message.content,
            "source_record_id": "source-record-1",
            "session_index": 0,
            "message_index": 0,
            "actor_role": "user",
        }
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript(
                """
                CREATE TABLE tmcra_service_batches(
                    scope_id TEXT, session_id TEXT, operation_id TEXT,
                    local_batch_index INTEGER, batch_index INTEGER
                );
                CREATE TABLE tmcra_service_messages(
                    scope_id TEXT, message_id TEXT, internal_message_id TEXT,
                    session_id TEXT, message_index INTEGER, role TEXT,
                    timestamp TEXT, content_sha256 TEXT, first_operation_id TEXT
                );
                CREATE TABLE records(
                    scope_id TEXT, memory_id TEXT, turn_index INTEGER,
                    metadata_json TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO tmcra_service_batches VALUES(?,?,?,?,?)",
                (scope_id, session_id, operation_id, 0, 0),
            )
            connection.execute(
                "INSERT INTO tmcra_service_messages VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    scope_id,
                    "public-message-1",
                    message.message_id,
                    session_id,
                    0,
                    message.role,
                    message.timestamp,
                    v4.sha256_text(message.content),
                    operation_id,
                ),
            )
            connection.execute(
                "UPDATE v4_source_journal SET source_record_id=?,"
                "source_turn_index=?,source_persisted_at=? "
                "WHERE scope_id=? AND message_id=?",
                (
                    "source-record-1",
                    7,
                    "2026-08-09T01:02:01Z",
                    scope_id,
                    message.message_id,
                ),
            )
            connection.execute(
                "INSERT INTO records VALUES(?,?,?,?)",
                (scope_id, "source-record-1", 7, json.dumps(metadata)),
            )
            connection.commit()
        (operation_dir / "input.json").write_text(
            json.dumps(
                [
                    {
                        "scope_id": scope_id,
                        "question_id": "scope",
                        "session_id": session_id,
                        "operation_id": operation_id,
                        "messages": [
                            {
                                "role": message.role,
                                "timestamp": message.timestamp,
                                "content": message.content,
                            }
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )
        return database, operation_dir, operation_id, batch.batch_id

    def test_audit_and_apply_use_atomic_v4_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            database, operation_dir, operation_id, batch_id = self._fixture(
                Path(raw_dir)
            )
            audit = recovery.audit_recovery(
                database=database,
                operation_dir=operation_dir,
                operation_id=operation_id,
                batch_id=batch_id,
            )
            self.assertTrue(audit["audit_passed"])
            self.assertEqual(audit["pending_source_count"], 1)
            result = recovery.apply_recovery(
                database=database,
                operation_dir=operation_dir,
                audit=audit,
                model=recovery.LOCAL_QWEN_MODEL,
            )
            self.assertEqual(result["result_status"], "prepared")
            with closing(sqlite3.connect(database)) as connection:
                status = connection.execute(
                    "SELECT status FROM v4_batch_journal WHERE batch_id=?",
                    (batch_id,),
                ).fetchone()[0]
            self.assertEqual(status, "prepared")
            artifact = json.loads(
                (operation_dir / "product_writer_interrupted_calls.jsonl")
                .read_text(encoding="utf-8")
                .strip()
            )
            self.assertEqual(artifact["model"], recovery.LOCAL_QWEN_MODEL)
            self.assertTrue(artifact["replacement_call_authorized"])

    def test_audit_refuses_changed_source_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            database, operation_dir, operation_id, batch_id = self._fixture(
                Path(raw_dir)
            )
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "UPDATE records SET metadata_json=?",
                    (json.dumps({"raw_content": "changed"}),),
                )
                connection.commit()
            with self.assertRaisesRegex(
                recovery.RecoveryAuditError, "Source graph metadata differs"
            ):
                recovery.audit_recovery(
                    database=database,
                    operation_dir=operation_dir,
                    operation_id=operation_id,
                    batch_id=batch_id,
                )


if __name__ == "__main__":
    unittest.main()
