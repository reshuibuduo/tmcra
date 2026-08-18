"""Durable local ingest outbox for LangGraph retries."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Mapping


@dataclass(frozen=True)
class PendingIngest:
    key: str
    scope_name: str
    request: Mapping[str, Any]
    status: str = "pending"
    job_id: str | None = None
    status_url: str | None = None


class JsonOutbox:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser()
        self._lock = RLock()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"TMCRA outbox is unreadable: {self.path}") from exc
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RuntimeError(f"TMCRA outbox has invalid shape: {self.path}")
        return rows

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

    def enqueue(self, key: str, scope_name: str, request: Mapping[str, Any]) -> PendingIngest:
        with self._lock:
            rows = self._read()
            encoded = dict(request)
            for row in rows:
                if row.get("key") == key:
                    if row.get("scope_name") != scope_name or row.get("request") != encoded:
                        raise ValueError("idempotency key is already bound to a different request")
                    return PendingIngest(key, scope_name, row["request"], row.get("status", "pending"), row.get("job_id"), row.get("status_url"))
            rows.append({"key": key, "scope_name": scope_name, "request": encoded, "status": "pending"})
            self._write(rows)
            return PendingIngest(key, scope_name, encoded)

    def pending(self) -> list[PendingIngest]:
        with self._lock:
            return [PendingIngest(row["key"], row["scope_name"], row["request"], row.get("status", "pending"), row.get("job_id"), row.get("status_url")) for row in self._read()]

    def mark_submitted(self, key: str, result: Any) -> None:
        job_id = result.get("job_id") if isinstance(result, Mapping) else getattr(result, "job_id", None)
        status_url = result.get("status_url") if isinstance(result, Mapping) else getattr(result, "status_url", None)
        with self._lock:
            rows = self._read()
            for row in rows:
                if row.get("key") == key:
                    row.update(status="submitted", job_id=job_id, status_url=status_url)
                    self._write(rows)
                    return
            raise KeyError(key)

    def acknowledge(self, key: str) -> None:
        with self._lock:
            self._write([row for row in self._read() if row.get("key") != key])
