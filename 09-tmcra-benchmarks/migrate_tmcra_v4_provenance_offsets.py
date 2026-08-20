#!/usr/bin/env python3
"""Add exact offsets to uniquely grounded Fast provenance entries.

The migration is dry-run by default. It never calls a model and changes only
Fast metadata whose provenance quote occurs exactly once in its declared local
immutable Source record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any


MIGRATION_VERSION = "tmcra-v4-provenance-offsets-2026-07-15.2"
REPORT_SCHEMA = "tmcra.v4.provenance-offset-migration.1"
JOURNAL_SCHEMA = "tmcra.v4.graph-repair-journal.1"
CURRENT_FAST_VARIANT = "product_semantic_memory"
CURRENT_FAST_NODE_KIND = "atomic_user_assertion"


class MigrationError(RuntimeError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decode_metadata(value: Any, *, label: str) -> dict[str, Any]:
    try:
        metadata = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as exc:
        raise MigrationError(f"{label}: metadata is invalid JSON: {exc}") from exc
    if not isinstance(metadata, dict):
        raise MigrationError(f"{label}: metadata must be an object")
    return metadata


def _is_fast(metadata: Mapping[str, Any]) -> bool:
    return (
        metadata.get("memory_layer") == "fast"
        and metadata.get("content_variant") == CURRENT_FAST_VARIANT
        and metadata.get("node_kind") == CURRENT_FAST_NODE_KIND
        and metadata.get("atomic_evidence_leaf") is True
        and metadata.get("authority") == "user_assertion"
    )


def _source_content(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    if (
        metadata.get("content_variant") != "source_message"
        or metadata.get("node_kind") != "immutable_source_message"
    ):
        raise MigrationError(
            f"{row.get('scope_id')}/{row.get('memory_id')}: provenance target is not immutable Source"
        )
    raw_content = metadata.get("raw_content")
    if not isinstance(raw_content, str) or not raw_content:
        raise MigrationError(
            f"{row.get('scope_id')}/{row.get('memory_id')}: immutable Source lacks raw_content"
        )
    if metadata.get("source_span") != raw_content or metadata.get("source_turn_text") != raw_content:
        raise MigrationError(
            f"{row.get('scope_id')}/{row.get('memory_id')}: immutable Source text fields disagree"
        )
    return raw_content


def _entries(value: Any, *, label: str) -> tuple[list[dict[str, Any]], str]:
    if value in (None, [], {}):
        return [], "array"
    if isinstance(value, Mapping):
        return [dict(value)], "object"
    if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
        return [dict(item) for item in value], "array"
    raise MigrationError(f"{label}: provenance must be an object or object array")


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _journal_evidence_offsets(
    connection: sqlite3.Connection,
    *,
    tables: set[str],
    provenance: Mapping[str, Any],
    source_content: str,
    quote: str,
    label: str,
) -> tuple[int, int] | None:
    if "v4_batch_journal" not in tables:
        return None
    batch_id = str(provenance.get("batch_id") or "")
    message_id = str(provenance.get("message_id") or "")
    evidence_span_id = str(provenance.get("evidence_span_id") or "")
    if not batch_id or not message_id or not evidence_span_id:
        return None
    row = connection.execute(
        "SELECT status,response_json FROM v4_batch_journal WHERE batch_id=?",
        (batch_id,),
    ).fetchone()
    if row is None:
        return None
    if str(row[0]) != "committed":
        raise MigrationError(f"{label}: provenance batch is not committed")
    try:
        response = json.loads(row[1])
    except (TypeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"{label}: committed batch response is invalid JSON") from exc
    messages = response.get("messages") if isinstance(response, Mapping) else None
    if not isinstance(messages, list):
        raise MigrationError(f"{label}: committed batch response lacks messages")
    matching_messages = [
        message
        for message in messages
        if isinstance(message, Mapping)
        and str(message.get("message_id") or "") == message_id
    ]
    if len(matching_messages) != 1:
        raise MigrationError(
            f"{label}: committed batch response does not identify one message"
        )
    v3 = matching_messages[0].get("v3")
    assertions = v3.get("assertions") if isinstance(v3, Mapping) else None
    if not isinstance(assertions, list):
        raise MigrationError(f"{label}: committed batch message lacks assertions")
    candidates: set[tuple[int, int]] = set()
    for assertion in assertions:
        if not isinstance(assertion, Mapping):
            continue
        if str(assertion.get("evidence_span_id") or "") != evidence_span_id:
            continue
        if assertion.get("evidence_quote") != quote:
            continue
        start = assertion.get("evidence_char_start")
        end = assertion.get("evidence_char_end")
        if isinstance(start, bool) or isinstance(end, bool):
            raise MigrationError(f"{label}: committed batch offsets are invalid")
        try:
            offset = (int(start), int(end))
        except (TypeError, ValueError) as exc:
            raise MigrationError(f"{label}: committed batch offsets are invalid") from exc
        if (
            offset[0] < 0
            or offset[1] < offset[0]
            or source_content[offset[0] : offset[1]] != quote
        ):
            raise MigrationError(
                f"{label}: committed batch offsets do not match immutable Source"
            )
        candidates.add(offset)
    if not candidates:
        return None
    if len(candidates) != 1:
        raise MigrationError(f"{label}: committed batch span has conflicting offsets")
    return next(iter(candidates))


def migrate_database(path: Path, *, apply: bool) -> dict[str, Any]:
    path = Path(path).resolve()
    if not path.is_file():
        raise MigrationError(f"database does not exist: {path}")
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        tables = _tables(connection)
        if "records" not in tables:
            raise MigrationError(f"{path}: records table is missing")
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT scope_id,memory_id,value,state,turn_index,metadata_json FROM records"
            )
        ]
        records: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
        for row in rows:
            key = (str(row.get("scope_id") or ""), str(row.get("memory_id") or ""))
            if not all(key) or key in records:
                raise MigrationError(f"{path}: record identity is missing or duplicated: {key}")
            records[key] = (
                row,
                _decode_metadata(row.get("metadata_json"), label=f"{path}:{key}"),
            )

        updates: list[tuple[str, str, str, str, int, str]] = []
        provenance_count = 0
        already_complete_count = 0
        journal_disambiguated_count = 0
        for (scope_id, memory_id), (row, metadata) in records.items():
            if not _is_fast(metadata):
                continue
            entries, original_shape = _entries(
                metadata.get("provenance"),
                label=f"{path}:{scope_id}/{memory_id}",
            )
            changed_count = 0
            for index, provenance in enumerate(entries):
                provenance_count += 1
                declared_scope = str(provenance.get("scope_id") or "")
                declared_db = str(provenance.get("db_path") or "")
                if declared_scope and declared_scope != scope_id:
                    raise MigrationError(
                        f"{path}:{scope_id}/{memory_id}: provenance {index} crosses scope"
                    )
                if declared_db and Path(declared_db).resolve() != path:
                    raise MigrationError(
                        f"{path}:{scope_id}/{memory_id}: provenance {index} crosses database"
                    )
                source_id = str(provenance.get("source_record_id") or "")
                source_pair = records.get((scope_id, source_id))
                if source_pair is None:
                    raise MigrationError(
                        f"{path}:{scope_id}/{memory_id}: provenance {index} Source is missing"
                    )
                source_row, source_metadata = source_pair
                content = _source_content(source_row, source_metadata)
                if str(provenance.get("message_id") or "") != str(
                    source_metadata.get("message_id") or ""
                ):
                    raise MigrationError(
                        f"{path}:{scope_id}/{memory_id}: provenance {index} message identity differs"
                    )
                if provenance.get("source_turn_index") is not None:
                    try:
                        same_turn = int(provenance["source_turn_index"]) == int(
                            source_row.get("turn_index")
                        )
                    except (TypeError, ValueError):
                        same_turn = False
                    if not same_turn:
                        raise MigrationError(
                            f"{path}:{scope_id}/{memory_id}: provenance {index} turn identity differs"
                        )
                quote = provenance.get("evidence_quote")
                if not isinstance(quote, str) or not quote:
                    raise MigrationError(
                        f"{path}:{scope_id}/{memory_id}: provenance {index} quote is missing"
                    )
                label = f"{path}:{scope_id}/{memory_id}: provenance {index}"
                start = content.find(quote)
                if start < 0:
                    raise MigrationError(f"{label} quote is not exact Source text")
                if content.find(quote, start + 1) < 0:
                    inferred = (start, start + len(quote))
                else:
                    journal_offsets = _journal_evidence_offsets(
                        connection,
                        tables=tables,
                        provenance=provenance,
                        source_content=content,
                        quote=quote,
                        label=label,
                    )
                    if journal_offsets is None:
                        raise MigrationError(
                            f"{label} quote is not unique exact Source text and "
                            "committed batch evidence cannot disambiguate it"
                        )
                    inferred = journal_offsets
                    journal_disambiguated_count += 1
                old_start = provenance.get("source_char_start")
                old_end = provenance.get("source_char_end")
                if old_start is None and old_end is None:
                    provenance["source_char_start"], provenance["source_char_end"] = inferred
                    changed_count += 1
                elif old_start is None or old_end is None:
                    raise MigrationError(
                        f"{path}:{scope_id}/{memory_id}: provenance {index} offsets are incomplete"
                    )
                else:
                    if isinstance(old_start, bool) or isinstance(old_end, bool):
                        raise MigrationError(
                            f"{path}:{scope_id}/{memory_id}: provenance {index} offsets are invalid"
                        )
                    try:
                        persisted = (int(old_start), int(old_end))
                    except (TypeError, ValueError) as exc:
                        raise MigrationError(
                            f"{path}:{scope_id}/{memory_id}: provenance {index} offsets are invalid"
                        ) from exc
                    if persisted != inferred:
                        raise MigrationError(
                            f"{path}:{scope_id}/{memory_id}: provenance {index} offsets do not match quote"
                        )
                    already_complete_count += 1
            if changed_count:
                before = _canonical_json(metadata)
                metadata["provenance"] = entries[0] if original_shape == "object" else entries
                after = _canonical_json(metadata)
                updates.append(
                    (
                        after,
                        scope_id,
                        memory_id,
                        _sha(before),
                        changed_count,
                        str(row.get("metadata_json") or ""),
                    )
                )

        before_digest = _sha(
            _canonical_json(
                [
                    (scope, memory_id, before_hash)
                    for _, scope, memory_id, before_hash, _, _ in updates
                ]
            )
        )
        after_digest = _sha(
            _canonical_json(
                [
                    (scope, memory_id, _sha(metadata_json))
                    for metadata_json, scope, memory_id, _, _, _ in updates
                ]
            )
        )
        result = {
            "database": str(path),
            "record_count": len(rows),
            "provenance_count": provenance_count,
            "changed_record_count": len(updates),
            "added_offset_count": sum(item[4] for item in updates),
            "already_complete_count": already_complete_count,
            "journal_disambiguated_count": journal_disambiguated_count,
            "rollback_record_count": len(updates),
            "before_digest": before_digest,
            "after_digest": after_digest,
            "applied": bool(apply and updates),
        }
        if apply and updates:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS v4_graph_repair_journal(
                        repair_id TEXT PRIMARY KEY,
                        schema_version TEXT NOT NULL,
                        migration_version TEXT NOT NULL,
                        applied_at TEXT NOT NULL,
                        changed_record_count INTEGER NOT NULL,
                        changed_provenance_count INTEGER NOT NULL,
                        before_digest TEXT NOT NULL,
                        after_digest TEXT NOT NULL,
                        report_json TEXT NOT NULL
                    )
                    """
                )
                existing = connection.execute(
                    "SELECT after_digest FROM v4_graph_repair_journal WHERE repair_id=?",
                    (MIGRATION_VERSION,),
                ).fetchone()
                if existing is not None:
                    raise MigrationError(
                        f"{path}: migration journal exists but metadata still needs changes"
                    )
                for metadata_json, scope_id, memory_id, _, _, _ in updates:
                    cursor = connection.execute(
                        "UPDATE records SET metadata_json=? WHERE scope_id=? AND memory_id=?",
                        (metadata_json, scope_id, memory_id),
                    )
                    if cursor.rowcount != 1:
                        raise MigrationError(
                            f"{path}:{scope_id}/{memory_id}: update did not target one record"
                        )
                journal_report = {
                    **result,
                    "rollback_schema_version": "tmcra.v4.provenance-offset-rollback.1",
                    "rollback_records": [
                        {
                            "scope_id": scope_id,
                            "memory_id": memory_id,
                            "before_metadata_json": before_metadata_json,
                            "before_canonical_sha256": before_hash,
                            "after_metadata_sha256": _sha(metadata_json),
                        }
                        for (
                            metadata_json,
                            scope_id,
                            memory_id,
                            before_hash,
                            _,
                            before_metadata_json,
                        ) in updates
                    ],
                }
                connection.execute(
                    "INSERT INTO v4_graph_repair_journal VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        MIGRATION_VERSION,
                        JOURNAL_SCHEMA,
                        MIGRATION_VERSION,
                        time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        len(updates),
                        result["added_offset_count"],
                        before_digest,
                        after_digest,
                        _canonical_json(journal_report),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return result


def migrate_run(run_dir: Path, *, apply: bool) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    databases = sorted(run_dir.rglob("native_memory.sqlite3"))
    if not databases:
        raise MigrationError(f"no worker databases found under {run_dir}")
    results = [migrate_database(path, apply=apply) for path in databases]
    return {
        "schema_version": REPORT_SCHEMA,
        "migration_version": MIGRATION_VERSION,
        "status": "complete",
        "mode": "apply" if apply else "dry_run",
        "run_dir": str(run_dir),
        "database_count": len(results),
        "changed_database_count": sum(int(row["changed_record_count"] > 0) for row in results),
        "changed_record_count": sum(row["changed_record_count"] for row in results),
        "added_offset_count": sum(row["added_offset_count"] for row in results),
        "physical_api_calls": 0,
        "databases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add exact offsets to uniquely grounded Fast provenance"
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        report = migrate_run(args.run_dir, apply=args.apply)
    except (MigrationError, OSError, sqlite3.Error) as exc:
        report = {
            "schema_version": REPORT_SCHEMA,
            "migration_version": MIGRATION_VERSION,
            "status": "failed",
            "mode": "apply" if args.apply else "dry_run",
            "physical_api_calls": 0,
            "error": str(exc),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
