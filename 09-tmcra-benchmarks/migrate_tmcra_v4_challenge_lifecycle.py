"""Repair graph-policy supersession caused by a completed challenge decision."""

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


SCHEMA_VERSION = "tmcra.v4.challenge-lifecycle-migration.1"
MIGRATION_VERSION = "challenge-lifecycle-authority-2026-07-15.1"
ACTIVE_STATES = {"active", "parallel_active", "promoted"}
AUTO_SUPERSESSION_REASONS = {
    "same_state_revision",
    "slot_disallows_parallel",
    "v4_reconciliation_replace_current",
}


class MigrationError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _json(value: Any, *, label: str) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"{label}: invalid JSON") from exc


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
class InvalidChallenge:
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
        "SELECT scope_id,memory_id,slot_key,turn_index,state,supersedes_json,metadata_json "
        "FROM records"
    ):
        metadata = _json(row[6], label=f"records[{row[1]}].metadata_json")
        supersedes = _json(row[5], label=f"records[{row[1]}].supersedes_json")
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
        if not all(record.key) or record.key in records:
            raise MigrationError(f"invalid record identity: {record.key}")
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
        value = _json(line, label=f"{path}:{line_number}")
        if not isinstance(value, dict) or not _text(value.get("job_id")):
            raise MigrationError(f"{path}:{line_number}: invalid historical binding")
        job_id = _text(value["job_id"])
        if job_id in result:
            raise MigrationError(f"{path}: duplicate historical job {job_id}")
        result[job_id] = value
    return result


def _prior_state(request: Mapping[str, Any], selected_memory_id: str) -> str:
    candidates = request.get("candidate_cited_leaves")
    if isinstance(candidates, list):
        for candidate in candidates:
            if (
                isinstance(candidate, Mapping)
                and _text(candidate.get("memory_id")) == selected_memory_id
                and _text(candidate.get("record_state")) in ACTIVE_STATES
            ):
                return _text(candidate.get("record_state"))
    raise MigrationError("challenge request does not preserve selected prior state")


def _assertion_binding_mode(
    record: Record,
    request: Mapping[str, Any],
    assertion_index: int,
) -> str:
    cited = request.get("new_cited_assertion")
    exact_reindexed_identity = (
        isinstance(cited, Mapping)
        and record.slot_key == _text(request.get("canonical_slot_key"))
        and _text(record.metadata.get("source_span"))
        == _text(cited.get("evidence_quote"))
        and _text(record.metadata.get("memory_type"))
        == _text(cited.get("memory_type"))
        and _text(record.metadata.get("entity_key"))
        == _text(cited.get("entity_key"))
        and _text(record.metadata.get("attribute_key"))
        == _text(cited.get("attribute_key"))
    )
    if int(record.metadata.get("llm_write_proposal_index", -1)) == assertion_index:
        return "original_assertion_index"
    if exact_reindexed_identity:
        return "exact_evidence_identity_after_commit_reindex"
    return ""


def _find_job_record(
    records: Mapping[tuple[str, str], Record],
    *,
    scope_id: str,
    message_id: str,
    decision: str,
    request: Mapping[str, Any],
    assertion_index: int,
    job_id: str,
) -> tuple[Record, str]:
    candidates = []
    for record in records.values():
        if (
            record.scope_id == scope_id
            and _text(record.metadata.get("message_id")) == message_id
            and _text(record.metadata.get("reconciliation_decision")) == decision
        ):
            binding_mode = _assertion_binding_mode(
                record, request, assertion_index
            )
            if binding_mode:
                candidates.append((record, binding_mode))
    if len(candidates) != 1:
        raise MigrationError(
            f"job[{job_id}]: expected one exact {decision} record, "
            f"found {len(candidates)}"
        )
    return candidates[0]


def _has_proven_later_replace_current(
    connection: sqlite3.Connection,
    records: Mapping[tuple[str, str], Record],
    *,
    challenge_job_id: str,
    scope_id: str,
    old: Record,
    challenge_incoming: Record,
    later: Record,
) -> bool:
    if (
        later.slot_key != old.slot_key
        or later.turn_index < challenge_incoming.turn_index
        or later.state not in ACTIVE_STATES
        or old.memory_id not in later.supersedes
        or _text(later.metadata.get("reconciliation_decision"))
        != "replace_current"
        or challenge_incoming.memory_id not in later.supersedes
        or _text(challenge_incoming.metadata.get("superseded_by"))
        != later.memory_id
    ):
        return False
    later_message_id = _text(later.metadata.get("message_id"))
    if not later_message_id:
        return False
    matches = 0
    for row in connection.execute(
        "SELECT job_id,assertion_index,request_json,response_json "
        "FROM v4_reconciliation_jobs WHERE scope_id=? AND message_id=? "
        "AND status='completed' AND decision='replace_current'",
        (scope_id, later_message_id),
    ):
        job_id = _text(row[0])
        if job_id == challenge_job_id:
            continue
        request = _json(row[2], label=f"job[{job_id}].request_json")
        response = _json(row[3], label=f"job[{job_id}].response_json")
        if not isinstance(request, Mapping) or not isinstance(response, Mapping):
            raise MigrationError(
                f"job[{job_id}]: later replacement request/response malformed"
            )
        if (
            _text(response.get("slot_decision")) == "bind_existing"
            and _text(response.get("decision")) == "replace_current"
            and _text(response.get("selected_memory_id")) == old.memory_id
            and _assertion_binding_mode(later, request, int(row[1]))
        ):
            matches += 1
    return matches == 1


def _discover_invalid(
    connection: sqlite3.Connection,
    records: Mapping[tuple[str, str], Record],
    historical: Mapping[str, Mapping[str, Any]],
) -> tuple[list[InvalidChallenge], int]:
    invalid: list[InvalidChallenge] = []
    scanned = 0
    for row in connection.execute(
        "SELECT job_id,scope_id,message_id,assertion_index,request_json,response_json "
        "FROM v4_reconciliation_jobs WHERE status='completed' AND decision='challenge' "
        "ORDER BY created_at,job_id"
    ):
        scanned += 1
        job_id = _text(row[0])
        scope_id = _text(row[1])
        message_id = _text(row[2])
        assertion_index = int(row[3])
        request = _json(row[4], label=f"job[{job_id}].request_json")
        response = _json(row[5], label=f"job[{job_id}].response_json")
        if not isinstance(request, dict) or not isinstance(response, dict):
            raise MigrationError(f"job[{job_id}]: request/response must be objects")
        if (
            _text(response.get("slot_decision")) != "bind_existing"
            or _text(response.get("decision")) != "challenge"
        ):
            raise MigrationError(f"job[{job_id}]: persisted challenge contract differs")
        selected_memory_id = _text(response.get("selected_memory_id"))
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
        incoming, binding_mode = _find_job_record(
            records,
            scope_id=scope_id,
            message_id=message_id,
            decision="challenge",
            request=request,
            assertion_index=assertion_index,
            job_id=job_id,
        )
        incoming_memory_id = _text(old.metadata.get("superseded_by"))
        actual_superseder = records.get((scope_id, incoming_memory_id))
        if actual_superseder is None:
            raise MigrationError(
                f"job[{job_id}]: selected record is superseded without a target"
            )
        reason = _text(old.metadata.get("superseded_reason"))
        if incoming.memory_id != incoming_memory_id:
            if reason not in AUTO_SUPERSESSION_REASONS or not (
                _has_proven_later_replace_current(
                    connection,
                    records,
                    challenge_job_id=job_id,
                    scope_id=scope_id,
                    old=old,
                    challenge_incoming=incoming,
                    later=actual_superseder,
                )
            ):
                raise MigrationError(
                    f"job[{job_id}]: cannot prove later authoritative supersession"
                )
            continue
        if (
            _text(incoming.metadata.get("message_id")) != message_id
            or _text(incoming.metadata.get("reconciliation_decision")) != "challenge"
            or not binding_mode
            or incoming.state != "challenged"
            or reason not in AUTO_SUPERSESSION_REASONS
            or old.slot_key != incoming.slot_key
            or old.turn_index > incoming.turn_index
            or old.memory_id not in incoming.supersedes
        ):
            raise MigrationError(
                f"job[{job_id}]: cannot prove same-job challenge overwrite"
            )
        invalid.append(
            InvalidChallenge(
                job_id=job_id,
                scope_id=scope_id,
                message_id=message_id,
                assertion_index=assertion_index,
                selected_memory_id=selected_memory_id,
                resolved_memory_id=resolved_memory_id,
                incoming_memory_id=incoming_memory_id,
                prior_state=_prior_state(request, selected_memory_id),
                original_reason=reason,
                assertion_binding_mode=binding_mode,
            )
        )
    return invalid, scanned


def _migration_id(job_id: str) -> str:
    return hashlib.sha256(f"{MIGRATION_VERSION}:{job_id}".encode("utf-8")).hexdigest()[:32]


def _journal_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS v4_challenge_lifecycle_migrations(
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
        "SELECT artifact_json FROM v4_challenge_lifecycle_migrations "
        "WHERE status='completed' ORDER BY created_at,job_id"
    ).fetchall()
    artifacts = [
        _json(row[0], label="v4_challenge_lifecycle_migrations.artifact_json")
        for row in rows
    ]
    path = worker_dir / "product_writer_challenge_lifecycle_migrations.jsonl"
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
        required = {"records", "slot_heads", "v4_reconciliation_jobs"}
        if not required.issubset(tables):
            raise MigrationError(
                f"{database}: required tables are missing: {sorted(required - tables)}"
            )
        existing_journal = "v4_challenge_lifecycle_migrations" in tables
        records = _load_records(connection)
        invalid, scanned = _discover_invalid(connection, records, historical)
        if apply and invalid:
            _journal_table(connection)
        before = {key: copy.deepcopy(value) for key, value in records.items()}
        before_heads: dict[tuple[str, str], str] = {}
        after_heads: dict[tuple[str, str], str] = {}
        artifacts: list[dict[str, Any]] = []
        for edge in invalid:
            old = records[(edge.scope_id, edge.resolved_memory_id)]
            incoming = records[(edge.scope_id, edge.incoming_memory_id)]
            head_row = connection.execute(
                "SELECT memory_id FROM slot_heads WHERE scope_id=? AND slot_key=?",
                (edge.scope_id, old.slot_key),
            ).fetchone()
            head_id = _text(head_row[0]) if head_row is not None else ""
            if head_id not in {"", old.memory_id, incoming.memory_id}:
                raise MigrationError(
                    f"job[{edge.job_id}]: slot head changed to unrelated record"
                )
            before_heads[(edge.scope_id, old.slot_key)] = head_id
            after_heads[(edge.scope_id, old.slot_key)] = old.memory_id
            old.state = edge.prior_state
            old.metadata.pop("superseded_by", None)
            old.metadata.pop("superseded_reason", None)
            incoming.supersedes = [
                memory_id
                for memory_id in incoming.supersedes
                if memory_id != old.memory_id
            ]
            incoming.metadata["conflict_action"] = "challenge"
            incoming.metadata["conflict_reason"] = "v4_reconciliation_challenge"

        changed_keys = {
            key
            for key, record in records.items()
            if record.snapshot() != before[key].snapshot()
            or record.supersedes != before[key].supersedes
            or record.metadata != before[key].metadata
        }
        for edge in invalid:
            related = {
                (edge.scope_id, edge.resolved_memory_id),
                (edge.scope_id, edge.incoming_memory_id),
            }
            head_key = (edge.scope_id, records[(edge.scope_id, edge.resolved_memory_id)].slot_key)
            before_payload = {
                "records": {
                    f"{scope}:{memory_id}": before[(scope, memory_id)].snapshot()
                    for scope, memory_id in sorted(related)
                },
                "slot_head": before_heads[head_key],
            }
            after_payload = {
                "records": {
                    f"{scope}:{memory_id}": records[(scope, memory_id)].snapshot()
                    for scope, memory_id in sorted(related)
                },
                "slot_head": after_heads[head_key],
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
                "restored_state": records[(edge.scope_id, edge.resolved_memory_id)].state,
                "restored_slot_head": after_heads[head_key],
                "before_sha256": _sha(before_payload),
                "after_sha256": _sha(after_payload),
                "physical_api_calls": 0,
                "status": "completed",
                "migrated_at": _now(),
            }
            artifacts.append(artifact)
            if apply:
                connection.execute(
                    "INSERT INTO v4_challenge_lifecycle_migrations VALUES(?,?,?,?,?,?,?,?,?,?)",
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
            for (scope_id, slot_key), memory_id in after_heads.items():
                connection.execute(
                    "INSERT INTO slot_heads(scope_id,slot_key,memory_id) VALUES(?,?,?) "
                    "ON CONFLICT(scope_id,slot_key) DO UPDATE SET memory_id=excluded.memory_id",
                    (scope_id, slot_key, memory_id),
                )
            connection.execute("COMMIT")
            if invalid or existing_journal:
                artifacts = _write_artifacts(worker_dir, connection)
        else:
            connection.execute("ROLLBACK")
        return {
            "worker_dir": str(worker_dir),
            "database": str(database),
            "apply": bool(apply),
            "challenge_jobs_scanned": scanned,
            "invalid_same_job_overwrites": len(invalid),
            "changed_records": len(changed_keys),
            "changed_slot_heads": len(after_heads),
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
    workers = sorted(
        path.parent
        for path in Path(run_dir).resolve().glob("writer/worker_*/native_memory.sqlite3")
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
                "challenge_jobs_scanned",
                "invalid_same_job_overwrites",
                "changed_records",
                "changed_slot_heads",
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
