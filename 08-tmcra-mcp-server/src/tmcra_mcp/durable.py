from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .receipts import ReceiptValidationError, validate_job
from .controls import may_write, policy, control_key


RECEIPT_CONTRACT_SCHEMA = "tmcra.receipts.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str, label: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"durable queue contains invalid {label}") from exc


@dataclass(frozen=True)
class QueueItem:
    item_id: str
    state: str
    attempts: int
    receipt: dict[str, Any] | None
    last_error: str | None


class DurableIngestQueue:
    """Small local SQLite queue for accepted-but-not-yet-reconciled ingest jobs."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        configured = str(path or os.getenv("TMCRA_MCP_QUEUE_FILE", "")).strip()
        self.path = configured or str(Path.home() / ".config" / "tmcra" / "mcp-queue.sqlite3")
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max_attempts
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys=ON")
            if self.path != ":memory:":
                self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ingest_queue (
                    item_id TEXT PRIMARY KEY,
                    scope_name TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    consistency TEXT NOT NULL,
                    slow_policy TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    metadata_json TEXT NOT NULL,
                    agent_id TEXT,
                    recall_receipt_json TEXT,
                    state TEXT NOT NULL CHECK(state IN ('pending','submitted','succeeded','dead_letter')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    receipt_json TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ingest_queue_recovery
                    ON ingest_queue(state, updated_at);
                CREATE TABLE IF NOT EXISTS prepared_turns (
                    turn_id TEXT PRIMARY KEY,
                    scope_name TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    user_message_id TEXT NOT NULL,
                    user_content TEXT NOT NULL,
                    user_timestamp TEXT NOT NULL,
                    recall_receipt_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('prepared','committed')),
                    item_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def enqueue(
        self,
        *,
        scope_name: str,
        session_id: str,
        messages: list[dict[str, Any]],
        consistency: str,
        slow_policy: str,
        idempotency_key: str,
        metadata: Mapping[str, Any],
        agent_id: str | None,
        recall_receipt: Mapping[str, Any] | None = None,
    ) -> str:
        item_id = "mcp-item-" + uuid.uuid4().hex
        now = _now()
        with self._lock:
            existing = self._connection.execute(
                "SELECT item_id, scope_name, session_id, messages_json, idempotency_key "
                "FROM ingest_queue WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["scope_name"] != scope_name
                    or existing["session_id"] != session_id
                    or existing["messages_json"] != _dump(messages)
                ):
                    raise ValueError("idempotency key is already bound to a different ingest")
                return str(existing["item_id"])
            self._connection.execute(
                """
                INSERT INTO ingest_queue (
                    item_id, scope_name, session_id, messages_json, consistency,
                    slow_policy, idempotency_key, metadata_json, agent_id,
                    recall_receipt_json, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    item_id,
                    scope_name,
                    session_id,
                    _dump(messages),
                    consistency,
                    slow_policy,
                    idempotency_key,
                    _dump(dict(metadata)),
                    agent_id,
                    _dump(dict(recall_receipt)) if recall_receipt else None,
                    now,
                    now,
                ),
            )
            self._connection.commit()
        return item_id

    def prepare_turn(
        self,
        *,
        turn_id: str,
        scope_name: str,
        session_id: str,
        user_message_id: str,
        user_content: str,
        user_timestamp: str,
        recall_receipt: Mapping[str, Any],
    ) -> None:
        now = _now()
        with self._lock:
            existing = self._connection.execute(
                "SELECT scope_name, session_id, user_message_id, user_content, "
                "recall_receipt_json FROM prepared_turns WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["scope_name"] != scope_name
                    or existing["session_id"] != session_id
                    or existing["user_message_id"] != user_message_id
                    or existing["user_content"] != user_content
                    or existing["recall_receipt_json"] != _dump(dict(recall_receipt))
                ):
                    raise ValueError("turn_id is already bound to a different prepared turn")
                return
            self._connection.execute(
                """
                INSERT INTO prepared_turns (
                    turn_id, scope_name, session_id, user_message_id, user_content,
                    user_timestamp, recall_receipt_json, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?)
                """,
                (
                    turn_id,
                    scope_name,
                    session_id,
                    user_message_id,
                    user_content,
                    user_timestamp,
                    _dump(dict(recall_receipt)),
                    now,
                    now,
                ),
            )
            self._connection.commit()

    def commit_turn(
        self,
        *,
        turn_id: str,
        assistant_message: dict[str, Any],
        consistency: str,
        slow_policy: str,
        idempotency_key: str,
        metadata: Mapping[str, Any],
        agent_id: str | None,
    ) -> tuple[str, dict[str, Any]]:
        """Atomically create the queue item and mark a prepared turn committed."""
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT * FROM prepared_turns WHERE turn_id = ?", (turn_id,)
            ).fetchone()
            if row is None:
                self._connection.rollback()
                raise KeyError("turn_id is unknown or expired")
            if row["state"] == "committed":
                item_id = str(row["item_id"] or "")
                item = self._connection.execute(
                    "SELECT * FROM ingest_queue WHERE item_id = ?", (item_id,)
                ).fetchone()
                if item is None:
                    self._connection.rollback()
                    raise RuntimeError("committed turn has no durable queue item")
                self._connection.commit()
                return item_id, self._row_receipt(item)

            item_id = "mcp-item-" + uuid.uuid4().hex
            now = _now()
            user_message = {
                "message_id": row["user_message_id"],
                "role": "user",
                "content": row["user_content"],
                "timestamp": row["user_timestamp"],
            }
            messages = [user_message, assistant_message]
            self._connection.execute(
                """
                INSERT INTO ingest_queue (
                    item_id, scope_name, session_id, messages_json, consistency,
                    slow_policy, idempotency_key, metadata_json, agent_id,
                    recall_receipt_json, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    item_id,
                    row["scope_name"],
                    row["session_id"],
                    _dump(messages),
                    consistency,
                    slow_policy,
                    idempotency_key,
                    _dump(dict(metadata)),
                    agent_id,
                    row["recall_receipt_json"],
                    now,
                    now,
                ),
            )
            self._connection.execute(
                "UPDATE prepared_turns SET state='committed', item_id=?, updated_at=? WHERE turn_id=?",
                (item_id, now, turn_id),
            )
            self._connection.commit()
            return item_id, {
                "schema_version": "tmcra.mcp.lifecycle-turn-receipt.v1",
                "contract_schema_version": RECEIPT_CONTRACT_SCHEMA,
                "receipt_type": "lifecycle_turn",
                "turn_id": turn_id,
                "status": "submitted",
                "recall": _load(row["recall_receipt_json"], "recall receipt"),
            }

    def _row_receipt(self, row: sqlite3.Row) -> dict[str, Any]:
        receipt = _load(row["receipt_json"], "ingest receipt") if row["receipt_json"] else {}
        return {
            "schema_version": "tmcra.mcp.lifecycle-turn-receipt.v1",
            "contract_schema_version": RECEIPT_CONTRACT_SCHEMA,
            "receipt_type": "lifecycle_turn",
            "status": row["state"],
            "item_id": row["item_id"],
            "ingest": receipt,
            "recall": _load(row["recall_receipt_json"], "recall receipt")
            if row["recall_receipt_json"]
            else None,
            "error": row["last_error"],
        }

    def get(self, item_id: str) -> QueueItem:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM ingest_queue WHERE item_id = ?", (item_id,)
            ).fetchone()
        if row is None:
            raise KeyError("queue item not found")
        receipt = _load(row["receipt_json"], "ingest receipt") if row["receipt_json"] else None
        return QueueItem(item_id, row["state"], row["attempts"], receipt, row["last_error"])

    def recovery_items(self) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT item_id FROM ingest_queue WHERE state IN ('pending','submitted') ORDER BY created_at"
            ).fetchall()
        return [str(row["item_id"]) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT state, COUNT(*) AS count FROM ingest_queue GROUP BY state"
            ).fetchall()
        result = {state: 0 for state in ("pending", "submitted", "succeeded", "dead_letter")}
        result.update({str(row["state"]): int(row["count"]) for row in rows})
        return result

    def _row_for_item(self, item_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM ingest_queue WHERE item_id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise KeyError("queue item not found")
        return row

    def _update_failure(self, item_id: str, message: str) -> None:
        with self._lock:
            row = self._row_for_item(item_id)
            attempts = int(row["attempts"]) + 1
            state = "dead_letter" if attempts >= self.max_attempts else row["state"]
            self._connection.execute(
                "UPDATE ingest_queue SET attempts=?, state=?, last_error=?, updated_at=? WHERE item_id=?",
                (attempts, state, message[:2000], _now(), item_id),
            )
            self._connection.commit()

    async def drain(
        self,
        client: Any,
        *,
        item_id: str | None = None,
        wait_for_terminal: bool = True,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 1.5,
    ) -> list[dict[str, Any]]:
        ids = [item_id] if item_id else self.recovery_items()
        results: list[dict[str, Any]] = []
        for current_id in ids:
            try:
                result = await self._drain_one(
                    client,
                    current_id,
                    wait_for_terminal=wait_for_terminal,
                    timeout_seconds=timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                )
            except ReceiptValidationError:
                raise
            except Exception as exc:  # keep other durable records recoverable
                self._update_failure(current_id, str(exc))
                result = self._row_receipt(self._row_for_item(current_id))
            results.append(result)
        return results

    async def _drain_one(
        self,
        client: Any,
        item_id: str,
        *,
        wait_for_terminal: bool,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> dict[str, Any]:
        with self._lock:
            row = self._row_for_item(item_id)
            state = row["state"]
        if state == "pending":
            capture = (_load(row["recall_receipt_json"], "recall receipt") if row["recall_receipt_json"] else {}).get("_local_capture")
            settings = getattr(client, "settings", None)
            legacy_policy = policy(control_key(settings, row["scope_name"]), row["session_id"]) if not capture and settings else None
            legacy_disabled = legacy_policy and (legacy_policy["generation"] > 0 or (legacy_policy.get("parentGeneration") or 0) > 0
                or not may_write({**legacy_policy, "turnHash": None, "parentTurnHash": None}))
            if (capture and not may_write(capture)) or legacy_disabled:
                with self._lock:
                    self._connection.execute("UPDATE ingest_queue SET state='dead_letter', messages_json='[]', last_error='discarded_by_memory_mode', updated_at=? WHERE item_id=?", (_now(), item_id))
                    self._connection.commit()
                return {"status": "discarded", "item_id": item_id, "submitted": False, "final": True}
            receipt = await client.ingest(
                scope=row["scope_name"],
                session_id=row["session_id"],
                messages=_load(row["messages_json"], "messages"),
                consistency=row["consistency"],
                slow_policy=row["slow_policy"],
                idempotency_key=row["idempotency_key"],
                metadata=_load(row["metadata_json"], "metadata"),
                agent_id=row["agent_id"],
            )
            with self._lock:
                self._connection.execute(
                    "UPDATE ingest_queue SET state='submitted', receipt_json=?, updated_at=? WHERE item_id=?",
                    (_dump(receipt), _now(), item_id),
                )
                self._connection.commit()
        with self._lock:
            row = self._row_for_item(item_id)
            receipt = _load(row["receipt_json"], "ingest receipt")
        jobs = receipt["jobs"]
        if wait_for_terminal:
            final_jobs = []
            for job in jobs:
                if job["status"] in {"succeeded", "failed", "cancelled"}:
                    final_jobs.append(job)
                else:
                    final_jobs.append(
                        await client.wait_job(
                            job["job_id"],
                            timeout_seconds=timeout_seconds,
                            poll_interval_seconds=poll_interval_seconds,
                        )
                    )
            receipt = dict(receipt)
            receipt["jobs"] = [validate_job(job) for job in final_jobs]
            states = {job["status"] for job in receipt["jobs"]}
            if states <= {"succeeded"}:
                receipt["status"] = "succeeded"
            elif states <= {"cancelled"}:
                receipt["status"] = "cancelled"
            else:
                receipt["status"] = "failed"
            with self._lock:
                self._connection.execute(
                    "UPDATE ingest_queue SET state=?, receipt_json=?, updated_at=? WHERE item_id=?",
                    (receipt["status"] if receipt["status"] == "succeeded" else "dead_letter", _dump(receipt), _now(), item_id),
                )
                self._connection.commit()
        return self._row_receipt(self._row_for_item(item_id))
