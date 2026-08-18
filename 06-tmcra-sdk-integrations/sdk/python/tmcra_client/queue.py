"""Small SQLite-backed queue for recoverable lifecycle ingests.

The queue stores the exact validated ingest payload and its idempotency key. It
does not invent retries for terminal failures; it only lets a later process
reconcile a submission whose response was lost or whose process exited.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload_hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _payload_identity(payload: Any) -> Any:
    """Exclude volatile message timestamps from retry identity."""

    if isinstance(payload, dict):
        return {
            key: _payload_identity(value)
            for key, value in payload.items()
            if key != "timestamp"
        }
    if isinstance(payload, list):
        return [_payload_identity(value) for value in payload]
    return payload


@dataclass(frozen=True)
class QueueEntry:
    idempotency_key: str
    scope_name: str
    session_id: str
    payload: dict[str, Any]
    message_ids: tuple[str, ...]
    payload_hash: str
    job_id: str | None
    submitted_status: str | None
    final_status: str | None
    status_url: str | None
    source_event_seq: int | None
    promoted_event_seq: int | None
    indexed_event_seq: int | None
    error: dict[str, Any] | None
    updated_at: float


class DurableLifecycleQueue:
    """A process-safe, single-file queue for automatic lifecycle turns."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        raw_path = os.fspath(path)
        if not raw_path:
            raise ValueError("durable_queue_path must not be empty")
        self.path = Path(raw_path).expanduser()
        self._memory_connection: sqlite3.Connection | None = None
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if str(self.path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
            connection = self._memory_connection
        else:
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS lifecycle_ingest_queue (
                    idempotency_key TEXT PRIMARY KEY,
                    scope_name TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    message_ids_json TEXT NOT NULL,
                    job_id TEXT,
                    submitted_status TEXT,
                    final_status TEXT,
                    status_url TEXT,
                    source_event_seq INTEGER,
                    promoted_event_seq INTEGER,
                    indexed_event_seq INTEGER,
                    error_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def close(self) -> None:
        """Close the optional in-memory connection; file operations are short-lived."""

        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None

    def _row_to_entry(self, row: sqlite3.Row) -> QueueEntry:
        return QueueEntry(
            idempotency_key=row["idempotency_key"],
            scope_name=row["scope_name"],
            session_id=row["session_id"],
            payload=json.loads(row["payload_json"]),
            message_ids=tuple(json.loads(row["message_ids_json"])),
            payload_hash=row["payload_hash"],
            job_id=row["job_id"],
            submitted_status=row["submitted_status"],
            final_status=row["final_status"],
            status_url=row["status_url"],
            source_event_seq=row["source_event_seq"],
            promoted_event_seq=row["promoted_event_seq"],
            indexed_event_seq=row["indexed_event_seq"],
            error=json.loads(row["error_json"]) if row["error_json"] else None,
            updated_at=float(row["updated_at"]),
        )

    def get(self, idempotency_key: str) -> QueueEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM lifecycle_ingest_queue WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self._row_to_entry(row) if row else None

    def upsert(
        self,
        *,
        idempotency_key: str,
        scope_name: str,
        session_id: str,
        payload: dict[str, Any],
        message_ids: tuple[str, ...],
    ) -> QueueEntry:
        payload_json = _canonical_json(payload)
        payload_hash = _payload_hash(
            _canonical_json(_payload_identity(payload))
        )
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM lifecycle_ingest_queue WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row:
                if row["payload_hash"] != payload_hash:
                    raise ValueError(
                        "idempotency_key is already bound to a different lifecycle payload"
                    )
                return self._row_to_entry(row)
            connection.execute(
                """
                INSERT INTO lifecycle_ingest_queue (
                    idempotency_key, scope_name, session_id, payload_json,
                    payload_hash, message_ids_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idempotency_key,
                    scope_name,
                    session_id,
                    payload_json,
                    payload_hash,
                    _canonical_json(list(message_ids)),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM lifecycle_ingest_queue WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        assert row is not None
        return self._row_to_entry(row)

    def mark_submitted(
        self,
        idempotency_key: str,
        *,
        job_id: str,
        submitted_status: str,
        status_url: str | None,
    ) -> QueueEntry:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE lifecycle_ingest_queue
                SET job_id = ?, submitted_status = ?, status_url = ?, updated_at = ?
                WHERE idempotency_key = ?
                """,
                (job_id, submitted_status, status_url, time.time(), idempotency_key),
            )
        entry = self.get(idempotency_key)
        if entry is None:
            raise RuntimeError("lifecycle queue entry disappeared while marking submission")
        return entry

    def mark_terminal(
        self,
        idempotency_key: str,
        *,
        final_status: str,
        status_url: str | None,
        source_event_seq: int | None,
        promoted_event_seq: int | None,
        indexed_event_seq: int | None,
        error: dict[str, Any] | None,
    ) -> QueueEntry:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE lifecycle_ingest_queue
                SET final_status = ?, status_url = ?, source_event_seq = ?,
                    promoted_event_seq = ?, indexed_event_seq = ?, error_json = ?,
                    updated_at = ?
                WHERE idempotency_key = ?
                """,
                (
                    final_status,
                    status_url,
                    source_event_seq,
                    promoted_event_seq,
                    indexed_event_seq,
                    _canonical_json(error) if error is not None else None,
                    time.time(),
                    idempotency_key,
                ),
            )
        entry = self.get(idempotency_key)
        if entry is None:
            raise RuntimeError("lifecycle queue entry disappeared while marking terminal state")
        return entry

    def active(self) -> list[QueueEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM lifecycle_ingest_queue
                WHERE final_status IS NULL
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [self._row_to_entry(row) for row in rows]
