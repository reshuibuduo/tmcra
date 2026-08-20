from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tmcra_service.provider_pool import ProviderKeyPool
from tmcra_service.writer import (
    IdentityRegistry,
    LeasedDeepSeekClient,
    ProductionWriterError,
    main,
)


class FakeMessage:
    def __init__(self, **values):
        self.__dict__.update(values)


class FakeBatch:
    def __init__(self, **values):
        self.__dict__.update(values)


FAKE_V4 = SimpleNamespace(SourceMessage=FakeMessage, SourceBatch=FakeBatch)


class ProductionIdentityTests(unittest.TestCase):
    def test_production_main_uses_control_db_for_both_ledger_clients(self) -> None:
        instances: list[dict[str, object]] = []

        class FakeLedgerClient:
            def __init__(self, **kwargs: object) -> None:
                instances.append(kwargs)

        class FakeWriter:
            def __init__(self, **_: object) -> None:
                pass

            def run(self, _: object) -> dict[str, str]:
                return {"status": "complete"}

        fake_v4 = SimpleNamespace(
            BATCH_SCHEMA_VERSION="test-batch",
            CANDIDATE_SELECTOR_VERSION="test",
            PROMPT_VERSION="test-prompt",
            V4BatchStore=lambda _: object(),
            RealGraphFactory=lambda **_: object(),
            V4BatchWriter=FakeWriter,
            _json=json.dumps,
            build_batches=lambda *_args, **_kwargs: [],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            input_path.write_text(
                json.dumps(
                    [
                        {
                            "scope_id": "tmcra_v4:svc_abc",
                            "session_id": "session-a",
                            "operation_id": "job-a",
                            "messages": [],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            control_db = root / "control.sqlite3"
            database = root / "native_memory.sqlite3"
            env = {
                "TMCRA_WRITER_BASE_URL": "https://provider.invalid",
                "TMCRA_WRITER_MODEL": "deepseek-v4-flash",
                "TMCRA_WRITER_API_KEY_POOL": "secret-value",
                "TMCRA_SERVICE_CONTROL_DB": str(control_db),
                "TMCRA_SERVICE_TENANT_ID": "tenant-a",
                "TMCRA_SERVICE_SCOPE_NAME": "scope-a",
                "TMCRA_SERVICE_JOB_ID": "job-a",
                "TMCRA_SERVICE_STAGE_ID": "job-a:writer",
            }
            argv = [
                "writer",
                "--input",
                str(input_path),
                "--out-dir",
                str(root / "out"),
                "--database",
                str(database),
                "--operation-id",
                "job-a",
                "--repo",
                str(root),
            ]
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                sys, "argv", argv
            ), mock.patch.dict(
                sys.modules, {"tmcra_v4_batch_writer": fake_v4}
            ), mock.patch(
                "tmcra_service.writer.LeasedDeepSeekClient", FakeLedgerClient
            ):
                self.assertEqual(main(), 0)

        self.assertEqual(len(instances), 2)
        self.assertEqual({item["ledger_database"] for item in instances}, {control_db})
        self.assertEqual(
            {
                (item["tenant_id"], item["scope_name"], item["job_id"], item["stage_id"])
                for item in instances
            },
            {("tenant-a", "scope-a", "job-a", "job-a:writer")},
        )

    def test_provider_calls_are_written_to_control_db_not_scope_db(self) -> None:
        class FakeClient:
            def __init__(self, **_: object) -> None:
                pass

            def complete(self, _: object) -> tuple[str, dict[str, object]]:
                return "response", {
                    "physical_call_id": "physical-control-1",
                    "stage": "batch_flash",
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "prompt_cache_hit_tokens": 4,
                    },
                }

            reconcile = complete

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control_db = root / "control.sqlite3"
            scope_db = root / "native_memory.sqlite3"
            IdentityRegistry(scope_db, "job-a")
            pool = ProviderKeyPool(
                control_db, pool="deepseek-writer", keys=["secret-value"]
            )
            client = LeasedDeepSeekClient(
                v4=SimpleNamespace(DeepSeekBatchClient=FakeClient),
                pool=pool,
                operation_id="job-a",
                base_url="https://provider.invalid",
                model="deepseek-v4-flash",
                timeout=1.0,
                max_tokens=32,
                ledger_database=control_db,
                tenant_id="tenant-a",
                scope_name="scope-a",
                job_id="job-a",
                stage_id="job-a:writer",
            )
            client.complete({"prompt": "not persisted"})
            with closing(sqlite3.connect(control_db)) as connection:
                provider_call = connection.execute(
                    "SELECT tenant_id, scope_name, job_id, stage_id, status "
                    "FROM provider_calls WHERE call_id=?",
                    ("physical-control-1",),
                ).fetchone()
            with closing(sqlite3.connect(scope_db)) as connection:
                scope_tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }

        self.assertEqual(
            provider_call,
            ("tenant-a", "scope-a", "job-a", "job-a:writer", "completed"),
        )
        self.assertNotIn("provider_calls", scope_tables)

    def test_production_main_fails_closed_without_ledger_identity(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            sys,
            "argv",
            [
                "writer",
                "--input",
                "input.json",
                "--out-dir",
                "out",
                "--database",
                "memory.sqlite3",
                "--operation-id",
                "job-a",
                "--repo",
                ".",
            ],
        ):
            with self.assertRaises(ProductionWriterError):
                main()

    def test_incremental_messages_and_batches_receive_stable_distinct_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            first = IdentityRegistry(database, "job-1")
            rows = [
                {
                    "scope_id": "tmcra_v4:svc_abc",
                    "session_id": "session-a",
                    "operation_id": "job-1",
                    "messages": [
                        {
                            "message_id": "message-1",
                            "role": "user",
                            "timestamp": "2026-07-14T00:00:00Z",
                            "content": "first",
                        }
                    ],
                }
            ]
            messages, exclusions = first.register_messages(rows, v4=FAKE_V4)
            self.assertEqual(exclusions, [])
            self.assertEqual(first.new_message_count, 1)
            self.assertEqual(first.replayed_message_count, 0)
            self.assertEqual(messages[0].message_index, 0)
            self.assertEqual(messages[0].message_id, "s000_m000")
            self.assertNotEqual(messages[0].message_id, "message-1")
            local = FakeBatch(
                scope_id=messages[0].scope_id,
                session_id=messages[0].session_id,
                session_index=messages[0].session_index,
                batch_index=0,
                messages=tuple(messages),
            )
            first_batch = first.remap_batches([local], v4=FAKE_V4)[0]
            replay_batch = first.remap_batches([local], v4=FAKE_V4)[0]
            self.assertEqual(first_batch.batch_index, replay_batch.batch_index)

            second = IdentityRegistry(database, "job-2")
            rows[0]["operation_id"] = "job-2"
            rows[0]["messages"][0] = {
                "message_id": "message-2",
                "role": "assistant",
                "timestamp": "2026-07-14T00:00:01Z",
                "content": "second",
            }
            messages2, _ = second.register_messages(rows, v4=FAKE_V4)
            self.assertEqual(second.new_message_count, 1)
            self.assertEqual(second.replayed_message_count, 0)
            self.assertEqual(messages2[0].message_index, 1)
            self.assertEqual(messages2[0].message_id, "s000_m001")
            self.assertNotEqual(messages[0].message_id, messages2[0].message_id)
            local2 = FakeBatch(
                scope_id=messages2[0].scope_id,
                session_id=messages2[0].session_id,
                session_index=messages2[0].session_index,
                batch_index=0,
                messages=tuple(messages2),
            )
            second_batch = second.remap_batches([local2], v4=FAKE_V4)[0]
            self.assertEqual(second_batch.batch_index, first_batch.batch_index + 1)

            replay, _ = second.register_messages([rows[0]], v4=FAKE_V4)
            self.assertEqual(replay[0].message_id, "s000_m001")
            self.assertEqual(second.new_message_count, 1)
            self.assertEqual(second.replayed_message_count, 1)
            with closing(sqlite3.connect(database)) as connection:
                persisted = connection.execute(
                    "SELECT message_id, internal_message_id "
                    "FROM tmcra_service_messages ORDER BY message_index"
                ).fetchall()
            self.assertEqual(
                persisted,
                [("message-1", "s000_m000"), ("message-2", "s000_m001")],
            )

    def test_legacy_message_rows_are_migrated_to_internal_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.executescript(
                    """
                    CREATE TABLE tmcra_service_sessions (
                        scope_id TEXT NOT NULL, session_id TEXT NOT NULL,
                        session_index INTEGER NOT NULL,
                        PRIMARY KEY(scope_id, session_id),
                        UNIQUE(scope_id, session_index)
                    );
                    CREATE TABLE tmcra_service_messages (
                        scope_id TEXT NOT NULL, message_id TEXT NOT NULL,
                        session_id TEXT NOT NULL, message_index INTEGER NOT NULL,
                        role TEXT NOT NULL, timestamp TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        PRIMARY KEY(scope_id, message_id),
                        UNIQUE(scope_id, session_id, message_index)
                    );
                    INSERT INTO tmcra_service_sessions VALUES
                        ('tmcra_v4:svc_abc', 'session-a', 0);
                    INSERT INTO tmcra_service_messages VALUES
                        ('tmcra_v4:svc_abc', 'legacy-message', 'session-a', 0,
                        'user', '2026-07-14T00:00:00Z', 'legacy-hash');
                    """
                )
                connection.execute(
                    "UPDATE tmcra_service_messages SET content_sha256=?",
                    (hashlib.sha256(b"legacy").hexdigest(),),
                )
            registry = IdentityRegistry(database, "job-1")
            rows = [
                {
                    "scope_id": "tmcra_v4:svc_abc",
                    "session_id": "session-a",
                    "operation_id": "job-1",
                    "messages": [
                        {
                            "message_id": "legacy-message",
                            "role": "user",
                            "timestamp": "2026-07-14T00:00:00Z",
                            "content": "legacy",
                        }
                    ],
                }
            ]
            messages, _ = registry.register_messages(rows, v4=FAKE_V4)
            self.assertEqual(messages[0].message_id, "s000_m000")
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT message_id, internal_message_id "
                        "FROM tmcra_service_messages"
                    ).fetchone(),
                    ("legacy-message", "s000_m000"),
                )

    def test_replayed_message_cannot_change_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            registry = IdentityRegistry(database, "job-1")
            row = {
                "scope_id": "tmcra_v4:svc_abc",
                "session_id": "session-a",
                "operation_id": "job-1",
                "messages": [
                    {
                        "message_id": "message-1",
                        "role": "user",
                        "timestamp": "2026-07-14T00:00:00Z",
                        "content": "first",
                    }
                ],
            }
            registry.register_messages([row], v4=FAKE_V4)
            row["messages"][0]["content"] = "changed"
            with self.assertRaises(ProductionWriterError):
                registry.register_messages([row], v4=FAKE_V4)

    def test_new_message_report_counts_only_non_replay_turns_and_local_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = IdentityRegistry(Path(directory) / "memory.sqlite3", "job-1")
            row = {
                "scope_id": "tmcra_v4:svc_abc",
                "session_id": "session-a",
                "operation_id": "job-1",
                "messages": [
                    {
                        "message_id": "message-1",
                        "role": "user",
                        "timestamp": "2026-07-14T00:00:00Z",
                        "content": "你好 abc",
                    },
                    {
                        "message_id": "message-2",
                        "role": "assistant",
                        "timestamp": "2026-07-14T00:00:01Z",
                        "content": "abcd",
                    },
                ],
            }
            registry.register_messages([row], v4=FAKE_V4)
            registry.register_messages([row], v4=FAKE_V4)
            self.assertEqual(registry.new_message_count, 2)
            self.assertEqual(registry.replayed_message_count, 2)
            self.assertEqual(registry.new_user_turn_count, 1)
            self.assertEqual(registry.new_raw_token_estimate, 4)


if __name__ == "__main__":
    unittest.main()
