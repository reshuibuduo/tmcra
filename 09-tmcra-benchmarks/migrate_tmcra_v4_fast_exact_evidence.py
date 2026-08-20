"""Restore exact Fast evidence quotes from committed immutable Source slices."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Any, Mapping


MIGRATION_VERSION = "tmcra-v4-fast-exact-evidence-2026-07-15.1"
JOURNAL_SCHEMA = "tmcra.v4.graph-repair-journal.1"
REPORT_SCHEMA = "tmcra.v4.fast-exact-evidence-migration.1"
FAST_VARIANT = "product_semantic_memory"
FAST_NODE_KIND = "atomic_user_assertion"


class MigrationError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decode(value: Any, *, label: str) -> dict[str, Any]:
    try:
        decoded = value if isinstance(value, Mapping) else json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"{label}: invalid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise MigrationError(f"{label}: expected JSON object")
    return dict(decoded)


def _transport_normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _is_fast(metadata: Mapping[str, Any]) -> bool:
    return (
        metadata.get("memory_layer") == "fast"
        and metadata.get("content_variant") == FAST_VARIANT
        and metadata.get("node_kind") == FAST_NODE_KIND
        and metadata.get("atomic_evidence_leaf") is True
        and metadata.get("authority") == "user_assertion"
    )


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _committed_assertion(
    connection: sqlite3.Connection,
    *,
    message_id: str,
    proposal_index: int,
    label: str,
) -> Mapping[str, Any]:
    matches: list[Mapping[str, Any]] = []
    for status, response_json in connection.execute(
        "SELECT status,response_json FROM v4_batch_journal ORDER BY batch_index"
    ):
        if str(status) != "committed":
            continue
        response = _decode(response_json, label=f"{label}: batch response")
        messages = response.get("messages")
        if not isinstance(messages, list):
            raise MigrationError(f"{label}: committed batch response lacks messages")
        for message in messages:
            if (
                not isinstance(message, Mapping)
                or str(message.get("message_id") or "") != message_id
            ):
                continue
            v3 = message.get("v3")
            assertions = v3.get("assertions") if isinstance(v3, Mapping) else None
            if not isinstance(assertions, list) or not 0 <= proposal_index < len(assertions):
                raise MigrationError(
                    f"{label}: committed assertion index is unavailable"
                )
            assertion = assertions[proposal_index]
            if not isinstance(assertion, Mapping):
                raise MigrationError(f"{label}: committed assertion is malformed")
            matches.append(assertion)
    if len(matches) != 1:
        raise MigrationError(
            f"{label}: expected exactly one committed assertion, found {len(matches)}"
        )
    return matches[0]


def migrate_database(path: Path, *, apply: bool) -> dict[str, Any]:
    path = Path(path).resolve()
    if not path.is_file():
        raise MigrationError(f"database does not exist: {path}")
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        tables = _tables(connection)
        required = {"records", "v4_batch_journal"}
        if not required.issubset(tables):
            raise MigrationError(
                f"{path}: missing required tables: {sorted(required - tables)}"
            )
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT scope_id,memory_id,value,state,turn_index,metadata_json "
                "FROM records"
            )
        ]
        records: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
        for row in rows:
            scope_id = str(row.get("scope_id") or "")
            memory_id = str(row.get("memory_id") or "")
            key = (scope_id, memory_id)
            if not scope_id or not memory_id or key in records:
                raise MigrationError(f"{path}: invalid record identity: {key}")
            records[key] = (
                row,
                _decode(
                    row.get("metadata_json"),
                    label=f"{path}:{scope_id}/{memory_id}",
                ),
            )

        updates: list[tuple[str, str, str, str, str]] = []
        fast_record_count = 0
        exact_record_count = 0
        for (scope_id, memory_id), (row, metadata) in records.items():
            if not _is_fast(metadata):
                continue
            fast_record_count += 1
            label = f"{path}:{scope_id}/{memory_id}"
            source_id = str(metadata.get("source_record_id") or "")
            source_pair = records.get((scope_id, source_id))
            if source_pair is None:
                raise MigrationError(f"{label}: immutable Source is missing")
            source_row, source_metadata = source_pair
            if (
                source_metadata.get("content_variant") != "source_message"
                or source_metadata.get("node_kind") != "immutable_source_message"
            ):
                raise MigrationError(f"{label}: source parent is not immutable Source")
            source_content = source_metadata.get("raw_content")
            if not isinstance(source_content, str) or not source_content:
                raise MigrationError(f"{label}: immutable Source lacks raw_content")
            if (
                source_metadata.get("source_span") != source_content
                or source_metadata.get("source_turn_text") != source_content
                or str(metadata.get("message_id") or "")
                != str(source_metadata.get("message_id") or "")
            ):
                raise MigrationError(f"{label}: Source identity or text fields disagree")
            start = metadata.get("evidence_char_start")
            end = metadata.get("evidence_char_end")
            if isinstance(start, bool) or isinstance(end, bool):
                raise MigrationError(f"{label}: evidence offsets are invalid")
            try:
                start_value, end_value = int(start), int(end)
            except (TypeError, ValueError) as exc:
                raise MigrationError(f"{label}: evidence offsets are invalid") from exc
            if (
                start_value < 0
                or end_value <= start_value
                or end_value > len(source_content)
            ):
                raise MigrationError(f"{label}: evidence offsets are out of bounds")
            exact_quote = source_content[start_value:end_value]
            stored_quote = metadata.get("evidence_quote")
            if not isinstance(stored_quote, str) or not stored_quote:
                raise MigrationError(f"{label}: evidence quote is missing")
            if stored_quote == exact_quote:
                exact_record_count += 1
                continue
            if (
                metadata.get("raw_content") != exact_quote
                or metadata.get("source_span") != exact_quote
                or metadata.get("source_turn_text") != source_content
                or _transport_normalized(stored_quote)
                != _transport_normalized(exact_quote)
            ):
                raise MigrationError(
                    f"{label}: mismatch is not a proven transport normalization"
                )
            try:
                proposal_index = int(metadata.get("llm_write_proposal_index"))
            except (TypeError, ValueError) as exc:
                raise MigrationError(f"{label}: proposal index is invalid") from exc
            assertion = _committed_assertion(
                connection,
                message_id=str(metadata.get("message_id") or ""),
                proposal_index=proposal_index,
                label=label,
            )
            if (
                assertion.get("evidence_quote") != exact_quote
                or int(assertion.get("evidence_char_start", -1)) != start_value
                or int(assertion.get("evidence_char_end", -1)) != end_value
            ):
                raise MigrationError(
                    f"{label}: committed assertion does not prove exact Source quote"
                )
            before = str(row.get("metadata_json") or "")
            metadata["evidence_quote"] = exact_quote
            after = _canonical(metadata)
            updates.append((after, scope_id, memory_id, _sha(before), before))

        before_digest = _sha(
            _canonical(
                [(scope, memory_id, before_hash) for _, scope, memory_id, before_hash, _ in updates]
            )
        )
        after_digest = _sha(
            _canonical(
                [
                    (scope, memory_id, _sha(after))
                    for after, scope, memory_id, _, _ in updates
                ]
            )
        )
        result = {
            "database": str(path),
            "fast_record_count": fast_record_count,
            "already_exact_record_count": exact_record_count,
            "changed_record_count": len(updates),
            "rollback_record_count": len(updates),
            "before_digest": before_digest,
            "after_digest": after_digest,
            "applied": bool(apply and updates),
            "physical_api_calls": 0,
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
                    "SELECT 1 FROM v4_graph_repair_journal WHERE repair_id=?",
                    (MIGRATION_VERSION,),
                ).fetchone()
                if existing is not None:
                    raise MigrationError(
                        f"{path}: repair journal exists but records still need changes"
                    )
                for metadata_json, scope_id, memory_id, _, _ in updates:
                    cursor = connection.execute(
                        "UPDATE records SET metadata_json=? "
                        "WHERE scope_id=? AND memory_id=?",
                        (metadata_json, scope_id, memory_id),
                    )
                    if cursor.rowcount != 1:
                        raise MigrationError(
                            f"{path}:{scope_id}/{memory_id}: update did not target one record"
                        )
                journal_report = {
                    **result,
                    "schema_version": REPORT_SCHEMA,
                    "migration_version": MIGRATION_VERSION,
                    "rollback_schema_version": "tmcra.v4.fast-exact-evidence-rollback.1",
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
                        0,
                        before_digest,
                        after_digest,
                        _canonical(journal_report),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {
        "schema_version": REPORT_SCHEMA,
        "migration_version": MIGRATION_VERSION,
        "mode": "apply" if args.apply else "dry_run",
        **migrate_database(args.database, apply=args.apply),
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.resolve().write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
