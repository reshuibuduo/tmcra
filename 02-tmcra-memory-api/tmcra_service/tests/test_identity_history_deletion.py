"""Correction provenance must not leave deleted values in later audit payloads."""
import json
import sqlite3
from contextlib import closing

from tmcra_service.adapters.v4 import V4StorageAdapter
from tmcra_service.tests.test_content_deletion_contract import _settings, _seed_scope, _record


def test_deleted_source_purges_later_correction_and_audit_payloads(tmp_path):
    adapter = V4StorageAdapter(_settings(tmp_path))
    _seed_scope(adapter, 'tenant', 'scope')
    paths = adapter.scope_paths('tenant', 'scope')
    history = {'previous_source_record_id': 'source-a', 'source_record_id': 'source-b',
               'old_value': 'synthetic erased port 7341', 'new_value': 'port 7482'}
    with closing(sqlite3.connect(paths.database)) as db:
        # Existing full-platform policy purges derived records grounded in an
        # erased source, rather than retaining a partially grounded correction.
        db.execute('INSERT INTO records VALUES (' + ','.join('?' * 15) + ')',
                   _record(paths.scope_id, 'correction-b', metadata={
                       'source_record_id': 'source-b', 'correction_history': [history]}))
        db.execute('CREATE TABLE v4_reconciliation_jobs(scope_id TEXT, message_id TEXT, request_json TEXT, response_json TEXT)')
        db.executemany('INSERT INTO v4_reconciliation_jobs VALUES(?,?,?,?)', [
            (paths.scope_id, 'message-b', json.dumps({'candidates': [history]}), '{}'),
            (paths.scope_id, 'unrelated', json.dumps({'source_record_id': 'source-ab', 'value': 'keep'}), '{}'),
            ('another-scope', 'message-b', json.dumps(history), '{}')])
        db.execute('ALTER TABLE v4_message_commit_journal ADD COLUMN plan_json TEXT')
        db.execute('INSERT INTO v4_message_commit_journal VALUES(?,?,?,?)',
                   (paths.scope_id, 'session-b', 'message-b', json.dumps(history)))
        db.execute('ALTER TABLE v4_batch_journal ADD COLUMN request_json TEXT')
        db.execute('INSERT INTO v4_batch_journal VALUES(?,?,?,?)',
                   (paths.scope_id, 'session-b', 'cross-source-batch', json.dumps(history)))
        db.commit()
    adapter.delete_memories(tenant_id='tenant', scope_name='scope', job_id='delete-identity', memory_ids=['source-a'])
    with closing(sqlite3.connect(paths.database)) as db:
        assert db.execute('SELECT 1 FROM records WHERE memory_id="correction-b"').fetchone() is None
        rows = db.execute('SELECT message_id,request_json FROM v4_reconciliation_jobs WHERE scope_id=?', (paths.scope_id,)).fetchall()
        assert [r[0] for r in rows] == ['unrelated'], rows
        assert db.execute('SELECT count(*) FROM v4_reconciliation_jobs WHERE scope_id="another-scope"').fetchone()[0] == 1
        for table, column in [('v4_message_commit_journal', 'plan_json'), ('v4_batch_journal', 'request_json')]:
            payloads = db.execute(f'SELECT {column} FROM {table} WHERE scope_id=?', (paths.scope_id,)).fetchall()
            assert not any('synthetic erased port' in (row[0] or '') for row in payloads)
