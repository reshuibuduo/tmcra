from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")]


def summarize_error(value: object) -> str:
    text = str(value or "")
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        message = str(parsed.get("message") or "").strip()
        traceback = str(parsed.get("traceback") or "").replace("\r", "\n")
        tail = [line.strip() for line in traceback.split("\n") if line.strip()][-3:]
        return " | ".join([part for part in (message, *tail) if part])[:600]
    normalized = text.replace("\r", "\n")
    first_line = next(
        (line.strip() for line in normalized.split("\n") if line.strip()), ""
    )
    return first_line[:600]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-db", type=Path, required=True)
    parser.add_argument("--native-db", type=Path)
    parser.add_argument("--scope", required=True)
    arguments = parser.parse_args()

    connection = connect_read_only(arguments.control_db.resolve())
    tables = {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    relevant = [
        name
        for name in (
            "jobs",
            "scope_ingest_watermark_commits",
            "scope_evolution_state",
            "scope_registry",
            "scopes",
        )
        if name in tables
    ]
    output: dict[str, object] = {
        "scope": arguments.scope,
        "tables": {name: table_columns(connection, name) for name in relevant},
    }

    if "jobs" in tables:
        columns = table_columns(connection, "jobs")
        scope_column = next(
            (name for name in ("scope", "scope_name", "scope_id") if name in columns),
            None,
        )
        state_column = next(
            (name for name in ("status", "state") if name in columns),
            None,
        )
        if scope_column and state_column:
            output["job_counts"] = {
                str(row["job_state"]): int(row["count"])
                for row in connection.execute(
                    f"SELECT {state_column} AS job_state, COUNT(*) AS count FROM jobs "
                    f"WHERE {scope_column} = ? GROUP BY {state_column} ORDER BY {state_column}",
                    (arguments.scope,),
                )
            }
            selected = [
                name
                for name in (
                    "job_id",
                    state_column,
                    "scope_seq",
                    "worker_id",
                    "heartbeat_at",
                    "lease_expires_at",
                    "updated_at",
                    "started_at",
                    "finished_at",
                )
                if name in columns
            ]
            order_columns = [name for name in ("scope_seq", "created_at") if name in columns]
            order_clause = f" ORDER BY {', '.join(order_columns)}" if order_columns else ""
            output["active_jobs"] = [
                dict(row)
                for row in connection.execute(
                    f"SELECT {', '.join(selected)} FROM jobs WHERE {scope_column} = ? "
                    f"AND {state_column} IN ('pending', 'running') "
                    f"{order_clause}",
                    (arguments.scope,),
                )
            ]
            if "error" in columns:
                failed_selected = [
                    name
                    for name in (
                        "job_id",
                        "scope_seq",
                        "updated_at",
                        "finished_at",
                        "error",
                    )
                    if name in columns
                ]
                failed_rows = list(
                    connection.execute(
                        f"SELECT {', '.join(failed_selected)} FROM jobs "
                        f"WHERE {scope_column} = ? AND {state_column} = 'failed' "
                        f"{order_clause}",
                        (arguments.scope,),
                    )
                )
                failed_summaries: dict[str, int] = {}
                for row in failed_rows:
                    summary = summarize_error(row["error"])
                    failed_summaries[summary] = failed_summaries.get(summary, 0) + 1
                output["failed_job_summaries"] = failed_summaries
                output["recent_failed_jobs"] = [
                    {
                        key: summarize_error(value) if key == "error" else value
                        for key, value in dict(row).items()
                    }
                    for row in failed_rows[-10:]
                ]

    for table in ("scope_ingest_watermark_commits", "scope_evolution_state"):
        if table not in tables:
            continue
        columns = table_columns(connection, table)
        scope_column = next(
            (name for name in ("scope", "scope_name", "scope_id") if name in columns),
            None,
        )
        if scope_column:
            output[table] = [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} WHERE {scope_column} = ?",
                    (arguments.scope,),
                )
            ]

    if arguments.native_db:
        native = connect_read_only(arguments.native_db.resolve())
        native_tables = {
            str(row["name"])
            for row in native.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        candidate_tables = sorted(
            name
            for name in native_tables
            if any(
                token in name.lower()
                for token in (
                    "source",
                    "journal",
                    "node",
                    "record",
                    "message",
                    "interaction",
                    "session",
                    "reconciliation",
                )
            )
        )
        output["native"] = {
            "quick_check": [str(row[0]) for row in native.execute("PRAGMA quick_check")],
            "table_names": sorted(native_tables),
            "tables": {
                name: table_columns(native, name)
                for name in candidate_tables
            },
        }
        native_output = output["native"]
        assert isinstance(native_output, dict)
        if "records" in native_tables:
            for field in ("category", "source_kind", "state"):
                native_output[f"record_counts_by_{field}"] = {
                    str(row["value"]): int(row["count"])
                    for row in native.execute(
                        f"SELECT {field} AS value, COUNT(*) AS count FROM records "
                        f"GROUP BY {field} ORDER BY count DESC, {field}"
                    )
                }
            native_output["record_samples"] = [
                dict(row)
                for row in native.execute(
                    "SELECT memory_id, category, source_kind, turn_index, state, "
                    "substr(metadata_json, 1, 800) AS metadata_json "
                    "FROM records ORDER BY rowid LIMIT 8"
                )
            ]
        for table in (
            "v4_source_journal",
            "v4_batch_journal",
            "v4_message_commit_journal",
            "tmcra_service_messages",
            "tmcra_service_sessions",
        ):
            if table not in native_tables:
                continue
            native_output[f"{table}_count"] = int(
                native.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            columns = table_columns(native, table)
            if "status" in columns:
                native_output[f"{table}_status_counts"] = {
                    str(row["status"]): int(row["count"])
                    for row in native.execute(
                        f"SELECT status, COUNT(*) AS count FROM {table} "
                        "GROUP BY status ORDER BY status"
                    )
                }

    print(json.dumps(output, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
