from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


SAMPLE_LIMIT = 12


def connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def summarize_error(value: Any) -> str:
    text = str(value or "")
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        message = str(parsed.get("message") or "").strip()
        traceback = str(parsed.get("traceback") or "").replace("\r", "\n")
        tail = [line.strip() for line in traceback.split("\n") if line.strip()][-3:]
        return " | ".join([part for part in (message, *tail) if part])[:900]
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
    return " | ".join(lines[-4:])[:900]


def normalized_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def add_mismatch(
    counts: Counter[str],
    samples: list[dict[str, Any]],
    kind: str,
    source_record_id: str | None,
    message_id: str | None,
) -> None:
    counts[kind] += 1
    if len(samples) < SAMPLE_LIMIT:
        samples.append(
            {
                "kind": kind,
                "source_record_id": source_record_id,
                "message_id": message_id,
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-db", type=Path, required=True)
    arguments = parser.parse_args()

    connection = connect_read_only(arguments.native_db)
    quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    source_rows = list(
        connection.execute(
            "SELECT memory_id, turn_index, metadata_json FROM records "
            "WHERE lower(category) = 'source' ORDER BY memory_id"
        )
    )
    journal_rows = list(
        connection.execute(
            "SELECT scope_id, session_id, message_id, session_index, message_index, "
            "message_role, content, content_sha256, status, source_record_id, "
            "source_turn_index FROM v4_source_journal "
            "ORDER BY session_index, message_index, message_id"
        )
    )

    sources: dict[str, dict[str, Any]] = {}
    duplicate_source_ids: list[str] = []
    metadata_parse_errors = 0
    for row in source_rows:
        source_id = str(row["memory_id"])
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
            metadata_parse_errors += 1
        if source_id in sources and len(duplicate_source_ids) < SAMPLE_LIMIT:
            duplicate_source_ids.append(source_id)
        sources[source_id] = {
            "turn_index": normalized_int(row["turn_index"]),
            "metadata": metadata,
        }

    status_counts = Counter(str(row["status"] or "") for row in journal_rows)
    journal_source_ids = [
        str(row["source_record_id"])
        for row in journal_rows
        if row["source_record_id"]
    ]
    journal_source_id_counts = Counter(journal_source_ids)
    duplicate_journal_source_ids = sorted(
        source_id
        for source_id, count in journal_source_id_counts.items()
        if count > 1
    )

    source_ids = set(sources)
    journal_source_id_set = set(journal_source_ids)
    source_ids_without_journal = sorted(source_ids - journal_source_id_set)
    journal_ids_without_source = sorted(journal_source_id_set - source_ids)

    mismatch_counts: Counter[str] = Counter()
    mismatch_samples: list[dict[str, Any]] = []
    journals_missing_source_id = 0
    status_with_source_id: Counter[str] = Counter()
    for row in journal_rows:
        status = str(row["status"] or "")
        source_id = str(row["source_record_id"]) if row["source_record_id"] else None
        message_id = str(row["message_id"]) if row["message_id"] else None
        if source_id:
            status_with_source_id[status] += 1
        else:
            journals_missing_source_id += 1

        content = str(row["content"] or "")
        if sha256_text(content) != str(row["content_sha256"] or ""):
            add_mismatch(
                mismatch_counts,
                mismatch_samples,
                "journal_content_hash",
                source_id,
                message_id,
            )
        if not source_id or source_id not in sources:
            continue

        source = sources[source_id]
        metadata = source["metadata"]
        comparisons = {
            "content": (str(metadata.get("raw_content", "")), content),
            "role": (
                str(metadata.get("speaker", "")).lower(),
                str(row["message_role"] or "").lower(),
            ),
            "session_id": (
                str(metadata.get("session_id", "")),
                str(row["session_id"] or ""),
            ),
            "message_id": (
                str(metadata.get("message_id", "")),
                str(row["message_id"] or ""),
            ),
            "session_index": (
                normalized_int(metadata.get("session_index")),
                normalized_int(row["session_index"]),
            ),
            "message_index": (
                normalized_int(metadata.get("message_index")),
                normalized_int(row["message_index"]),
            ),
            "turn_index": (
                source["turn_index"],
                normalized_int(row["source_turn_index"]),
            ),
        }
        for kind, (source_value, journal_value) in comparisons.items():
            if source_value != journal_value:
                add_mismatch(
                    mismatch_counts,
                    mismatch_samples,
                    kind,
                    source_id,
                    message_id,
                )

    all_journals_enriched = bool(journal_rows) and set(status_counts) == {"enriched"}
    sets_equal = source_ids == journal_source_id_set
    failed_batches = [
        {
            "batch_id": row["batch_id"],
            "session_id": row["session_id"],
            "status": row["status"],
            "error": summarize_error(row["error"]),
            "updated_at": row["updated_at"],
        }
        for row in connection.execute(
            "SELECT batch_id, session_id, status, error, updated_at "
            "FROM v4_batch_journal WHERE status = 'failed' "
            "ORDER BY updated_at DESC LIMIT ?",
            (SAMPLE_LIMIT,),
        )
    ]
    failed_sources = [
        {
            "message_id": row["message_id"],
            "session_id": row["session_id"],
            "source_record_id": row["source_record_id"],
            "error": summarize_error(row["enrichment_error"]),
            "updated_at": row["updated_at"],
        }
        for row in connection.execute(
            "SELECT message_id, session_id, source_record_id, enrichment_error, updated_at "
            "FROM v4_source_journal WHERE status = 'failed' "
            "ORDER BY updated_at DESC LIMIT ?",
            (SAMPLE_LIMIT,),
        )
    ]
    output = {
        "quick_check": quick_check,
        "source_count": len(source_rows),
        "source_unique_id_count": len(source_ids),
        "duplicate_source_ids": duplicate_source_ids,
        "source_metadata_parse_errors": metadata_parse_errors,
        "journal_count": len(journal_rows),
        "journal_status_counts": dict(sorted(status_counts.items())),
        "journal_status_with_source_id": dict(sorted(status_with_source_id.items())),
        "journals_missing_source_id": journals_missing_source_id,
        "duplicate_journal_source_id_count": len(duplicate_journal_source_ids),
        "duplicate_journal_source_ids_sample": duplicate_journal_source_ids[:SAMPLE_LIMIT],
        "source_ids_without_journal_count": len(source_ids_without_journal),
        "source_ids_without_journal_sample": source_ids_without_journal[:SAMPLE_LIMIT],
        "journal_ids_without_source_count": len(journal_ids_without_source),
        "journal_ids_without_source_sample": journal_ids_without_source[:SAMPLE_LIMIT],
        "mismatch_counts": dict(sorted(mismatch_counts.items())),
        "mismatch_samples": mismatch_samples,
        "recent_failed_batches": failed_batches,
        "recent_failed_sources": failed_sources,
        "all_journals_enriched": all_journals_enriched,
        "source_journal_sets_equal": sets_equal,
        "retry_gate_passed": (
            quick_check == ["ok"]
            and all_journals_enriched
            and sets_equal
            and not duplicate_source_ids
            and not duplicate_journal_source_ids
            and metadata_parse_errors == 0
            and not mismatch_counts
        ),
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
