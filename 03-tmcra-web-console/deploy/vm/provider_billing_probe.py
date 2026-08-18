#!/usr/bin/env python3
"""Read-only provider billing and key-pool diagnostic for TMCRA operations."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _rows(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-db", type=Path, required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--pool", default="deepseek-writer")
    args = parser.parse_args()

    now = time.time()
    with _connect(args.control_db) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "provider_calls" not in tables or "provider_keys" not in tables:
            raise SystemExit("provider ledger tables are missing")

        call_summary = _rows(
            connection,
            """
            SELECT status, COALESCE(error, '') AS error, COUNT(*) AS calls,
                   COUNT(DISTINCT key_id) AS distinct_keys,
                   MAX(created_at) AS latest_at
            FROM provider_calls
            WHERE scope_name=?
            GROUP BY status, COALESCE(error, '')
            ORDER BY latest_at DESC
            """,
            (args.scope,),
        )
        billing_by_key = _rows(
            connection,
            """
            SELECT key_id, COUNT(*) AS calls, MIN(created_at) AS first_at,
                   MAX(created_at) AS latest_at
            FROM provider_calls
            WHERE scope_name=? AND (
                LOWER(COALESCE(error, '')) LIKE '%http_status=402%'
                OR LOWER(COALESCE(error, '')) LIKE '%status=402%'
                OR LOWER(COALESCE(error, '')) LIKE '%insufficient%balance%'
            )
            GROUP BY key_id
            ORDER BY latest_at DESC
            """,
            (args.scope,),
        )
        recent_calls = _rows(
            connection,
            """
            SELECT call_id, job_id, stage_id, model, operation, status, key_id,
                   error, created_at, finished_at
            FROM provider_calls
            WHERE scope_name=?
            ORDER BY created_at DESC
            LIMIT 30
            """,
            (args.scope,),
        )
        pool_rows = _rows(
            connection,
            """
            SELECT key_id, ordinal, enabled, max_concurrency, cooldown_until,
                   failure_streak, success_count, failure_count, last_used_at,
                   CASE WHEN enabled=1 AND cooldown_until<=? THEN 1 ELSE 0 END
                       AS healthy
            FROM provider_keys
            WHERE pool=?
            ORDER BY ordinal
            """,
            (now, args.pool),
        )
        active_leases = int(
            connection.execute(
                "SELECT COUNT(*) FROM provider_leases WHERE pool=? AND expires_at>?",
                (args.pool, now),
            ).fetchone()[0]
        )

    output = {
        "scope": args.scope,
        "pool": args.pool,
        "call_summary": call_summary,
        "billing_failures": {
            "calls": sum(int(row["calls"]) for row in billing_by_key),
            "distinct_keys": len(billing_by_key),
            "by_key": billing_by_key,
        },
        "recent_calls": recent_calls,
        "pool_stats": {
            "total_keys": len(pool_rows),
            "enabled_keys": sum(int(row["enabled"]) for row in pool_rows),
            "healthy_keys": sum(int(row["healthy"]) for row in pool_rows),
            "active_leases": active_leases,
            "successes": sum(int(row["success_count"]) for row in pool_rows),
            "failures": sum(int(row["failure_count"]) for row in pool_rows),
        },
        "pool_keys": pool_rows,
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
