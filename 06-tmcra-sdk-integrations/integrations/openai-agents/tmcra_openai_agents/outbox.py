"""Small process-safe-enough JSON outbox for the OpenAI Agents adapter.

The adapter deliberately keeps this queue local to the host application. It
stores the exact request and idempotency key, never a new key on retry.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Mapping


@dataclass(frozen=True)
class OutboxRecord:
    key: str
    scope_name: str
    request: Mapping[str, Any]
    status: str = "pending"
    job_id: str | None = None
    status_url: str | None = None


class JsonOutbox:
    """Atomic JSON outbox used before the remote ingest request is sent."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser()
        self._lock = RLock()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"TMCRA outbox is unreadable: {self.path}") from exc
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise RuntimeError(f"TMCRA outbox has invalid shape: {self.path}")
        return value

    def _write(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(rows, stream, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def enqueue(self, key: str, scope_name: str, request: Mapping[str, Any]) -> OutboxRecord:
        with self._lock:
            rows = self._read()
            encoded = dict(request)
            for row in rows:
                if row.get("key") == key:
                    if row.get("scope_name") != scope_name or row.get("request") != encoded:
                        raise ValueError("idempotency key is already bound to a different request")
                    return OutboxRecord(**{name: row.get(name) for name in OutboxRecord.__dataclass_fields__})
            row = {"key": key, "scope_name": scope_name, "request": encoded, "status": "pending"}
            rows.append(row)
            self._write(rows)
            return OutboxRecord(key=key, scope_name=scope_name, request=encoded)

    def pending(self) -> list[OutboxRecord]:
        with self._lock:
            return [
                OutboxRecord(**{name: row.get(name) for name in OutboxRecord.__dataclass_fields__})
                for row in self._read()
                if row.get("status") in {"pending", "submitted"}
            ]

    def mark_submitted(self, key: str, result: Any) -> OutboxRecord | None:
        job_id = result.get("job_id") if isinstance(result, Mapping) else getattr(result, "job_id", None)
        status_url = result.get("status_url") if isinstance(result, Mapping) else getattr(result, "status_url", None)
        with self._lock:
            rows = self._read()
            for row in rows:
                if row.get("key") == key:
                    row.update(status="submitted", job_id=job_id, status_url=status_url)
                    self._write(rows)
                    return OutboxRecord(**{name: row.get(name) for name in OutboxRecord.__dataclass_fields__})
        return None

    def acknowledge(self, key: str) -> None:
        with self._lock:
            rows = [row for row in self._read() if row.get("key") != key]
            self._write(rows)
