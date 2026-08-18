from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path

from tmcra_service.control_db import ControlDB
from tmcra_service.native_harness import (
    _read_only_store_class,
    _redirect_audit_persistence,
)


class _FakeIntegratedStore:
    @contextmanager
    def _managed_connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def record_count(self) -> int:
        with self._managed_connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM records").fetchone()[0])

    def attempt_write(self) -> None:
        with self._managed_connection() as connection:
            connection.execute(
                "INSERT INTO records(scope_id, memory_id) VALUES ('scope', 'new')"
            )


class _FakeGraph:
    def __init__(self) -> None:
        self.retrieval_log: list[dict[str, object]] = []
        self.answer_support_log: list[dict[str, object]] = []
        self.audit_event_totals = {
            "retrieval_log": 0,
            "answer_support_log": 0,
        }
        self.audit_trimmed_counts = {
            "retrieval_log": 0,
            "answer_support_log": 0,
        }


class _FakeAdapter:
    def __init__(self) -> None:
        self.scope_id = "tmcra_v4:scope"
        self.audit_retention = 2
        self.graph = _FakeGraph()

    def _reload_graph(self) -> None:
        self.graph = _FakeGraph()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _graph_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE records(scope_id TEXT, memory_id TEXT);
            CREATE TABLE memory_edges(scope_id TEXT);
            CREATE TABLE slot_heads(scope_id TEXT);
            CREATE TABLE slot_history(scope_id TEXT);
            CREATE TABLE meta(scope_id TEXT);
            INSERT INTO records(scope_id, memory_id) VALUES ('scope', 'one');
            """
        )


class NativeHarnessReadOnlyTests(unittest.TestCase):
    def test_production_graph_store_never_mutates_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            _graph_database(database)
            before = _sha256(database)

            store_class = _read_only_store_class(_FakeIntegratedStore)
            store = store_class(database)

            self.assertEqual(store.record_count(), 1)
            self.assertEqual(_sha256(database), before)
            self.assertFalse(Path(str(database) + "-wal").exists())
            self.assertFalse(Path(str(database) + "-shm").exists())
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                store.attempt_write()
            self.assertEqual(_sha256(database), before)

    def test_read_only_store_wrapper_is_idempotent(self) -> None:
        wrapped = _read_only_store_class(_FakeIntegratedStore)
        self.assertIs(_read_only_store_class(wrapped), wrapped)

    def test_runtime_audits_are_external_and_rehydrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = ControlDB(Path(directory) / "control.sqlite3")
            adapter = _redirect_audit_persistence(_FakeAdapter(), database)

            for number in range(1, 4):
                adapter.graph.retrieval_log.append({"query": f"q{number}"})
                persisted = adapter._persist_latest_audit("retrieval_log")
                self.assertEqual(persisted["query_id"], f"query:{number}")
                adapter._reload_graph()

            self.assertEqual(
                [item["query_id"] for item in adapter.graph.retrieval_log],
                ["query:2", "query:3"],
            )
            self.assertEqual(adapter.graph.audit_event_totals["retrieval_log"], 3)
            self.assertEqual(adapter.graph.audit_trimmed_counts["retrieval_log"], 1)


if __name__ == "__main__":
    unittest.main()
