"""Repair the V4 graph-policy overwrite of completed keep_parallel decisions.

The migration is deliberately API-free. It only accepts a completed Pro
reconciliation job whose selected active leaf was superseded by the new leaf
from that same job. Every mutation is journaled in SQLite and mirrored to a
JSONL audit artifact.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "tmcra.v4.keep-parallel-migration.1"
MIGRATION_VERSION = "keep-parallel-authority-2026-07-12.1"
ACTIVE_STATES = {"active", "parallel_active", "promoted"}
AUTO_SUPERSESSION_REASONS = {
    "same_state_revision",
    "slot_disallows_parallel",
    "v4_reconciliation_replace_current",
}
VALID_DOWNSTREAM_REASONS = {
    *AUTO_SUPERSESSION_REASONS,
    "v4_reconciliation_replace_current",
}


class MigrationError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _json(value: Any, *, path: str) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"{path}: invalid JSON: {exc}") from exc


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


@dataclass
class Record:
    scope_id: str
    memory_id: str
    slot_key: str
    turn_index: int
    state: str
    supersedes: list[str]
    metadata: dict[str, Any]

    @property
    def key(self) -> tuple[str, str]:
        return self.scope_id, self.memory_id

    def snapshot(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "memory_id": self.memory_id,
            "slot_key": self.slot_key,
            "turn_index": self.turn_index,
            "state": self.state,
            "supersedes": list(self.supersedes),
            "superseded_by": _text(self.metadata.get("superseded_by")),
            "superseded_reason": _text(self.metadata.get("superseded_reason")),
            "conflict_action": _text(self.metadata.get("conflict_action")),
            "conflict_reason": _text(self.metadata.get("conflict_reason")),
        }


@dataclass(frozen=True)
class InvalidEdge:
    job_id: str
    scope_id: str
    message_id: str
    assertion_index: int
    selected_memory_id: str
    resolved_memory_id: str
    incoming_memory_id: str
    prior_state: str
    original_reason: str
    assertion_binding_mode: str


def _load_records(connection: sqlite3.Connection) -> dict[tuple[str, str], Record]:
    records: dict[tuple[str, str], Record] = {}
    for row in connection.execute(
        "SELECT scope_id,memory_id,slot_key,turn_index,state,supersedes_json,metadata_json FROM records"
    ):
        metadata = _json(row[6], path=f"records[{row[1]}].metadata_json")
        supersedes = _json(row[5], path=f"records[{row[1]}].supersedes_json")
        if not isinstance(metadata, dict) or not isinstance(supersedes, list):
            raise MigrationError(f"records[{row[1]}]: malformed graph JSON")
        record = Record(
            scope_id=_text(row[0]),
            memory_id=_text(row[1]),
            slot_key=_text(row[2]),
            turn_index=int(row[3]),
            state=_text(row[4]),
            supersedes=[_text(item) for item in supersedes if _text(item)],
            metadata=dict(metadata),
        )
        records[record.key] = record
    return records


def _load_historical_bindings(worker_dir: Path) -> dict[str, dict[str, Any]]:
    path = worker_dir / "product_writer_historical_binding_recoveries.jsonl"
    result: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return result
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = _json(line, path=f"{path}:{line_number}")
        if not isinstance(value, dict) or not _text(value.get("job_id")):
            raise MigrationError(f"{path}:{line_number}: invalid historical binding")
        job_id = _text(value["job_id"])
        if job_id in result:
            raise MigrationError(f"{path}: duplicate historical job {job_id}")
        result[job_id] = value
    return result


def _prior_state(request: Mapping[str, Any], selected_memory_id: str, old: Record) -> str:
    candidates = request.get("candidate_cited_leaves")
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            if _text(candidate.get("memory_id")) != selected_memory_id:
                continue
            state = _text(candidate.get("record_state"))
            if state in ACTIVE_STATES:
                return state
    if _text(old.metadata.get("reconciliation_decision")) == "keep_parallel":
        return "parallel_active"
    return "active"


def _discover_invalid_edges(
    connection: sqlite3.Connection,
    records: Mapping[tuple[str, str], Record],
    historical: Mapping[str, Mapping[str, Any]],
) -> tuple[list[InvalidEdge], int]:
    result: list[InvalidEdge] = []
    scanned = 0
    rows = connection.execute(
        "SELECT job_id,scope_id,message_id,assertion_index,request_json,response_json "
        "FROM v4_reconciliation_jobs "
        "WHERE status='completed' AND decision='keep_parallel' ORDER BY created_at,job_id"
    )
    for row in rows:
        scanned += 1
        job_id = _text(row[0])
        scope_id = _text(row[1])
        message_id = _text(row[2])
        assertion_index = int(row[3])
        request = _json(row[4], path=f"job[{job_id}].request_json")
        response = _json(row[5], path=f"job[{job_id}].response_json")
        if not isinstance(request, dict) or not isinstance(response, dict):
            raise MigrationError(f"job[{job_id}]: request/response must be objects")
        if (
            _text(response.get("slot_decision")) != "bind_existing"
            or _text(response.get("decision")) != "keep_parallel"
        ):
            raise MigrationError(f"job[{job_id}]: persisted keep_parallel contract differs")
        selected_memory_id = _text(response.get("selected_memory_id"))
        if not selected_memory_id:
            raise MigrationError(f"job[{job_id}]: selected memory ID is empty")
        resolved_memory_id = _text(
            historical.get(job_id, {}).get("resolved_memory_id")
        ) or selected_memory_id
        old = records.get((scope_id, resolved_memory_id))
        if old is None:
            raise MigrationError(
                f"job[{job_id}]: selected/resolved record is missing: {resolved_memory_id}"
            )
        if old.state != "superseded":
            continue
        incoming_memory_id = _text(old.metadata.get("superseded_by"))
        incoming = records.get((scope_id, incoming_memory_id))
        if incoming is None:
            continue
        incoming_index = int(incoming.metadata.get("llm_write_proposal_index", -1))
        cited = request.get("new_cited_assertion")
        exact_reindexed_identity = (
            isinstance(cited, Mapping)
            and incoming.slot_key == _text(request.get("canonical_slot_key"))
            and _text(incoming.metadata.get("source_span"))
            == _text(cited.get("evidence_quote"))
            and _text(incoming.metadata.get("memory_type"))
            == _text(cited.get("memory_type"))
            and _text(incoming.metadata.get("entity_key"))
            == _text(cited.get("entity_key"))
            and _text(incoming.metadata.get("attribute_key"))
            == _text(cited.get("attribute_key"))
        )
        if incoming_index == assertion_index:
            assertion_binding_mode = "original_assertion_index"
        elif exact_reindexed_identity:
            assertion_binding_mode = "exact_evidence_identity_after_commit_reindex"
        else:
            assertion_binding_mode = ""
        is_same_job_overwrite = (
            _text(incoming.metadata.get("message_id")) == message_id
            and _text(incoming.metadata.get("reconciliation_decision"))
            == "keep_parallel"
            and bool(assertion_binding_mode)
        )
        if not is_same_job_overwrite:
            continue
        reason = _text(old.metadata.get("superseded_reason"))
        if (
            reason not in AUTO_SUPERSESSION_REASONS
            or old.slot_key != incoming.slot_key
            or old.turn_index > incoming.turn_index
            or old.memory_id not in incoming.supersedes
        ):
            raise MigrationError(
                f"job[{job_id}]: cannot prove same-job graph-policy overwrite"
            )
        result.append(
            InvalidEdge(
                job_id=job_id,
                scope_id=scope_id,
                message_id=message_id,
                assertion_index=assertion_index,
                selected_memory_id=selected_memory_id,
                resolved_memory_id=resolved_memory_id,
                incoming_memory_id=incoming_memory_id,
                prior_state=_prior_state(request, selected_memory_id, old),
                original_reason=reason,
                assertion_binding_mode=assertion_binding_mode,
            )
        )
    by_old: dict[tuple[str, str], InvalidEdge] = {}
    for edge in result:
        key = edge.scope_id, edge.resolved_memory_id
        previous = by_old.get(key)
        if previous is not None and previous != edge:
            raise MigrationError(
                f"multiple keep_parallel jobs claim the same overwritten leaf: {key}"
            )
        by_old[key] = edge
    return result, scanned


def _downstream_lifecycle(
    edge: InvalidEdge,
    records: Mapping[tuple[str, str], Record],
    invalid_by_old: Mapping[tuple[str, str], InvalidEdge],
) -> tuple[str, str, str]:
    old = records[(edge.scope_id, edge.resolved_memory_id)]
    cursor = records[(edge.scope_id, edge.incoming_memory_id)]
    seen: set[str] = set()
    while cursor.state == "superseded":
        if cursor.memory_id in seen:
            raise MigrationError(f"job[{edge.job_id}]: supersession cycle detected")
        seen.add(cursor.memory_id)
        next_id = _text(cursor.metadata.get("superseded_by"))
        reason = _text(cursor.metadata.get("superseded_reason"))
        next_record = records.get((edge.scope_id, next_id))
        if next_record is None:
            raise MigrationError(
                f"job[{edge.job_id}]: downstream supersession target is missing"
            )
        invalid = invalid_by_old.get(cursor.key)
        if invalid is not None and invalid.incoming_memory_id == next_id:
            cursor = next_record
            continue
        if (
            reason not in VALID_DOWNSTREAM_REASONS
            or cursor.slot_key != old.slot_key
            or next_record.slot_key != old.slot_key
            or cursor.turn_index > next_record.turn_index
            or _text(cursor.metadata.get("state_signature"))
            != _text(old.metadata.get("state_signature"))
        ):
            raise MigrationError(
                f"job[{edge.job_id}]: downstream lifecycle cannot be propagated"
            )
        return "superseded", next_id, reason
    if cursor.state not in ACTIVE_STATES:
        raise MigrationError(
            f"job[{edge.job_id}]: keep_parallel chain ends in non-active state {cursor.state!r}"
        )
    return edge.prior_state, "", ""


def _migration_id(job_id: str) -> str:
    return hashlib.sha256(f"{MIGRATION_VERSION}:{job_id}".encode("utf-8")).hexdigest()[:32]


def _journal_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS v4_keep_parallel_migrations(
            migration_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL UNIQUE,
            scope_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            incoming_record_id TEXT NOT NULL,
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            artifact_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def _write_artifacts(worker_dir: Path, connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT artifact_json FROM v4_keep_parallel_migrations "
        "WHERE status='completed' ORDER BY created_at,job_id"
    ).fetchall()
    artifacts = [
        _json(row[0], path="v4_keep_parallel_migrations.artifact_json")
        for row in rows
    ]
    path = worker_dir / "product_writer_keep_parallel_migrations.jsonl"
    content = "".join(_canonical(item) + "\n" for item in artifacts)
    if content:
        _atomic_write(path, content)
    elif path.exists():
        path.unlink()
    return artifacts


def migrate_worker(worker_dir: Path, *, apply: bool) -> dict[str, Any]:
    worker_dir = Path(worker_dir).resolve()
    database = worker_dir / "native_memory.sqlite3"
    if not database.is_file():
        raise MigrationError(f"worker database does not exist: {database}")
    historical = _load_historical_bindings(worker_dir)
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        tables = {
            _text(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {"records", "v4_reconciliation_jobs"}
        if not required.issubset(tables):
            raise MigrationError(
                f"{database}: required tables are missing: {sorted(required - tables)}"
            )
        existing_journal = "v4_keep_parallel_migrations" in tables
        records = _load_records(connection)
        edges, scanned = _discover_invalid_edges(connection, records, historical)
        if apply and edges:
            _journal_table(connection)
        invalid_by_old = {
            (edge.scope_id, edge.resolved_memory_id): edge for edge in edges
        }
        before = {key: copy.deepcopy(value) for key, value in records.items()}
        artifacts: list[dict[str, Any]] = []
        for edge in edges:
            old = records[(edge.scope_id, edge.resolved_memory_id)]
            incoming = records[(edge.scope_id, edge.incoming_memory_id)]
            next_state, downstream_id, downstream_reason = _downstream_lifecycle(
                edge, before, invalid_by_old
            )
            old.state = next_state
            if next_state in ACTIVE_STATES:
                old.metadata.pop("superseded_by", None)
                old.metadata.pop("superseded_reason", None)
            else:
                old.metadata["superseded_by"] = downstream_id
                old.metadata["superseded_reason"] = downstream_reason
                downstream = records[(edge.scope_id, downstream_id)]
                if old.memory_id not in downstream.supersedes:
                    downstream.supersedes.append(old.memory_id)
            incoming.supersedes = [
                memory_id
                for memory_id in incoming.supersedes
                if memory_id != old.memory_id
            ]
            incoming.metadata["conflict_action"] = "keep_parallel"
            incoming.metadata["conflict_reason"] = "v4_reconciliation_keep_parallel"

        changed_keys = {
            key
            for key, record in records.items()
            if record.snapshot() != before[key].snapshot()
            or record.supersedes != before[key].supersedes
            or record.metadata != before[key].metadata
        }
        for edge in edges:
            old_key = edge.scope_id, edge.resolved_memory_id
            incoming_key = edge.scope_id, edge.incoming_memory_id
            related = {old_key, incoming_key}
            downstream_id = _text(records[old_key].metadata.get("superseded_by"))
            if downstream_id:
                related.add((edge.scope_id, downstream_id))
            before_payload = {
                f"{scope}:{memory_id}": before[(scope, memory_id)].snapshot()
                for scope, memory_id in sorted(related)
            }
            after_payload = {
                f"{scope}:{memory_id}": records[(scope, memory_id)].snapshot()
                for scope, memory_id in sorted(related)
            }
            artifact = {
                "schema_version": SCHEMA_VERSION,
                "migration_version": MIGRATION_VERSION,
                "migration_id": _migration_id(edge.job_id),
                "job_id": edge.job_id,
                "scope_id": edge.scope_id,
                "message_id": edge.message_id,
                "assertion_index": edge.assertion_index,
                "selected_memory_id": edge.selected_memory_id,
                "resolved_memory_id": edge.resolved_memory_id,
                "incoming_memory_id": edge.incoming_memory_id,
                "assertion_binding_mode": edge.assertion_binding_mode,
                "original_superseded_reason": edge.original_reason,
                "restored_state": records[old_key].state,
                "propagated_superseded_by": _text(
                    records[old_key].metadata.get("superseded_by")
                ),
                "propagated_superseded_reason": _text(
                    records[old_key].metadata.get("superseded_reason")
                ),
                "before_sha256": _sha(before_payload),
                "after_sha256": _sha(after_payload),
                "physical_api_calls": 0,
                "status": "completed",
                "migrated_at": _now(),
            }
            artifacts.append(artifact)
            if apply:
                connection.execute(
                    "INSERT INTO v4_keep_parallel_migrations VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        artifact["migration_id"],
                        edge.job_id,
                        edge.scope_id,
                        edge.resolved_memory_id,
                        edge.incoming_memory_id,
                        _canonical(before_payload),
                        _canonical(after_payload),
                        _canonical(artifact),
                        "completed",
                        artifact["migrated_at"],
                    ),
                )
        if apply:
            for key in sorted(changed_keys):
                record = records[key]
                connection.execute(
                    "UPDATE records SET state=?,supersedes_json=?,metadata_json=? "
                    "WHERE scope_id=? AND memory_id=?",
                    (
                        record.state,
                        _canonical(record.supersedes),
                        _canonical(record.metadata),
                        record.scope_id,
                        record.memory_id,
                    ),
                )
            connection.execute("COMMIT")
            if edges or existing_journal:
                artifacts = _write_artifacts(worker_dir, connection)
        else:
            connection.execute("ROLLBACK")
        return {
            "worker_dir": str(worker_dir),
            "database": str(database),
            "apply": bool(apply),
            "keep_parallel_jobs_scanned": scanned,
            "invalid_same_job_overwrites": len(edges),
            "changed_records": len(changed_keys),
            "journaled_migrations": len(artifacts),
            "physical_api_calls": 0,
        }
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def discover_workers(run_dir: Path) -> list[Path]:
    run_dir = Path(run_dir).resolve()
    workers = sorted(
        path.parent
        for path in run_dir.glob("writer/worker_*/native_memory.sqlite3")
    )
    if not workers:
        raise MigrationError(f"no writer workers found under {run_dir}")
    return workers


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-dir", type=Path)
    group.add_argument("--worker-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    workers = (
        discover_workers(args.run_dir)
        if args.run_dir is not None
        else [args.worker_dir.resolve()]
    )
    reports = [migrate_worker(worker, apply=args.apply) for worker in workers]
    report = {
        "schema_version": SCHEMA_VERSION,
        "migration_version": MIGRATION_VERSION,
        "apply": bool(args.apply),
        "workers": reports,
        "totals": {
            key: sum(int(item[key]) for item in reports)
            for key in (
                "keep_parallel_jobs_scanned",
                "invalid_same_job_overwrites",
                "changed_records",
                "journaled_migrations",
                "physical_api_calls",
            )
        },
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        _atomic_write(args.output.resolve(), serialized)
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
