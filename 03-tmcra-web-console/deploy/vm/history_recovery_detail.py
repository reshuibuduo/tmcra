#!/usr/bin/env python3
"""Read-only detail probe for TMCRA source-journal recovery planning."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-db", required=True, type=Path)
    args = parser.parse_args()

    connection = sqlite3.connect(
        f"file:{args.native_db.resolve()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        source_rows = connection.execute(
            """
            SELECT memory_id, turn_index, value, metadata_json
            FROM records
            WHERE category='source'
            ORDER BY turn_index, memory_id
            """
        ).fetchall()
        journals = connection.execute(
            "SELECT * FROM v4_source_journal ORDER BY session_index, message_index"
        ).fetchall()
        journal_by_source = {
            _text(row["source_record_id"]): row
            for row in journals
            if _text(row["source_record_id"])
        }
        journal_by_message = {
            (_text(row["session_id"]), _text(row["message_id"])): row
            for row in journals
        }

        orphan_sources: list[dict[str, Any]] = []
        for row in source_rows:
            source_id = _text(row["memory_id"])
            if source_id in journal_by_source:
                continue
            try:
                metadata = json.loads(_text(row["metadata_json"]) or "{}")
            except json.JSONDecodeError:
                metadata = {}
            key = (_text(metadata.get("session_id")), _text(metadata.get("message_id")))
            candidate = journal_by_message.get(key)
            orphan_sources.append(
                {
                    "source_record_id": source_id,
                    "turn_index": int(row["turn_index"]),
                    "session_id": key[0],
                    "message_id": key[1],
                    "message_role": _text(metadata.get("speaker")),
                    "session_index": metadata.get("session_index"),
                    "message_index": metadata.get("message_index"),
                    "content_sha256": _sha(row["value"]),
                    "candidate_journal": None
                    if candidate is None
                    else {
                        "status": _text(candidate["status"]),
                        "source_record_id": _text(candidate["source_record_id"]),
                        "source_turn_index": int(candidate["source_turn_index"] or 0),
                        "content_matches": _sha(candidate["content"]) == _sha(row["value"]),
                        "role_matches": _text(candidate["message_role"])
                        == _text(metadata.get("speaker")),
                        "session_index_matches": int(candidate["session_index"])
                        == int(metadata.get("session_index", -1)),
                        "message_index_matches": int(candidate["message_index"])
                        == int(metadata.get("message_index", -1)),
                    },
                }
            )

        missing_source = [
            row for row in journals if not _text(row["source_record_id"])
        ]
        grouped: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
        for row in missing_source:
            grouped[(_text(row["session_id"]), _text(row["status"]))].append(row)
        missing_groups = []
        for (session_id, status), rows in sorted(grouped.items()):
            indexes = sorted(int(row["message_index"]) for row in rows)
            missing_groups.append(
                {
                    "session_id": session_id,
                    "status": status,
                    "count": len(rows),
                    "message_index_min": indexes[0],
                    "message_index_max": indexes[-1],
                    "message_ids_sample": [_text(row["message_id"]) for row in rows[:8]],
                    "errors": dict(
                        Counter(_text(row["enrichment_error"]) for row in rows)
                    ),
                }
            )

        failed_batches = []
        for row in connection.execute(
            """
            SELECT batch_id, status, response_json, response_metadata_json,
                   error, updated_at
            FROM v4_batch_journal
            WHERE status='failed'
            ORDER BY updated_at
            """
        ):
            try:
                metadata = json.loads(_text(row["response_metadata_json"]) or "{}")
            except json.JSONDecodeError:
                metadata = {}
            failed_batches.append(
                {
                    "batch_id": _text(row["batch_id"]),
                    "error_type": _text(row["error"]).split(":", 1)[0],
                    "has_response": bool(_text(row["response_json"])),
                    "metadata": {
                        key: metadata.get(key)
                        for key in (
                            "status",
                            "http_status",
                            "physical_api_call",
                            "physical_api_calls",
                            "response_sha256",
                        )
                        if key in metadata
                    },
                    "updated_at": _text(row["updated_at"]),
                }
            )

        output = {
            "orphan_source_count": len(orphan_sources),
            "orphan_sources": orphan_sources,
            "journals_without_source_count": len(missing_source),
            "journals_without_source_groups": missing_groups,
            "failed_batches": failed_batches,
        }
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
