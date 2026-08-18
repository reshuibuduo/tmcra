from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from unittest import mock
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from tmcra_service.adapters.v4 import V4StorageAdapter
from tmcra_service.app import create_app
from tmcra_service.commercial import CommercialContractError, CommercialControl
from tmcra_service.control_db import ControlDB
from tmcra_service.jobs import JobStore
from tmcra_service.settings import ServiceSettings


def _settings(root: Path) -> ServiceSettings:
    files = {
        "writer_env": root / "writer.env",
        "native_harness": root / "harness.py",
        "node_model": root / "node.pt",
        "path_model": root / "path.pt",
        "checkpoint": root / "checkpoint.pt",
    }
    for path in files.values():
        path.write_text("test", encoding="utf-8")
    (root / "tmcra_v4_batch_writer.py").write_text("test", encoding="utf-8")
    production = root / "tmcra_service" / "writer.py"
    production.parent.mkdir()
    production.write_text("test", encoding="utf-8")
    return ServiceSettings(
        state_dir=root / "state",
        control_db=root / "state" / "control.sqlite3",
        bind_host="127.0.0.1",
        bind_port=2009,
        public_base_url="https://example.invalid",
        v4_root=root,
        integrated_repo=root,
        writer_env=files["writer_env"],
        embedding_model=root,
        native_harness=files["native_harness"],
        node_model=files["node_model"],
        path_model=files["path_model"],
        checkpoint=files["checkpoint"],
        cross_model=root,
        device="cpu",
        graph_device="cpu",
        request_body_limit=1024 * 1024,
        provider_lease_seconds=30,
        provider_key_concurrency=1,
        disk_free_min_bytes=1,
        startup_preflight_mode="off",
    )


def _record(
    scope_id: str,
    memory_id: str,
    *,
    metadata: dict[str, object],
    evidence: list[str] | None = None,
) -> tuple[object, ...]:
    return (
        scope_id,
        memory_id,
        "test",
        memory_id,
        memory_id,
        "test",
        "[]",
        json.dumps(evidence or []),
        1.0,
        1.0,
        "test",
        1,
        "active",
        "[]",
        json.dumps(metadata),
    )


def _seed_scope(adapter: V4StorageAdapter, tenant_id: str, scope_name: str) -> None:
    paths = adapter.scope_paths(tenant_id, scope_name)
    paths.database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(paths.database)) as connection:
        connection.executescript(
            """
            CREATE TABLE records(
                scope_id TEXT NOT NULL,memory_id TEXT NOT NULL,category TEXT NOT NULL,
                slot_key TEXT NOT NULL,value TEXT NOT NULL,relation TEXT NOT NULL,
                anchor_concepts_json TEXT NOT NULL,evidence_anchors_json TEXT NOT NULL,
                salience REAL NOT NULL,confidence REAL NOT NULL,source_kind TEXT NOT NULL,
                turn_index INTEGER NOT NULL,state TEXT NOT NULL,supersedes_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,PRIMARY KEY(scope_id,memory_id)
            );
            CREATE TABLE slot_heads(scope_id TEXT,slot_key TEXT,memory_id TEXT);
            CREATE TABLE slot_history(scope_id TEXT,slot_key TEXT,ordinal INTEGER,memory_id TEXT);
            CREATE TABLE subject_depth_heads(scope_id TEXT,subject_signature TEXT,depth_layer TEXT,memory_id TEXT);
            CREATE TABLE memory_edges(
                scope_id TEXT,edge_id TEXT,source_memory_id TEXT,target_memory_id TEXT,
                edge_type TEXT,score REAL,model_score REAL,evidence_turn INTEGER,
                evidence TEXT,metadata_json TEXT
            );
            CREATE TABLE audit_turn_log(scope_id TEXT,event_index INTEGER,payload_json TEXT);
            CREATE TABLE audit_retrieval_log(scope_id TEXT,event_index INTEGER,payload_json TEXT);
            CREATE TABLE audit_answer_support(scope_id TEXT,event_index INTEGER,payload_json TEXT);
            CREATE TABLE meta(scope_id TEXT,key TEXT,value_json TEXT,PRIMARY KEY(scope_id,key));
            CREATE TABLE tmcra_service_sessions(scope_id TEXT,session_id TEXT,session_index INTEGER);
            CREATE TABLE tmcra_service_messages(
                scope_id TEXT,message_id TEXT,session_id TEXT,message_index INTEGER,
                role TEXT,timestamp TEXT,content_sha256 TEXT
            );
            CREATE TABLE tmcra_service_batches(
                scope_id TEXT,session_id TEXT,operation_id TEXT,local_batch_index INTEGER,
                batch_index INTEGER
            );
            CREATE TABLE v4_source_journal(scope_id TEXT,session_id TEXT,message_id TEXT);
            CREATE TABLE v4_batch_journal(scope_id TEXT,session_id TEXT,batch_id TEXT);
            CREATE TABLE v4_interactions(scope_id TEXT,session_id TEXT,message_id TEXT);
            CREATE TABLE v4_message_commit_journal(scope_id TEXT,session_id TEXT,message_id TEXT);
            CREATE TABLE slow_graph_jobs(job_id TEXT,scope_id TEXT);
            CREATE TABLE slow_graph_patches(patch_id TEXT,scope_id TEXT);
            CREATE TABLE slow_graph_patch_operations(operation_id TEXT,patch_id TEXT);
            CREATE TABLE slow_graph_provenance(provenance_id TEXT,scope_id TEXT);
            """
        )
        rows = [
            _record(
                paths.scope_id,
                "source-a",
                metadata={
                    "content_variant": "source_message",
                    "source_record_id": "source-a",
                    "session_id": "session-a",
                    "message_id": "message-a",
                },
            ),
            _record(
                paths.scope_id,
                "fast-a",
                metadata={
                    "content_variant": "product_semantic_memory",
                    "memory_layer": "fast",
                    "source_record_id": "source-a",
                },
                evidence=["source-a"],
            ),
            _record(
                paths.scope_id,
                "source-b",
                metadata={
                    "content_variant": "source_message",
                    "source_record_id": "source-b",
                    "session_id": "session-b",
                    "message_id": "message-b",
                },
            ),
            _record(
                paths.scope_id,
                "slow-a",
                metadata={
                    "content_variant": "slow_memory_capsule",
                    "memory_layer": "slow",
                },
                evidence=["fast-a"],
            ),
        ]
        connection.executemany(
            "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        connection.executemany(
            "INSERT INTO tmcra_service_messages VALUES(?,?,?,?,?,?,?)",
            [
                (paths.scope_id, "message-a", "session-a", 0, "user", "t", "a"),
                (paths.scope_id, "message-b", "session-b", 0, "user", "t", "b"),
            ],
        )
        connection.executemany(
            "INSERT INTO v4_source_journal VALUES(?,?,?)",
            [
                (paths.scope_id, "session-a", "message-a"),
                (paths.scope_id, "session-b", "message-b"),
            ],
        )
        connection.execute(
            "INSERT INTO meta VALUES(?,?,?)",
            (paths.scope_id, "storage_revision", "1"),
        )
        connection.execute(
            "INSERT INTO slow_graph_patches VALUES(?,?)", ("patch-a", paths.scope_id)
        )
        connection.execute(
            "INSERT INTO slow_graph_patch_operations VALUES(?,?)", ("op-a", "patch-a")
        )
        connection.commit()


class ContentDeletionStorageTests(unittest.TestCase):
    def test_external_message_id_resolves_to_exact_source_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = V4StorageAdapter(_settings(Path(directory)))
            _seed_scope(adapter, "tenant-a", "scope-a")
            result = adapter.resolve_source_memory_ids_for_messages(
                tenant_id="tenant-a",
                scope_name="scope-a",
                message_ids=["message-a"],
            )
            self.assertEqual(result, ["source-a"])

    def test_session_deletion_cascades_provenance_and_preserves_other_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = V4StorageAdapter(_settings(Path(directory)))
            _seed_scope(adapter, "tenant-a", "scope-a")
            result = adapter.delete_memories(
                tenant_id="tenant-a",
                scope_name="scope-a",
                job_id="delete-a",
                session_id="session-a",
            )
            self.assertEqual(result["deleted_memory_count"], 3)
            self.assertEqual(result["deleted_message_count"], 1)
            self.assertTrue(result["slow_rebuild_required"])
            paths = adapter.scope_paths("tenant-a", "scope-a")
            with closing(sqlite3.connect(paths.database)) as connection:
                remaining = {
                    row[0]
                    for row in connection.execute(
                        "SELECT memory_id FROM records WHERE scope_id=?",
                        (paths.scope_id,),
                    )
                }
                messages = {
                    row[0]
                    for row in connection.execute(
                        "SELECT message_id FROM tmcra_service_messages"
                    )
                }
                patch_ops = connection.execute(
                    "SELECT COUNT(*) FROM slow_graph_patch_operations"
                ).fetchone()[0]
            self.assertEqual(remaining, {"source-b"})
            self.assertEqual(messages, {"message-b"})
            self.assertEqual(patch_ops, 0)
            commit = adapter.content_deletion_commit(
                tenant_id="tenant-a", scope_name="scope-a", job_id="delete-a"
            )
            self.assertIsNotNone(commit)
            self.assertEqual(commit["result"]["deleted_memory_count"], 3)

    def test_session_deletion_resolves_internal_service_message_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = V4StorageAdapter(_settings(Path(directory)))
            _seed_scope(adapter, "tenant-a", "scope-a")
            paths = adapter.scope_paths("tenant-a", "scope-a")
            with closing(sqlite3.connect(paths.database)) as connection:
                connection.execute("PRAGMA foreign_keys=OFF")
                connection.executescript(
                    """
                    DROP TABLE tmcra_service_messages;
                    DROP TABLE tmcra_service_sessions;
                    CREATE TABLE tmcra_service_sessions(
                        scope_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        session_index INTEGER NOT NULL,
                        PRIMARY KEY(scope_id,session_id)
                    );
                    CREATE TABLE tmcra_service_messages(
                        scope_id TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        internal_message_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        message_index INTEGER NOT NULL,
                        role TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        PRIMARY KEY(scope_id,message_id),
                        UNIQUE(scope_id,internal_message_id),
                        FOREIGN KEY(scope_id,session_id)
                            REFERENCES tmcra_service_sessions(scope_id,session_id)
                    );
                    CREATE TABLE tmcra_service_message_actor_provenance(
                        scope_id TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        actor_metadata_json TEXT NOT NULL,
                        actor_metadata_sha256 TEXT NOT NULL,
                        PRIMARY KEY(scope_id,message_id),
                        FOREIGN KEY(scope_id,message_id)
                            REFERENCES tmcra_service_messages(scope_id,message_id)
                            ON DELETE CASCADE
                    );
                    """
                )
                connection.executemany(
                    "INSERT INTO tmcra_service_sessions VALUES(?,?,?)",
                    [
                        (paths.scope_id, "session-a", 0),
                        (paths.scope_id, "session-b", 1),
                    ],
                )
                connection.executemany(
                    "INSERT INTO tmcra_service_messages VALUES(?,?,?,?,?,?,?,?)",
                    [
                        (
                            paths.scope_id,
                            "external-message-a",
                            "message-a",
                            "session-a",
                            0,
                            "user",
                            "t",
                            "a",
                        ),
                        (
                            paths.scope_id,
                            "external-message-b",
                            "message-b",
                            "session-b",
                            0,
                            "user",
                            "t",
                            "b",
                        ),
                    ],
                )
                connection.executemany(
                    "INSERT INTO tmcra_service_message_actor_provenance VALUES(?,?,?,?)",
                    [
                        (paths.scope_id, "external-message-a", "{}", "a"),
                        (paths.scope_id, "external-message-b", "{}", "b"),
                    ],
                )
                connection.commit()

            result = adapter.delete_memories(
                tenant_id="tenant-a",
                scope_name="scope-a",
                job_id="delete-internal-service-id",
                session_id="session-a",
            )

            self.assertEqual(result["deleted_message_count"], 1)
            with closing(sqlite3.connect(paths.database)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                sessions = {
                    row[0]
                    for row in connection.execute(
                        "SELECT session_id FROM tmcra_service_sessions"
                    )
                }
                messages = {
                    (row[0], row[1])
                    for row in connection.execute(
                        "SELECT message_id,internal_message_id "
                        "FROM tmcra_service_messages"
                    )
                }
                actors = {
                    row[0]
                    for row in connection.execute(
                        "SELECT message_id "
                        "FROM tmcra_service_message_actor_provenance"
                    )
                }
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(sessions, {"session-b"})
            self.assertEqual(messages, {("external-message-b", "message-b")})
            self.assertEqual(actors, {"external-message-b"})
            self.assertEqual(violations, [])

    def test_deleting_final_session_commits_an_empty_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = V4StorageAdapter(_settings(Path(directory)))
            _seed_scope(adapter, "tenant-a", "scope-a")
            adapter.delete_memories(
                tenant_id="tenant-a",
                scope_name="scope-a",
                job_id="delete-a",
                session_id="session-a",
            )
            adapter.delete_memories(
                tenant_id="tenant-a",
                scope_name="scope-a",
                job_id="delete-b",
                session_id="session-b",
            )
            paths = adapter.scope_paths("tenant-a", "scope-a")
            paths.indexes.mkdir(parents=True, exist_ok=True)
            paths.active_index.write_text('{"stale":true}\n', encoding="utf-8")
            paths.active_delta.write_text('{"stale":true}\n', encoding="utf-8")
            builder = mock.Mock(
                side_effect=AssertionError("empty scope must not call the model")
            )

            result = adapter.build_index(
                tenant_id="tenant-a",
                scope_name="scope-a",
                job_id="empty-index",
                source_event_seq=0,
                builder=builder,
            )
            replay = adapter.build_index(
                tenant_id="tenant-a",
                scope_name="scope-a",
                job_id="empty-index",
                source_event_seq=0,
                builder=builder,
            )

            self.assertIsNone(result["active_index"])
            self.assertEqual(result["report"]["record_count"], 0)
            self.assertEqual(replay["report"], result["report"])
            self.assertFalse(paths.active_index.exists())
            self.assertFalse(paths.active_delta.exists())
            builder.assert_not_called()


class ContentDeletionControlTests(unittest.TestCase):
    def test_failed_deletion_is_fail_closed_and_can_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = ControlDB(Path(directory) / "control.sqlite3")
            commercial = CommercialControl(database)
            jobs = JobStore(database)
            job_id = "job-delete"

            def register(connection: object, _keys: tuple[str, ...]) -> None:
                commercial.register_content_deletion_in_transaction(
                    connection,
                    "tenant-a",
                    "scope-a",
                    deletion_id="del-a",
                    job_id=job_id,
                    mode="session",
                    target_sha256="a" * 64,
                    target_count=1,
                )

            jobs.submit(
                "tenant-a",
                "delete-key",
                {
                    "job_type": "delete_session",
                    "scope_name": "scope-a",
                    "session_id": "session-a",
                    "deletion_id": "del-a",
                },
                scope_name="scope-a",
                requested_job_id=job_id,
                on_new_jobs=register,
            )
            self.assertTrue(
                database.scope_scheduler_gate(
                    "tenant-a", "scope-a", candidate_job_id=job_id
                )["ready"]
            )
            with self.assertRaisesRegex(CommercialContractError, "in progress"):
                commercial.require_scope_active("tenant-a", "scope-a")
            commercial.update_content_deletion(
                "tenant-a",
                "scope-a",
                "del-a",
                job_id,
                state="failed",
                error_code="test-failure",
            )
            with self.assertRaisesRegex(CommercialContractError, "in progress"):
                commercial.require_scope_active("tenant-a", "scope-a")
            resumed = commercial.resume_content_deletion(
                "tenant-a", "scope-a", "del-a", job_id
            )
            self.assertEqual(resumed["state"], "requested")
            completed = commercial.update_content_deletion(
                "tenant-a",
                "scope-a",
                "del-a",
                job_id,
                state="completed",
                result={"deleted": True},
            )
            self.assertEqual(completed["result"], {"deleted": True})
            commercial.require_scope_active("tenant-a", "scope-a")

    def test_control_cleanup_removes_deleted_source_accounting_and_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = ControlDB(Path(directory) / "control.sqlite3")
            commercial = CommercialControl(database)
            database.record_committed_source_records(
                "tenant-a",
                "scope-a",
                "operation-a",
                [
                    {
                        "source_record_id": "source-a",
                        "raw_token_estimate": 11,
                        "user_turns": 1,
                    },
                    {
                        "source_record_id": "source-b",
                        "raw_token_estimate": 13,
                        "user_turns": 1,
                    },
                ],
            )
            now = time.time()
            with database.transaction() as connection:
                connection.execute(
                    "INSERT INTO scope_catalog VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        "tenant-a",
                        "scope-a",
                        now,
                        now,
                        now,
                        None,
                        1,
                        0,
                        2,
                    ),
                )
                connection.executemany(
                    "INSERT INTO scope_sessions VALUES(?,?,?,?,?,?,?)",
                    [
                        ("tenant-a", "scope-a", "session-a", now, now, 1, 1),
                        ("tenant-a", "scope-a", "session-b", now, now, 1, 1),
                    ],
                )
                connection.execute(
                    "INSERT INTO memory_graph_views VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "tenant-a",
                        "scope-a",
                        "atlas",
                        "v1",
                        None,
                        "fingerprint",
                        "test",
                        None,
                        None,
                        "{}",
                        now,
                        now,
                    ),
                )
            commercial.apply_content_deletion_control_cleanup(
                "tenant-a",
                "scope-a",
                deleted_source_record_ids=["source-a"],
                deleted_session_message_counts={"session-a": 1},
                deleted_session_id="session-a",
            )
            state = database.get_scope_evolution_state("tenant-a", "scope-a")
            self.assertEqual(state["source_event_seq"], 1)
            self.assertEqual(state["source_raw_token_estimate"], 13)
            self.assertEqual(state["source_user_turns"], 1)
            with database.transaction(immediate=False) as connection:
                source_ids = {
                    row[0]
                    for row in connection.execute(
                        "SELECT source_record_id FROM scope_source_event_commits"
                    )
                }
                sessions = {
                    row[0]
                    for row in connection.execute(
                        "SELECT session_id FROM scope_sessions"
                    )
                }
                view_count = connection.execute(
                    "SELECT COUNT(*) FROM memory_graph_views"
                ).fetchone()[0]
                catalog_count = connection.execute(
                    "SELECT message_count FROM scope_catalog"
                ).fetchone()[0]
            self.assertEqual(source_ids, {"source-b"})
            self.assertEqual(sessions, {"session-b"})
            self.assertEqual(view_count, 0)
            self.assertEqual(catalog_count, 1)

    def test_reindexing_deletion_can_reclaim_index_after_source_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = ControlDB(Path(directory) / "control.sqlite3")
            commercial = CommercialControl(database)
            jobs = JobStore(database)

            ingest = jobs.submit(
                "tenant-a",
                "ingest-key",
                {"job_type": "ingest", "scope_name": "scope-a"},
                scope_name="scope-a",
            )
            running_ingest = jobs.claim(ingest.job_id, "worker-ingest")
            jobs.succeed(
                ingest.job_id,
                {"ok": True},
                worker_id="worker-ingest",
                job_version=running_ingest.version,
            )
            database.record_committed_source_records(
                "tenant-a",
                "scope-a",
                ingest.job_id,
                [
                    {
                        "source_record_id": "source-a",
                        "raw_token_estimate": 11,
                        "user_turns": 1,
                    }
                ],
            )

            job_id = "job-delete-reindex"

            def register(connection: object, _keys: tuple[str, ...]) -> None:
                commercial.register_content_deletion_in_transaction(
                    connection,
                    "tenant-a",
                    "scope-a",
                    deletion_id="del-reindex",
                    job_id=job_id,
                    mode="session",
                    target_sha256="b" * 64,
                    target_count=1,
                )

            jobs.submit(
                "tenant-a",
                "delete-reindex-key",
                {
                    "job_type": "delete_session",
                    "scope_name": "scope-a",
                    "session_id": "session-a",
                    "deletion_id": "del-reindex",
                },
                scope_name="scope-a",
                requested_job_id=job_id,
                on_new_jobs=register,
            )
            running_delete = jobs.claim(job_id, "worker-delete")
            commercial.update_content_deletion(
                "tenant-a",
                "scope-a",
                "del-reindex",
                job_id,
                state="reindexing",
                result={"deleted": True},
            )
            commercial.apply_content_deletion_control_cleanup(
                "tenant-a",
                "scope-a",
                deleted_source_record_ids=["source-a"],
                deleted_session_message_counts={"session-a": 1},
                deleted_session_id="session-a",
            )

            gate = database.scope_scheduler_gate(
                "tenant-a", "scope-a", candidate_job_id=job_id
            )
            self.assertTrue(gate["ready"])
            self.assertEqual(
                gate["reason_code"], "content_deletion_reindex_owner"
            )
            self.assertTrue(
                database.claim_index_job(
                    "tenant-a",
                    "scope-a",
                    job_id,
                    job_version=running_delete.version,
                )
            )


class ContentDeletionApiTests(unittest.TestCase):
    def test_delete_message_resolves_source_and_uses_audited_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(_settings(Path(directory)))
            components = app.state.components
            components.auth.set_tenant_scopes(
                "tenant-a", {"memory:read", "memory:delete"}
            )
            key = components.auth.create_key("tenant-a").api_key
            _seed_scope(components.storage, "tenant-a", "scope-a")
            client = TestClient(app)
            try:
                response = client.request(
                    "DELETE",
                    "/v1/scopes/scope-a/messages",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Idempotency-Key": "delete-message-a",
                        "X-TMCRA-Confirm-Message-Count": "1",
                    },
                    json={"message_ids": ["message-a"]},
                )
                self.assertEqual(response.status_code, 202, response.text)
                job = components.jobs.get(
                    response.json()["job_id"], tenant_id="tenant-a"
                )
                self.assertIsNotNone(job)
                self.assertEqual(job.payload["memory_ids"], ["source-a"])
                self.assertEqual(job.payload["message_ids"], ["message-a"])
            finally:
                client.close()

    def test_delete_session_requires_confirmation_and_blocks_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(_settings(Path(directory)))
            components = app.state.components
            components.auth.set_tenant_scopes(
                "tenant-a", {"memory:read", "memory:delete"}
            )
            key = components.auth.create_key("tenant-a").api_key
            _seed_scope(components.storage, "tenant-a", "scope-a")
            headers = {
                "Authorization": f"Bearer {key}",
                "Idempotency-Key": "delete-session-a",
            }
            with (
                mock.patch.object(components.worker, "start"),
                mock.patch.object(components.worker, "stop"),
                TestClient(app) as client,
            ):
                mismatch = client.request(
                    "DELETE",
                    "/v1/scopes/scope-a/sessions/session-a",
                    headers={**headers, "X-TMCRA-Confirm-Session": "wrong"},
                )
                self.assertEqual(mismatch.status_code, 409)
                missing = client.request(
                    "DELETE",
                    "/v1/scopes/scope-a/sessions/missing-session",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Idempotency-Key": "delete-missing-session",
                        "X-TMCRA-Confirm-Session": "missing-session",
                    },
                )
                self.assertEqual(missing.status_code, 404, missing.text)
                self.assertEqual(
                    missing.json()["error"]["code"], "deletion_target_not_found"
                )
                self.assertIsNone(
                    components.commercial.active_content_deletion(
                        "tenant-a", "scope-a"
                    )
                )
                accepted = client.request(
                    "DELETE",
                    "/v1/scopes/scope-a/sessions/session-a",
                    headers={
                        **headers,
                        "X-TMCRA-Confirm-Session": "session-a",
                    },
                )
                self.assertEqual(accepted.status_code, 202, accepted.text)
                payload = accepted.json()
                self.assertEqual(payload["job_type"], "delete_session")
                deletion = client.get(
                    payload["deletion_status_url"].replace(
                        "https://example.invalid", ""
                    ),
                    headers={"Authorization": f"Bearer {key}"},
                )
                self.assertEqual(deletion.status_code, 200, deletion.text)
                self.assertEqual(deletion.json()["state"], "requested")
                summary = client.get(
                    "/v1/scopes/scope-a/summary",
                    headers={"Authorization": f"Bearer {key}"},
                )
                # Catalog summary itself can be absent, while all content reads
                # and writes are blocked by require_scope_active.
                self.assertIn(summary.status_code, {404, 409})


class UsageBreakdownTests(unittest.TestCase):
    def test_time_window_and_provider_group_use_recorded_ledger_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = ControlDB(Path(directory) / "control.sqlite3")
            jobs = JobStore(database)
            for call_id, provider, created_at, cost in (
                ("old", "provider-a", 100.0, 10),
                ("new-a", "provider-a", 200.0, 20),
                ("new-b", "provider-b", 220.0, 30),
            ):
                jobs.record_provider_call(
                    "tenant-a",
                    provider,
                    "model-a",
                    scope_name="scope-a",
                    call_id=call_id,
                    status="completed",
                    input_tokens=10,
                    output_tokens=2,
                    total_tokens=12,
                    cost_micro_cny=cost,
                    usage_state="complete",
                )
                with database.transaction() as connection:
                    connection.execute(
                        "UPDATE provider_calls SET created_at=? WHERE call_id=?",
                        (created_at, call_id),
                    )
            summary = jobs.usage_cost_summary(
                "tenant-a",
                scope_name="scope-a",
                from_timestamp=150.0,
                to_timestamp=250.0,
                group_by="provider",
            )
            self.assertEqual(summary["calls"]["registered_call_count"], 2)
            self.assertEqual(summary["calls"]["known_cost_micro_cny"], 50)
            self.assertEqual(
                summary["source_ledger_coverage"], "operation_commits_only"
            )
            self.assertEqual(summary["quota_events"]["recall_requests"], 0)
            self.assertEqual(
                summary["quota_event_scope_coverage"]["recall_requests"],
                "scope_attributed_since_usage_attribution_v1",
            )
            buckets = {row["key"]: row for row in summary["buckets"]}
            self.assertEqual(buckets["provider-a"]["known_cost_micro_cny"], 20)
            self.assertEqual(buckets["provider-b"]["known_cost_micro_cny"], 30)

    def test_time_window_source_totals_use_committed_operation_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = ControlDB(Path(directory) / "control.sqlite3")
            jobs = JobStore(database)
            database.record_committed_source_events(
                "tenant-a",
                "scope-a",
                1,
                operation_id="old-ingest",
                new_message_count=1,
                raw_token_estimate=100,
                user_turns=1,
                ingested_at=100.0,
            )
            database.record_committed_source_events(
                "tenant-a",
                "scope-a",
                2,
                operation_id="new-ingest",
                new_message_count=1,
                raw_token_estimate=250,
                user_turns=1,
                ingested_at=200.0,
            )
            summary = jobs.usage_cost_summary(
                "tenant-a",
                scope_name="scope-a",
                from_timestamp=150.0,
                to_timestamp=250.0,
            )
            self.assertEqual(
                summary["source"],
                {
                    "scope_count": 1,
                    "ingested_raw_token_estimate": 250,
                    "ingested_user_turns": 1,
                    "source_event_count": 1,
                },
            )


if __name__ == "__main__":
    unittest.main()
