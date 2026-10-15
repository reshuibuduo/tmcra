"""Non-content revocation identities for permanently deleted service sources.

The owning scope already binds tenant + external scope. Message identity is
scope-wide, matching IdentityRegistry's primary key; changing session or ingest
idempotency key cannot reopen a deleted identity. No source text is retained.
Callers must use the same SQLite transaction as deletion / registration.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterable

SOURCE_REVOCATION_CONTRACT = "tmcra.source-identity-revocation.1"


def source_revocation_binding(message_ids: Iterable[str]) -> dict[str, object]:
    values = sorted(set(message_ids))
    digest = hashlib.sha256(json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    return {"source_revocation_contract": SOURCE_REVOCATION_CONTRACT,
            "revoked_message_count": len(values), "revoked_message_ids_sha256": digest}


def _identity(scope_id: str, kind: str, external_id: str) -> str:
    value = f"tmcra.source-revocation.1\0{scope_id}\0{kind}\0{external_id}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def initialize_source_tombstones(connection: sqlite3.Connection) -> None:
    # execute(), not executescript(): preserve the caller's deletion transaction.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS tmcra_service_source_tombstones (
            scope_id TEXT NOT NULL,
            message_id_sha256 TEXT NOT NULL,
            session_id_sha256 TEXT NOT NULL,
            deletion_job_id TEXT NOT NULL,
            deleted_at REAL NOT NULL,
            PRIMARY KEY(scope_id, message_id_sha256)
        )
    """)


def record_source_tombstones(
    connection: sqlite3.Connection, scope_id: str,
    identities: Iterable[tuple[str, str]], *, deletion_job_id: str,
) -> None:
    initialize_source_tombstones(connection)
    now = time.time()
    connection.executemany(
        "INSERT OR IGNORE INTO tmcra_service_source_tombstones "
        "(scope_id,message_id_sha256,session_id_sha256,deletion_job_id,deleted_at) "
        "VALUES (?,?,?,?,?)",
        ((scope_id, _identity(scope_id, "message", message_id),
          _identity(scope_id, "session", session_id), deletion_job_id, now)
         for message_id, session_id in identities),
    )


def source_identity_revoked(
    connection: sqlite3.Connection, scope_id: str, message_id: str,
) -> bool:
    return connection.execute(
        "SELECT 1 FROM tmcra_service_source_tombstones "
        "WHERE scope_id=? AND message_id_sha256=?",
        (scope_id, _identity(scope_id, "message", message_id)),
    ).fetchone() is not None
