"""Synthetic deletion/replay regression; no model or production data."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from tmcra_service.adapters.v4 import V4StorageAdapter
from tmcra_service.commercial import CommercialControl
from tmcra_service.control_db import ControlDB
from tmcra_service.writer import IdentityRegistry, ProductionWriterError
from tmcra_service.source_tombstones import (
    initialize_source_tombstones, record_source_tombstones, source_identity_revoked,
)
from test_content_deletion_contract import _seed_scope, _settings


class SourceRevocationTests(unittest.TestCase):
    def test_tombstone_uses_hashes_and_rolls_back_with_its_transaction(self):
        with closing(sqlite3.connect(':memory:')) as db:
            initialize_source_tombstones(db)
            db.commit()
            db.execute('BEGIN IMMEDIATE')
            record_source_tombstones(db, 'scope-a', [('private-id', 'private-session')], deletion_job_id='delete-1')
            self.assertTrue(source_identity_revoked(db, 'scope-a', 'private-id'))
            self.assertFalse(source_identity_revoked(db, 'scope-b', 'private-id'))
            rows = repr(db.execute('SELECT * FROM tmcra_service_source_tombstones').fetchall())
            self.assertNotIn('private-id', rows)
            self.assertNotIn('private-session', rows)
            db.rollback()
            self.assertFalse(source_identity_revoked(db, 'scope-a', 'private-id'))

    def test_deletion_fences_reimport_under_a_new_session_and_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = V4StorageAdapter(_settings(Path(directory)))
            _seed_scope(adapter, 'tenant-a', 'scope-a')
            paths = adapter.scope_paths('tenant-a', 'scope-a')
            adapter.delete_memories(tenant_id='tenant-a', scope_name='scope-a', job_id='delete-a', session_id='session-a')
            registry = IdentityRegistry(paths.database, 'new-ingest-key', expected_scope_id=paths.scope_id)
            row = {'scope_id': paths.scope_id, 'session_id': 'new-session', 'operation_id': 'new-ingest-key',
                   'messages': [{'message_id': 'message-a', 'role': 'user', 'timestamp': 'now', 'content': 'synthetic source'}]}
            with self.assertRaisesRegex(ProductionWriterError, 'permanently revoked'):
                registry.register_messages([row], v4=SimpleNamespace(SourceMessage=SimpleNamespace))
            with closing(sqlite3.connect(paths.database)) as db:
                self.assertIsNone(db.execute("SELECT 1 FROM tmcra_service_sessions WHERE session_id='new-session'").fetchone())
                self.assertFalse(source_identity_revoked(db, paths.scope_id, 'message-b'))
            # The same external identity in a different owner/scope is independent.
            row['scope_id'] = 'tmcra_v4:svc_independent'
            other = IdentityRegistry(Path(directory)/'other.sqlite3', 'new-ingest-key', expected_scope_id=row['scope_id'])
            messages, _ = other.register_messages([row], v4=SimpleNamespace(SourceMessage=SimpleNamespace))
            self.assertEqual(len(messages), 1)

    def test_external_id_equal_to_other_internal_id_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = V4StorageAdapter(_settings(Path(directory)))
            _seed_scope(adapter, 'tenant-a', 'scope-a')
            paths = adapter.scope_paths('tenant-a', 'scope-a')
            with closing(sqlite3.connect(paths.database)) as db:
                db.execute("ALTER TABLE tmcra_service_messages ADD COLUMN internal_message_id TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE tmcra_service_messages SET internal_message_id=message_id")
                db.execute("UPDATE tmcra_service_messages SET message_id='external-a' WHERE session_id='session-a'")
                db.execute("UPDATE tmcra_service_messages SET message_id='message-a' WHERE session_id='session-b'")
                db.commit()
            adapter.delete_memories(tenant_id='tenant-a', scope_name='scope-a', job_id='delete-a', session_id='session-a')
            with closing(sqlite3.connect(paths.database)) as db:
                self.assertEqual(db.execute('SELECT message_id FROM tmcra_service_messages').fetchall(), [('message-a',)])
                self.assertTrue(source_identity_revoked(db, paths.scope_id, 'external-a'))
                self.assertFalse(source_identity_revoked(db, paths.scope_id, 'message-a'))

    def test_control_cleanup_keeps_content_free_completion_fence(self):
        with tempfile.TemporaryDirectory() as directory:
            database = ControlDB(Path(directory)/'control.sqlite3')
            commercial = CommercialControl(database)
            database.record_committed_source_records('tenant', 'scope', 'ingest-1', [
                {'source_record_id': 'source-1', 'raw_token_estimate': 11, 'user_turns': 1}])
            commercial.apply_content_deletion_control_cleanup('tenant', 'scope',
                deleted_source_record_ids=['source-1'], deleted_session_message_counts={})
            with database.transaction(immediate=False) as db:
                row = db.execute('SELECT new_message_count,raw_token_estimate,user_turns FROM scope_ingest_watermark_commits WHERE operation_id=?', ('ingest-1',)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(tuple(row), (0,0,0))


if __name__ == '__main__':
    unittest.main()
